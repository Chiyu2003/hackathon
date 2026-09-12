"""Convert recognized form text into an unconfirmed valuation draft."""
import re
import unicodedata
from app.domain.models import Case, Factor, Evidence

NUM=r'[+-]?\d[\d,]*(?:\.\d+)?'
INDIVIDUAL_IDS = 'area width depth shape frontage terrain road_type road_width school market park station business nuisance parking zoning coverage far restriction'.split()
REGIONAL_IDS = ['r_' + key for key in 'plan zoning coverage far ban restriction road_width avg_width station bus interchange development drainage terrain market park tourism parking utility funeral waste pollution department bank entertainment hotel customers shops'.split()]


def parse_case(pages, title, ruleset):
    pages = [dict(p, text=unicodedata.normalize('NFKC', p['text'])) for p in pages]
    rules=ruleset['rules']
    case=Case(title=title,source_kind='pdf',ruleset_id=ruleset['id'],locality=ruleset['locality'],land_use=ruleset['land_use'],factors=[Factor(id=r['id'],evidence=Evidence(page=3 if r['scope']=='individual' else 1,method='layout-parser')) for r in rules])
    case.extraction_warnings=['匯入內容尚未確認；請核對原文後勾選確認。']
    if ruleset.get('import_kind') == 'ocr-structured':
        case.extraction_warnings.append('題目欄位依上傳基準的因素名稱與級距整理；無法唯一對應的內容保持待確認。')
    else:
        case.extraction_warnings.append('目前支援提供範本的單一比較標的版型；不同版型或多比較標的請人工整理後匯入 JSON。')
    by_id = {factor.id: factor for factor in case.factors}
    _parse_location_fields(case, pages, ruleset)
    comparison=next((p for p in pages if re.search(r'表\s*4\s*比較法調查估價表',p['text'])),None)
    if not comparison:
        case.extraction_warnings.append('未找到表 4；已保留原文，請手動填寫或使用本機 AI 抽取。')
        _parse_dynamic_factors(case, pages, ruleset, by_id)
        return case
    text=comparison['text'];lines=text.splitlines()
    # Detect populated additional comparison columns before selecting any values.
    for line in lines:
        if re.search(r'(?<!\d)(?:7\s*面積|8\s*寬度|9\s*深度|14\s*面前道路寬度)',line) and len(re.findall(rf'({NUM})\s*%',line))>1:
            case.extraction_warnings.append('偵測到多筆已填比較標的；本版停止自動欄位抽取，避免將不同標的混在一起。')
            return case
    code=re.search(r'案號[：:]\s*([\w-]+)',text)
    if code:case.case_number=code[1]
    date=re.search(r'估價基準日[：:]\s*(\d+)',text)
    if date:case.valuation_date=date[1]
    for line in lines:
        if '0基本資料' in line:
            names=re.split(r'\s{2,}',line.split('0基本資料',1)[1].strip())
            if len(names)>2:
                case.extraction_warnings.append('偵測到多筆已填比較標的；本版保留原文，不將不同標的合併成單一案件。')
                return case
            if len(names)==2:case.subject_name,case.comparable_name=names
        if '地價區段' in line:
            sections=re.findall(r'\b[A-Z]\d{3}-\d{2}\b',line)
            if len(sections)>=2:case.subject_section,case.comparable_section=sections[:2]
    row_labels=['面積','寬度','深度','形狀','臨街情形','地勢','道路種類','面前道路寬度','接近學校之程度','接近市場之程度','接近公園、廣場之程度','接近車站之程度','接近商圈之程度','嫌惡設施','停車方便性','使用分區或編定用地','建蔽率','容積率','有無禁限建']
    for index,(factor_id,label) in enumerate(zip(INDIVIDUAL_IDS,row_labels),7):
        f = by_id.get(factor_id)
        if f is None: continue
        label_pattern = re.escape(label)
        for traditional, simplified in [('寬','宽'),('勢','势'),('場','场'),('編','编')]:
            label_pattern = label_pattern.replace(traditional, '[' + traditional + simplified + ']')
        for li,line in enumerate(lines):
            m=re.search(rf'(?<!\d){index}\s*{label_pattern}',line)
            if not m:continue
            tail=line[m.end():]
            # In this template the restriction values are printed one line above its label.
            if index==25 and '%' not in tail and li:
                tail=lines[li-1]
            rates=re.findall(rf'({NUM})\s*%',tail)
            if len(rates)!=1:
                # Coverage/FAR contain three percentages. Read the last as the rate.
                if index not in (23,24) or len(rates)!=3:break
            f.entered_rate=float(rates[-1].replace(',',''))
            tail=re.sub(rf'({NUM})\s*%\s*$','',tail).strip()
            if index in (7,8,9,23,24):
                tail=re.sub(r'^\([^)]*\)','',tail)
                vals=re.findall(NUM,tail)
                if len(vals)==2:f.subject,f.comparable=[v.replace(',','') for v in vals]
            elif 14<=index<=20:
                vals=re.findall(rf'({NUM})\s*M\b',tail,re.I)
                if len(vals)==2:f.subject,f.comparable=[v.replace(',','') for v in vals]
            else:
                vals=re.split(r'\s{2,}',tail)
                if index==25:vals=vals[-2:]
                if len(vals)==2:f.subject,f.comparable=vals
            f.evidence=Evidence(page=comparison['page'],quote=line.strip(),method='layout-parser')
            break
    patterns={'normal_price':rf'土地正常單價\s+({NUM})',
              'adjusted_price':rf'調整至估價基準日單價[^\n]*?\s{{2,}}({NUM})',
              'time_rate':rf'交易日期[^\n]*?({NUM})%',
              'regional_carried':rf'區域因素調整百分率[^\n]*?({NUM})%',
              'individual':rf'合計\s+({NUM})%',
              'absolute':rf'調整百分率絕對值加總[^\n]*?({NUM})%',
              'trial_price':rf'試算價格[^\n]*?\s{{2,}}({NUM})\s+',
              'weight':rf'試算價格[^\n]*?({NUM})%'}
    for name,pattern in patterns.items():
        m=re.search(pattern,text)
        if m:
            setattr(case.totals,name,float(m[1].replace(',','')))
            case.total_evidence[name] = Evidence(page=comparison['page'], quote=m[0].strip()[:3000], method='layout-parser')
    detail=next((p for p in pages if '影響地價區域因素分析明細表' in p['text']),None)
    if detail:
        rows=re.findall(r'([^\n]*?)\s+[1-9]\s+(優|稍優|普通|稍劣|劣|無|有)\s+[1-9]\s+(優|稍優|普通|稍劣|劣|無|有)\s+('+NUM+r')\s*$',detail['text'],re.M)
        if len(rows)==28:
            for factor_id,(quote,a,b,rate) in zip(REGIONAL_IDS,rows):
                f = by_id.get(factor_id)
                if f is None: continue
                f.subject_grade=a;f.comparable_grade=b;f.entered_rate=float(rate.replace(',',''))
                f.evidence=Evidence(page=detail['page'],quote=f'{quote.strip()} {a} / {b} / {rate}%',method='layout-parser')
        m=re.search(r'=\(1\)[^\n]*?\s{2,}('+NUM+r')[%％]',detail['text'])
        if m:
            case.totals.regional_detail=float(m[1].replace(',',''))
            case.total_evidence['regional_detail'] = Evidence(page=detail['page'], quote=m[0].strip()[:3000], method='layout-parser')
    survey=next((p for p in pages if re.search(r'表\s*1\s*地[價价]區段勘查表',p['text'])),None)
    if survey:
        # Only unambiguous single-line measurements are extracted automatically.
        patterns={
            'r_plan':r'都市計畫\(內外\)\s+(都市計畫內|都市計畫外)',
            'r_zoning':r'使用分區\(使用地類別\)\s+(\S+)',
            'r_coverage':r'建\s*蔽\s*率\s+(\d+)%',
            'r_far':r'容\s*積\s*率\s+(\d+)%',
            'r_ban':r'有無禁止建築\s+(無|有)',
            'r_restriction':r'有無限制建築[^\n]*?\s{2,}(無|有)',
            'r_road_width':r'主要道路[^\n]*?寬度[：:]\s*(\d+)',
            'r_avg_width':r'區段內道路平均寬度\s*(\d+)',
            'r_development':r'區段內道路規劃及闢建程度\s+(\S+)',
            'r_drainage':r'(有排水系統不易淹水|排水系統完善不曾淹水)',
            'r_terrain':r'(該區地勢平坦)',
            'r_customers':r'(顧客通行量多|顧客通行量稍多|顧客通行量普通|顧客通行量較少|顧客通行量少)',
            'r_shops':r'店鋪之毗連狀態\s+(\d+)%'}
        for f in [by_id[key] for key in REGIONAL_IDS if key in by_id]:
            pattern=patterns.get(f.id)
            m=re.search(pattern,survey['text']) if pattern else None
            if m:
                f.subject=m[1]
                if case.subject_section and case.subject_section==case.comparable_section:f.comparable=m[1]
                f.evidence.quote+='；表 1：'+m[0];f.evidence.page=survey['page']
        case.extraction_warnings.append('表 1 跨欄設施與勾選符號保留人工核對；同區段才將已抽取區域條件套用雙方。')
    _parse_dynamic_factors(case, pages, ruleset, by_id)
    if not any(f.subject is not None for f in case.factors):
        case.extraction_warnings.append('無法可靠辨識欄列；未以猜測值補齊。')
    return case


_LOCALITY = re.compile(
    r'([\u4e00-\u9fff]{2,4}[縣市][\u4e00-\u9fff]{1,5}(?:區|鄉|鎮|市))'
)
_ADDRESS_END = r'(?:號(?:之\d+)?|地號)'


def _parse_location_fields(case, pages, ruleset):
    """Extract explicit locality/address text without geocoding or inference."""

    text = '\n'.join(page['text'] for page in pages)
    expected = ruleset['locality']
    localities = list(dict.fromkeys(_LOCALITY.findall(text)))
    if expected in text:
        case.locality = expected
    elif ruleset.get('import_kind') == 'ocr-structured':
        case.locality = localities[0] if len(localities) == 1 else ''
        if case.locality:
            case.extraction_warnings.append(
                f'題目辨識地區為「{case.locality}」，與所選基準「{expected}」不同，已停止自動適用判定。'
            )
        else:
            case.extraction_warnings.append('題目未能唯一辨識縣市行政區，請人工補填並核對基準。')

    address_pattern = re.compile(
        rf'((?:{re.escape(expected)}|[\u4e00-\u9fff]{{2,4}}[縣市][\u4e00-\u9fff]{{1,5}}(?:區|鄉|鎮|市))'
        rf'[^\s，,；;。()（）]{{1,100}}?{_ADDRESS_END})'
    )
    subject_labels = ('比準地', '勘估標的', '估價標的', '宗地')
    comparable_labels = ('比較標的一', '比較標的1', '比較標的')
    unassigned = []
    for line in text.splitlines():
        addresses = list(dict.fromkeys(address_pattern.findall(line)))
        if not addresses:
            continue
        if any(label in line for label in subject_labels) and not case.subject_address:
            case.subject_address = addresses[0]
        elif any(label in line for label in comparable_labels) and not case.comparable_address:
            case.comparable_address = addresses[0]
        else:
            unassigned.extend(addresses)
    if not case.subject_address and len(unassigned) == 1:
        case.subject_address = unassigned[0]
    if case.subject_address and not case.subject_name:
        case.subject_name = case.subject_address
    if case.comparable_address and not case.comparable_name:
        case.comparable_name = case.comparable_address
    if ruleset.get('import_kind') == 'ocr-structured' and not case.subject_address:
        case.extraction_warnings.append('題目未能唯一辨識比準地詳細地址，請人工核對。')


def _parse_dynamic_factors(case, pages, ruleset, by_id):
    """Use only uploaded rule names/labels to conservatively read table rows."""

    if ruleset.get('import_kind') != 'ocr-structured':
        return
    extracted = 0
    for rule in ruleset['rules']:
        factor = by_id.get(rule['id'])
        if factor is None:
            continue
        compact_name = _compact(rule['name'])
        if not compact_name:
            continue
        for page in pages:
            for line in page['text'].splitlines():
                compact_line = _compact(line)
                if compact_name not in compact_line:
                    continue
                compact_tail = compact_line.split(compact_name, 1)[1]
                changed = False
                labels = [band['label'] for band in rule['bands']]
                grade_values = _ordered_tokens(compact_tail, labels)
                if len(grade_values) >= 2:
                    factor.subject_grade, factor.comparable_grade = grade_values[:2]
                    changed = True

                rate_matches = re.findall(rf'({NUM})\s*[%％]', line)
                rate_value = None
                if len(grade_values) >= 2 and rate_matches:
                    rate_value = rate_matches[-1]
                elif len(grade_values) >= 2:
                    trailing_rate = re.search(rf'({NUM})\s*$', line)
                    if trailing_rate:
                        rate_value = trailing_rate[1]

                aliases = [
                    value
                    for band in rule['bands']
                    for value in band.get('values', [])
                    if value and value not in labels
                ]
                categorical = _ordered_tokens(compact_tail, aliases)
                if len(categorical) >= 2:
                    factor.subject, factor.comparable = categorical[:2]
                    changed = True
                    if rate_matches:
                        rate_value = rate_matches[-1]
                elif rule.get('unit') and factor.subject is None:
                    raw = re.sub(r'^\s*\d+\s*', '', line)
                    raw = re.sub(rf'({NUM})\s*[%％]\s*$', '', raw)
                    numbers = [value.replace(',', '') for value in re.findall(NUM, raw)]
                    if len(numbers) == 2:
                        factor.subject, factor.comparable = numbers
                        changed = True
                        if rate_matches:
                            rate_value = rate_matches[-1]

                if rate_value is not None:
                    factor.entered_rate = float(rate_value.replace(',', ''))
                    changed = True

                if changed:
                    factor.evidence = Evidence(
                        page=page['page'], quote=line.strip()[:3000], method='ruleset-row-parser'
                    )
                    extracted += 1
                    break
            if factor.evidence.method == 'ruleset-row-parser':
                break
    case.extraction_warnings.append(
        f'已依所選 structured ruleset 對應 {extracted} 個因素列；其餘欄位未猜測填值。'
    )


def _ordered_tokens(text, values):
    originals = {}
    for value in values:
        compact = _compact(value)
        if compact and compact not in originals:
            originals[compact] = value
    choices = sorted(originals, key=len, reverse=True)
    if not choices:
        return []
    pattern = re.compile('|'.join(re.escape(value) for value in choices))
    return [originals[match.group(0)] for match in pattern.finditer(text)]


def _compact(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value)))
