"""Trend-following signals derived from the verified WAMO price pages.

Builds wamo_trend.json (market weather, RS-line leaders, sell warnings,
upcoming earnings, signal scorecard) from the histories already embedded in
index.html / us.html. The only extra network calls are one Yahoo index
history per market and a cached Nasdaq earnings calendar (US, at most every
12 hours). A failed optional source never blocks the rest.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request

from wamo_update import extract_old_payload
import wamo_climax as climax_mod

ROOT = Path(__file__).resolve().parent
OUTPUT = 'wamo_trend.json'
SIGNAL_LOG = 'wamo_signal_log.json'
EARNINGS_CACHE = 'wamo_earnings_cache.json'
PAGES = {'KR': 'index.html', 'US': 'us.html'}
INDEX = {'KR': ('^KS11', 'KOSPI'), 'US': ('^GSPC', 'S&P 500'), 'JP': ('^N225', '닛케이 225'), 'TW': ('^TWII', '대만 가권')}
# Growth-heavy second index: market weather needs both (O'Neil watches both).
# Japan/Taiwan: Yahoo has no free second index with volume (TOPIX, TPEx), so they run on one index.
SECONDARY = {'KR': ('^KQ11', 'KOSDAQ'), 'US': ('^IXIC', '나스닥')}
# 화면 맨 위 지수 띠: 주지수·보조지수는 이미 받은 자료를 쓰고, 나머지만 따로 받는다.
STRIP = {'US': [('^DJI', '다우'), ('^GSPC', None), ('^IXIC', None), ('^NDX', '나스닥100'), ('^RUT', '러셀2000')],
         'KR': [('^KS11', None), ('^KQ11', None), ('^KS200', '코스피200')]}
EARNINGS_NOTE = {
    'KR': '한국은 실적발표 예정일을 한곳에서 공식으로 모아 주는 자료가 없어 아직 표시하지 않아요. 공시(DART) 탭에서 확인하세요.',
    'JP': '일본은 실적발표 예정일을 무료로 한곳에 모아 주는 공식 자료가 없어 아직 표시하지 않아요.',
    'TW': '대만은 실적발표 예정일을 무료로 한곳에 모아 주는 공식 자료가 없어 아직 표시하지 않아요.',
}
TREND_VERSION = 20
UA = 'Mozilla/5.0 (WAMO daily market dashboard)'
HORIZONS = (5, 20, 60)
SIGNALS = {
    'high52': {'label': '52주 신고가 돌파', 'side': 'buy',
               'why': '20거래일 만에 처음으로 종가가 52주 최고 종가를 넘은 날'},
    'stage2': {'label': 'Stage 2 진입', 'side': 'buy',
               'why': '이동평균선이 상승 정렬로 바뀐 첫날 (10거래일 안에 같은 신호 없음)'},
    'rsLead': {'label': 'RS선 선행 신고가', 'side': 'buy',
               'why': '주가는 고점 아래인데 지수 대비 강도(RS선)가 먼저 52주 신고가'},
    'sell50': {'label': '50일선 대량 이탈', 'side': 'sell',
               'why': '평소의 1.4배 넘는 거래량으로 50일선 아래로 내려간 날'},
    'revTop': {'label': '일중전환 · 천장형', 'side': 'sell',
               'why': '장중 50일 최고가를 새로 찍고도 전날보다 낮게, 그날 범위 아래쪽 절반에서 끝난 날'},
    'climaxTop': {'label': '천장 경고 (클라이맥스 뒤 일중전환)', 'side': 'sell',
                  'why': '클라이맥스 런이 나온 뒤 10거래일 안에 천장형 일중전환이 나온 날'},
}


# ----------------------------------------------------------------- helpers
def num(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def rnd(value, digits=2):
    return None if value is None else round(value, digits)


def sma(values, n, end):
    """Average of values[end-n:end]; None when the window is incomplete."""
    if end < n or end > len(values):
        return None
    window = values[end - n:end]
    if any(v is None for v in window):
        return None
    return sum(window) / n


def clean_rows(raw):
    rows = []
    for r in raw or []:
        if not isinstance(r, dict):
            continue
        d, c, h, l = str(r.get('date', '')), num(r.get('close')), num(r.get('high')), num(r.get('low'))
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', d) or not c or c <= 0:
            continue
        row = {'date': d, 'close': c, 'high': h if h and h > 0 else c,
               'low': l if l and l > 0 else c, 'volume': num(r.get('volume')) or 0.0}
        o = num(r.get('open'))
        # 시가는 캔들 차트에만 쓴다. 그날 고가·저가 안에 있을 때만 남긴다.
        if o and row['low'] <= o <= row['high']:
            row['open'] = o
        rows.append(row)
    rows.sort(key=lambda r: r['date'])
    out = []
    for r in rows:
        if out and out[-1]['date'] == r['date']:
            out[-1] = r
        else:
            out.append(r)
    return out


def symbol_of(stock, market):
    symbol = str(stock.get('stock_code') or stock.get('ticker') or '').strip()
    if market == 'US':
        symbol = str(stock.get('ticker') or symbol).strip().upper()
    ok = re.fullmatch(r'[A-Z0-9]{6}', symbol) if market == 'KR' else re.fullmatch(r'[A-Z0-9][A-Z0-9.^=-]{0,19}', symbol)
    return symbol if ok else None


_KR_MAP = None


def kr_industry_map():
    """WAMO 산업 분류표(제품 단위) — the same table the radar's industry tab uses."""
    global _KR_MAP
    if _KR_MAP is None:
        try:
            raw = json.loads((ROOT / 'wamo_industry_map_KR.json').read_text(encoding='utf-8'))
            table = raw.get('map') if isinstance(raw, dict) else None
            _KR_MAP = {k: v['industry'].strip() for k, v in (table or {}).items()
                       if isinstance(v, dict) and isinstance(v.get('industry'), str) and v['industry'].strip()}
        except (OSError, ValueError, AttributeError):
            _KR_MAP = {}
    return _KR_MAP


def industry_of(stock, market):
    if market == 'KR':
        code = str(stock.get('stock_code') or stock.get('ticker') or '').split('.')[0]
        mapped = kr_industry_map().get(code)
        if mapped:
            return mapped
    keys = ('industryEn', 'industry', 'sector') if market == 'US' else ('industry', 'sector', 'industryEn')
    for key in keys:
        if isinstance(stock.get(key), str) and stock[key].strip():
            return stock[key].strip()
    return '미분류'


# ------------------------------------------------------- per-stock series
def stage_at(closes, i):
    """Weinstein/Minervini style stage at index i (inclusive). None if unknown."""
    end = i + 1
    ma50, ma150, ma200 = sma(closes, 50, end), sma(closes, 150, end), sma(closes, 200, end)
    ma200_prev = sma(closes, 200, end - 20)
    if None in (ma50, ma150, ma200, ma200_prev):
        return None
    c = closes[i]
    if c > ma50 > ma150 > ma200 and ma200 > ma200_prev:
        return 2
    if c < ma200 and ma200 < ma200_prev and ma50 < ma200:
        return 4
    if ma200 >= ma200_prev * 0.995 and (c < ma50 or ma50 < ma150):
        return 3
    return 1


def oneil_raw(closes, end):
    """O'Neil-style weighted 12 month strength using closes[:end]."""
    def ret(older, newer):
        oi, ni = end - 1 - older, end - 1 - newer
        if oi < 0 or ni < 0 or not closes[oi]:
            return None
        return closes[ni] / closes[oi] - 1
    parts = [ret(63, 0), ret(126, 63), ret(189, 126), ret(252, 189)]
    if parts[0] is None or parts[1] is None:
        return None
    w = (0.4, 0.2, 0.2, 0.2)
    return sum(wt * (p or 0) for wt, p in zip(w, parts))


def percentile_map(values):
    vals = sorted(v for v in values.values() if v is not None)
    out = {}
    if not vals:
        return out
    import bisect
    for key, v in values.items():
        if v is None:
            continue
        out[key] = round(bisect.bisect_left(vals, v) / max(1, len(vals) - 1) * 100, 1) if len(vals) > 1 else 50.0
    return out


def aligned(closes, i):
    end = i + 1
    mas = [sma(closes, n, end) for n in (20, 60, 120, 200)]
    if None in mas:
        return None
    return closes[i] > mas[0] > mas[1] > mas[2] > mas[3]


def rs_line(rows, bench_by_date):
    pts = [(r['date'], r['close'] / bench_by_date[r['date']]) for r in rows if bench_by_date.get(r['date'])]
    return pts


def rs_high_at(rs_values, i, window=252):
    """True when RS line at i is at/above the high of the previous window-1 points."""
    if i < 199:
        return None
    start = max(0, i - window + 1)
    prior = rs_values[start:i]
    if len(prior) < 199:
        return None
    return rs_values[i] >= max(prior) * (1 - 1e-9)


def close_high_at(closes, i, window=252):
    if i < window - 1:
        return None
    return closes[i] > max(closes[i - window + 1:i]) * (1 + 1e-9)


def heavy_ma50_break_at(rows, closes, i):
    """Crossed below the 50-day average at i on >=1.4x 50-day average volume."""
    if i < 51:
        return None
    ma_now, ma_prev = sma(closes, 50, i + 1), sma(closes, 50, i)
    if ma_now is None or ma_prev is None:
        return None
    crossed = closes[i] < ma_now and closes[i - 1] >= ma_prev
    vols = [r['volume'] for r in rows[i - 50:i]]
    avg = sum(vols) / 50 if vols else 0
    if not crossed:
        return False
    if avg <= 0 or rows[i]['volume'] <= 0:
        return 'NO_VOLUME'
    return rows[i]['volume'] >= 1.4 * avg


# ------------------------------------------------------------- universe
def build_universe(payload, market):
    asof = (payload.get('meta') or {}).get('asOf')
    out = []
    for stock in payload.get('stocks') or []:
        if not isinstance(stock, dict):
            continue
        symbol = symbol_of(stock, market)
        rows = clean_rows(stock.get('history'))
        if not symbol or not rows or rows[-1]['date'] != asof:
            continue
        if stock.get('isStale') or stock.get('dataStatus') in ('STALE', 'FAILED', 'ERROR'):
            continue
        cap = num(stock.get('market_cap_krw' if market == 'KR' else 'market_cap_usd'))
        out.append({'id': f'{market}:{symbol}', 'symbol': symbol, 'ticker': stock.get('ticker') or symbol,
                    'name': stock.get('name') or symbol, 'industry': industry_of(stock, market),
                    'marketCap': cap, 'rows': rows, 'closes': [r['close'] for r in rows],
                    'rsPercentile': num(stock.get('oneilRsPercentile')) or num(stock.get('rsPercentile')),
                    'vcp': stock.get('vcp') if isinstance(stock.get('vcp'), dict) else None,
                    'growth': growth_of(stock, market),
                    # 상장 시장: 거래대금을 지수별로 나눌 때 씀 (KOSPI/KOSDAQ, NASDAQ/NYSE)
                    'board': str(stock.get('krx_market') or stock.get('exchange') or '').upper()})
    return asof, out


def growth_of(stock, market):
    """Is recent growth backing the price? True / False / None (unknown), plus a short reason.

    Uses only figures already on the page (OpenDART for KR, SEC/Nasdaq for US).
    Rule (WAMO default): latest-quarter EPS test passes (CAN SLIM 'C'), or sales grow >= 20% YoY.
    """
    c = (((stock.get('canSlim') or {}).get('items') or {}).get('C') or {}).get('status')
    if market == 'KR':
        sales = num(stock.get('sales_yoy'))
        eps_pass = c == 'PASS'
        sepa = {}
    else:
        sales = num(stock.get('annualSalesYoyNasdaq'))
        sepa = stock.get('sepaFundamental') if isinstance(stock.get('sepaFundamental'), dict) else {}
        eps_pass = c == 'PASS' or bool(sepa.get('epsPass'))
    reasons = []
    if eps_pass:
        reasons.append('EPS 성장')
    if sales is not None and sales >= 20:
        reasons.append(f'매출 +{sales:.0f}%')
    elif sepa.get('salesPass'):
        reasons.append('매출 성장')
    if reasons:
        return {'ok': True, 'text': ' · '.join(reasons)}
    if sales is not None or c == 'FAIL':
        return {'ok': False, 'text': f'매출 {sales:+.0f}%' if sales is not None else 'EPS 기준 미달'}
    return {'ok': None, 'text': '실적 자료 없음'}


def synthetic_benchmark(universe):
    """Equal-weight median-return index of the screened universe (fallback)."""
    by_date = {}
    for s in universe:
        rows = s['rows']
        for a, b in zip(rows, rows[1:]):
            by_date.setdefault(b['date'], []).append(b['close'] / a['close'] - 1)
    dates = sorted(d for d, v in by_date.items() if len(v) >= max(10, len(universe) * 0.5))
    level, out = 100.0, []
    for d in dates:
        level *= 1 + median(by_date[d])
        out.append({'date': d, 'close': level, 'volume': None})
    return out


# ------------------------------------------------------------ index data
def fetch_index(symbol, days=420, opener=None):
    now = int(time.time())
    q = urllib.parse.urlencode({'period1': now - days * 86400, 'period2': now + 86400,
                                'interval': '1d', 'events': 'history'})
    last = None
    for host in ('query1.finance.yahoo.com', 'query2.finance.yahoo.com'):
        url = f"https://{host}/v8/finance/chart/{urllib.parse.quote(symbol, safe='^')}?{q}"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
            with (opener or urllib.request.urlopen)(req, timeout=20) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            res = (data.get('chart') or {}).get('result') or []
            r0 = res[0]
            if r0.get('meta', {}).get('symbol') != symbol:
                raise ValueError('index symbol mismatch')
            ts = r0.get('timestamp') or []
            quote = ((r0.get('indicators') or {}).get('quote') or [{}])[0]
            tz = r0.get('meta', {}).get('exchangeTimezoneName')
            offset = r0.get('meta', {}).get('gmtoffset') or 0
            rows = []
            for i, t in enumerate(ts):
                c = num((quote.get('close') or [None] * len(ts))[i])
                if not c:
                    continue
                v = num((quote.get('volume') or [None] * len(ts))[i])
                d = datetime.fromtimestamp(t + offset, tz=timezone.utc).date().isoformat()
                def field(key):
                    values = quote.get(key) or []
                    return num(values[i]) if i < len(values) else None
                rows.append({'date': d, 'open': field('open'), 'close': c, 'high': field('high'), 'low': field('low'), 'volume': v})
            if len(rows) < 120:
                raise ValueError('short index history')
            return rows
        except Exception as exc:  # noqa: BLE001 - provider diagnostics stay in logs
            last = exc
    raise RuntimeError(f'index unavailable: {type(last).__name__}')


def load_fixture(market):
    path = os.getenv('WAMO_INDEX_FIXTURE')
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        return data.get(market)
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------- weather
def distribution_days(bench, window=25, reset_on=None):
    """reset_on: follow-through day date - a new uptrend starts the count fresh (IBD practice)."""
    from wamo_ftd import near_recent_high
    offset = max(0, len(bench) - (window + 1))
    rows = bench[-(window + 1):]
    vols = [r.get('volume') for r in rows]
    if len(rows) < window + 1 or sum(1 for v in vols if v) < window * 0.8:
        return {'status': 'UNAVAILABLE', 'count': None, 'window': window, 'days': [],
                'note': '지수 거래량 자료가 부족해 분산일을 셀 수 없습니다.'}
    from wamo_ftd import distribution_kind, close_position, DIST_RULE, watch_reason
    days, count, score, kinds = [], 0, 0.0, {k: 0 for k in DIST_RULE['weights']}
    for i in range(1, len(rows)):
        prev, cur = rows[i - 1], rows[i]
        chg = cur['close'] / prev['close'] - 1
        advancing = near_recent_high(bench, offset + i)
        kind = distribution_kind(prev, cur, advancing=advancing)
        is_dist = kind is not None
        # 지운 이유를 화면에 적기 위해, 분산일 종가 대비 처음으로 5% 이상 오른 날을 기록
        hit = next((r for r in rows[i + 1:] if is_dist and r['close'] >= cur['close'] * 1.05), None)
        ftd_reset = bool(is_dist and hit is None and reset_on and cur['date'] < reset_on)
        expired = hit is not None or ftd_reset
        if is_dist and not expired:
            count += 1
            kinds[kind] += 1
            score += DIST_RULE['weights'][kind]
        day = {'date': cur['date'], 'changePct': rnd(chg * 100), 'distribution': is_dist, 'expired': expired}
        why = None if is_dist else watch_reason(prev, cur, advancing)
        if why:
            day.update(stallWatch=True, watchReason=why, volumeRatio=rnd(cur['volume'] / prev['volume'], 3))
        if ftd_reset:
            day['expiredByFtd'] = reset_on
        if is_dist:
            pos = close_position(cur)
            day.update(close=rnd(cur['close']), kind=kind, closePos=None if pos is None else rnd(pos * 100, 0),
                       # Mini candle on the card: open may be missing in older stored history.
                       candle={k: rnd(cur.get(k)) for k in ('open', 'high', 'low', 'close')},
                       # Volume under the mini candle: today vs the day before, each coloured by its own candle.
                       volume=cur.get('volume'), prevVolume=prev.get('volume'),
                       volumeRatio=rnd(cur['volume'] / prev['volume']) if cur.get('volume') and prev.get('volume') else None,
                       prevCandle={k: rnd(prev.get(k)) for k in ('open', 'close')})
        if hit is not None:
            day['expiredOn'] = hit['date']
            day['expiredClose'] = rnd(hit['close'])
            day['expiredRisePct'] = rnd((hit['close'] / cur['close'] - 1) * 100)
        days.append(day)
    # 보조 신호: 올랐는데 거래량이 전날보다 줄어든 날 (분산일로 세지는 않음 — 매수세가 약해지는 뜻)
    thin = []
    for i in range(1, len(rows)):
        prev, cur = rows[i - 1], rows[i]
        if cur['close'] > prev['close'] * (1 + DIST_RULE['stallMaxPct'] / 100) and cur.get('volume') and prev.get('volume'):
            thin.append((cur['date'], cur['volume'] < prev['volume']))
    last5 = {r['date'] for r in rows[-5:]}
    up5 = [t for d, t in thin if d in last5]
    thin_rally = {'upDays': len(thin), 'thinDays': sum(t for _, t in thin),
                  'upDays5': len(up5), 'thinDays5': sum(up5),
                  'warn': len(up5) >= 2 and sum(up5) >= 2 and sum(up5) / len(up5) >= 0.6}
    return {'status': 'OK', 'count': count, 'score': rnd(score, 1), 'kinds': kinds, 'window': window, 'days': days,
            'weights': DIST_RULE['weights'], 'stage': distribution_stage(count, kinds.get('HEAVY', 0)),
            'thinRally': thin_rally,
            'resetOn': reset_on,
            'note': ('분산일(오닐·IBD 기준): 지수가 0.2% 이상 내리면서 거래량이 전날보다 많았던 날. 같은 날이라도 종가가 중요해서, '
                     '그날 고가~저가 범위의 아래쪽 25% 안에서 끝나면(꽉 찬 음봉) 강한 분산일, 위쪽 절반에서 끝나면(긴 아랫꼬리) 약한 분산일로 봅니다. '
                     '정체일·위장 분산일은 IBD도 사람이 판단해 더하는 날이라 보수적으로 셉니다: 지수가 최근 고점 3% 안에 있고(오른 뒤), '
                     '거래량이 전날보다 5% 이상 늘었고, 찔끔(+0.1% 미만) 올라 범위 아래쪽 절반에서 끝났거나(정체일) 올랐지만 범위 아래쪽 1/3에 처졌을 때(위장)만 셉니다. '
                     '조건이 하나라도 모자라면 세지 않고 후보로만 표시합니다. 25거래일이 지나거나, 그 뒤 지수가 그날 종가보다 5% 넘게 오르거나, '
                     '팔로우스루데이(FTD)로 새 상승 추세가 시작되면 지웁니다.')}


DIST_STAGES = [
    # (최소 횟수, key, 시장 상태, 대응) — 25거래일 안 분산일 누적 횟수 기준
    (0, 'HEALTHY', '건강함', '정상 매매 계속'),
    (3, 'CAUTION', '주의 필요', '신규 매수 자제, 보유 종목 점검 강화'),
    (4, 'WARNING', '경고', '신규 매수 중단, 보유 종목 축소 시작(현금화)'),
    (6, 'DANGER', '위험', '보유 물량 대부분 정리, 현금 비중 극대화'),
]


def distribution_stage(count, heavy):
    """누적 횟수로 단계를 정하고, 3번 이상인데 그중 강한 분산일(꽉 찬 음봉)이 절반 이상이면 한 단계 올린다."""
    base = max(i for i, st in enumerate(DIST_STAGES) if count >= st[0])
    bumped = count >= 3 and heavy >= 2 and heavy * 2 >= count and base < len(DIST_STAGES) - 1
    _, key, label, action = DIST_STAGES[base + bumped]
    out = {'key': key, 'label': label, 'action': action, 'level': base + bumped, 'baseKey': DIST_STAGES[base][1],
           'baseLabel': DIST_STAGES[base][2], 'bumped': bumped,
           'ladder': [{'key': k, 'label': lb, 'range': r, 'action': a}
                      for (_, k, lb, a), r in zip(DIST_STAGES, ('0~2번', '3번', '4~5번', '6번 이상'))]}
    if bumped:
        out['reason'] = f'{count}번 중 {heavy}번이 꽉 찬 음봉(강한 분산일)이라, 횟수 기준({DIST_STAGES[base][2]})보다 한 단계 높여 봐요.'
    return out


def breadth(universe, sessions=60):
    by_date = {}
    for s in universe:
        closes, rows = s['closes'], s['rows']
        n = len(rows)
        for i in range(max(0, n - sessions), n):
            ma50, ma200 = sma(closes, 50, i + 1), sma(closes, 200, i + 1)
            slot = by_date.setdefault(rows[i]['date'], [0, 0, 0, 0])
            if ma50 is not None:
                slot[0] += closes[i] > ma50
                slot[1] += 1
            if ma200 is not None:
                slot[2] += closes[i] > ma200
                slot[3] += 1
    if not by_date:
        return {'status': 'UNAVAILABLE'}
    top = max(v[1] for v in by_date.values()) or 1
    series = [{'date': d, 'above50': rnd(v[0] / v[1] * 100, 1),
               'above200': rnd(v[2] / v[3] * 100, 1) if v[3] else None, 'n': v[1]}
              for d, v in sorted(by_date.items()) if v[1] >= top * 0.8]
    if not series:
        return {'status': 'UNAVAILABLE'}
    last = series[-1]
    ago5 = series[-6] if len(series) >= 6 else None
    ago20 = series[-21] if len(series) >= 21 else None
    return {'status': 'OK', 'above50Pct': last['above50'], 'above200Pct': last['above200'],
            'denominator50': last['n'],
            'above50Change5d': rnd(last['above50'] - ago5['above50'], 1) if ago5 else None,
            'above50Change20d': rnd(last['above50'] - ago20['above50'], 1) if ago20 else None,
            'series': [{'date': p['date'], 'above50': p['above50'], 'above200': p['above200']} for p in series]}


def highs_lows(universe, sessions=10):
    by_date = {}
    for s in universe:
        rows = s['rows']
        n = len(rows)
        for i in range(max(251, n - sessions), n):
            prior = rows[i - 251:i]
            slot = by_date.setdefault(rows[i]['date'], [0, 0, 0])
            slot[0] += rows[i]['high'] > max(r['high'] for r in prior)
            slot[1] += rows[i]['low'] < min(r['low'] for r in prior)
            slot[2] += 1
    if not by_date:
        return {'status': 'UNAVAILABLE'}
    top = max(v[2] for v in by_date.values())
    series = [{'date': d, 'newHighs': v[0], 'newLows': v[1], 'n': v[2]}
              for d, v in sorted(by_date.items()) if v[2] >= top * 0.8]
    last = series[-1]
    return {'status': 'OK', 'newHighs': last['newHighs'], 'newLows': last['newLows'], 'denominator': last['n'],
            'series': series, 'note': '최근 252거래일 장중 고가·저가를 새로 넘은 종목 수입니다.'}


# Slope of a moving average: its % change over a lookback, then a direction.
# 50-day over 10 sessions (±0.5%), 200-day over 20 sessions (±0.3%).
SLOPE_RULES = {50: (10, 0.5), 200: (20, 0.3)}


def ma_slope(closes, n):
    lookback, band = SLOPE_RULES[n]
    now, before = sma(closes, n, len(closes)), sma(closes, n, len(closes) - lookback)
    if now is None or before is None or not before:
        return None, None
    change = (now / before - 1) * 100
    return round(change, 2), 'up' if change > band else 'down' if change < -band else 'flat'


def strip_item(rows, symbol, label, asof):
    """지수 띠 한 칸: 기준일까지의 마지막 두 종가로 등락을 계산한다."""
    rows = [r for r in (rows or []) if r.get('date') and r['date'] <= asof and num(r.get('close'))]
    if len(rows) < 2:
        return None
    last, prev = rows[-1], rows[-2]
    return {'symbol': symbol, 'label': label, 'close': rnd(last['close']),
            'change': rnd(last['close'] - prev['close']) + 0.0, 'changePct': rnd((last['close'] / prev['close'] - 1) * 100) + 0.0,
            'date': last['date'], 'stale': last['date'] != asof,
            'spark': [rnd(r['close']) for r in rows[-20:]]}


STRIP_NAMES = {'^GSPC': 'S&P 500', '^IXIC': '나스닥', '^KS11': 'KOSPI', '^KQ11': 'KOSDAQ'}


def fetch_naver_kospi200():
    """야후에는 코스피200 일봉 이력이 없어 네이버 KRX 지수 일봉을 쓴다(하루 한 번 호출)."""
    req = urllib.request.Request('https://api.stock.naver.com/chart/domestic/index/KPI200?periodType=dayCandle',
                                 headers={'User-Agent': UA, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    if data.get('code') != 'KPI200' or data.get('infoType') != 'index':
        raise ValueError('코스피200 식별자 불일치')
    rows = []
    for r in data.get('priceInfos') or []:
        d = str(r.get('localDate') or '')
        c = num(r.get('closePrice'))
        if len(d) == 8 and c:
            rows.append({'date': f'{d[:4]}-{d[4:6]}-{d[6:]}', 'close': c})
    return rows


def index_strip(market, asof, known, fetch_network, index_fetcher):
    """known: {symbol: (label, rows)} — 이미 받은 지수. 띠에 필요한 나머지 지수는 실패해도 그냥 빠진다."""
    out = []
    for sym, label in STRIP.get(market, [(INDEX[market][0], None)]):
        name, rows = known.get(sym, (label, None))
        if rows is None and fetch_network and not os.getenv('WAMO_INDEX_FIXTURE'):
            try:
                rows = fetch_naver_kospi200() if sym == '^KS200' else index_fetcher(sym)
            except Exception as exc:  # noqa: BLE001 - 띠 한 칸이 빠질 뿐 나머지는 계속
                print(f'{market} 지수 띠 {label or sym} 실패: {exc}', flush=True)
        item = strip_item(rows, sym, label or name or STRIP_NAMES.get(sym, sym), asof)
        if item:
            out.append(item)
    return out


def index_trend(bench):
    closes = [r['close'] for r in bench]
    n = len(closes)
    ma50, ma200 = sma(closes, 50, n), sma(closes, 200, n)
    ma200_prev = sma(closes, 200, n - 20)
    chg = (closes[-1] / closes[-2] - 1) * 100 if n >= 2 else None
    s50, d50 = ma_slope(closes, 50)
    s200, d200 = ma_slope(closes, 200)
    return {'close': rnd(closes[-1]), 'changePct': rnd(chg), 'ma50': rnd(ma50), 'ma200': rnd(ma200),
            'aboveMa50': None if ma50 is None else closes[-1] > ma50,
            'aboveMa200': None if ma200 is None else closes[-1] > ma200,
            'ma200Rising': None if ma200 is None or ma200_prev is None else ma200 > ma200_prev,
            'ma50Slope': s50, 'ma50Dir': d50, 'ma200Slope': s200, 'ma200Dir': d200,
            'date': bench[-1]['date']}


DIR_WORD = {'up': '상승', 'flat': '평평', 'down': '하락'}


def _slope_state(i):
    d50, d200 = i.get('ma50Dir'), i.get('ma200Dir')
    if not d50 or not d200:
        return None, ''
    if d50 == 'up' and d200 == 'up':
        score = 1
    elif d200 == 'down' or (d50 == 'down' and d200 != 'up'):
        score = -1
    else:
        score = 0
    return score, f'50일선 {DIR_WORD[d50]}({i["ma50Slope"]:+.1f}%) · 200일선 {DIR_WORD[d200]}({i["ma200Slope"]:+.1f}%)'


def _index_state(i):
    if i.get('aboveMa50') is None:
        return None, '이동평균선 계산 불가'
    if i['aboveMa50'] and i.get('aboveMa200'):
        return 1, '50·200일선 위'
    if not i.get('aboveMa200'):
        return -1, '200일선 아래'
    # Above the 200-day but below the 50-day: a pullback inside an uptrend.
    return 0, '50일선 아래 · 200일선 위'


def weather(idx, dist, br, hl, synthetic, secondary=None, liq=None, scope='전체 종목'):
    factors = []

    def add(key, label, score, text):
        factors.append({'key': key, 'label': label, 'score': score, 'text': text})

    indexes = [idx] + ([secondary] if secondary and secondary.get('aboveMa50') is not None else [])
    states = [(i.get('label') or '지수', *_index_state(i)) for i in indexes]
    known = [st for _, st, _ in states if st is not None]
    if not known:
        add('index', '지수 추세', 0, '지수 이동평균선을 계산할 수 없습니다.')
    else:
        score = -1 if -1 in known else 1 if all(k == 1 for k in known) else 0
        if len(states) == 1:
            text = {1: '지수가 50일선과 200일선 위에 있어요.', -1: '지수가 200일선 아래로 내려갔어요.',
                    0: '지수가 50일선 아래예요. 200일선 위라 큰 추세는 아직 살아 있어요.'}[known[0]]
        else:
            text = ' · '.join(f'{label} {desc}' for label, _, desc in states)
            text += {1: ' — 둘 다 상승 추세예요.', -1: ' — 200일선 아래로 내려간 지수가 있어요.',
                     0: ' — 큰 추세는 살아 있지만 단기 힘이 빠졌어요.'}[score]
        add('index', '지수 추세', score, text)
    slopes = [(i.get('label') or '지수', *_slope_state(i)) for i in indexes]
    known_s = [st for _, st, _ in slopes if st is not None]
    if known_s:
        score = -1 if -1 in known_s else 1 if all(k == 1 for k in known_s) else 0
        text = ' / '.join(f'{label}: {desc}' if len(slopes) > 1 else desc for label, st, desc in slopes if st is not None)
        text += {1: ' — 이평선이 모두 위를 향해요.', -1: ' — 이평선이 아래를 향하기 시작했어요.',
                 0: ' — 기울기가 꺾이거나 평평해졌어요.'}[score]
        add('slope', '이평선 기울기', score, text)
    dists = [(idx.get('label') or '지수', dist)]
    if secondary and (secondary.get('distribution') or {}).get('status') == 'OK':
        dists.append((secondary.get('label'), secondary['distribution']))
    counted = [(label, d) for label, d in dists if d.get('status') == 'OK']
    if counted:
        # Judge on the 4-step stage (count, raised one step when strong distribution days dominate).
        stage_of = lambda d: d.get('stage') or distribution_stage(d['count'], (d.get('kinds') or {}).get('HEAVY', 0))
        worst_label, worst = max(((label, stage_of(d)) for label, d in counted), key=lambda x: x[1]['level'])
        def one(d):
            k = d.get('kinds') or {}
            parts = [f'{name} {k[key]}' for key, name in (('HEAVY', '꽉 찬 음봉'), ('HIDDEN', '위장'), ('STALL', '정체'), ('LIGHT', '긴 아랫꼬리')) if k.get(key)]
            return f"{d['count']}번" + (f" ({' · '.join(parts)})" if parts else '') + f" · {stage_of(d)['label']}"
        detail = ' / '.join(f'{label} {one(d)}' for label, d in counted) if len(counted) > 1 else one(counted[0][1])
        score_ = {0: 1, 1: 0}.get(worst['level'], -1)
        in_corr = all(d.get('inCorrection') for _, d in counted)
        if in_corr and score_ == 1:
            score_ = 0
        add('distribution', '분산일', score_,
            f'최근 25거래일 동안 기관이 판 흔적(분산일)이 {detail}이에요. '
            + (f"대응: {worst['action']}." if worst['level']
               else '지금은 조정 중이라 분산일이 적어도 안심할 때가 아니에요. 새 매수는 FTD를 기다려요.' if in_corr
               else '정상 매매를 이어가도 돼요.'))
    if br.get('status') == 'OK':
        p = br['above50Pct']
        add('breadth', '시장 체력', 1 if p >= 60 else -1 if p < 40 else 0,
            f'{scope} 중 {p:.0f}%가 50일선 위에 있어요.')
    if hl.get('status') == 'OK':
        h, l, n = hl['newHighs'], hl['newLows'], max(1, hl.get('denominator') or 1)
        # A handful of new highs in a large universe is not broad leadership.
        good = h >= max(2 * l, 0.02 * n)
        bad = l > h or l >= 0.05 * n
        add('highsLows', '신고가 vs 신저가', -1 if bad else 1 if good else 0,
            f'오늘 신고가 {h}종목, 신저가 {l}종목이에요.'
            + (' 신고가 종목이 너무 적어 아직 힘이 약해요.' if not good and not bad and h <= l * 2 + 0.02 * n else ''))
    if liq and liq.get('status') == 'OK':
        gs = liq.get('groups') or []
        if len(gs) == 1:
            g = gs[0]
            text = f"{g['trend']['label']} · 유동성 {g['liquidity']['label']}. {g['trend']['text']}"
        else:
            text = liq['summary'] + '. 두 시장 중 나쁜 쪽으로 점수를 매겨요.'
        add('liquidity', '시장 거래대금', liq['score'], text)
    total = sum(f['score'] for f in factors)
    measured = len(factors)
    if measured < 2:
        level, label, advice = 'UNKNOWN', '판단 보류', '자료가 부족해서 시장 날씨를 정하지 못했어요.'
    elif total >= 2:
        level, label, advice = 'GREEN', '공격 가능', '추세가 좋아요. 신고가 돌파 종목을 적극적으로 볼 때예요.'
    elif total <= -2:
        level, label, advice = 'RED', '현금 비중 확대', '시장이 약해요. 새로 사기보다 지키는 게 먼저예요.'
    else:
        level, label, advice = 'YELLOW', '조심', '좋고 나쁜 신호가 섞여 있어요. 사더라도 조금씩, 확실한 종목만요.'
    if synthetic:
        advice += ' (지수 자료가 없어 화면 속 종목들의 중앙값으로 대신 계산했어요.)'
    return {'level': level, 'label': label, 'advice': advice, 'score': total, 'factors': factors}


# ------------------------------------------------------- per-market split
# 한국은 KOSPI·KOSDAQ 종목을 각자 소속 지수와 비교한다(RS선·차트). 미국은 S&P 500이 NYSE·나스닥 상장사를
# 모두 담고 있어 모든 종목을 S&P 500과 비교하고(오닐·IBD 방식), 지수별 날씨만 나스닥을 따로 본다.
BOARD_FILTERS = {'KR': (('KOSPI', lambda s: s.get('board') == 'KOSPI'), ('KOSDAQ', lambda s: s.get('board') == 'KOSDAQ')),
                 'US': (('S&P 500', lambda s: True), ('나스닥', lambda s: s.get('board') == 'NASDAQ'))}


def assign_board_bench(universe, market, bench2, label2, asof):
    """KOSDAQ 종목에 KOSDAQ 지수를 비교 지수로 붙인다. 지수 자료가 기준일까지 200일 이상 있어야 쓴다."""
    for s in universe:
        s.pop('benchByDate', None); s.pop('benchRows', None); s.pop('benchLabel', None)
    if market != 'KR' or not bench2 or bench2[-1]['date'] != asof or len(bench2) < 200:
        return 0
    by_date = {r['date']: r['close'] for r in bench2}
    n = 0
    for s in universe:
        if s.get('board') == 'KOSDAQ':
            s['benchRows'], s['benchByDate'], s['benchLabel'] = bench2, by_date, label2
            n += 1
    return n


def weather_by_index(market, universe, idx, dist, secondary, liq, synthetic):
    """지수마다 따로 매긴 시장 날씨. 지수 추세·기울기·분산일·거래대금은 그 지수로, 시장 체력·신고가는 그 시장 종목으로."""
    filters = BOARD_FILTERS.get(market)
    if synthetic or not filters or not secondary or secondary.get('aboveMa50') is None:
        return []
    groups = {g.get('symbol'): g for g in (liq or {}).get('groups') or [] if isinstance(g, dict)}
    out = []
    for (ix, d), (name, keep) in zip(((idx, dist), (secondary, secondary.get('distribution') or {})), filters):
        sub = [s for s in universe if keep(s)]
        sym = ix.get('symbol') or INDEX[market][0]
        g = groups.get(sym)
        one = {'status': 'OK', 'groups': [g], 'score': g['score'], 'summary': ''} if g and 'score' in g else None
        label = ix.get('label') or name
        w = weather(ix, d or {}, breadth(sub), highs_lows(sub), False, None, one, scope=f'{label} 종목' if market == 'KR' else ('나스닥 상장 종목' if name == '나스닥' else '전체 종목'))
        out.append({**w, 'symbol': sym, 'indexLabel': label, 'universe': len(sub)})
    return out


# ------------------------------------------------------- stock signals
def analyze_stocks(universe, bench):
    bench_by_date = {r['date']: r['close'] for r in bench}
    raw_now, raw_ago = {}, {}
    for s in universe:
        n = len(s['closes'])
        raw_now[s['id']] = oneil_raw(s['closes'], n)
        raw_ago[s['id']] = oneil_raw(s['closes'], n - 5) if n > 5 else None
    pct_now, pct_ago = percentile_map(raw_now), percentile_map(raw_ago)

    leaders, warnings = [], []
    for s in universe:
        rows, closes = s['rows'], s['closes']
        n = len(closes)
        i = n - 1
        high252 = max(r['high'] for r in rows[-252:]) if n >= 252 else None
        dist = (1 - closes[i] / high252) * 100 if high252 else None
        chg = (closes[i] / closes[i - 1] - 1) * 100 if n >= 2 else None
        stage = stage_at(closes, i)
        rs_now = pct_now.get(s['id'])
        rs_ago = pct_ago.get(s['id'])
        base = {'id': s['id'], 'symbol': s['symbol'], 'name': s['name'], 'industry': s['industry'],
                'close': rnd(closes[i], 4), 'changePct': rnd(chg), 'highDistancePct': rnd(dist),
                'rs': rs_now if rs_now is not None else s['rsPercentile'], 'stage': stage,
                'marketCap': s['marketCap']}
        s['stage'] = stage
        s['highDistancePct'] = dist

        # RS line leadership (KOSDAQ 종목은 KOSDAQ 지수 기준)
        pts = rs_line(rows, s.get('benchByDate') or bench_by_date)
        if len(pts) >= 200:
            values = [v for _, v in pts]
            k = len(values) - 1
            hit = rs_high_at(values, k)
            last_hit = None
            for back in range(0, 5):
                if rs_high_at(values, k - back):
                    last_hit = back
                    break
            if hit or last_hit is not None:
                lead = dist is not None and dist >= 3
                spark = values[-60:]
                lo, hi = min(spark), max(spark)
                leaders.append({**base, 'rsLineHigh': bool(hit), 'rsHighDaysAgo': last_hit, 'leading': lead,
                                'rsSpark': [rnd((v - lo) / (hi - lo) * 100 if hi > lo else 50, 1) for v in spark],
                                'priceSpark': closes[-60:]})

        # Sell warnings only matter for stocks that were trending recently.
        recent_stage2 = any(stage_at(closes, j) == 2 for j in range(max(0, i - 20), i + 1))
        dist10 = None
        if n >= 262:
            h = max(r['high'] for r in rows[-262:-10])
            dist10 = (1 - closes[i - 10] / h) * 100
        if not (recent_stage2 or (dist10 is not None and dist10 <= 15)):
            continue
        reasons = []
        for j in range(i, max(i - 3, 0), -1):
            br = heavy_ma50_break_at(rows, closes, j)
            if br and closes[i] < (sma(closes, 50, n) or 0):
                when = '오늘' if j == i else f'{i - j}거래일 전'
                if br is True:
                    reasons.append({'code': 'MA50_VOLUME', 'severity': 3, 'label': '50일선 대량 이탈',
                                    'detail': f'{when} 거래량이 크게 늘면서 50일선 아래로 내려갔어요.'})
                else:
                    reasons.append({'code': 'MA50', 'severity': 1, 'label': '50일선 이탈',
                                    'detail': f'{when} 50일선 아래로 내려갔어요.'})
                break
        if stage == 4:
            reasons.append({'code': 'STAGE4', 'severity': 3, 'label': 'Stage 4 (하락 단계)',
                            'detail': '200일선 아래이고 200일선도 내려가는 중이에요.'})
        elif stage == 3 and recent_stage2:
            reasons.append({'code': 'STAGE3', 'severity': 1, 'label': 'Stage 3 (고점 다지기)',
                            'detail': '상승 정렬이 무너지고 200일선 상승이 멈추고 있어요.'})
        if rs_now is not None and rs_ago is not None and rs_ago - rs_now >= 20 and rs_now < 70:
            reasons.append({'code': 'RS_DROP', 'severity': 1, 'label': 'RS 급락',
                            'detail': f'상대강도 순위가 5거래일 만에 {rs_ago:.0f} → {rs_now:.0f}로 떨어졌어요.'})
        if n > 6 and aligned(closes, i - 5) and aligned(closes, i) is False:
            reasons.append({'code': 'ALIGN', 'severity': 1, 'label': '정배열 붕괴',
                            'detail': '주가 > 20일 > 60일 > 120일 > 200일선 순서가 이번 주에 깨졌어요.'})
        for j in (i, i - 1):
            if j < 51:
                continue
            day = closes[j] / closes[j - 1] - 1
            avg = sum(r['volume'] for r in rows[j - 50:j]) / 50
            if day <= -0.07 and avg > 0 and rows[j]['volume'] >= 2 * avg:
                reasons.append({'code': 'BIG_DROP', 'severity': 3, 'label': '대량 급락',
                                'detail': f"{'오늘' if j == i else '어제'} {day * 100:.1f}% 떨어졌고 거래량이 평소의 2배가 넘었어요."})
                break
        if reasons:
            score = sum(r['severity'] for r in reasons)
            warnings.append({**base, 'severity': 'HIGH' if score >= 3 else 'MID', 'score': score,
                             'reasons': reasons})

    leaders.sort(key=lambda x: (not x['leading'], -(x['rs'] or 0)))
    warnings.sort(key=lambda x: (-x['score'], -(x['marketCap'] or 0)))
    return leaders, warnings


# ---------------------------------------------------------- scorecard
def signals_at(s, i, bench_by_date):
    rows, closes = s['rows'], s['closes']
    found = []
    if close_high_at(closes, i) and not any(close_high_at(closes, j) for j in range(i - 20, i)):
        found.append('high52')
    if stage_at(closes, i) == 2 and all(stage_at(closes, j) not in (2, None) for j in range(i - 10, i)):
        found.append('stage2')
    br = heavy_ma50_break_at(rows, closes, i)
    if br is True:
        found.append('sell50')
    rv = climax_mod.reversal_at(rows, i)
    if rv and rv['kind'] == 'TOP':
        found.append('revTop')
        if any((climax_mod.climax_at(rows, j) or {}).get('met') for j in range(max(0, i - climax_mod.RECENT), i + 1)):
            found.append('climaxTop')
    if bench_by_date:
        pts = [(k, r['close'] / bench_by_date[r['date']]) for k, r in enumerate(rows[:i + 1]) if bench_by_date.get(r['date'])]
        if len(pts) >= 200 and pts[-1][0] == i:
            values = [v for _, v in pts]
            k = len(values) - 1
            high = max(r['high'] for r in rows[max(0, i - 251):i + 1])
            if rs_high_at(values, k) and closes[i] <= high * 0.97 and not any(rs_high_at(values, k - b) for b in range(1, 11)):
                found.append('rsLead')
    return found


def update_signal_log(log, market, universe, bench, asof, backfill_sessions=40):
    book = log.setdefault('markets', {}).setdefault(market, {'events': []})
    events = book['events']
    keys = {(e['date'], e['id'], e['signal']) for e in events}
    bench_by_date = {r['date']: r['close'] for r in bench}
    first_run = not events
    # 새로 추가된 신호는 한 번만 최근 40거래일을 거꾸로 계산해 성적표를 빨리 채움
    known = set(book.get('signalsKnown') or {e['signal'] for e in events})
    fresh = set(SIGNALS) - known if not first_run else set()
    for s in universe:
        n = len(s['rows'])
        start = n - backfill_sessions if (first_run or fresh) else n - 1
        for i in range(max(start, 230), n):
            for sig in signals_at(s, i, s.get('benchByDate') or bench_by_date):
                if i < n - 1 and not first_run and sig not in fresh:
                    continue
                d = s['rows'][i]['date']
                if (d, s['id'], sig) in keys:
                    continue
                keys.add((d, s['id'], sig))
                events.append({'date': d, 'id': s['id'], 'symbol': s['symbol'], 'name': s['name'],
                               'signal': sig, 'close': rnd(s['closes'][i], 4),
                               'bench': rnd((s.get('benchByDate') or bench_by_date).get(d), 4), 'benchLabel': s.get('benchLabel'), 'backfill': (first_run or sig in fresh) and i < n - 1,
                               'ret': {}, 'excess': {}})
    # Fill forward returns from current verified histories.
    by_id = {s['id']: s for s in universe}
    positions = {}
    def bench_of(s):   # 종목의 비교 지수와 날짜 위치(같은 지수는 한 번만 계산)
        rows_ = s.get('benchRows') or bench
        if id(rows_) not in positions:
            positions[id(rows_)] = (rows_, {r['date']: k for k, r in enumerate(rows_)})
        return positions[id(rows_)]
    for e in events:
        s = by_id.get(e['id'])
        if not s:
            continue
        b_rows, bench_pos = bench_of(s)
        # 예전에 다른 지수로 기록된 사건(KOSDAQ 종목인데 KOSPI로 적힌 것)은 초과수익을 섞지 않는다. 수익률은 계속 채운다.
        same_bench = e.get('benchLabel') == s.get('benchLabel')
        pos = {r['date']: k for k, r in enumerate(s['rows'])}
        k0 = pos.get(e['date'])
        if k0 is None:
            continue
        for h in HORIZONS:
            if str(h) in e['ret'] or k0 + h >= len(s['rows']):
                continue
            ret = s['closes'][k0 + h] / s['closes'][k0] - 1
            e['ret'][str(h)] = rnd(ret * 100)
            b0 = bench_pos.get(e['date'])
            if same_bench and b0 is not None and b0 + h < len(b_rows) and e.get('bench'):
                bret = b_rows[b0 + h]['close'] / b_rows[b0]['close'] - 1
                e['excess'][str(h)] = rnd((ret - bret) * 100)
    cutoff = (date.fromisoformat(asof) - timedelta(days=400)).isoformat()
    book['events'] = [e for e in events if e['date'] >= cutoff]
    book['updatedAsOf'] = asof
    book['signalsKnown'] = sorted(set(SIGNALS) | known)
    return book


def scorecard(book):
    events = book.get('events') or []
    out = {}
    for key, meta in SIGNALS.items():
        evs = [e for e in events if e['signal'] == key]
        stats = {}
        for h in HORIZONS:
            rets = [e['ret'][str(h)] for e in evs if str(h) in e.get('ret', {})]
            exc = [e['excess'][str(h)] for e in evs if str(h) in e.get('excess', {})]
            if not rets:
                stats[str(h)] = {'n': 0}
                continue
            good = [r < 0 for r in rets] if meta['side'] == 'sell' else [r > 0 for r in rets]
            stats[str(h)] = {'n': len(rets), 'avg': rnd(sum(rets) / len(rets)), 'median': rnd(median(rets)),
                             'hitRate': rnd(sum(good) / len(good) * 100, 0),
                             'avgExcess': rnd(sum(exc) / len(exc)) if exc else None}
        recent = sorted(evs, key=lambda e: e['date'], reverse=True)[:12]
        out[key] = {**meta, 'count': len(evs), 'stats': stats,
                    'recent': [{k: e.get(k) for k in ('date', 'id', 'symbol', 'name', 'ret')} for e in recent]}
    dates = sorted(e['date'] for e in events)
    return {'status': 'OK' if events else 'EMPTY', 'since': dates[0] if dates else None,
            'eventCount': len(events), 'backfilled': any(e.get('backfill') for e in events),
            'signals': out,
            'note': '신호가 나온 날 종가로 샀다고 치고, 5·20·60거래일 뒤 수익률을 셉니다. 수수료·세금은 빼지 않았어요. '
                    '처음 40거래일은 현재 종목 목록으로 거꾸로 계산한 값이라 이미 빠진 종목은 들어 있지 않아요.'}


# ------------------------------------------------------------ earnings
def fetch_nasdaq_earnings(day, opener=None):
    url = f'https://api.nasdaq.com/api/calendar/earnings?date={day}'
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json',
                                               'Accept-Language': 'en-US,en;q=0.9'})
    with (opener or urllib.request.urlopen)(req, timeout=15) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    rows = ((data.get('data') or {}).get('rows')) or []
    return [{'symbol': str(r.get('symbol') or '').upper(), 'time': r.get('time') or '',
             'epsForecast': r.get('epsForecast') or None} for r in rows if r.get('symbol')]


def refresh_earnings_cache(cache, asof, days=21, fetcher=fetch_nasdaq_earnings, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        fresh = now - datetime.fromisoformat(cache.get('fetchedAt', '')) < timedelta(hours=12)
    except (TypeError, ValueError):
        fresh = False
    start = date.fromisoformat(asof) + timedelta(days=1)
    wanted = [(start + timedelta(days=k)).isoformat() for k in range(days)
              if (start + timedelta(days=k)).weekday() < 5]
    stored = cache.setdefault('days', {})
    if fresh and all(d in stored for d in wanted):
        return cache, 0
    calls, failures = 0, 0
    for d in wanted:
        if fresh and d in stored:
            continue
        try:
            stored[d] = fetcher(d)
            calls += 1
            time.sleep(0.3)
        except Exception:  # noqa: BLE001
            failures += 1
            if failures >= 3:
                break
    if calls:
        cache['fetchedAt'] = now.isoformat()
    cache['days'] = {d: v for d, v in stored.items() if d >= (start - timedelta(days=7)).isoformat()}
    cache['lastFailures'] = failures
    return cache, calls


def upcoming_earnings(cache, universe, asof, horizon=14):
    by_symbol = {s['symbol'].replace('-', '.'): s for s in universe}
    by_symbol.update({s['symbol']: s for s in universe})
    start = date.fromisoformat(asof)
    out = []
    for d, rows in sorted((cache.get('days') or {}).items()):
        day = date.fromisoformat(d)
        until = (day - start).days
        if until <= 0 or until > horizon:
            continue
        for r in rows:
            s = by_symbol.get(r['symbol']) or by_symbol.get(r['symbol'].replace('.', '-'))
            if not s:
                continue
            in_trend = s.get('stage') == 2 or (s.get('highDistancePct') is not None and s['highDistancePct'] <= 15)
            out.append({'id': s['id'], 'symbol': s['symbol'], 'name': s['name'], 'industry': s['industry'],
                        'date': d, 'daysUntil': until,
                        'time': {'time-after-hours': '장 마감 후', 'time-pre-market': '장 시작 전'}.get(r['time'], '시간 미정'),
                        'inTrend': in_trend, 'highDistancePct': rnd(s.get('highDistancePct')),
                        'stage': s.get('stage')})
    out.sort(key=lambda x: (x['daysUntil'], not x['inTrend'], x['symbol']))
    return out


# ---------------------------------------------------------------- charts
CHART_DAYS = 260      # closes kept per stock (moving averages need 200 + window)
CHART_VOLUME_DAYS = 120


def atr14(rows, n=14):
    """Wilder ATR: average true range over the last n sessions (gap to the prior close included).
    Rows whose high/low were back-filled with the close (missing data) still count; they just shrink the range."""
    if len(rows) < n + 1:
        return None
    trs = []
    for prev, cur in zip(rows[-n - 1:-1], rows[-n:]):
        trs.append(max(cur['high'] - cur['low'], abs(cur['high'] - prev['close']), abs(cur['low'] - prev['close'])))
    atr = trs[0]
    for tr in trs[1:]:
        atr = (atr * (n - 1) + tr) / n
    return atr if atr > 0 else None


def chart_payload(universe, bench, asof, market, bench2=None):
    """Compact per-stock daily bars for the detail chart (loaded on demand).

    c = closes (CHART_DAYS); v, h, l, o = volume/high/low/open for the last CHART_VOLUME_DAYS
    sessions (daily candles; o only once opens are collected, null on days without a bar).
    """
    dates = [r['date'] for r in bench][-CHART_DAYS:]
    if len(dates) < 60 or dates[-1] != asof:
        dates = sorted({r['date'] for s in universe for r in s['rows']})[-CHART_DAYS:]
    digits = 0 if market == 'KR' else 2
    stocks = {}
    for s in universe:
        by_date = {r['date']: r for r in s['rows']}
        first = next((k for k, d in enumerate(dates) if d in by_date), None)
        if first is None:
            continue
        closes, volumes, opens, highs, lows, last = [], [], [], [], [], None
        price = (lambda v: round(v, digits)) if digits else (lambda v: int(round(v)))
        for d in dates[first:]:
            r = by_date.get(d)
            if r:
                last = r
            closes.append(price(last['close']))
            volumes.append(int(r['volume']) if r else 0)
            # 일봉 캔들. 거래가 없던 날(r 없음)은 봉을 그리지 않는다(null).
            opens.append(price(r['open']) if r and r.get('open') else None)
            highs.append(price(r['high']) if r else None)
            lows.append(price(r['low']) if r else None)
        stocks[s['symbol']] = {'s': first, 'c': closes, 'v': volumes[-CHART_VOLUME_DAYS:],
                               'h': highs[-CHART_VOLUME_DAYS:], 'l': lows[-CHART_VOLUME_DAYS:]}
        # 시가는 수집을 시작한 날부터 있다. 하나도 없으면 싣지 않는다(화면은 고가·저가 막대로 그린다).
        if any(v is not None for v in opens[-CHART_VOLUME_DAYS:]):
            stocks[s['symbol']]['o'] = opens[-CHART_VOLUME_DAYS:]
        if s.get('benchRows') is not None and bench2:
            stocks[s['symbol']]['b'] = 2   # 이 종목은 보조 지수(bench2)와 비교한다
        atr = atr14([r for r in s['rows'] if r['date'] <= dates[-1]])
        if atr and last and last['close'] > 0:
            stocks[s['symbol']]['a'] = [round(atr, 4 if digits else 0), round(atr / last['close'] * 100, 2)]
    bench_by_date = {r['date']: r['close'] for r in bench}
    out = {'asOf': asof, 'dates': dates,
           'bench': [round(bench_by_date[d], 2) if d in bench_by_date else None for d in dates],
           'stocks': stocks}
    if bench2 and any(v.get('b') == 2 for v in stocks.values()):
        b2 = {r['date']: r['close'] for r in bench2}
        out['bench2'] = [round(b2[d], 2) if d in b2 else None for d in dates]
    return out


# --------------------------------------------------------------- build
def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def write_json(path, value, compact=False):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    text = json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':') if compact else None, indent=None if compact else 1)
    tmp.write_text(text + '\n', encoding='utf-8')
    tmp.replace(path)


CONFIRM_SLOTS = {'US': ('18:10', '21:10')}   # KST evening rechecks for Yahoo's revised index volume


def volume_schedule(checks):
    """Summarise the evening rechecks: is the volume really final by the first (18:10 KST) check?"""
    if not checks:
        return None
    first = [c for c in checks if c.get('slot') == 'first']
    hit = sum(1 for c in first if c.get('confirmed'))
    verified = len(first) >= 3 and hit / len(first) >= 0.8
    return {'firstSlot': '18:10', 'secondSlot': '21:10', 'checks': len(first), 'confirmedAtFirst': hit,
            'verified': verified, 'last': checks[-1]}


def build_market(root, market, log, earnings_cache, fetch_network=True, index_fetcher=fetch_index, allow_synthetic=True):
    payload = extract_old_payload((root / PAGES[market]).read_text(encoding='utf-8'))
    asof, universe = build_universe(payload, market)
    return build_block(root, market, asof, universe, log, earnings_cache, fetch_network, index_fetcher, allow_synthetic)


def has_real_index(block):
    return bool(block) and not (block.get('index') or {}).get('synthetic')


def mark_kept(block, reason, now):
    """Keep the last verified block on screen and say why (the next scheduled check retries)."""
    kept = dict(block)
    kept['kept'] = {'reason': reason, 'at': now.isoformat()}
    return kept


class IndexDelayed(RuntimeError):
    """The real index bar for the session is not available yet; keep the previous verified block."""


def korean_second_source(rows, symbol, asof, fetch_network=True):
    """Yahoo missing today's KOSPI/KOSDAQ bar: take it from Naver after cross-checking the history."""
    if not fetch_network or symbol not in ('^KS11', '^KQ11') or not rows:
        return rows, None
    from wamo_ftd import extend_korean_latest, fetch_naver_index
    try:
        return extend_korean_latest(rows, symbol, fetch_naver_index(symbol), asof)
    except Exception as exc:  # noqa: BLE001 - verification failure means: do not use it
        print(f'{symbol} 네이버 보조 자료 검증 실패: {type(exc).__name__}: {exc}', flush=True)
        return rows, None


def stored_index(root, symbol, asof):
    """Verified index history saved by an earlier run (wamo_ftd_history.json).

    Yahoo sometimes serves an index without the latest bar for a while after the close even
    though an earlier run already verified that day. Use the stored rows only when they end
    exactly on the expected session with a full bar (close, high, low and volume).
    """
    from wamo_ftd import valid_num
    rows = [r for r in (read_json(root / 'wamo_ftd_history.json', {}).get(symbol) or []) if r.get('date', '') <= asof]
    if len(rows) < 200 or rows[-1]['date'] != asof:
        return None
    if not all(valid_num(rows[-1].get(k)) for k in ('close', 'high', 'low', 'volume')):
        return None
    return rows


def build_block(root, market, asof, universe, log, earnings_cache, fetch_network=True, index_fetcher=fetch_index,
                allow_synthetic=True):
    """Trend, FTD and leadership for one market from verified daily histories (any market).

    Index sources, in order: Yahoo live -> (Korea) Naver after cross-check -> stored verified
    history. If none has the session and a verified block already exists (allow_synthetic=False),
    raise IndexDelayed instead of publishing a stock-median substitute.
    """
    if not asof or len(universe) < 30:
        raise RuntimeError(f'{market} 검증 가격 부족')
    symbol, label = INDEX[market]
    bench, synthetic, source = load_fixture(market), False, 'Yahoo Finance ' + symbol
    if bench:
        source = '미리보기 고정자료 ' + symbol
    elif fetch_network:
        try:
            bench = index_fetcher(symbol)
        except Exception as exc:  # noqa: BLE001
            print(f'{market} 지수 이력 실패 · 종목 중앙값으로 대체: {exc}', flush=True)
    live = bench
    if bench:
        bench = [r for r in bench if r['date'] <= asof]
        if not bench or bench[-1]['date'] != asof:
            print(f'{market} 지수 기준일 불일치 ({bench[-1]["date"] if bench else "자료 없음"} ≠ {asof})', flush=True)
            bench = None
    if not bench and market == 'KR' and not load_fixture(market):
        base = [r for r in (live or []) if r['date'] <= asof] or (read_json(root / 'wamo_ftd_history.json', {}).get(symbol) or [])
        extended, added = korean_second_source(base, symbol, asof, fetch_network)
        if added:
            bench, source = extended, f'Yahoo Finance {symbol} + 네이버 KRX 지수({added} 보완)'
            print(f'{market} {added} 지수 일봉을 네이버에서 교차검증 후 보완', flush=True)
    if not bench and not load_fixture(market):
        saved = stored_index(root, symbol, asof)
        if saved:
            bench, source = saved, '저장된 검증 지수 자료 ' + symbol + ' (야후 실시간 지연)'
            print(f'{market} 저장된 검증 지수 자료로 대신 계산 ({asof})', flush=True)
    if not bench and not allow_synthetic:
        raise IndexDelayed(f'{market} {asof} 지수 일봉을 아직 받지 못함 · 직전 판정 유지')
    if not bench:
        print(f'{market} 종목 중앙값으로 대체', flush=True)
        bench, synthetic, source = synthetic_benchmark(universe), True, '스크리닝 종목 동일가중 중앙값(대체)'
    from wamo_ftd import same_basis_latest, raw_rows, confirmed_latest
    history_meta = read_json(root / 'wamo_ftd_history.json', {})
    first_seen_all = history_meta.get('_firstSeen', {})
    prelim, confirmed = {}, {}
    if not synthetic:
        confirmed[symbol] = confirmed_latest(bench, first_seen_all.get(symbol, {}))
        bench, prelim[symbol] = same_basis_latest(bench, first_seen_all.get(symbol, {}))
    idx = {**index_trend(bench), 'label': label if not synthetic else '종목 중앙값 지수'}
    secondary = None
    sym2, label2 = SECONDARY.get(market, (None, None))
    bench2 = (load_fixture(market + '2') if os.getenv('WAMO_INDEX_FIXTURE') else None)
    if sym2 and bench2 is None and fetch_network and not os.getenv('WAMO_INDEX_FIXTURE'):
        try:
            bench2 = index_fetcher(sym2)
        except Exception as exc:  # noqa: BLE001
            print(f'{market} 보조지수 {label2} 실패 · 주지수만 사용: {exc}', flush=True)
    if bench2:
        bench2 = [r for r in bench2 if r['date'] <= asof]
    if sym2 and not (bench2 and bench2[-1]['date'] == asof) and not os.getenv('WAMO_INDEX_FIXTURE'):
        if market == 'KR':
            base2 = bench2 or (read_json(root / 'wamo_ftd_history.json', {}).get(sym2) or [])
            extended2, added2 = korean_second_source(base2, sym2, asof, fetch_network)
            if added2:
                bench2 = extended2
        if not (bench2 and bench2[-1]['date'] == asof):
            bench2 = stored_index(root, sym2, asof) or bench2
    if bench2:
        confirmed[sym2] = confirmed_latest(bench2, first_seen_all.get(sym2, {}))
        bench2, prelim[sym2] = same_basis_latest(bench2, first_seen_all.get(sym2, {}))
    if bench2:
        if bench2 and bench2[-1]['date'] == asof and len(bench2) >= 200:
            secondary = {**index_trend(bench2), 'symbol': sym2, 'label': label2,
                         'source': 'Yahoo Finance ' + sym2, 'distribution': dict(distribution_days(bench2), prelim=prelim.get(sym2), confirmed=confirmed.get(sym2))}
    dist = (dict(distribution_days(bench), prelim=prelim.get(symbol), confirmed=confirmed.get(symbol)) if not synthetic else {
        'status': 'UNAVAILABLE', 'count': None, 'window': 25, 'days': [],
        'note': '지수 자료를 받지 못해 분산일을 계산하지 않았어요.'})
    br = breadth(universe)
    hl = highs_lows(universe)
    assign_board_bench(universe, market, bench2, label2, asof)
    leaders, warnings = analyze_stocks(universe, bench)
    book = update_signal_log(log, market, universe, bench, asof)
    earnings = {'status': 'UNAVAILABLE', 'upcoming': [], 'note': EARNINGS_NOTE.get(market, '')}
    if market == 'US':
        calls = 0
        if fetch_network:
            earnings_cache, calls = refresh_earnings_cache(earnings_cache, asof)
        upcoming = upcoming_earnings(earnings_cache, universe, asof)
        earnings = {'status': 'OK' if earnings_cache.get('days') else 'UNAVAILABLE', 'upcoming': upcoming[:80],
                    'total': len(upcoming), 'fetchedAt': earnings_cache.get('fetchedAt'), 'newCalls': calls,
                    'source': 'Nasdaq 실적 일정 (공개 자료)',
                    'note': '발표 날짜는 회사 사정으로 바뀔 수 있어요. 발표 전후로 주가가 크게 움직일 수 있어요.'}
    from wamo_ftd import for_market, merge_history, valid_num, fetch_naver_index, repair_korean_history
    from wamo_runtime import expected_session
    expected = expected_session(market)
    traded = None
    if market not in ('KR', 'US'):
        # Days on which at least half the screened stocks traded; typhoon/emergency closures drop out.
        counts = {}
        for s in universe:
            for r in s['rows']:
                counts[r['date']] = counts.get(r['date'], 0) + 1
        traded = {d for d, n in counts.items() if n >= len(universe) * 0.5}
    ftd_indexes, climax_rows = [], []
    ftd_history = read_json(root / "wamo_ftd_history.json", {})
    pairs = [(symbol, label, bench, synthetic)]
    if sym2 and (market in ('KR', 'US') or bench2):   # optional second index: shown only when it arrived
        pairs.append((sym2, label2, bench2, False))
    for sym, name, raw, is_synthetic in pairs:
        repaired_dates = []
        if raw and not is_synthetic:
            raw = merge_history(raw, ftd_history.get(sym, []))
        if market == 'KR' and raw and not is_synthetic and fetch_network:
            if any(not all(valid_num(r.get(k)) for k in ('close','high','low','volume')) for r in raw):
                try:
                    raw, repaired_dates = repair_korean_history(raw, sym, fetch_naver_index(sym))
                except Exception as exc:
                    print(f'{sym} FTD 보완 자료 검증 실패: {type(exc).__name__}', flush=True)
        combined = merge_history(raw or [], ftd_history.get(sym, [])) if not is_synthetic else raw
        signal = for_market(combined or [], market, asof, synthetic=is_synthetic, expected=expected, traded=traded)
        if combined and not is_synthetic:
            climax_rows.append((sym, name, [r for r in combined if r['date'] <= asof]))
        if signal["status"] == "OK":
            ftd_history[sym] = raw_rows([r for r in combined if r["date"] <= asof])
        if combined and not is_synthetic:
            # Remember the first volume seen for each new session (Yahoo revises it later).
            last = raw_rows(combined[-1:])[0]
            seen = ftd_history.setdefault('_firstSeen', {}).setdefault(sym, {})
            if last['date'] <= asof and last['date'] not in seen and last.get('source') != 'naver' and valid_num(last.get('volume')):
                seen[last['date']] = last['volume']
            for old in sorted(seen)[:-20]:
                del seen[old]
        if prelim.get(sym):
            signal['volumePrelim'] = prelim[sym]
        signal.update(symbol=sym, label=name, source='Yahoo Finance ' + sym,
                      sourceUrl='https://finance.yahoo.com/quote/' + urllib.parse.quote(sym, safe='') + '/history/',
                      volumeNote='Yahoo 제공 지수 거래량 · ETF 거래량을 사용하지 않음')
        archived_repairs = [d for d in ftd_history.get('_repairs', {}).get(sym, []) if any(r['date']==d for r in (combined or []))]
        repaired_dates = sorted(set(repaired_dates + archived_repairs))
        if repaired_dates:
            ftd_history.setdefault('_repairs', {})[sym] = repaired_dates
            signal.update(source='Yahoo Finance + 네이버 KRX 지수 교차검증',
                          repairedDates=repaired_dates,
                          repairSourceUrl='https://m.stock.naver.com/domestic/index/' + {'^KS11':'KOSPI','^KQ11':'KOSDAQ'}[sym] + '/total')
        ftd_indexes.append(signal)
    write_json(root / "wamo_ftd_history.json", ftd_history, compact=True)
    # IBD practice: a follow-through day starts a new uptrend, so distribution days from the
    # correction before it are wiped. Recount with that reset when an FTD sits inside the window.
    last_ftd = {sig.get('symbol'): max((e['date'] for e in sig.get('events') or [] if e.get('type') == 'FTD'), default=None)
                for sig in ftd_indexes}
    for sym_, rows_, holder in ((symbol, bench, None), (sym2, bench2, secondary)):
        reset = last_ftd.get(sym_)
        cur = dist if holder is None else (holder or {}).get('distribution')
        if not (reset and rows_ and cur and cur.get('status') == 'OK') or reset < cur['days'][0]['date']:
            continue
        new = dict(distribution_days(rows_, reset_on=reset), prelim=cur.get('prelim'), confirmed=cur.get('confirmed'), resetOn=reset)
        if holder is None:
            dist = new
        else:
            holder['distribution'] = new
    # Distribution days catch the top of a RISING market. In a correction / rally attempt the count is
    # naturally low because the selling already happened, so a low count must not read as "healthy".
    phase_of = {sig.get('symbol'): sig.get('phase') for sig in ftd_indexes}
    for sym_, d_ in ((symbol, dist), (sym2, (secondary or {}).get('distribution'))):
        if d_ and d_.get('status') == 'OK' and phase_of.get(sym_) in ('CORRECTION', 'RALLY', 'LATE'):
            d_['inCorrection'] = phase_of[sym_]
    try:
        import wamo_liquidity
        # Use the same verified index history as FTD (Yahoo merged with stored rows, and for Korea
        # repaired from Naver where Yahoo's volume is missing) — raw Yahoo KOSPI volume has gaps.
        verified = {sym_: rows_ for sym_, _, rows_ in climax_rows}
        # If today's index quote failed (synthetic weather), fall back to the last verified stored
        # history; the card shows that date, so a one-day-old volume verdict is still honest.
        stored = lambda sym_: [r for r in ftd_history.get(sym_, []) if r.get('date', '') <= asof] or None
        main_rows = verified.get(symbol) or stored(symbol) or (None if synthetic else bench)
        bench2 = verified.get(sym2) or stored(sym2) or bench2
        if market == 'KR':
            groups = [('KOSPI', 'KOSPI', symbol, lambda s: s.get('board') == 'KOSPI', main_rows),
                      ('KOSDAQ', 'KOSDAQ', sym2, lambda s: s.get('board') == 'KOSDAQ', bench2)]
        elif market == 'US':
            # S&P 500 has both NYSE and Nasdaq listings (Apple, Nvidia...), so it pairs with every stock.
            groups = [('ALL', 'S&P 500', symbol, lambda s: True, main_rows),
                      ('NASDAQ', '나스닥', sym2, lambda s: s.get('board') == 'NASDAQ', bench2)]
        else:
            groups = [('ALL', label, symbol, lambda s: True, main_rows)]
        liq = wamo_liquidity.split(universe, groups)
        for g in liq.get('groups') or []:
            pv = prelim.get(g.get('symbol'))
            if pv and pv['date'] == g.get('asOf'):
                g['volumePrelim'] = pv
            cv = confirmed.get(g.get('symbol'))
            if cv and cv['date'] == g.get('asOf'):
                g['volumeConfirmed'] = cv
    except Exception as exc:  # noqa: BLE001 - never block the rest
        print(f'{market} 거래대금 계산 실패: {type(exc).__name__}: {exc}', flush=True)
        liq = {'status': 'UNAVAILABLE', 'note': '거래대금 계산에 실패했어요. 다음 갱신 때 다시 계산해요.'}
    try:
        from wamo_leaders import analyze as analyze_leadership
        leadership = {'status': 'OK', **analyze_leadership(universe, bench, market, ftd_indexes)}
        if synthetic:
            leadership['note'] = '지수 자료가 없어 RS선은 화면 속 종목 중앙값 지수와 비교했습니다.'
    except Exception as exc:  # noqa: BLE001 - optional module never blocks the rest
        print(f'{market} 선도주·선도업종 계산 실패: {type(exc).__name__}: {exc}', flush=True)
        leadership = {'status': 'UNAVAILABLE', 'note': '선도주·선도업종 계산에 실패했습니다. 다음 갱신 때 다시 계산합니다.'}
    try:
        climax = climax_mod.build(universe, climax_rows)
    except Exception as exc:  # noqa: BLE001 - optional module never blocks the rest
        print(f'{market} 천장 신호 계산 실패: {type(exc).__name__}: {exc}', flush=True)
        climax = {'status': 'UNAVAILABLE', 'note': '천장 신호 계산에 실패했습니다. 다음 갱신 때 다시 계산합니다.'}
    charts = chart_payload(universe, bench, asof, market, bench2)
    charts['benchLabel'] = idx['label']
    if 'bench2' in charts:
        charts['bench2Label'] = label2
    known = {} if synthetic else {symbol: (label, bench)}
    if secondary and bench2:
        known[sym2] = (label2, bench2)
    strip = index_strip(market, asof, known, fetch_network, index_fetcher)
    return {
        'asOf': asof, 'universe': len(universe), 'version': TREND_VERSION,
        'strip': strip,
        'ftd': {'indexes': ftd_indexes},
        'leadership': leadership,
        'index2': secondary,
        'index': {'symbol': symbol, 'label': label if not synthetic else '종목 중앙값 지수', 'source': source,
                  'synthetic': synthetic, **idx},
        'weather': {**weather(idx, dist, br, hl, synthetic, secondary, liq), 'distribution': dist, 'breadth': br, 'highsLows': hl,
                    'byIndex': weather_by_index(market, universe, {**idx, 'symbol': symbol}, dist, secondary, liq, synthetic)},
        'liquidity': liq,
        'volumeSchedule': volume_schedule(history_meta.get('_confirmChecks', {}).get(market)),
        'rsLeaders': {'count': len(leaders), 'leadingCount': sum(x['leading'] for x in leaders), 'items': leaders[:50],
                      'note': 'RS선 = 주가 ÷ 지수. RS선이 1년 중 가장 높다는 건 지수보다 가장 강하게 버티고 있다는 뜻이에요.'},
        'sellWarnings': {'count': len(warnings), 'highCount': sum(x['severity'] == 'HIGH' for x in warnings),
                         'items': warnings[:80],
                         'note': '최근 20거래일 안에 상승 추세였던 종목만 봐요. 경고는 팔라는 명령이 아니라 확인하라는 신호예요.'},
        'earnings': earnings,
        'climax': climax,
        'scorecard': scorecard(book),
    }, earnings_cache, charts


def build(root=None, markets=('KR', 'US'), fetch_network=True, now=None):
    root = Path(root or ROOT)
    now = now or datetime.now(timezone.utc)
    previous = read_json(root / OUTPUT, {})
    log = read_json(root / SIGNAL_LOG, {'schemaVersion': 1, 'markets': {}})
    earnings_cache = read_json(root / EARNINGS_CACHE, {})
    result = {'schemaVersion': 1, 'generatedAt': now.isoformat(), 'markets': dict(previous.get('markets') or {})}
    built = []
    for market in markets:
        prev_block = (previous.get('markets') or {}).get(market)
        try:
            block, earnings_cache, charts = build_market(root, market, log, earnings_cache, fetch_network,
                                                         allow_synthetic=not has_real_index(prev_block))
            write_json(root / f'wamo_chart_{market}.json', charts, compact=True)
            block['builtAt'] = now.isoformat()
            result['markets'][market] = block
            built.append(market)
            print(f"추세 신호 {market}: 기준일 {block['asOf']} · 날씨 {block['weather']['label']} · "
                  f"RS선 {block['rsLeaders']['count']} · 경고 {block['sellWarnings']['count']}", flush=True)
        except IndexDelayed as exc:
            # Never replace a real-index verdict with a stock-median substitute; retry next check.
            result['markets'][market] = mark_kept(prev_block, '지수 자료 지연', now)
            built.append(market)
            print(f'추세 신호 {market}: {exc}', flush=True)
        except Exception as exc:  # noqa: BLE001 - keep the other market and prior output
            print(f'추세 신호 {market} 생성 실패 · 이전 결과 유지: {type(exc).__name__}: {exc}', flush=True)
    if not built:
        raise RuntimeError('추세 신호 생성 실패')
    write_json(root / OUTPUT, result, compact=True)
    write_json(root / SIGNAL_LOG, log, compact=True)
    if earnings_cache:
        write_json(root / EARNINGS_CACHE, earnings_cache, compact=True)
    return result, built


OUTPUTS = [OUTPUT, SIGNAL_LOG, EARNINGS_CACHE, 'wamo_ftd_history.json', 'wamo_chart_KR.json', 'wamo_chart_US.json']


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--market', choices=['KR', 'US', 'both'], default='both')
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    build(markets=('KR', 'US') if args.market == 'both' else (args.market,), fetch_network=not args.offline)
