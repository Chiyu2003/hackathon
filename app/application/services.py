"""Valuation use cases. No imports of FastAPI, AWS, Paddle or SQLite."""
from app.application.drafts import parse_case
from app.application.evidence import normalized
from app.application.rag import RagService
from app.application.ports import FieldExtractor, PdfReader, ReviewRepository, RevisionConflict
from app.domain.engine import review
from app.domain.confirmation import invalidate_confirmations
from app.domain.models import Case
from app.domain.rule_validation import validate_ruleset
from app.domain.sample import sample_case
from app.application.export_contracts import ExportUnavailable, FormRenderer


class ReviewService:
    def __init__(self, repository: ReviewRepository, pdf: PdfReader, ai: FieldExtractor,
                 rag: RagService | None = None, renderer: FormRenderer | None = None,
                 ruleset_import=None):
        self.repository, self.pdf, self.ai = repository, pdf, ai
        self.rag = rag
        self.renderer = renderer
        self.ruleset_import = ruleset_import

    def export_document(self, case_id, kind, revision, generated_at):
        case = self.repository.get_case(case_id)
        if case.revision != revision:
            raise RevisionConflict('案件已更新，請重新載入後再匯出。')
        if self.renderer is None:
            raise ExportUnavailable('書表輸出尚未設定。')
        rules = self.repository.get_rules(case.ruleset_id)
        result = review(case, rules)
        try:
            artifact = self.renderer.render(case.model_copy(deep=True), result, rules, kind, generated_at)
        except (ImportError, OSError) as error:
            raise ExportUnavailable('書表產製失敗；請檢查輸出套件、模板與中文字型設定。') from error
        if self.repository.get_case(case_id).revision != revision:
            raise RevisionConflict('產製期間案件已更新，請重新匯出。')
        return artifact

    def seed_examples(self, document=None):
        if self.repository.list_cases():
            return
        document_id = self.repository.save_document(*document) if document else None
        for demo in (False, True):
            case = sample_case(demo)
            case.document_id = document_id
            self.repository.save_case(case, '建立內建範例', new=True)

    def payload(self, case):
        return {'case': case.model_dump(), 'review': review(case, self.repository.get_rules(case.ruleset_id))}

    def get_case(self, case_id):
        return self.payload(self.repository.get_case(case_id))

    def list_cases(self):
        return [dict(id=case.id, title=case.title, case_number=case.case_number, demo=case.demo,
                     updated=updated, counts=self.payload(case)['review']['counts'], source_kind=case.source_kind)
                for case, updated in self.repository.list_cases()]

    def validate_case(self, case):
        self.repository.get_rules(case.ruleset_id)
        ids = [f.id for f in case.factors]
        if len(ids) != len(set(ids)):
            raise ValueError('因素 ID 不可重複。')
        if case.document_id:
            document = self.repository.get_document(case.document_id)
            pages = {page['page']: normalized(page['text']) for page in document['pages']}
            for evidence in case.total_evidence.values():
                quote = normalized(evidence.quote)
                if not quote or quote not in pages.get(evidence.page, ''):
                    raise ValueError('計算欄位的來源頁碼或引文不符原文。')

    def save_case(self, case: Case, *, new=False):
        previous = None if new else self.repository.get_case(case.id)
        if previous is not None and previous.revision != case.revision:
            raise RevisionConflict('案件已更新，請重新載入。')
        candidate = invalidate_confirmations(previous, case)
        self.validate_case(candidate)
        saved = self.repository.save_case(candidate, '建立或匯入案件' if new else '儲存欄位與重新審查', new=new)
        return self.payload(saved)

    def copy_case(self, case_id: str, revision: int):
        source = self.repository.get_case(case_id)
        if source.revision != revision:
            raise RevisionConflict('案件已更新，請重新載入後再複製。')

        copied = source.model_copy(deep=True)
        suffix = ' · 副本'
        copied.title = source.title[:150 - len(suffix)] + suffix
        copied = invalidate_confirmations(None, copied)
        self.validate_case(copied)
        saved = self.repository.save_case(
            copied,
            f'複製案件（來源 {source.id}，版本 {source.revision}）',
            new=True,
        )
        return self.payload(saved)

    def create_sample(self, kind, document=None):
        if kind not in ('original', 'errors'):
            raise ValueError('未知的範例類型。')
        case = sample_case(kind == 'errors')
        if document:
            case.document_id = self.repository.save_document(*document)
        return self.payload(self.repository.save_case(case, '建立範例副本', new=True))

    def upload(self, data, name, ruleset_id):
        ruleset = self.repository.get_rules(ruleset_id)
        pages = self.pdf.read(data)
        case = parse_case(pages, name.removesuffix('.pdf')[:140] or '匯入案件', ruleset)
        case.extraction_warnings.insert(0, 'PaddleOCR 已辨識頁面文字；表格欄位及比較方向仍須人工核對。')
        for factor in case.factors:
            factor.evidence.method = 'paddleocr-layout'
        case.document_id = self.repository.save_document(data, name[:200], pages)
        return self.payload(self.repository.save_case(case, '上傳 PDF 與 PaddleOCR 辨識', new=True))

    def fix(self, case_id, check_id, revision):
        case = self.repository.get_case(case_id)
        previous = case.model_copy(deep=True)
        if revision != case.revision:
            raise RevisionConflict('案件已更新，請重新載入。')
        result = review(case, self.repository.get_rules(case.ruleset_id))
        item = next((row for row in result['checks'] if row['id'] == check_id), None)
        if not item or item['status'] != 'error' or item['expected'] is None:
            raise ValueError('此項目前沒有可直接採用的修正建議。')
        if item.get('factor_id'):
            factor = next(f for f in case.factors if f.id == item['factor_id'])
            factor.entered_rate = item['expected']
            if factor.subject_grade is not None:
                factor.subject_grade = item['subject_grade']
            if factor.comparable_grade is not None:
                factor.comparable_grade = item['comparable_grade']
        elif item.get('total_field'):
            setattr(case.totals, item['total_field'], item['expected'])
        else:
            raise ValueError('請手動處理此項。')
        return self.payload(self.repository.save_case(invalidate_confirmations(previous, case), '採用建議：' + item['title']))

    def extract_ai(self, case_id, revision, cloud_data_approved=False):
        if cloud_data_approved is not True:
            raise ValueError('請先確認本文件符合競賽上雲規範；含個資或財務資訊的文件不可直接送至 AWS。')
        case = self.repository.get_case(case_id)
        if revision != case.revision:
            raise RevisionConflict('案件已更新，請重新載入。')
        if not case.document_id:
            raise ValueError('請先匯入 PDF。')
        document = self.repository.get_document(case.document_id)
        factors = self.ai.extract(document['pages'], self.repository.get_rules(case.ruleset_id))
        if self.repository.get_case(case_id).revision != revision:
            raise RevisionConflict('抽取期間案件已更新，請重新載入後再試。')
        return dict(factors=[f.model_dump() for f in factors], revision=revision,
                    message='Bedrock 草稿尚未套用。引用與欄位值已做原文存在性檢查，兩側對應仍須人工確認。')

    def create_ruleset(self, ruleset):
        return self.repository.add_rules(validate_ruleset(ruleset))
