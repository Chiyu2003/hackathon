"""Small synthetic acceptance suite. No AWS calls unless --bedrock is supplied."""
import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import time
from app.bootstrap import build_service
from app.domain.models import Case, Factor
from app.domain.engine import review
from app.domain.rule_validation import validate_ruleset
from app.infrastructure.settings import Settings, ROOT
from app.infrastructure.bedrock_agent import PROMPT_VERSION
from app.infrastructure.text_pdf import read_pdf

FIXTURES=ROOT/'tests/fixtures/agent_eval'


def specifications():
    return json.loads((FIXTURES/'cases.json').read_text())


def prepare(service, spec):
    rules=service.repository.get_rules('jinshan-commercial-v1')
    rules.update(name='純合成驗收基準',source='Synthetic fixtures only; not valuation guidance',version='eval-v1')
    rules['rules']=[dict(id='width',name='寬度',group='合成測試',unit='m',scope='individual',source_page=1,
        bands=[dict(label='窄',low=0,high=10),dict(label='寬',low=10,high=None)],matrix=[[0,-2],[2,0]])]
    rules=service.repository.add_rules(validate_ruleset(rules))
    case=Case(title='合成驗收 '+spec['id'],valuation_date='2025-09-01',ruleset_id=rules['id'],
              subject_name='合成比準地',comparable_name='合成比較標的',
              factors=[Factor(id='width',subject=None if spec.get('missing') else '12',comparable='8',entered_rate=2,confirmed=True)])
    case=service.repository.save_case(case,'建立合成驗收資料',new=True)
    sources={}
    for item in spec['documents']:
        data=(FIXTURES/item['file']).read_bytes()
        pages=[dict(p,method='synthetic-pdf-text') for p in read_pdf(data)]
        assert '寬度' in pages[0]['text']
        bound=dict(rules,version='eval-old') if item.get('binding')=='other-version' else rules
        saved=service.repository.save_evidence_document(data,item['file'],pages,bound,date(2025,1,1),date(2025,12,31))
        if bound['version']==rules['version']:
            sources[saved['document_id']]=dict(sha256=hashlib.sha256(data).hexdigest(),pages=pages)
    return case,rules,sources


def assess(spec,result,case,rules,sources,tool_results):
    criteria={}
    trace=result.get('tool_trace',[])
    successful={row['tool'] for row in trace if row['status']=='success'}
    criteria['required_tools']=set(spec['tools'])<=successful
    criteria['expected_status']=result.get('status')==spec['expected_status']
    criteria['no_tool_errors']=all(row['status']=='success' for row in trace)
    hits=result.get('hits',[])
    citations_valid=True
    for hit in hits:
        span=hit['source'];doc=sources.get(span['document_id'])
        page=next((p for p in doc['pages'] if p['page']==span['page']),None) if doc else None
        citations_valid &= bool(page and span['document_sha256']==doc['sha256'] and
            span['quote']==page['text'][span['start']:span['end']] and hit['ruleset_id']==rules['id'] and hit['ruleset_version']==rules['version'])
    criteria['source_scope_and_quotes']=citations_valid
    ids={h['id'] for h in hits}
    statements=result.get('statements',[])
    criteria['statement_citations']=all(s['citation_ids'] and set(s['citation_ids'])<=ids for s in statements)
    if spec['expected_status']=='insufficient_evidence':criteria['abstained']=not statements
    else:criteria['answered']=bool(statements)
    if spec.get('empty_hits'):criteria['empty_hits']=not hits
    if spec.get('min_documents'):criteria['retrieved_documents']=len({h['source']['document_id'] for h in hits})>=spec['min_documents']
    if spec.get('review'):
        actual=result.get('review')
        criteria['review_matches_engine']=actual==review(case,rules)
        criteria['golden_numeric_result']=bool(actual and actual['computed']['individual']==spec['expected_individual'])
        width=next((c for c in actual['checks'] if c['id']=='width'),{}) if actual else {}
        criteria['golden_factor_status']=width.get('status')==spec['expected_width_status']
        criteria['not_false_complete']=bool(actual and actual['complete'] is False)
        if not spec.get('missing'):criteria['golden_matrix_rate']=width.get('expected')==2
    if 'get_rule' in spec['tools']:
        criteria['rule_tool_payload']=any(r['status']=='success' and r['data'].get('rule')==rules['rules'][0] and
                                        r['data'].get('ruleset_id')==rules['id'] and r['data'].get('ruleset_version')==rules['version'] for r in tool_results)
    # Automated checks do not prove semantic entailment or general model accuracy.
    return criteria


class RecordingModel:
    def __init__(self,delegate):self.delegate=delegate;self.results=[];self.turns=0
    def next_turn(self,context,history,tools):
        self.turns+=1
        self.results=[r for step in history for r in step.get('results', [])]
        return self.delegate.next_turn(context,history,tools)


def run(settings, selected=None, live=False):
    service=build_service(settings)
    native=service.rag.agent.model
    calls=[]
    if live:
        real=native.transport._client()
        class Metered:
            def converse(self,**kwargs):
                response=real.converse(**kwargs)
                calls.append(dict(stop_reason=response.get('stopReason'),usage=response.get('usage',{}),
                    tools=[b['toolUse']['name'] for b in response.get('output',{}).get('message',{}).get('content',[]) if 'toolUse' in b],
                    final_text=''.join(b.get('text','') for b in response.get('output',{}).get('message',{}).get('content',[])) if response.get('stopReason')=='end_turn' else ''))
                return response
        native.transport.client=Metered()
    report=dict(mode='real-bedrock' if live else 'fixture-validation',model=settings.model_id,region=settings.region,
                prompt_version=PROMPT_VERSION,suite_sha256=hashlib.sha256((FIXTURES/'cases.json').read_bytes()).hexdigest(),
                started_at=datetime.now(timezone.utc).isoformat(),data='synthetic PDFs and synthetic single-factor rules only',results=[])
    for spec in specifications():
        if selected and spec['id'] not in selected:continue
        start=time.monotonic();offset=len(calls)
        case,rules,sources=prepare(service,spec)
        expected=review(case,rules)
        if spec.get('review'):
            assert expected['computed']['individual']==spec['expected_individual']
            assert next(c for c in expected['checks'] if c['id']=='width')['status']==spec['expected_width_status']
        row=dict(id=spec['id'],question=spec['question'])
        if live:
            recording=RecordingModel(native);service.rag.agent.model=recording
            history=service.repository.audit(case.id)
            try:
                result=service.rag.agent.query(case.id,case.revision,spec['question'],True)
                criteria=assess(spec,result,case,rules,sources,recording.results)
                criteria['case_and_audit_unchanged']=service.repository.get_case(case.id)==case and service.repository.audit(case.id)==history
                row.update(criteria=criteria,passed=all(criteria.values()),response=result,tool_results=recording.results)
            except Exception as exc:
                # Fixed class names only: never print provider responses/credentials.
                row.update(passed=False,error_type=type(exc).__name__,tool_results=recording.results)
            row['model_turns']=recording.turns
            row['aws_calls']=calls[offset:]
        else:
            row['fixture_valid']=True
        row['elapsed_seconds']=round(time.monotonic()-start,2)
        report['results'].append(row)
        print(spec['id']+': '+str(row.get('passed','fixture validated')),flush=True)
    report['aws_requests']=len(calls)
    report['tokens']=sum(c['usage'].get('totalTokens',0) for c in calls)
    if live:report['passed']=bool(report['results']) and all(row['passed'] for row in report['results'])
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bedrock',action='store_true')
    parser.add_argument('--profile')
    parser.add_argument('--case',action='append',choices=[s['id'] for s in specifications()])
    parser.add_argument('--report',type=Path,default=ROOT/'.analysis/agent-evaluation.json')
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='landwise-agent-eval-') as temp:
        settings=Settings(data_dir=Path(temp),reference_dir=Path(temp),aws_profile=args.profile,ai_enabled=args.bedrock)
        report=run(settings,args.case,args.bedrock)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=False))
    if args.bedrock and not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
