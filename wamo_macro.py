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

자료 출처(모두 무료·공개)
  한국은행 ECOS(인증키 ECOS_API_KEY — GitHub 비밀값), 미국 연준 FRED, Yahoo(MOVE),
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
         help='튼튼한 회사가 나라보다 돈을 빌릴 때 더 내는 이자예요. 이 차이가 벌어지면 회사들이 돈 빌리기 어려워졌다는 뜻이에요.'),
    dict(key='kr_bbb', group='한국 신용', name='비우량 회사채 가산금리 (BBB- − 국고채 3년)', unit='bp', risk='high',
         help='신용등급이 낮은 편인 회사가 나라보다 더 내는 이자예요. 돈줄이 막히기 시작하면 가장 먼저, 가장 크게 벌어져요.'),
    dict(key='kr_cpcd', group='한국 신용', name='기업 단기자금 금리차 (CP − CD 91일)', unit='bp', risk='high',
         help='회사가 3개월짜리로 급히 빌리는 돈(CP)의 이자에서 은행 단기 금리(CD)를 뺀 값이에요. 벌어지면 회사들의 당장 쓸 돈 사정이 나빠졌다는 신호예요.'),
    dict(key='kr_curve', group='한국 금리', name='한국 장단기 금리차 (국고채 10년 − 3년)', unit='bp', risk='both',
         help='10년 국채 금리에서 3년 국채 금리를 뺀 값이에요. 0 아래로 뒤집히면 경기 둔화 신호이고, 갑자기 크게 벌어져도 불안 신호예요.'),
    dict(key='us_hy', group='미국 신용', name='미국 하이일드 스프레드 (저신용 회사채)', unit='bp', risk='high',
         help='신용등급이 낮은 미국 회사가 국채보다 더 내는 이자예요. 위기가 오면 가장 먼저, 가장 빠르게 치솟는 대표 경보등이에요.'),
    dict(key='us_ig', group='미국 신용', name='미국 투자등급 스프레드 (우량 회사채)', unit='bp', risk='high',
         help='튼튼한 미국 회사가 국채보다 더 내는 이자예요. 하이일드보다 느리게 움직이지만, 이것까지 오르면 불안이 넓게 퍼졌다는 뜻이에요.'),
    dict(key='us_liq', group='미국 유동성', name='미국 순유동성 (시장에 풀린 달러)', unit='십억$', risk='low',
         help='연준이 푼 돈에서 정부 통장(TGA)과 역레포(RRP)에 묶인 돈을 뺀 값이에요. 시장에서 실제로 돌고 있는 돈이라 줄어들면 주식에 부담이에요.'),
    dict(key='us_curve', group='미국 금리', name='미국 장단기 금리차 (10년 − 2년)', unit='bp', risk='both',
         help='10년 국채 금리에서 2년 국채 금리를 뺀 값이에요. 0 아래로 뒤집히면 경기침체 예고이고, 뒤집혔다가 빠르게 다시 벌어질 때도 과거 침체 직전에 자주 나왔어요.'),
    dict(key='vix', group='변동성', name='VIX (미국 주식 공포지수)', unit='', risk='high',
         help='미국 주식이 앞으로 한 달 동안 얼마나 출렁일지 시장이 예상하는 값이에요. 20 아래면 평온, 30 위면 공포 구간이에요.'),
    dict(key='vix_ratio', group='변동성', name='VIX ÷ VIX3M (단기 공포 쏠림)', unit='x', risk='high',
         help='한 달짜리 공포(VIX)를 석 달짜리 공포(VIX3M)로 나눈 값이에요. 1을 넘으면 먼 미래보다 지금 당장이 더 무섭다는 뜻이고, 급락장에서 나타나요.'),
    dict(key='move', group='변동성', name='MOVE (미국 채권 공포지수)', unit='', risk='high',
         help='미국 국채 금리가 앞으로 얼마나 출렁일지 예상하는 값이에요(채권판 VIX). 높으면 금리가 불안해서 주식과 채권 모두에 부담이에요.'),
    dict(key='vkospi', group='변동성', name='VKOSPI (한국 주식 공포지수)', unit='', risk='high',
         help='코스피200이 앞으로 한 달 동안 얼마나 출렁일지 예상하는 값이에요(한국판 VIX). 높을수록 한국 시장이 불안하다는 뜻이에요.'),
    dict(key='fng', group='심리', name='CNN 공포탐욕지수', unit='점', risk='fng',
         help='미국 투자자들의 분위기를 0~100점으로 나타낸 값이에요. 25점 아래면 겁먹은 시장, 75점 위면 너무 들뜬(과열) 시장이에요.'),
]


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
    for t, c in zip(ts, closes):
        if c is not None:
            out[datetime.fromtimestamp(t, timezone.utc).astimezone(timezone(timedelta(hours=-4))).date()] = float(c)
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
    """13개 단계를 하나로. 평균 위험도에 '위기 지표가 여럿이면 가중'을 더한다."""
    if not levels:
        return None
    risk = statistics.fmean(6 - lv for lv in levels)          # 1(정상)~5(위기)
    reds = sum(1 for lv in levels if lv == 1)
    oranges = sum(1 for lv in levels if lv == 2)
    risk += 0.5 * (reds >= 3) + 0.5 * (reds + oranges >= len(levels) / 2)
    return max(1, min(5, 6 - round(risk)))


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
        'BAMLH0A0HYM2', 'BAMLC0A0CM', 'T10Y2Y', 'VIXCLS', 'VXVCLS', 'WALCL', 'WTREGEN', 'RRPONTSYD')}
    if f['BAMLH0A0HYM2']:
        series['us_hy'] = {d: round(v * 100, 1) for d, v in f['BAMLH0A0HYM2'].items()}
    if f['BAMLC0A0CM']:
        series['us_ig'] = {d: round(v * 100, 1) for d, v in f['BAMLC0A0CM'].items()}
    if f['T10Y2Y']:
        series['us_curve'] = {d: round(v * 100, 1) for d, v in f['T10Y2Y'].items()}
    if f['VIXCLS']:
        series['vix'] = f['VIXCLS']
    if f['VIXCLS'] and f['VXVCLS']:
        series['vix_ratio'] = combine(f['VIXCLS'], f['VXVCLS'], lambda a, b: round(a / b, 3))
    if f['WALCL'] and f['WTREGEN'] and f['RRPONTSYD']:
        days = sorted(f['RRPONTSYD'])
        walcl, tga = ffill_to(f['WALCL'], days), ffill_to(f['WTREGEN'], days)
        series['us_liq'] = {d: round(walcl[d] / 1000 - tga[d] / 1000 - f['RRPONTSYD'][d], 1)
                            for d in days if d in walcl and d in tga}

    # MOVE (Yahoo)
    move = attempt('Yahoo MOVE', lambda: yahoo_series('%5EMOVE'))
    if move:
        series['move'] = move

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
                row.update(judge(s, ind['risk']))
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
        items.append(row)

    levels = [r['level'] for r in items if r['level']]
    comp = composite(levels)
    counts = {str(lv): sum(1 for x in levels if x == lv) for lv in range(1, 6)}
    data = dict(
        asOf=datetime.now(KST).isoformat(timespec='minutes'),
        composite=dict(level=comp, emoji=LEVELS[comp][0] if comp else '⚪',
                       label=LEVELS[comp][1] if comp else '확인 불가', counts=counts,
                       available=len(levels), total=len(items)),
        indicators=items, errors=errors,
        method='5년 백분위(위험한 쪽) 5단계 + 1개월 변화 표준점수 2↑ 한 단계·3↑ 두 단계 보정. '
               '장단기 금리차는 양쪽 끝, 공포탐욕은 고정 구간(10/25/40/55, 75↑ 과열).')
    return data, history


def _market_of(r):
    return 'KR' if r['key'] == 'vkospi' or r['key'].startswith('kr_') else 'US'


def _vix_temp(v):
    return 0 if v < 15 else 1 if v < 20 else 2 if v < 25 else 3 if v < 30 else 4


def _fng_temp(v):
    return 0 if v >= 55 else 1 if v >= 45 else 2 if v >= 35 else 3 if v >= 25 else 4


def market_summary(rows, mk):
    """시장 하나의 종합 단계와 한두 줄 결론(화면의 문장과 같은 기준)."""
    ok = [r for r in rows if r.get('level')]
    if not ok:
        return ['종합 확인 불가']
    lv = composite([r['level'] for r in ok])
    cnt = {k: sum(1 for r in ok if r['level'] == k) for k in range(1, 6)}
    out = [f'종합 {lv}단계({LEVELS[lv][1]}) {LEVELS[lv][0]}, 🔴{cnt[1]} 🟠{cnt[2]} 🟡{cnt[3]} 🔵{cnt[4]} 🟢{cnt[5]}']
    bad = [r for r in ok if r['level'] <= 2]
    fast = [r for r in ok if (r.get('speedBump') or 0) > 0]
    words = [f'{len(ok)}개 지표 중 {len(bad)}개가 경계 이상' if bad else f'{len(ok)}개 지표 모두 경계보다 아래']
    if bad:
        words.append('경보: ' + ', '.join(r['name'].split(' (')[0] for r in bad))
    if fast:
        words.append('한 달 사이 빠르게 나빠짐: ' + ', '.join(r['name'].split(' (')[0] for r in fast))
    if mk == 'US':
        vx = next((r for r in ok if r['key'] == 'vix'), None)
        fg = next((r for r in ok if r['key'] == 'fng'), None)
        if vx and fg and abs(_fng_temp(fg['value']) - _vix_temp(vx['value'])) >= 2:
            words.append('VIX와 공포탐욕지수가 엇갈림')
    out.append('결론: ' + '. '.join(words) + '.')
    return out


def telegram_text(data):
    c = data['composite']
    cnt = c['counts']
    d = datetime.fromisoformat(data['asOf'])
    lines = [f'📊 매크로 지수 {d.month}/{d.day}',
             f'전체 종합 {c["level"]}단계({c["label"]}) {c["emoji"]}, 🔴{cnt["1"]} 🟠{cnt["2"]} 🟡{cnt["3"]} 🔵{cnt["4"]} 🟢{cnt["5"]}']
    # 미국 먼저, 굵은 구분선 뒤에 한국 (화면과 같은 순서)
    bar = '━━━━━━━━━━━━━━'
    for mk, title in (('US', '🇺🇸 미국'), ('KR', '🇰🇷 한국')):
        rows = [r for r in data['indicators'] if _market_of(r) == mk]
        if not rows:
            continue
        lines += ['', bar, f'{title} 지표 {len(rows)}개', bar]
        lines += market_summary(rows, mk)
        lines.append('')
        for r in rows:
            extra = f' ({r["zone"]})' if r.get('zone') else ''
            warn = ' ⚠️과열' if r.get('overheat') else ''
            ch = f' (1M {r["changeText"]})' if r.get('changeText') else ''
            stale = ' (이전 자료)' if r.get('stale') else ''
            lines.append(f'{r["emoji"]} {r["name"]}: {r["valueText"]}{extra}{ch}{warn}{stale}')
    # 화면은 산업강도판의 세 번째 탭(🌐 매크로 지수)에 있다
    lines += ['', '👉 https://dhandori.github.io/industry-strength/#US-macro (한국은 #KR-macro)']
    return '\n'.join(lines)


def main(argv):
    data, history = build()
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=0) + '\n', encoding='utf-8')
    text = telegram_text(data)
    log(text)
    if '--telegram' in argv:
        token, chat = os.getenv('TELEGRAM_BOT_TOKEN'), os.getenv('TELEGRAM_CHAT_ID')
        if token and chat:
            from wamo_alerts import telegram_send
            try:
                telegram_send(token, chat, text)
                log('텔레그램 전송 완료')
            except Exception as exc:  # noqa: BLE001 - 토큰은 출력하지 않는다
                log(f'텔레그램 전송 실패: {type(exc).__name__}')
        else:
            log('텔레그램 비밀값 없음 · 전송 생략')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
