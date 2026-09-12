from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from app.domain.models import Case


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        n=Decimal(str(value).replace(',','').strip().removesuffix('%').removesuffix('％'))
        return n if n.is_finite() else None
    except InvalidOperation:
        return None


def classify(value, rule):
    if value is None or str(value).strip() in ('','-','—'):
        return None
    value=str(value).strip()
    matches=[]
    for i,b in enumerate(rule['bands']):
        if value in (b.get('values') or []):
            matches.append(i); continue
        # Categorical bands have values only and no numeric meaning.
        if not rule['unit']:
            continue
        if b.get('values') and b.get('high') is None and b.get('low') == 0 and not b.get('ranges'):
            continue
        n=number(value)
        if n is None or n < 0:
            continue
        ranges=b.get('ranges') or [[b.get('low',0),b.get('high')]]
        if any(n>=Decimal(str(lo)) and (hi is None or n<Decimal(str(hi))) for lo,hi in ranges):
            matches.append(i)
    return matches[0] if len(matches)==1 else None


def review(case: Case, ruleset: dict):
    checks=[]
    def check(key,title,status,message,actual=None,expected=None,**extra):
        row=dict(id=key,title=title,status=status,message=message,actual=actual,expected=expected,**extra)
        checks.append(row); return row
    applicable=case.locality==ruleset['locality'] and case.land_use==ruleset['land_use']
    if not applicable:
        check('scope','基準適用性','pending','案件地區或用地類別與基準不符，停止自動判定。')
    if not case.subject_name or not case.comparable_name:
        check('identity','標的識別','missing','請補齊比準地與比較標的識別。')
    factors={f.id:f for f in case.factors}
    sums={'individual':Decimal(0),'regional':Decimal(0)}
    abs_sums={'individual':Decimal(0),'regional':Decimal(0)}
    ready={'individual':True,'regional':True}
    for r in ruleset['rules']:
        scope=r['scope']; f=factors.get(r['id'])
        base=dict(factor_id=r['id'],scope=scope,group=r['group'],rule_page=r['source_page'],page=3 if scope=='individual' else 2)
        if not f:
            check(r['id'],r['name'],'missing','缺少此項資料。',**base);ready[scope]=False;continue
        base['page']=f.evidence.page
        if not applicable or not f.confirmed:
            check(r['id'],r['name'],'pending','請先核對原文並確認抽取內容及案件適用基準。',f.entered_rate,**base)
            ready[scope]=False;continue
        if f.exempt:
            check(r['id'],r['name'],'pending','免比較項目須由審查人員判定；'+('已附理由：'+f.note if f.note.strip() else '尚未填寫理由。'),f.entered_rate,**base)
            ready[scope]=False;continue
        if r.get('blocked'):
            check(r['id'],r['name'],'pending',r['warning'],f.entered_rate,**base);ready[scope]=False;continue
        if scope=='regional' and (not f.subject_grade or not f.comparable_grade):
            check(r['id'],r['name'],'missing','請補齊表 5-2 兩側原填等級，才能核對等級與修正率。',f.entered_rate,**base)
            ready[scope]=False;continue
        if scope=='regional' and case.subject_section and case.subject_section==case.comparable_section and f.subject is not None and f.comparable is not None:
            left=number(f.subject) if number(f.subject) is not None else f.subject.strip()
            right=number(f.comparable) if number(f.comparable) is not None else f.comparable.strip()
            if left!=right:
                check(r['id'],r['name'],'error','雙方區段號相同，但區域條件不一致；請核對區段與資料來源。',f.entered_rate,**base)
                ready[scope]=False;continue
        a,b=classify(f.subject,r),classify(f.comparable,r)
        labels=[x['label'] for x in r['bands']]
        raw_absent = all(
            value is None or (isinstance(value, str) and not value.strip())
            for value in (f.subject, f.comparable)
        )
        if r.get('allow_grade_only') and raw_absent and (a is None or b is None):
            if f.subject_grade in labels and f.comparable_grade in labels:
                a=labels.index(f.subject_grade);b=labels.index(f.comparable_grade)
        if a is None or b is None:
            msg='資料缺漏、單位不符或未能對應唯一級距。'
            if r.get('warning'): msg+=' '+r['warning']
            check(r['id'],r['name'],'missing',msg,f.entered_rate,**base);ready[scope]=False;continue
        expected=Decimal(str(r['matrix'][a][b]));actual=number(f.entered_rate)
        grade_bad=(f.subject_grade is not None and f.subject_grade!=labels[a]) or (f.comparable_grade is not None and f.comparable_grade!=labels[b])
        mismatch=actual!=expected or grade_bad
        status='pending' if mismatch and f.note.strip() else ('error' if mismatch else 'pass')
        message=f'比準地「{labels[a]}」對比較標的「{labels[b]}」，查矩陣為 {expected:+}%。'
        if grade_bad: message+=' 原填等級與級距不符。'
        if mismatch and f.note.strip(): message+=' 已附特殊調整理由，請人工審查。'
        check(r['id'],r['name'],status,message,f.entered_rate,float(expected),subject_grade=labels[a],comparable_grade=labels[b],**base)
        sums[scope]+=expected; abs_sums[scope]+=abs(expected)
        if status=='pending':ready[scope]=False
    for fid in factors.keys()-{r['id'] for r in ruleset['rules']}:
        check('unknown_'+fid,'未對應因素','pending',f'因素 {fid} 未包含在此版本基準中。')
        ready={'individual':False,'regional':False}
    t=case.totals
    def total_page(field):
        evidence = case.total_evidence.get(field)
        return evidence.page if case.document_id and evidence and evidence.quote.strip() else None
    # Arithmetic consistency uses entered values. Normative totals use independently recomputed values.
    for scope,field,label in [('individual','individual','個別因素合計'),('regional','regional_detail','表 5-2 區域因素總修正數')]:
        entered=[number(factors[r['id']].entered_rate) if r['id'] in factors else None for r in ruleset['rules'] if r['scope']==scope]
        actual=number(getattr(t,field))
        if t and case.totals_confirmed and all(x is not None for x in entered):
            expected=sum(entered,Decimal(0))
            check('sum_'+field,label+'・填值加總','pass' if actual==expected else 'error','依各細項原填修正率加總。',float(actual) if actual is not None else None,float(expected),total_field=field,page=total_page(field))
        if ready[scope] and case.totals_confirmed:
            expected=sums[scope]
            check('norm_'+field,label+'・基準重算','pass' if actual==expected else 'error','依已確認資料與基準矩陣重新計算。',float(actual) if actual is not None else None,float(expected),total_field=field,page=total_page(field))
        else:
            check('norm_'+field,label+'・基準重算','pending','上游資料或基準待確認，暫不判定總修正數。',total_field=field,page=total_page(field))
    def total_check(key,title,actual,expected,msg,field):
        if not case.totals_confirmed or expected is None or actual is None:
            check(key,title,'pending','總計欄位未確認或計算資料不足。',actual,total_field=field,page=total_page(field));return
        status='pass' if number(actual)==expected else 'error'
        if field in ['adjusted_price','trial_price'] and status=='error' and abs(number(actual)-expected)<=1:
            status='pending'
            msg+=' 與顯示值重算結果相差不超過 1 元，可能涉及中間值精度；請核對原始計算式，暫不認定填錯。'
        check(key,title,status,msg,actual,float(expected),total_field=field,page=total_page(field))
    total_check('cross','跨表抄填',t.regional_carried,number(t.regional_detail),'表 4 區域因素調整率應等於表 5-2 總修正數。','regional_carried')
    rates=[number(f.entered_rate) for f in case.factors if not f.exempt]
    expected_abs=None
    if len(rates)==len(ruleset['rules']) and all(v is not None for v in rates) and number(t.time_rate) is not None:
        expected_abs=sum(map(abs,rates),abs(number(t.time_rate)))
    total_check('absolute','調整百分率絕對值加總',t.absolute,expected_abs,'日期調整及區域、個別細項各自取絕對值後加總。','absolute')
    adjusted=None
    if t.normal_price is not None and t.time_rate is not None:
        adjusted=(number(t.normal_price)*(1+number(t.time_rate)/100)).quantize(Decimal('1'),rounding=ROUND_HALF_UP)
    total_check('adjusted','估價基準日單價',t.adjusted_price,adjusted,'正常單價 × (1 + 日期調整率)，本 MVP 依範本取至整元。','adjusted_price')
    trial=None
    if all(v is not None for v in [t.adjusted_price,t.regional_carried,t.individual]):
        trial=(number(t.adjusted_price)*(1+number(t.regional_carried)/100)*(1+number(t.individual)/100)).quantize(Decimal('1'),rounding=ROUND_HALF_UP)
    total_check('trial','比較標的試算價格',t.trial_price,trial,'以原填基準日單價 × (1 + 區域調整率) × (1 + 個別調整率)，四捨五入至元；不代表上游已通過審查。','trial_price')
    total_check('weight','單一比較標的權重',t.weight,Decimal(100),'本 MVP 支援單一比較標的，權重應為 100%。','weight')
    counts={k:Counter(c['status'] for c in checks)[k] for k in ['pass','error','pending','missing']}
    return dict(checks=checks,counts=counts,complete=not any(counts[k] for k in ['error','pending','missing']),
                ruleset_id=ruleset['id'],ruleset_version=ruleset['version'],
                computed={s:float(sums[s]) if ready[s] else None for s in sums})
