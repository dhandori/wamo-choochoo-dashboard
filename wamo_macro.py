"""매크로 지수 — 한·미 유동성·신용·변동성·심리 지표를 모아 5단계 위험 단계로 판정한다.

산업강도판(dhandori/industry-strength)의 '🌐 매크로 지수' 탭과 매일 아침 텔레그램 알림이 이 파일의 결과(wamo_macro.json)를 쓴다.
다른 WAMO 갱신 작업과 섞이지 않도록 별도 파일·별도 실행(macro.yml)으로 돈다.

판정 방식
  1) 수준: 최근 5년 중 '위험한 쪽'으로 몇 % 위치인지(백분위)
       하위 50% 🟢5 정상 · 50~75% 🔵4 관심 · 75~90% 🟡3 주의 · 90~97% 🟠2 경계 · 97%↑ 🔴1 위기
  2) 속도 보정: 1개월(21거래일) 변화가 지난 5년의 1개월 변화들과 비교해
       평소보다 유난히 크면(표준점수 2 이상) 한 단계, 아주 크면(3 이상) 두 단계 더 위험하게
  금리차(장단기)는 너무 낮아도(역전) 너무 높아도(급격한 재가파름) 경기 신호라서 양쪽 끝을 모두 본다.
  공포탐욕지수는 0~100 점수 자체가 기준이라 고정 구간을 쓴다.
  3) 절대 기준 바닥(2026-11-22): VIX 30↑·VKOSPI 40↑ 은 최소 🟠경계, VIX 40↑·VKOSPI 60↑ 은 🔴위기.
       공포지수가 오래 높으면 '5년 중 위치'가 무뎌지는 것을 막는다.

종합 판정(2026-11-22 사용자 결정): 지표를 한데 평균하지 않고 나라별로 두 줄로 나눠 낸다.
  '주식시장 충격'(변동성·심리·주가 낙폭)과 '신용·자금 스트레스'(금리차·유동성). 나라의 대표 단계는 둘 중 나쁜 쪽,
  전체 대표 단계(composite.level)는 한국·미국 중 나쁜 쪽이다. 11/20 급락 때 채권 지표가 조용하다는 이유로
  종합이 '관심'~'경계'에 머문 것을 고치려는 것이다. 주가를 직접 보는 지표(1년 고점 대비 낙폭)도 이때 넣었다.

자료 출처(모두 무료·공개)
  한국은행 ECOS(인증키 ECOS_API_KEY — GitHub 비밀값), 미국 연준 FRED,
  야후 파이낸스(VIX·VIX3M·S&P500·코스피. VIX는 FRED가 하루 늦어 2026-11-22 야후로 바꿨고 FRED는 예비),
  한국투자증권 API(VKOSPI, 지수코드 0503), feargreedmeter.com·finhacker.cz(CNN 공포탐욕)
"""
from __future__ import annotations

import json
import math
import os
import re
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'wamo_macro.json'
HISTORY = ROOT / 'wamo_macro_history.json'   # 출처가 과거치를 주지 않는 지표(VKOSPI·공포탐욕)를 매일 쌓는 곳
KST = timezone(timedelta(hours=9))
YEARS = 5
CHANGE_DAYS = 21          # 1개월 ≈ 21거래일
CHART_DAYS = 520          # 화면 그래프: 최근 약 2년 (2026-10-25 1년→2년)
UA = {'User-Agent': 'Mozilla/5.0'}
LEVELS = {
    5: ('🟢', '정상'), 4: ('🔵', '관심'), 3: ('🟡', '주의'), 2: ('🟠', '경계'), 1: ('🔴', '위기'),
}

# ── 지표 정의 ───────────────────────────────────────────────────────────────
# risk: 'high' 높을수록 위험 / 'low' 낮을수록 위험 / 'both' 양쪽 끝 모두 경계
INDICATORS = [
    dict(key='kr_aa', group='한국 신용', name='우량 회사채 가산금리 (AA- − 국고채 3년)', unit='bp', risk='high',
         help='튼튼한 회사가 나라보다 돈을 빌릴 때 더 내는 이자입니다. 이 차이가 벌어지면 회사들이 돈 빌리기가 어려워진 것입니다.'),
    dict(key='kr_bbb', group='한국 신용', name='비우량 회사채 가산금리 (BBB- − 국고채 3년)', unit='bp', risk='high',
         help='신용등급이 낮은 편인 회사가 나라보다 더 내는 이자입니다. 돈줄이 막히기 시작하면 가장 먼저, 가장 크게 벌어집니다.'),
    dict(key='kr_cpcd', group='한국 신용', name='기업 단기자금 금리차 (CP − CD 91일)', unit='bp', risk='high',
         help='회사가 3개월짜리로 급히 빌리는 돈(CP)의 이자에서 은행 단기 금리(CD)를 뺀 값입니다. 이 차이가 벌어지면 회사들이 당장 쓸 돈 사정이 나빠졌다는 신호입니다.'),
    dict(key='kr_curve', group='한국 금리', name='한국 장단기 금리차 (국고채 10년 − 3년)', unit='bp', risk='both',
         help='10년 국채 금리에서 3년 국채 금리를 뺀 값입니다. 0 아래로 뒤집히면 경기 둔화 신호이고, 갑자기 크게 벌어져도 불안 신호입니다.'),
    dict(key='us_hy', group='미국 신용', name='미국 하이일드 스프레드 (저신용 회사채)', unit='bp', risk='high',
         help='신용등급이 낮은 미국 회사가 국채보다 더 내는 이자입니다. 위기가 오면 가장 먼저, 가장 빠르게 치솟는 대표 경보등입니다.'),
    dict(key='us_ig', group='미국 신용', name='미국 투자등급 스프레드 (우량 회사채)', unit='bp', risk='high',
         help='튼튼한 미국 회사가 국채보다 더 내는 이자입니다. 하이일드보다 느리게 움직이지만, 이것까지 오르면 불안이 넓게 퍼졌다고 봐야 합니다.'),
    dict(key='us_liq', group='미국 유동성', name='미국 순유동성 (시장에 풀린 달러)', unit='십억$', risk='low',
         help='연준이 푼 돈에서 정부 통장(TGA)과 역레포(RRP)에 묶인 돈을 뺀 값입니다. 시장에서 실제로 도는 돈이라서 줄어들면 주식에 부담이 됩니다.'),
    dict(key='us_curve', group='미국 금리', name='미국 장단기 금리차 (10년 − 2년)', unit='bp', risk='both',
         help='10년 국채 금리에서 2년 국채 금리를 뺀 값입니다. 0 아래로 뒤집히면 경기침체 예고로 보고, 뒤집혔다가 빠르게 다시 벌어지는 모습도 과거 침체 직전에 자주 나왔습니다.'),
    dict(key='vix', group='변동성', name='VIX (미국 주식 공포지수)', unit='', risk='high',
         help='미국 주식이 앞으로 한 달 동안 얼마나 출렁일지 시장이 예상하는 값입니다. 20 아래면 평온하고, 30 위면 공포 구간입니다.'),
    dict(key='vix_ratio', group='변동성', name='VIX ÷ VIX3M (단기 공포 쏠림)', unit='x', risk='high',
         help='한 달짜리 공포(VIX)를 석 달짜리 공포(VIX3M)로 나눈 값입니다. 1을 넘으면 시장이 먼 앞날보다 지금 당장을 더 무서워하는 상태인데, 급락장에서 이렇게 됩니다.'),
    dict(key='vkospi', group='변동성', name='VKOSPI (한국 주식 공포지수)', unit='', risk='high',
         help='코스피200이 앞으로 한 달 동안 얼마나 출렁일지 예상하는 값입니다(한국판 VIX). 높을수록 한국 시장이 불안합니다.'),
    dict(key='fng', group='심리', name='CNN 공포탐욕지수', unit='점', risk='fng',
         help='미국 투자자들의 분위기를 0~100점으로 나타낸 값입니다. 25점 아래면 겁먹은 시장이고, 75점 위면 너무 들뜬(과열) 시장입니다.'),
    dict(key='sp_dd', group='주가', name='S&P500 고점 대비 낙폭 (최근 1년 최고가 기준)', unit='%', risk='low',
         help='S&P500이 최근 1년 동안 가장 높았던 종가에서 지금 몇 % 내려와 있는지 나타냅니다. 0이면 신고가이고, -10%를 넘으면 조정장, -20%를 넘으면 약세장이라고 부릅니다.'),
    dict(key='ks_dd', group='주가', name='코스피 고점 대비 낙폭 (최근 1년 최고가 기준)', unit='%', risk='low',
         help='코스피가 최근 1년 동안 가장 높았던 종가에서 지금 몇 % 내려와 있는지 나타냅니다. 0이면 신고가이고, -10%를 넘으면 조정장, -20%를 넘으면 약세장이라고 부릅니다.'),
]

# 종합 판정의 두 줄: 주식시장 충격(변동성·심리·주가) / 신용·자금 스트레스(나머지)
STOCK_KEYS = {'vix', 'vix_ratio', 'vkospi', 'fng', 'sp_dd', 'ks_dd'}
AXES = (('stock', '주식시장 충격'), ('credit', '신용·자금 스트레스'))
# 절대 기준 바닥: (이 값 이상이면, 이 단계보다 좋게 매기지 않는다). 지난 10년 VIX 30↑ 은 2020년 80일·2022년 48일 말고는
# 한 해 12일 이하, VKOSPI 40↑ 은 2022~2025년 4년 동안 8일이었다(2026-11-22 확인).
FLOORS = {'vix': ((40, 1), (30, 2)), 'vkospi': ((60, 1), (40, 2))}
DD_WINDOW = 252           # 고점 대비 낙폭: 최근 1년(252거래일) 최고 종가 기준


def axis_of(key):
    return 'stock' if key in STOCK_KEYS else 'credit'


def log(msg):
    print(msg, flush=True)


def get(url, **kw):
    kw.setdefault('timeout', 45)   # FRED는 브라우저 흉내 헤더를 거부하므로 기본 헤더를 쓴다
    for attempt in range(3):
        try:
            r = requests.get(url, **kw)
            if r.status_code == 200:
                return r
            if r.status_code in (403, 404):   # 막힌 곳은 다시 두드리지 않는다
                break
        except requests.RequestException:
            pass
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f'받기 실패 {url.split("?")[0]}')


# ── 수집 ────────────────────────────────────────────────────────────────────
def ecos_series(code, start, end):
    key = os.getenv('ECOS_API_KEY', '').strip()
    if not key:
        raise RuntimeError('ECOS_API_KEY 없음')
    url = (f'https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/20000/817Y002/D/'
           f'{start:%Y%m%d}/{end:%Y%m%d}/{code}')
    rows = get(url).json().get('StatisticSearch', {}).get('row') or []
    out = {}
    for row in rows:
        try:
            out[datetime.strptime(row['TIME'], '%Y%m%d').date()] = float(row['DATA_VALUE'])
        except (KeyError, ValueError):
            continue
    if not out:
        raise RuntimeError(f'ECOS {code} 비어 있음')
    return out


def fred_series(sid, start):
    text = get(f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start:%Y-%m-%d}').text
    out = {}
    for line in text.splitlines()[1:]:
        d, _, v = line.partition(',')
        try:
            out[date.fromisoformat(d)] = float(v)
        except ValueError:
            continue   # '.' = 휴일 결측
    if not out:
        raise RuntimeError(f'FRED {sid} 비어 있음')
    return out


def yahoo_series(symbol):
    data = get(f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=10y&interval=1d', headers=UA).json()
    res = (data.get('chart') or {}).get('result') or []
    if not res:
        raise RuntimeError(f'Yahoo {symbol} 없음')
    ts, closes = res[0].get('timestamp') or [], res[0]['indicators']['quote'][0].get('close') or []
    out = {}
    # 날짜는 그 거래소 시각으로 끊는다(코스피를 뉴욕 시각으로 끊으면 하루 밀린다)
    tz = timezone(timedelta(seconds=(res[0].get('meta') or {}).get('gmtoffset', -4 * 3600)))
    for t, c in zip(ts, closes):
        if c is not None:
            out[datetime.fromtimestamp(t, timezone.utc).astimezone(tz).date()] = float(c)
    if not out:
        raise RuntimeError(f'Yahoo {symbol} 비어 있음')
    return out


def kis_vkospi(start):
    """한국투자증권 국내업종 기간별 시세(지수코드 0503 = VKOSPI). 한 번에 최대 약 50거래일."""
    sys.path.insert(0, str(ROOT))
    from wamo_kis import KIS
    kis = KIS()
    if not kis.configured:
        raise RuntimeError('KIS 비밀값 없음')
    out, end = {}, datetime.now(KST).date()
    while end > start:
        data = kis.get('/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice', 'FHKUP03500100', {
            'FID_COND_MRKT_DIV_CODE': 'U', 'FID_INPUT_ISCD': '0503',
            'FID_INPUT_DATE_1': f'{max(start, end - timedelta(days=70)):%Y%m%d}',
            'FID_INPUT_DATE_2': f'{end:%Y%m%d}', 'FID_PERIOD_DIV_CODE': 'D'})
        rows = data.get('output2') or []
        got = 0
        for row in rows:
            try:
                d = datetime.strptime(row['stck_bsop_date'], '%Y%m%d').date()
                out[d] = float(row['bstp_nmix_prpr'])
                got += 1
            except (KeyError, ValueError, TypeError):
                continue
        if not got:
            break
        end = min(out) - timedelta(days=1)
    if not out:
        raise RuntimeError('KIS VKOSPI 비어 있음')
    return out


def fear_greed():
    """CNN 공포탐욕지수. CNN 원본은 자동 접속을 막아 두어, 같은 값을 공개하는 두 곳을 서로 검증해 쓴다."""
    result = {}
    try:
        html = get('https://feargreedmeter.com/').text
        m = re.search(r'"latest":\{"now":(\d+),"one_month_ago":(\d+),"one_week_ago":(\d+),'
                      r'"one_year_ago":(\d+),"previous_close":(\d+),"date":"(\d{4}-\d{2}-\d{2})"', html)
        if m:
            now, m1, w1, y1, prev, d = m.groups()
            result = dict(value=int(now), month_ago=int(m1), week_ago=int(w1), year_ago=int(y1),
                          prev=int(prev), date=d, source='feargreedmeter.com')
    except Exception as exc:  # noqa: BLE001
        log(f'공포탐욕 주 출처 실패: {exc}')
    try:
        live = get('https://www.finhacker.cz/wp-content/data/fng-live.json').json()
        check = int(round(float(live['score'])))
        if not result:
            result = dict(value=check, date=str(live.get('timestamp_et', ''))[:10], source='finhacker.cz')
        else:
            result['crossCheck'] = check
    except Exception as exc:  # noqa: BLE001
        log(f'공포탐욕 예비 출처 실패: {exc}')
    if not result:
        raise RuntimeError('공포탐욕 두 출처 모두 실패')
    return result


# ── 계산 도우미 ─────────────────────────────────────────────────────────────
def combine(a, b, fn):
    return {d: fn(a[d], b[d]) for d in sorted(set(a) & set(b))}


def ffill_to(series, dates):
    """주간 자료(연준 자산 등)를 일별 날짜에 맞춰 직전 값으로 채운다."""
    keys, out, i, last = sorted(series), {}, 0, None
    for d in dates:
        while i < len(keys) and keys[i] <= d:
            last = series[keys[i]]
            i += 1
        if last is not None:
            out[d] = last
    return out


def drawdown(prices, window=DD_WINDOW):
    """{날짜: 종가} → {날짜: 최근 window거래일 최고 종가 대비 낙폭(%)}. 0 = 신고가."""
    days, out = sorted(prices), {}
    for i in range(window - 1, len(days)):
        peak = max(prices[d] for d in days[i - window + 1:i + 1])
        out[days[i]] = round((prices[days[i]] / peak - 1) * 100, 2)
    return out


def apply_floor(key, row):
    """절대 기준 바닥. 값이 기준을 넘으면 단계를 그만큼 위험하게 고치고 floor 칸에 기준값을 남긴다."""
    for limit, worst in FLOORS.get(key, ()):
        if row.get('value') is not None and row['value'] >= limit:
            if row['level'] > worst:
                row.update(level=worst, floor=limit)
            break
    return row


def percentile(values, x):
    below = sum(1 for v in values if v < x)
    equal = sum(1 for v in values if v == x)
    return 100.0 * (below + 0.5 * equal) / len(values)


def judge(series, risk):
    """series: {date: value} → 판정 사전. 5년 백분위 + 1개월 변화 속도 보정."""
    pts = sorted(series.items())
    cutoff = pts[-1][0] - timedelta(days=365 * YEARS)
    pts = [(d, v) for d, v in pts if d >= cutoff]
    vals = [v for _, v in pts]
    last_d, last = pts[-1]
    pct = percentile(vals, last)
    risk_pct = {'high': pct, 'low': 100 - pct, 'both': abs(pct - 50) * 2}[risk]
    level = 5 if risk_pct < 50 else 4 if risk_pct < 75 else 3 if risk_pct < 90 else 2 if risk_pct < 97 else 1

    change = z = None
    if len(vals) > CHANGE_DAYS * 3:
        change = last - vals[-1 - CHANGE_DAYS]
        diffs = [vals[i] - vals[i - CHANGE_DAYS] for i in range(CHANGE_DAYS, len(vals))]
        sd = statistics.pstdev(diffs)
        if sd > 0:
            mean = statistics.fmean(diffs)
            raw = (change - mean) / sd
            z = {'high': raw, 'low': -raw}.get(risk)
            if risk == 'both':   # 이미 치우친 쪽으로 더 빨리 가면 위험
                z = raw if pct >= 50 else -raw
    bump = 2 if (z or 0) >= 3 else 1 if (z or 0) >= 2 else 0
    final = max(1, level - bump)
    return dict(value=last, date=last_d.isoformat(), pct=round(pct, 1), riskPct=round(risk_pct, 1),
                change1m=change, speedZ=None if z is None else round(z, 2),
                baseLevel=level, speedBump=bump, level=final, sample=len(vals))


def judge_fng(fg):
    v = fg['value']
    level = 1 if v <= 10 else 2 if v <= 25 else 3 if v <= 40 else 4 if v <= 55 else 5
    change = v - fg['month_ago'] if 'month_ago' in fg else None
    bump = 1 if change is not None and change <= -20 else 0
    zone = ('극단적 공포' if v <= 25 else '공포' if v <= 45 else '중립' if v <= 55
            else '탐욕' if v < 75 else '극단적 탐욕')
    return dict(value=v, date=fg.get('date'), change1m=change, baseLevel=level, speedBump=bump,
                level=max(1, level - bump), zone=zone, overheat=v >= 75,
                compare={k: fg.get(k) for k in ('prev', 'week_ago', 'month_ago', 'year_ago')},
                crossCheck=fg.get('crossCheck'), source=fg.get('source'))


def composite(levels):
    """한 묶음의 단계들을 하나로. 평균 위험도에 '위기 지표가 여럿이면 가중'을 더한다."""
    if not levels:
        return None
    risk = statistics.fmean(6 - lv for lv in levels)          # 1(정상)~5(위기)
    reds = sum(1 for lv in levels if lv == 1)
    oranges = sum(1 for lv in levels if lv == 2)
    risk += 0.5 * (reds >= 3) + 0.5 * (reds + oranges >= len(levels) / 2)
    # round() 는 2.5 를 2 로, 3.5 를 4 로 내려 같은 반 칸이 다르게 처리된다. 반 칸은 늘 위험한 쪽으로 올린다
    return max(1, min(5, 6 - math.floor(risk + 0.5)))


def _verdict(rows):
    levels = [r['level'] for r in rows if r.get('level')]
    lv = composite(levels)
    return dict(level=lv, emoji=LEVELS[lv][0] if lv else '⚪', label=LEVELS[lv][1] if lv else '확인 불가',
                counts={str(k): sum(1 for x in levels if x == k) for k in range(1, 6)},
                available=len(levels), total=len(rows))


def market_axes(rows, mk):
    """나라 하나의 두 줄 판정. level 은 둘 중 나쁜 쪽(대표 단계)."""
    mine = [r for r in rows if _market_of(r) == mk]
    out = {ax: _verdict([r for r in mine if axis_of(r['key']) == ax]) for ax, _ in AXES}
    got = [v['level'] for v in out.values() if v['level']]
    lv = min(got) if got else None
    out.update(level=lv, emoji=LEVELS[lv][0] if lv else '⚪', label=LEVELS[lv][1] if lv else '확인 불가')
    return out


def fmt_value(ind, v):
    if v is None:
        return '-'
    if ind['unit'] == 'x':
        return f'{v:.2f}x'
    if ind['unit'] == 'bp':
        return f'{v:,.0f}bp'
    if ind['unit'] == '십억$':
        return f'{v:,.0f}'
    if ind['unit'] == '점':
        return f'{v:.0f}'
    return f'{v:.1f}'


def fmt_change(ind, c):
    if c is None:
        return ''
    if ind['unit'] == 'x':
        return f'{c:+.2f}'
    if ind['unit'] in ('bp', '십억$', '점'):
        return f'{c:+,.0f}' + ('bp' if ind['unit'] == 'bp' else '')
    return f'{c:+.1f}'


# ── 실행 ────────────────────────────────────────────────────────────────────
def load_history():
    try:
        return json.loads(HISTORY.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def build():
    today = datetime.now(KST).date()
    start = today - timedelta(days=365 * YEARS + 60)
    history = load_history()
    series, errors = {}, {}

    def attempt(label, fn):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - 하나가 실패해도 나머지는 계속
            errors[label] = str(exc)[:160]
            log(f'⚠️ {label}: {exc}')
            return None

    # 한국 (ECOS, 연%)
    kr = {n: attempt(f'ECOS {n}', lambda c=c: ecos_series(c, start, today)) for n, c in {
        't3': '010200000', 't10': '010210000', 'aa': '010300000', 'bbb': '010320000',
        'cd': '010502000', 'cp': '010503000'}.items()}
    bp = lambda a, b: round((a - b) * 100, 1)
    if kr['aa'] and kr['t3']:
        series['kr_aa'] = combine(kr['aa'], kr['t3'], bp)
    if kr['bbb'] and kr['t3']:
        series['kr_bbb'] = combine(kr['bbb'], kr['t3'], bp)
    if kr['cp'] and kr['cd']:
        series['kr_cpcd'] = combine(kr['cp'], kr['cd'], bp)
    if kr['t10'] and kr['t3']:
        series['kr_curve'] = combine(kr['t10'], kr['t3'], bp)

    # 미국 (FRED)
    f = {s: attempt(f'FRED {s}', lambda s=s: fred_series(s, start)) for s in (
        'BAMLH0A0HYM2', 'BAMLC0A0CM', 'T10Y2Y', 'WALCL', 'WTREGEN', 'RRPONTSYD')}
    if f['BAMLH0A0HYM2']:
        series['us_hy'] = {d: round(v * 100, 1) for d, v in f['BAMLH0A0HYM2'].items()}
    if f['BAMLC0A0CM']:
        series['us_ig'] = {d: round(v * 100, 1) for d, v in f['BAMLC0A0CM'].items()}
    if f['T10Y2Y']:
        series['us_curve'] = {d: round(v * 100, 1) for d, v in f['T10Y2Y'].items()}
    # VIX·VIX3M: 야후(미국 마감 직후 값). FRED 는 하루 늦게 올라와 급락한 날 판정이 한두 단계 늦었다(2026-11-20).
    # 야후를 못 받을 때만 FRED 로 대신한다.
    def vol(symbol, sid):
        got = attempt(f'야후 {symbol}', lambda: yahoo_series(symbol))
        if not got:
            got = attempt(f'FRED {sid}', lambda: fred_series(sid, start))
        return got
    vix, vix3m = vol('%5EVIX', 'VIXCLS'), vol('%5EVIX3M', 'VXVCLS')
    if vix:
        series['vix'] = vix
    if vix and vix3m:
        series['vix_ratio'] = combine(vix, vix3m, lambda a, b: round(a / b, 3))
    # 주가: 1년 고점 대비 낙폭 (야후 일봉 종가)
    for key, symbol in (('sp_dd', '%5EGSPC'), ('ks_dd', '%5EKS11')):
        px = attempt(f'야후 {symbol}', lambda symbol=symbol: yahoo_series(symbol))
        if px and len(px) > DD_WINDOW + 200:
            series[key] = drawdown(px)
    if f['WALCL'] and f['WTREGEN'] and f['RRPONTSYD']:
        days = sorted(f['RRPONTSYD'])
        walcl, tga = ffill_to(f['WALCL'], days), ffill_to(f['WTREGEN'], days)
        series['us_liq'] = {d: round(walcl[d] / 1000 - tga[d] / 1000 - f['RRPONTSYD'][d], 1)
                            for d in days if d in walcl and d in tga}

    # MOVE(채권 공포지수)는 2026-11-12 사용자 결정으로 뺐다(지표 13개 → 12개).

    # VKOSPI (한투 API, 결과는 이력 파일에 쌓아 다음날 다시 받지 않아도 되게)
    vk_hist = {date.fromisoformat(k): v for k, v in (history.get('vkospi') or {}).items()}
    need_from = (max(vk_hist) - timedelta(days=10)) if len(vk_hist) > 500 else start
    vk = attempt('KIS VKOSPI', lambda: kis_vkospi(need_from))
    if vk:
        vk_hist.update(vk)
    if vk_hist:
        series['vkospi'] = vk_hist
        history['vkospi'] = {d.isoformat(): v for d, v in sorted(vk_hist.items())}

    # 공포탐욕
    fg = attempt('공포탐욕', fear_greed)
    fg_hist = dict(history.get('fng') or {})
    if fg and fg.get('date'):
        fg_hist[fg['date']] = fg['value']
        history['fng'] = dict(sorted(fg_hist.items()))

    # 판정
    try:
        previous = {r['key']: r for r in json.loads(OUT.read_text(encoding='utf-8')).get('indicators', [])}
    except (OSError, ValueError):
        previous = {}
    items = []
    for ind in INDICATORS:
        row = {k: ind[k] for k in ('key', 'group', 'name', 'unit', 'risk', 'help')}
        try:
            if ind['key'] == 'fng':
                if not fg:
                    raise RuntimeError('자료 없음')
                row.update(judge_fng(fg))
                row['series'] = [[d, v] for d, v in sorted(fg_hist.items())][-CHART_DAYS:]
            else:
                s = series.get(ind['key'])
                if not s:
                    raise RuntimeError('자료 없음')
                row.update(apply_floor(ind['key'], judge(s, ind['risk'])))
                row['series'] = [[d.isoformat(), round(v, 4)] for d, v in sorted(s.items())][-CHART_DAYS:]
            emoji, label = LEVELS[row['level']]
            row.update(emoji=emoji, label=label, valueText=fmt_value(ind, row['value']),
                       changeText=fmt_change(ind, row.get('change1m')))
        except Exception as exc:  # noqa: BLE001
            old = previous.get(ind['key'])
            if old and old.get('level'):
                # 오늘 못 받았으면 직전 결과를 그대로 보여 주되 '이전 자료'라고 표시한다
                row = {**old, 'stale': True, 'staleReason': str(exc)[:120]}
            else:
                row.update(level=None, emoji='⚪', label='확인 불가', valueText='-', changeText='',
                           error=str(exc)[:120], series=[])
        row['axis'] = axis_of(ind['key'])
        items.append(row)

    levels = [r['level'] for r in items if r['level']]
    markets = {mk: market_axes(items, mk) for mk in ('US', 'KR')}
    got = [m['level'] for m in markets.values() if m['level']]
    comp = min(got) if got else None          # 전체 대표 단계 = 두 나라 중 나쁜 쪽
    counts = {str(lv): sum(1 for x in levels if x == lv) for lv in range(1, 6)}
    data = dict(
        asOf=datetime.now(KST).isoformat(timespec='minutes'),
        composite=dict(level=comp, emoji=LEVELS[comp][0] if comp else '⚪',
                       label=LEVELS[comp][1] if comp else '확인 불가', counts=counts,
                       available=len(levels), total=len(items)),
        markets=markets, indicators=items, errors=errors,
        method='5년 백분위(위험한 쪽) 5단계 + 1개월 변화 표준점수 2↑ 한 단계·3↑ 두 단계 보정. '
               '장단기 금리차는 양쪽 끝, 공포탐욕은 고정 구간(10/25/40/55, 75↑ 과열). '
               'VIX 30↑·VKOSPI 40↑ 은 최소 경계, VIX 40↑·VKOSPI 60↑ 은 위기. '
               '종합은 나라별로 주식시장 충격과 신용·자금 스트레스를 따로 내고 대표 단계는 나쁜 쪽.')
    return data, history


def _market_of(r):
    return 'KR' if r['key'] in ('vkospi', 'ks_dd') or r['key'].startswith('kr_') else 'US'


def _vix_temp(v):
    return 0 if v < 15 else 1 if v < 20 else 2 if v < 25 else 3 if v < 30 else 4


def _fng_temp(v):
    return 0 if v >= 55 else 1 if v >= 45 else 2 if v >= 35 else 3 if v >= 25 else 4


def market_summary(rows, mk):
    """시장 하나의 종합 단계와 한두 줄 결론(화면의 문장과 같은 기준)."""
    ok = [r for r in rows if r.get('level')]
    if not ok:
        return ['종합 확인 불가']
    ax = market_axes(rows, mk)
    cnt = {k: sum(1 for r in ok if r['level'] == k) for k in range(1, 6)}
    out = [' · '.join(f'{title} {ax[k]["emoji"]} {ax[k]["label"]}' for k, title in AXES if ax[k]['level']),
           f'🔴{cnt[1]} 🟠{cnt[2]} 🟡{cnt[3]} 🔵{cnt[4]} 🟢{cnt[5]}']
    bad = [r for r in ok if r['level'] <= 2]
    fast = [r for r in ok if (r.get('speedBump') or 0) > 0]
    words = [f'{len(ok)}개 지표 중 {len(bad)}개가 경계 이상입니다' if bad else f'{len(ok)}개 지표 모두 경계보다 아래입니다']
    if bad:
        words.append(', '.join(r['name'].split(' (')[0] for r in bad) + '에 경보가 켜졌습니다')
    if fast:
        words.append('한 달 사이 빠르게 나빠진 지표는 ' + ', '.join(r['name'].split(' (')[0] for r in fast) + '입니다')
    if mk == 'US':
        vx = next((r for r in ok if r['key'] == 'vix'), None)
        fg = next((r for r in ok if r['key'] == 'fng'), None)
        if vx and fg and abs(_fng_temp(fg['value']) - _vix_temp(vx['value'])) >= 2:
            words.append('VIX와 공포탐욕지수가 엇갈립니다')
    out.append('결론: ' + '. '.join(words) + '.')
    return out


SHORT = {'kr_aa': '한국 우량 회사채 가산금리', 'kr_bbb': '한국 비우량 회사채 가산금리', 'kr_cpcd': '한국 CP−CD 금리차',
         'kr_curve': '한국 장단기 금리차', 'us_hy': '미국 하이일드 스프레드', 'us_ig': '미국 투자등급 스프레드',
         'us_liq': '미국 순유동성', 'us_curve': '미국 장단기 금리차', 'vix': 'VIX', 'vix_ratio': 'VIX÷VIX3M',
         'vkospi': 'VKOSPI', 'fng': '공포탐욕지수', 'sp_dd': 'S&P500 낙폭', 'ks_dd': '코스피 낙폭'}


def _short(r):
    return SHORT.get(r['key']) or r['name'].split(' (')[0]


def _val(r):
    v = r.get('valueText', '-')
    return v + ('%' if r.get('unit') == '%' and v != '-' else '')


def _with_change(r):
    return f'{_short(r)} {_val(r)}' + (f'(한 달 {r["changeText"]})' if r.get('changeText') else '')


def _josa(word, pair):
    """받침에 맞는 조사('이/가', '로/으로'). 숫자는 한자음, %·x·bp 는 받침 없는 소리로 읽는다."""
    w = re.sub(r'[)\s]+$', '', str(word))
    ch = w[-1] if w else ''
    if ch.isdigit():
        jong = {'0': 'ㅇ', '1': 'ㄹ', '3': 'ㅁ', '6': 'ㄱ', '7': 'ㄹ', '8': 'ㄹ'}.get(ch, '')
    elif '가' <= ch <= '힣':
        k = (ord(ch) - 0xAC00) % 28
        jong = '' if k == 0 else ('ㄹ' if k == 8 else 'x')
    else:
        jong = ''
    if pair == '이/가':
        return '이' if jong else '가'
    return '로' if jong in ('', 'ㄹ') else '으로'


def telegram_text(data):
    """2026-11-26 사용자 요청: 지표를 늘어놓지 않고 줄글로 쓴다. 문단은 ① 달라진 것 ② 실물경제(기업 자금 사정)에 주는 뜻
    ③ 주식시장에 주는 뜻 ④ 볼 것 순서다. 문장은 그날 단계 조합에 따라 고른다. 지표 전체 표는 화면(산업강도판 매크로 탭)에 있다.
    data['sentLevels'·'sentValues'] 는 직전 알림 때의 단계와 값이다(main 이 이어 붙인다)."""
    c = data['composite']
    d = datetime.fromisoformat(data['asOf'])
    rows = [r for r in data['indicators'] if r.get('level')]
    by = {r['key']: r for r in rows}
    ax = {mk: market_axes([r for r in data['indicators'] if _market_of(r) == mk], mk) for mk in ('US', 'KR')}
    lab = lambda r: LEVELS[r['level']][1]
    lv = lambda k: min([ax[mk][k]['level'] for mk in ('US', 'KR') if ax[mk][k]['level']] or [5])
    s_lv, c_lv = lv('stock'), lv('credit')
    ch = lambda r: f'한 달 {r["changeText"]}' if r.get('changeText') else ''
    vc = lambda r: f'{_val(r)}({ch(r)})' if ch(r) else _val(r)
    out = [f'📊 매크로 지수 {d.month}/{d.day} · {c["emoji"]} {c["level"]}단계({c["label"]})']

    # ① 달라진 것
    prev, pval = data.get('sentLevels') or {}, data.get('sentValues') or {}
    J = _josa
    moved = lambda r: (f'{_short(r)}{J(_short(r), "이/가")} {pval[r["key"]]}에서 {_val(r)}{J(_val(r), "로/으로")}'
                       if pval.get(r['key']) and pval[r['key']] != _val(r) else f'{_short(r)}{J(_short(r), "이/가")} {_val(r)}{J(_val(r), "로/으로")}')
    step = lambda r: f'{moved(r)} 움직여 {LEVELS[prev[r["key"]]][1]}에서 {lab(r)}{J(lab(r), "로/으로")}'
    worse = [r for r in rows if prev.get(r['key']) and r['level'] < prev[r['key']]]
    better = [r for r in rows if prev.get(r['key']) and r['level'] > prev[r['key']]]
    p1 = []
    if worse:
        p1.append('직전 알림 뒤 ' + ', '.join(step(r) for r in worse) + ' 나빠졌습니다.')
    if better:
        p1.append(('반대로 ' if worse else '직전 알림 뒤 ') + ', '.join(step(r) for r in better) + ' 나아졌습니다.')
    if prev and not worse and not better:
        p1.append('직전 알림 뒤 단계가 바뀐 지표는 없습니다.')
    said = {r['key'] for r in worse + better}
    movers = sorted([r for r in rows if r['key'] not in said and abs(r.get('speedZ') or 0) >= 1 and r.get('changeText')],
                    key=lambda r: -abs(r.get('speedZ') or 0))[:3]
    if movers:
        p1.append('한 달 기준으로 평소보다 크게 움직인 지표는 ' + ', '.join(f'{_short(r)}({r["changeText"]}, 지금 {_val(r)})' for r in movers) + '입니다.')
    elif not p1:
        p1.append('한 달 사이 평소 범위를 벗어나게 움직인 지표는 없습니다.')
    out += ['', ' '.join(p1)]

    # ② 실물경제: 기업이 돈을 빌리는 사정
    p2 = []
    hy, ig, liq, cur = by.get('us_hy'), by.get('us_ig'), by.get('us_liq'), by.get('us_curve')
    if hy and ig:
        if hy['level'] <= 2 and ig['level'] <= 2:
            p2.append(f'실물경제 쪽에서는 미국 회사채 스프레드가 하이일드 {vc(hy)}, 투자등급 {vc(ig)}로 함께 {lab(ig)} 이상입니다. '
                      '우량 기업까지 자금 조달 비용이 오르는 구간이라, 이 상태가 이어지면 설비투자와 고용이 줄면서 경기 둔화로 이어집니다.')
        elif hy['level'] <= 2:
            p2.append(f'실물경제 쪽에서는 미국 하이일드 스프레드가 {vc(hy)}로 {lab(hy)} 단계입니다. 신용등급이 낮은 기업부터 차입 비용이 오르고 '
                      f'차환이 어려워지는 단계라 이런 기업의 투자와 고용이 먼저 줄어듭니다. 다만 투자등급 스프레드는 {vc(ig)}로 {lab(ig)}에 머물러, '
                      '우량 기업의 자금 조달까지 막힌 것은 아닙니다.')
        else:
            p2.append(f'실물경제 쪽에서는 미국 회사채 스프레드가 하이일드 {vc(hy)}, 투자등급 {vc(ig)}로 경계 아래입니다. '
                      '기업이 돈을 빌리는 데 이상이 없어, 금융시장 불안이 투자와 고용으로 번질 조짐은 아직 없습니다.')
    if liq and liq['level'] <= 2:
        p2.append(f'미국 순유동성도 {lab(liq)} 단계라 시장에 풀린 달러가 줄고 있습니다.')
    if cur and cur['level'] <= 2:
        p2.append(f'미국 장단기 금리차는 {vc(cur)}로 {lab(cur)}인데, 금리차가 평소 범위를 크게 벗어나면 경기 전환 신호로 읽힙니다.')
    kc = [by[k] for k in ('kr_aa', 'kr_bbb', 'kr_cpcd') if k in by]
    if kc:
        kw = min(kc, key=lambda r: r['level'])
        if kw['level'] <= 2:
            p2.append(f'한국은 {_short(kw)}가 {vc(kw)}로 {lab(kw)} 단계입니다. 기업이 회사채와 단기 자금을 구하기 어려워졌다는 뜻이라, '
                      '신용도가 낮은 건설·중소기업부터 자금 부담이 커집니다.')
        else:
            p2.append('한국은 ' + ', '.join(f'{_short(r).replace("한국 ", "")} {vc(r)}' for r in kc)
                      + ('로 한 달 동안 큰 변화가 없어' if all(abs(r.get('speedZ') or 0) < 1 for r in kc) else '로 경계 아래라')
                      + ' 기업 자금 조달은 평소와 같습니다.')
    if p2:
        out += ['', ' '.join(p2)]

    # ③ 주식시장
    p3 = []
    vix, ratio, fng, spd = by.get('vix'), by.get('vix_ratio'), by.get('fng'), by.get('sp_dd')
    if vix:
        t = f'주식시장 쪽에서는 미국 VIX가 {vc(vix)}로 {lab(vix)} 단계입니다. 옵션시장이 S&P500의 하루 움직임을 ±{vix["value"] / 15.87:.1f}% 안팎으로 보고 있다는 뜻입니다.'
        if ratio:
            t += (f' VIX가 3개월물보다 높아({_val(ratio)}) 투자자들이 당장의 급락에 대비해 보험을 사고 있고, 이런 구간에서는 하루 등락폭이 커집니다.'
                  if ratio['value'] >= 1 else f' VIX가 3개월물보다 낮아({_val(ratio)}) 단기 공포가 한꺼번에 몰린 상태는 아닙니다.')
        p3.append(t)
    if fng:
        if fng['value'] <= 25:
            p3.append(f'공포탐욕지수는 {_val(fng)}로 극단적 공포 구간입니다. 매도가 한쪽으로 쏠려 있어 급한 반등이 나오기 쉽지만, 그것만으로 추세가 돌았다고 보기는 어렵습니다.')
        elif fng['value'] >= 75:
            p3.append(f'공포탐욕지수는 {_val(fng)}로 과열 구간이라 작은 악재에도 조정이 나오기 쉽습니다.')
    if spd:
        p3.append(f'S&P500은 1년 고점에서 {_val(spd).lstrip("-")} 내려와 있습니다.')
    ksd, vk = by.get('ks_dd'), by.get('vkospi')
    if ksd or vk:
        t = '한국은 '
        if ksd:
            t += f'코스피가 1년 고점에서 {_val(ksd).lstrip("-")} 내려왔' + ('고 ' if vk else '습니다.')
        if vk:
            t += f'VKOSPI가 {vc(vk)}로 {lab(vk)} 단계입니다. 하루 ±{vk["value"] / 15.87:.1f}% 안팎의 움직임이 가격에 들어 있어' + (' 미국보다 흔들림이 훨씬 큽니다.' if vix and vk['value'] > vix['value'] * 1.3 else ' 변동성이 높은 편입니다.' if vk['level'] <= 2 else ' 평소 수준입니다.')
        p3.append(t)
    if s_lv <= 2 and c_lv >= 3:
        p3.append('정리하면 주가는 크게 흔들리는데 신용 쪽은 아직 버티고 있어, 지금까지는 자금 경색이 아니라 주가 조정입니다.')
    elif s_lv <= 2:
        p3.append('정리하면 주가와 신용이 함께 경계 이상이라, 주가 조정이 자금 사정 악화로 번지는 구간입니다.')
    elif c_lv <= 2:
        p3.append('정리하면 주가는 조용한데 신용 쪽에 경보가 켜져 있어, 주가가 뒤늦게 반응할 수 있습니다.')
    else:
        p3.append('정리하면 주식과 신용 두 축 모두 경계 아래라 시장 전체에 걸린 경보는 없습니다.')
    out += ['', ' '.join(p3)]

    # ④ 볼 것
    credit = [r for r in rows if axis_of(r['key']) == 'credit' and r['key'] not in ('us_curve', 'kr_curve')]
    stock = [r for r in rows if axis_of(r['key']) == 'stock']
    pick = lambda xs: max(xs, key=lambda r: ((r.get('speedZ') or 0), (r.get('riskPct') or 0))) if xs else None
    worst = lambda xs: min(xs, key=lambda r: (r['level'], -(r.get('riskPct') or 0))) if xs else None
    if s_lv <= 2 and c_lv >= 3 and credit:
        r = pick(credit)
        watch = (f'신용이 따라 나빠지는지 봅니다. {_short(r)}{J(_short(r), "이/가")} 지금 {_val(r)}{", " + ch(r) if ch(r) else ""}로 {lab(r)} 단계인데, 여기서 한 단계 더 나빠지면 '
                 '주가 조정이 기업 자금 문제로 번진다는 신호입니다.')
    elif s_lv <= 2 and stock:
        r = worst(stock)
        watch = f'{_short(r)}{J(_short(r), "이/가")} 지금 {_val(r)}인데, 한 단계 내려오는지가 첫 확인 지점입니다.'
    elif c_lv <= 2 and credit:
        r = worst(credit)
        watch = f'{_short(r)}{J(_short(r), "이/가")} 지금 {_val(r)}{", " + ch(r) if ch(r) else ""}인데, 더 나빠지는지와 주가 변동성이 따라 오르는지 봅니다.'
    else:
        r = pick([x for x in rows if (x.get('speedZ') or 0) >= 1])
        watch = f'가장 빠르게 움직인 {_short(r)}{J(_short(r), "이/가")} 계속 같은 방향으로 가는지 봅니다(지금 {_val(r)}{", " + ch(r) if ch(r) else ""}).' if r else '눈에 띄게 빨라진 지표가 생기는지만 보면 됩니다.'
    out += ['', f'👀 볼 것: {watch}', '👉 지표 14개 전체: https://dhandori.github.io/industry-strength/#US-macro (한국은 #KR-macro)']
    return '\n'.join(out)


def previous_values():
    """직전 알림 때의 지표별 값 글자({key: '28.3'}). 없으면 빈 dict."""
    try:
        return json.loads(OUT.read_text(encoding='utf-8')).get('sentValues') or {}
    except (OSError, ValueError, AttributeError):
        return {}


def previous_levels():
    """직전 알림 때의 지표별 단계({key: level}). 없으면 빈 dict."""
    try:
        return json.loads(OUT.read_text(encoding='utf-8')).get('sentLevels') or {}
    except (OSError, ValueError, AttributeError):
        return {}


def previous_sent():
    """지난 결과에 적힌 '텔레그램 보낸 날'(KST 날짜). 없으면 None."""
    try:
        return json.loads(OUT.read_text(encoding='utf-8')).get('telegramSent')
    except (OSError, ValueError, AttributeError):
        return None


def write_out(data):
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')


def main(argv):
    sent_before, sent_levels, sent_values = previous_sent(), previous_levels(), previous_values()
    data, history = build()
    if sent_before:
        data['telegramSent'] = sent_before
    if sent_levels:
        data['sentLevels'] = sent_levels   # 직전 알림 때의 단계. 알림을 보내면 그때 새 값으로 바꾼다
    if sent_values:
        data['sentValues'] = sent_values
    write_out(data)
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=0) + '\n', encoding='utf-8')
    text = telegram_text(data)
    log(text)
    if '--telegram' in argv:
        today = datetime.now(KST).date().isoformat()
        token, chat = os.getenv('TELEGRAM_BOT_TOKEN'), os.getenv('TELEGRAM_CHAT_ID')
        if sent_before == today:
            # 아침 회차를 여러 길(깃허브 예약·정시 깨우개·Claude 깨우기)이 겹쳐 걸어도 하루 한 통만 보낸다(2026-11-20)
            log(f'오늘({today}) 이미 텔레그램을 보냄 · 전송 생략')
        elif token and chat:
            from wamo_alerts import telegram_send
            try:
                telegram_send(token, chat, text)
                log('텔레그램 전송 완료')
                data['telegramSent'] = today
                data['sentLevels'] = {r['key']: r['level'] for r in data.get('indicators', []) if r.get('level')}
                data['sentValues'] = {r['key']: _val(r) for r in data.get('indicators', []) if r.get('level')}
                write_out(data)
            except Exception as exc:  # noqa: BLE001 - 토큰은 출력하지 않는다
                log(f'텔레그램 전송 실패: {type(exc).__name__}')
        else:
            log('텔레그램 비밀값 없음 · 전송 생략')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
