"""업종 분류 하나로 (2026-10-31 사용자 결정: 분류표 통일).

예전에는 레이더·흐름판·노션 기록·Global High-Radar가 서로 다른 분류표를 썼다. 이 모듈이 공통 분류다.
- 큰 업종: 노션 52주 신고가 기록이 쓰는 21개(노션 선택지와 같다). 순서는 ① 한국식 테마 표(직접 확인한 종목)
  → ② 야후 파이낸스 업종(taxonomy/yahoo_class.json 캐시, 없으면 조회) → ③ WAMO 분류표의 대분류를 21개로 옮긴 값.
- 세부 산업: WAMO 산업 분류표(시장 흐름판 config/baskets.json, 제품 단위) 바구니 이름을 먼저 쓴다. 한 종목이 여러
  바구니에 들어가면(ESS 겹침 소속 등) 모두 baskets 에 남긴다. 표에 없으면 ①·② 의 업종 이름.
원본: global-high-radar claude-automations/state/notion-52w/n52_class.py 의 표를 옮겨 왔다(이후 이 파일이 원본).
"""
import json
import urllib.parse
import urllib.request
from pathlib import Path

CACHE = Path(__file__).resolve().parent / 'taxonomy' / 'yahoo_class.json'
BASKETS_URL = 'https://raw.githubusercontent.com/dhandori/industry-strength/main/config/baskets.json'
BIG = ('2차전지', 'IT 하드웨어', '경기 소비재', '금융', '기계·건설', '기타', '미디어·통신', '바이오·헬스케어', '반도체',
       '부동산', '산업 서비스', '소재', '소프트웨어·인터넷', '에너지', '운송', '유틸리티', '자동차', '전력·에너지 설비',
       '조선·방산·항공', '지주사', '필수 소비재')

# 야후 업종 → (업종, 큰 업종)
Y = {
 'Semiconductors': ('반도체', '반도체'),
 'Semiconductor Equipment & Materials': ('반도체 장비·소재', '반도체'),
 'Electronic Components': ('전자 부품', 'IT 하드웨어'),
 'Communication Equipment': ('통신·네트워크 장비', 'IT 하드웨어'),
 'Computer Hardware': ('컴퓨터 하드웨어', 'IT 하드웨어'),
 'Consumer Electronics': ('가전·전자제품', 'IT 하드웨어'),
 'Scientific & Technical Instruments': ('계측·광학 장비', 'IT 하드웨어'),
 'Electronics & Computer Distribution': ('전자 유통', 'IT 하드웨어'),
 'Solar': ('태양광', '전력·에너지 설비'),
 'Software—Application': ('응용 소프트웨어', '소프트웨어·인터넷'),
 'Software—Infrastructure': ('인프라·보안 소프트웨어', '소프트웨어·인터넷'),
 'Information Technology Services': ('IT 서비스', '소프트웨어·인터넷'),
 'Internet Content & Information': ('인터넷 플랫폼', '소프트웨어·인터넷'),
 'Electronic Gaming & Multimedia': ('게임', '미디어·통신'),
 'Entertainment': ('엔터·콘텐츠', '미디어·통신'),
 'Broadcasting': ('방송', '미디어·통신'),
 'Publishing': ('출판', '미디어·통신'),
 'Advertising Agencies': ('광고', '미디어·통신'),
 'Telecom Services': ('통신 서비스', '미디어·통신'),
 'Oil & Gas Integrated': ('석유 메이저', '에너지'),
 'Oil & Gas E&P': ('석유·가스 생산', '에너지'),
 'Oil & Gas Refining & Marketing': ('정유', '에너지'),
 'Oil & Gas Midstream': ('미드스트림(가스관·유조선)', '에너지'),
 'Oil & Gas Equipment & Services': ('유전 장비·서비스', '에너지'),
 'Oil & Gas Drilling': ('시추', '에너지'),
 'Thermal Coal': ('석탄', '에너지'),
 'Coking Coal': ('석탄', '에너지'),
 'Uranium': ('우라늄', '에너지'),
 'Utilities—Regulated Electric': ('전력 유틸리티', '유틸리티'),
 'Utilities—Independent Power Producers': ('독립 발전사', '유틸리티'),
 'Utilities—Renewable': ('신재생 발전', '유틸리티'),
 'Utilities—Diversified': ('종합 유틸리티', '유틸리티'),
 'Utilities—Regulated Gas': ('가스 공급', '유틸리티'),
 'Utilities—Regulated Water': ('수도', '유틸리티'),
 'Electrical Equipment & Parts': ('전기 장비', '전력·에너지 설비'),
 'Aerospace & Defense': ('방산·항공우주', '조선·방산·항공'),
 'Specialty Industrial Machinery': ('산업 기계', '기계·건설'),
 'Farm & Heavy Construction Machinery': ('건설기계·농기계', '기계·건설'),
 'Engineering & Construction': ('건설·엔지니어링', '기계·건설'),
 'Building Products & Equipment': ('건축 자재·설비', '기계·건설'),
 'Building Materials': ('시멘트·건자재', '기계·건설'),
 'Metal Fabrication': ('금속 가공', '기계·건설'),
 'Tools & Accessories': ('공구·부품', '기계·건설'),
 'Industrial Distribution': ('산업재 유통', '기계·건설'),
 'Pollution & Treatment Controls': ('환경 설비', '기계·건설'),
 'Conglomerates': ('지주사·복합기업', '지주사'),
 'Rental & Leasing Services': ('렌탈·리스', '산업 서비스'),
 'Specialty Business Services': ('기업 서비스', '산업 서비스'),
 'Consulting Services': ('컨설팅', '산업 서비스'),
 'Staffing & Employment Services': ('인력·채용', '산업 서비스'),
 'Security & Protection Services': ('보안 서비스', '산업 서비스'),
 'Waste Management': ('폐기물 처리', '산업 서비스'),
 'Business Equipment & Supplies': ('사무기기', '산업 서비스'),
 'Airlines': ('항공사', '운송'),
 'Airports & Air Services': ('공항·항공 서비스', '운송'),
 'Marine Shipping': ('해운', '운송'),
 'Railroads': ('철도', '운송'),
 'Trucking': ('육상 운송', '운송'),
 'Integrated Freight & Logistics': ('물류·택배', '운송'),
 'Chemicals': ('화학', '소재'),
 'Specialty Chemicals': ('특수 화학', '소재'),
 'Agricultural Inputs': ('비료·농약', '소재'),
 'Steel': ('철강', '소재'),
 'Aluminum': ('알루미늄', '소재'),
 'Copper': ('구리', '소재'),
 'Gold': ('금', '소재'),
 'Silver': ('은', '소재'),
 'Other Precious Metals & Mining': ('귀금속', '소재'),
 'Other Industrial Metals & Mining': ('비철금속·광물', '소재'),
 'Paper & Paper Products': ('제지', '소재'),
 'Lumber & Wood Production': ('목재', '소재'),
 'Packaging & Containers': ('포장재', '소재'),
 'Biotechnology': ('바이오', '바이오·헬스케어'),
 'Drug Manufacturers—General': ('대형 제약', '바이오·헬스케어'),
 'Drug Manufacturers—Specialty & Generic': ('제약', '바이오·헬스케어'),
 'Medical Devices': ('의료기기', '바이오·헬스케어'),
 'Medical Instruments & Supplies': ('의료 소모품', '바이오·헬스케어'),
 'Diagnostics & Research': ('진단·연구 장비', '바이오·헬스케어'),
 'Healthcare Plans': ('건강보험', '바이오·헬스케어'),
 'Medical Care Facilities': ('병원·의료 서비스', '바이오·헬스케어'),
 'Medical Distribution': ('의약품 유통', '바이오·헬스케어'),
 'Health Information Services': ('헬스케어 IT', '바이오·헬스케어'),
 'Auto Manufacturers': ('자동차', '자동차'),
 'Auto Parts': ('자동차 부품', '자동차'),
 'Auto & Truck Dealerships': ('자동차 판매', '자동차'),
 'Recreational Vehicles': ('레저 차량', '자동차'),
 'Household & Personal Products': ('화장품·생활용품', '필수 소비재'),
 'Packaged Foods': ('식품', '필수 소비재'),
 'Confectioners': ('제과', '필수 소비재'),
 'Beverages—Non-Alcoholic': ('음료', '필수 소비재'),
 'Beverages—Brewers': ('주류', '필수 소비재'),
 'Beverages—Wineries & Distilleries': ('주류', '필수 소비재'),
 'Tobacco': ('담배', '필수 소비재'),
 'Farm Products': ('농산물', '필수 소비재'),
 'Food Distribution': ('식품 유통', '필수 소비재'),
 'Grocery Stores': ('식료품 소매', '필수 소비재'),
 'Discount Stores': ('할인점', '필수 소비재'),
 'Education & Training Services': ('교육', '경기 소비재'),
 'Apparel Manufacturing': ('의류', '경기 소비재'),
 'Apparel Retail': ('의류 소매', '경기 소비재'),
 'Footwear & Accessories': ('신발·잡화', '경기 소비재'),
 'Textile Manufacturing': ('섬유', '경기 소비재'),
 'Luxury Goods': ('명품', '경기 소비재'),
 'Furnishings, Fixtures & Appliances': ('가구·가전', '경기 소비재'),
 'Home Improvement Retail': ('홈센터', '경기 소비재'),
 'Department Stores': ('백화점', '경기 소비재'),
 'Specialty Retail': ('전문 소매', '경기 소비재'),
 'Internet Retail': ('온라인 쇼핑', '경기 소비재'),
 'Restaurants': ('외식', '경기 소비재'),
 'Lodging': ('호텔', '경기 소비재'),
 'Resorts & Casinos': ('카지노·리조트', '경기 소비재'),
 'Gambling': ('게이밍', '경기 소비재'),
 'Leisure': ('레저', '경기 소비재'),
 'Travel Services': ('여행', '경기 소비재'),
 'Personal Services': ('생활 서비스', '경기 소비재'),
 'Residential Construction': ('주택 건설', '기계·건설'),
 'Banks—Diversified': ('대형 은행', '금융'),
 'Banks—Regional': ('지역 은행', '금융'),
 'Capital Markets': ('증권·투자은행', '금융'),
 'Asset Management': ('자산운용', '금융'),
 'Credit Services': ('카드·결제', '금융'),
 'Financial Data & Stock Exchanges': ('거래소·금융정보', '금융'),
 'Financial Conglomerates': ('금융 지주', '금융'),
 'Insurance Brokers': ('보험 중개', '금융'),
 'Insurance—Diversified': ('종합 보험', '금융'),
 'Insurance—Life': ('생명보험', '금융'),
 'Insurance—Property & Casualty': ('손해보험', '금융'),
 'Insurance—Reinsurance': ('재보험', '금융'),
 'Insurance—Specialty': ('특수 보험', '금융'),
 'Mortgage Finance': ('주택금융', '금융'),
 'Shell Companies': ('스팩·기타', '금융'),
 'Real Estate Services': ('부동산 서비스', '부동산'),
 'Real Estate—Development': ('부동산 개발', '부동산'),
 'Real Estate—Diversified': ('부동산', '부동산'),
}
def _reit(ind):
    return ('리츠', '부동산') if ind.startswith('REIT') else None

# 한국식 테마 표: 코드 → (업종, 큰 업종). 회사 사업을 확인한 종목만 넣는다(모르면 넣지 않음).
_SHIP = ('조선', '조선·방산·항공'); _SHIPEQ = ('조선 기자재', '조선·방산·항공')
_DEF = ('방산', '조선·방산·항공'); _AERO = ('항공우주', '조선·방산·항공')
_BAT = ('2차전지 셀', '2차전지'); _BATM = ('2차전지 소재', '2차전지'); _BATP = ('배터리·부품', '2차전지')
_NUC = ('원전', '전력·에너지 설비'); _GRID = ('전력기기', '전력·에너지 설비'); _CABLE = ('전선', '전력·에너지 설비')
_MEM = ('메모리', '반도체'); _ROBOT = ('로봇', '기계·건설')
THEME = {
 # 조선
 '329180': _SHIP, '009540': _SHIP, '042660': _SHIP, '010140': _SHIP, '010620': _SHIP, '097230': _SHIP,
 '443060': ('선박 유지보수', '조선·방산·항공'),
 '082740': _SHIPEQ, '071970': _SHIPEQ, '077970': _SHIPEQ, '075580': _SHIPEQ, '017960': _SHIPEQ, '033500': _SHIPEQ,
 # 방산·항공우주
 '012450': _DEF, '079550': _DEF, '064350': _DEF, '103140': _DEF, '272210': _DEF, '047810': _AERO,
 # 2차전지
 '373220': _BAT, '006400': _BAT,
 '086520': _BATM, '247540': _BATM, '450080': _BATM, '003670': _BATM, '066970': _BATM, '348370': _BATM, '005070': _BATM,
 '278280': _BATM, '121600': _BATM, '020150': _BATM, '361610': _BATM, '336370': _BATM, '393890': _BATM,
 '010580': _BATP, '419050': _BATP,
 # 원전·전력기기·전선
 '034020': _NUC, '052690': _NUC, '051600': _NUC, '083650': _NUC,
 '010120': _GRID, '267260': _GRID, '298040': _GRID, '103590': _GRID, '033100': _GRID, '062040': _GRID,
 '001440': _CABLE, '000500': _CABLE, '229640': _CABLE,
 # 반도체
 '005930': _MEM, '000660': _MEM,
 # 로봇
 '454910': _ROBOT, '277810': _ROBOT, '108490': _ROBOT, '090360': _ROBOT, '396300': _ROBOT,
 # 기타 확인분
 '453340': ('급식·식자재', '필수 소비재'), '021240': ('가전 렌탈', '경기 소비재'),
}
US_THEME = {}


# WAMO 분류표의 대분류 → 21개 큰 업종(야후·테마 표로 정하지 못한 바구니 종목에만 쓴다).
WAMO_GROUP = {
    '반도체': '반도체', '2차전지': '2차전지', '전자·IT하드웨어': 'IT 하드웨어', '전력·에너지': '전력·에너지 설비',
    '조선·기계': '조선·방산·항공', '방산·우주': '조선·방산·항공', '자동차': '자동차', '헬스케어': '바이오·헬스케어',
    '인터넷·소프트웨어': '소프트웨어·인터넷', '소프트웨어·인터넷': '소프트웨어·인터넷', '금융': '금융', '부동산': '부동산',
    '건설·소재': '기계·건설', '소비재': '경기 소비재', '미디어·레저': '미디어·통신', '운송·통신': '운송',
    'AI 인프라': 'IT 하드웨어', '산업재': '기계·건설', '에너지·소재': '에너지', '통신·부동산': '미디어·통신',
}


def load_baskets(fetch=None):
    """{시장('KR'|'US'): {코드: [바구니 이름...]}} — 처음 나온 바구니가 대표 세부 산업."""
    if fetch is None:
        with urllib.request.urlopen(urllib.request.Request(BASKETS_URL, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20) as r:
            data = json.loads(r.read().decode('utf-8'))
    else:
        data = fetch(BASKETS_URL)
    out = {}
    for market, block in (data.get('markets') or {}).items():
        table = out.setdefault(market, {})
        for item in block.get('items') or []:
            for m in item.get('members') or []:
                code = str(m.get('code') or '').upper()
                names = table.setdefault(code, {'group': item.get('group'), 'names': []})['names']
                if code and item.get('name') not in names:
                    names.append(item.get('name'))
    return out


def _yahoo_lookup(market, code):
    qs = [code + '.KS', code + '.KQ'] if market == 'KR' else [code.replace('.', '-')]
    for q in qs:
        try:
            url = 'https://query2.finance.yahoo.com/v1/finance/search?' + urllib.parse.urlencode({'q': q, 'quotesCount': 1, 'newsCount': 0})
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=15) as r:
                d = json.loads(r.read().decode('utf-8'))
            for x in d.get('quotes', []):
                if str(x.get('symbol', '')).upper() == q.upper():
                    return {'sector': x.get('sector'), 'industry': x.get('industry')}
        except Exception:  # noqa: BLE001 - 분류는 결과를 막지 않는다
            continue
    return None


class Classifier:
    """classify('KR'|'US', 코드) → {'group': 큰 업종, 'industry': 세부 산업, 'baskets': [...], 'source': ...}."""

    def __init__(self, baskets=None, cache=None, lookup=_yahoo_lookup):
        self.baskets = baskets or {}
        try:
            self.cache = cache if cache is not None else json.loads(CACHE.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.cache = {}
        self.lookup = lookup

    def _yahoo(self, market, code):
        key = ('KRX:' if market == 'KR' else 'US:') + code
        if key not in self.cache and self.lookup:
            self.cache[key] = self.lookup(market, code)
        ind = (self.cache.get(key) or {}).get('industry')
        return (Y.get(ind) or _reit(ind)) if ind else None

    def classify(self, market, code):
        code = str(code).upper()
        theme = (THEME if market == 'KR' else US_THEME).get(code)
        basket = (self.baskets.get(market) or {}).get(code)
        hit, source = (theme, '테마표') if theme else (self._yahoo(market, code), '야후')
        if hit:
            industry, group = hit
        elif basket:
            industry, group, source = basket['names'][0], WAMO_GROUP.get(basket['group'], '기타'), 'WAMO 분류표'
        else:
            industry, group, source = '기타', '기타', '분류 없음'
        if basket:
            industry, source = basket['names'][0], source + '+WAMO 분류표'
        return {'group': group, 'industry': industry, 'baskets': list(basket['names']) if basket else [], 'source': source}
