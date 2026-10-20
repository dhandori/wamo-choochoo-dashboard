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

ROOT = Path(__file__).resolve().parent
OUTPUT = 'wamo_trend.json'
SIGNAL_LOG = 'wamo_signal_log.json'
EARNINGS_CACHE = 'wamo_earnings_cache.json'
PAGES = {'KR': 'index.html', 'US': 'us.html'}
INDEX = {'KR': ('^KS11', 'KOSPI'), 'US': ('^GSPC', 'S&P 500'), 'JP': ('^N225', '닛케이 225'), 'TW': ('^TWII', '대만 가권')}
# Growth-heavy second index: market weather needs both (O'Neil watches both).
# Japan/Taiwan: Yahoo has no free second index with volume (TOPIX, TPEx), so they run on one index.
SECONDARY = {'KR': ('^KQ11', 'KOSDAQ'), 'US': ('^IXIC', '나스닥')}
EARNINGS_NOTE = {
    'KR': '한국은 실적발표 예정일을 한곳에서 공식으로 모아 주는 자료가 없어 아직 표시하지 않아요. 공시(DART) 탭에서 확인하세요.',
    'JP': '일본은 실적발표 예정일을 무료로 한곳에 모아 주는 공식 자료가 없어 아직 표시하지 않아요.',
    'TW': '대만은 실적발표 예정일을 무료로 한곳에 모아 주는 공식 자료가 없어 아직 표시하지 않아요.',
}
TREND_VERSION = 6
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
        rows.append({'date': d, 'close': c, 'high': h if h and h > 0 else c,
                     'low': l if l and l > 0 else c, 'volume': num(r.get('volume')) or 0.0})
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
                    'growth': growth_of(stock, market)})
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
                rows.append({'date': d, 'close': c, 'high': field('high'), 'low': field('low'), 'volume': v})
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
def distribution_days(bench, window=25):
    rows = bench[-(window + 1):]
    vols = [r.get('volume') for r in rows]
    if len(rows) < window + 1 or sum(1 for v in vols if v) < window * 0.8:
        return {'status': 'UNAVAILABLE', 'count': None, 'window': window, 'days': [],
                'note': '지수 거래량 자료가 부족해 분산일을 셀 수 없습니다.'}
    from wamo_ftd import distribution_kind, close_position, DIST_RULE
    days, count, score, kinds = [], 0, 0.0, {'HEAVY': 0, 'NORMAL': 0, 'LIGHT': 0, 'HIDDEN': 0}
    for i in range(1, len(rows)):
        prev, cur = rows[i - 1], rows[i]
        chg = cur['close'] / prev['close'] - 1
        kind = distribution_kind(prev, cur)
        is_dist = kind is not None
        # 지운 이유를 화면에 적기 위해, 분산일 종가 대비 처음으로 5% 이상 오른 날을 기록
        hit = next((r for r in rows[i + 1:] if is_dist and r['close'] >= cur['close'] * 1.05), None)
        expired = hit is not None
        if is_dist and not expired:
            count += 1
            kinds[kind] += 1
            score += DIST_RULE['weights'][kind]
        day = {'date': cur['date'], 'changePct': rnd(chg * 100), 'distribution': is_dist, 'expired': expired}
        if is_dist:
            pos = close_position(cur)
            day.update(close=rnd(cur['close']), kind=kind, closePos=None if pos is None else rnd(pos * 100, 0))
        if hit is not None:
            day['expiredOn'] = hit['date']
            day['expiredClose'] = rnd(hit['close'])
            day['expiredRisePct'] = rnd((hit['close'] / cur['close'] - 1) * 100)
        days.append(day)
    return {'status': 'OK', 'count': count, 'score': rnd(score, 1), 'kinds': kinds, 'window': window, 'days': days,
            'weights': DIST_RULE['weights'],
            'note': ('분산일: 지수가 0.2% 이상 내리면서 거래량이 전날보다 많았던 날. 같은 날이라도 종가가 어디서 끝났는지가 중요해서, '
                     '그날 고가~저가 범위에서 아래쪽 1/3에서 끝나면(꽉 찬 음봉) 위험, 위쪽 절반에서 끝나면(저가에서 회복) 덜 위험으로 봅니다. '
                     '지수가 올랐어도 거래량이 늘고 종가가 범위 아래쪽 1/3에 처지면(윗꼬리) 막판 매물이 끌어내린 위장 분산일로 셉니다. '
                     '그 뒤 지수가 5% 넘게 오르면 지웁니다.')}


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


def weather(idx, dist, br, hl, synthetic, secondary=None):
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
        # Judge on the risk score (heavy 1.5 · normal 1 · light 0.5 · hidden 1), same 4/6 lines as the plain count.
        risk = lambda d: d.get('score', d['count'])
        c = max(risk(d) for _, d in counted)
        def one(d):
            k = d.get('kinds') or {}
            parts = [f'{name} {k[key]}' for key, name in (('HEAVY', '꽉 찬 음봉'), ('HIDDEN', '위장'), ('LIGHT', '저가 회복')) if k.get(key)]
            return f"{d['count']}번" + (f" ({' · '.join(parts)}) · 위험 점수 {risk(d):g}" if parts else '')
        detail = ' / '.join(f'{label} {one(d)}' for label, d in counted) if len(counted) > 1 else one(counted[0][1])
        add('distribution', '분산일', -1 if c >= 6 else 0 if c >= 4 else 1,
            f'최근 25거래일 동안 기관이 판 흔적(분산일)이 {detail} 있었어요.' + (' 위험 점수 6 이상은 경고 신호예요.' if c >= 6 else ''))
    if br.get('status') == 'OK':
        p = br['above50Pct']
        add('breadth', '시장 체력', 1 if p >= 60 else -1 if p < 40 else 0,
            f'전체 종목 중 {p:.0f}%가 50일선 위에 있어요.')
    if hl.get('status') == 'OK':
        h, l, n = hl['newHighs'], hl['newLows'], max(1, hl.get('denominator') or 1)
        # A handful of new highs in a large universe is not broad leadership.
        good = h >= max(2 * l, 0.02 * n)
        bad = l > h or l >= 0.05 * n
        add('highsLows', '신고가 vs 신저가', -1 if bad else 1 if good else 0,
            f'오늘 신고가 {h}종목, 신저가 {l}종목이에요.'
            + (' 신고가 종목이 너무 적어 아직 힘이 약해요.' if not good and not bad and h <= l * 2 + 0.02 * n else ''))
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

        # RS line leadership
        pts = rs_line(rows, bench_by_date)
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
    for s in universe:
        n = len(s['rows'])
        start = n - backfill_sessions if first_run else n - 1
        for i in range(max(start, 230), n):
            for sig in signals_at(s, i, bench_by_date):
                d = s['rows'][i]['date']
                if (d, s['id'], sig) in keys:
                    continue
                keys.add((d, s['id'], sig))
                events.append({'date': d, 'id': s['id'], 'symbol': s['symbol'], 'name': s['name'],
                               'signal': sig, 'close': rnd(s['closes'][i], 4),
                               'bench': rnd(bench_by_date.get(d), 4), 'backfill': first_run and i < n - 1,
                               'ret': {}, 'excess': {}})
    # Fill forward returns from current verified histories.
    by_id = {s['id']: s for s in universe}
    bench_dates = [r['date'] for r in bench]
    bench_pos = {d: k for k, d in enumerate(bench_dates)}
    for e in events:
        s = by_id.get(e['id'])
        if not s:
            continue
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
            if b0 is not None and b0 + h < len(bench) and e.get('bench'):
                bret = bench[b0 + h]['close'] / bench[b0]['close'] - 1
                e['excess'][str(h)] = rnd((ret - bret) * 100)
    cutoff = (date.fromisoformat(asof) - timedelta(days=400)).isoformat()
    book['events'] = [e for e in events if e['date'] >= cutoff]
    book['updatedAsOf'] = asof
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


def chart_payload(universe, bench, asof, market):
    """Compact per-stock daily closes for the detail chart (loaded on demand)."""
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
        closes, volumes, last = [], [], None
        for d in dates[first:]:
            r = by_date.get(d)
            if r:
                last = r
            closes.append(round(last['close'], digits) if digits else int(round(last['close'])))
            volumes.append(int(r['volume']) if r else 0)
        stocks[s['symbol']] = {'s': first, 'c': closes, 'v': volumes[-CHART_VOLUME_DAYS:]}
    bench_by_date = {r['date']: r['close'] for r in bench}
    return {'asOf': asof, 'dates': dates,
            'bench': [round(bench_by_date[d], 2) if d in bench_by_date else None for d in dates],
            'stocks': stocks}


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


def build_market(root, market, log, earnings_cache, fetch_network=True, index_fetcher=fetch_index):
    payload = extract_old_payload((root / PAGES[market]).read_text(encoding='utf-8'))
    asof, universe = build_universe(payload, market)
    return build_block(root, market, asof, universe, log, earnings_cache, fetch_network, index_fetcher)


def build_block(root, market, asof, universe, log, earnings_cache, fetch_network=True, index_fetcher=fetch_index):
    """Trend, FTD and leadership for one market from verified daily histories (any market)."""
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
    if bench:
        bench = [r for r in bench if r['date'] <= asof]
        if not bench or bench[-1]['date'] != asof:
            print(f'{market} 지수 기준일 불일치 · 종목 중앙값으로 대체', flush=True)
            bench = None
    if not bench:
        bench, synthetic, source = synthetic_benchmark(universe), True, '스크리닝 종목 동일가중 중앙값(대체)'
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
        if bench2 and bench2[-1]['date'] == asof and len(bench2) >= 200:
            secondary = {**index_trend(bench2), 'symbol': sym2, 'label': label2,
                         'source': 'Yahoo Finance ' + sym2, 'distribution': distribution_days(bench2)}
    dist = distribution_days(bench) if not synthetic else {
        'status': 'UNAVAILABLE', 'count': None, 'window': 25, 'days': [],
        'note': '지수 자료를 받지 못해 분산일을 계산하지 않았어요.'}
    br = breadth(universe)
    hl = highs_lows(universe)
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
    ftd_indexes = []
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
        if signal["status"] == "OK":
            ftd_history[sym] = [r for r in combined if r["date"] <= asof]
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
    try:
        from wamo_leaders import analyze as analyze_leadership
        leadership = {'status': 'OK', **analyze_leadership(universe, bench, market, ftd_indexes)}
        if synthetic:
            leadership['note'] = '지수 자료가 없어 RS선은 화면 속 종목 중앙값 지수와 비교했습니다.'
    except Exception as exc:  # noqa: BLE001 - optional module never blocks the rest
        print(f'{market} 선도주·선도업종 계산 실패: {type(exc).__name__}: {exc}', flush=True)
        leadership = {'status': 'UNAVAILABLE', 'note': '선도주·선도업종 계산에 실패했습니다. 다음 갱신 때 다시 계산합니다.'}
    charts = chart_payload(universe, bench, asof, market)
    charts['benchLabel'] = idx['label']
    return {
        'asOf': asof, 'universe': len(universe), 'version': TREND_VERSION,
        'ftd': {'indexes': ftd_indexes},
        'leadership': leadership,
        'index2': secondary,
        'index': {'symbol': symbol, 'label': label if not synthetic else '종목 중앙값 지수', 'source': source,
                  'synthetic': synthetic, **idx},
        'weather': {**weather(idx, dist, br, hl, synthetic, secondary), 'distribution': dist, 'breadth': br, 'highsLows': hl},
        'rsLeaders': {'count': len(leaders), 'leadingCount': sum(x['leading'] for x in leaders), 'items': leaders[:50],
                      'note': 'RS선 = 주가 ÷ 지수. RS선이 1년 중 가장 높다는 건 지수보다 가장 강하게 버티고 있다는 뜻이에요.'},
        'sellWarnings': {'count': len(warnings), 'highCount': sum(x['severity'] == 'HIGH' for x in warnings),
                         'items': warnings[:80],
                         'note': '최근 20거래일 안에 상승 추세였던 종목만 봐요. 경고는 팔라는 명령이 아니라 확인하라는 신호예요.'},
        'earnings': earnings,
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
        try:
            block, earnings_cache, charts = build_market(root, market, log, earnings_cache, fetch_network)
            write_json(root / f'wamo_chart_{market}.json', charts, compact=True)
            block['builtAt'] = now.isoformat()
            result['markets'][market] = block
            built.append(market)
            print(f"추세 신호 {market}: 기준일 {block['asOf']} · 날씨 {block['weather']['label']} · "
                  f"RS선 {block['rsLeaders']['count']} · 경고 {block['sellWarnings']['count']}", flush=True)
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
