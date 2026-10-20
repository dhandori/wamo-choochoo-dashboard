"""천장 신호 — 일중전환(장중 반전)과 클라이맥스 런.

오닐(How to Make Money in Stocks, 매도 규칙)과 미너비니(Trade Like a Stock Market Wizard,
'climax run')가 쓰는 두 가지 '끝물' 신호를 이미 검증된 일봉(고가·저가·종가·거래량)으로 계산합니다.

일중전환 · 천장형  장중에 최근 50거래일 최고가를 새로 찍고도 전날보다 낮게, 그날 범위의
                  아래쪽 절반에서 끝난 날. 높은 곳에서 큰손이 물량을 넘긴 흔적으로 봅니다.
일중전환 · 바닥형  장중에 최근 20거래일 최저가를 깨고도 전날보다 높게, 그날 범위의 위쪽에서
                  끝난 날. 털어내기 뒤 되돌림 반등 신호입니다.
클라이맥스 런      이미 오르던 종목이 1~3주 사이에 크게 가속하며 수직으로 치솟는 구간.
천장 경고          클라이맥스 런이 나온 뒤 10거래일 안에 천장형 일중전환이 나온 경우.

모든 숫자는 와모 기본값이며 아래 표(STOCK·INDEX)에서 바꿀 수 있습니다. 시가가 없는 종목
이력에서는 '갭 상승 뒤 반전' 점수를 고가·저가로 판단합니다.
"""
from __future__ import annotations

from statistics import mean

# 종목 기준(오닐: 1~3주 25~50% 급등, 미너비니: 50일선과의 큰 이격)
STOCK = {'run_pct': 25.0, 'run_window': 15, 'ext50': 25.0, 'ext200': 70.0,
         'vol_mult': 1.3, 'swing_pct': 3.0, 'min_points': 2, 'open_gap': True}
# 지수는 움직임이 작아 같은 뜻을 작은 숫자로 옮겼습니다.
# 지수는 시가 갭이 흔해서 '빈칸이 메워지지 않은 갭'(저가 > 전날 고가)만 소진 갭으로 셉니다.
INDEX = {'run_pct': 12.0, 'run_window': 15, 'ext50': 10.0, 'ext200': 25.0,
         'vol_mult': 1.15, 'swing_pct': 1.5, 'min_points': 3, 'open_gap': False}
TOP_LOOKBACK = 50       # 천장형: 최근 50거래일 장중 최고가 갱신
BOTTOM_LOOKBACK = 20    # 바닥형: 최근 20거래일 장중 최저가 이탈
RANGE_DAYS = 20         # '평소 하루 움직임' = 최근 20거래일 평균 (고가−저가) ÷ 전날 종가
COMPARE_DAYS = 240      # '사상 최대'를 비교하는 기간(약 1년)
RECENT = 10             # 클라이맥스 구간·천장 경고를 찾는 최근 거래일 수
SHOW_REVERSAL = 5       # 종목 목록에 올리는 일중전환의 최근 거래일 수

POINT_TEXT = {
    # 클라이맥스 런
    'BIG_DAY': ('최대 하루 상승', '이번 급등 안에서 1년 중 가장 큰 하루 상승이 나왔어요.'),
    'BIG_WEEK': ('최대 주간 상승', '5거래일 상승폭이 1년 중 가장 커요.'),
    'UP_DAYS': ('쉬지 않는 상승', '최근 10거래일 중 {n}일이 올랐어요.'),
    'GAP': ('소진 갭', '{d} 전날 고가보다 높게 시작해 빈칸(갭)을 남겼어요. 끝물에 자주 나와요.'),
    'EXTENDED': ('이평선과 너무 벌어짐', '종가가 50일선보다 {e50}% · 200일선보다 {e200}% 위예요.'),
    'VOL_CLIMAX': ('최대 거래량', '최근 10거래일 안에 1년 중 가장 많은 거래량이 터졌어요.'),
    # 일중전환
    'WIDE': ('넓은 하루 범위', '하루 움직임이 평소의 {r}배예요.'),
    'VOLUME': ('대량 거래', '거래량이 50일 평균의 {v}배예요.'),
    'NEAR_LOW': ('저가 근처 마감', '그날 범위의 아래쪽 {p}% 자리에서 끝났어요.'),
    'NEAR_HIGH': ('고가 근처 마감', '그날 범위의 위쪽, {p}% 자리에서 끝났어요.'),
    'GAP_TRAP': ('갭 상승 뒤 반전', '전날 고가보다 높게 시작했다가 밀렸어요.'),
    'GAP_DOWN_TRAP': ('갭 하락 뒤 반전', '전날 저가보다 낮게 시작했다가 말아 올렸어요.'),
    'SWING': ('장중 큰 되밀림', '장중 +{up}%까지 올랐다가 {chg}%로 끝났어요.'),
    'SWING_UP': ('장중 큰 말아올림', '장중 {down}%까지 빠졌다가 {chg}%로 끝났어요.'),
}


def _r(v, d=2):
    return None if v is None else round(v, d)


def _sma(values, n, end):
    if end < n:
        return None
    w = values[end - n:end]
    return sum(w) / n if all(v is not None for v in w) else None


def _point(code, **kw):
    label, text = POINT_TEXT[code]
    return {'code': code, 'label': label, 'detail': text.format(**kw)}


def _avg_range(rows, i, n=RANGE_DAYS):
    vals = [(rows[j]['high'] - rows[j]['low']) / rows[j - 1]['close']
            for j in range(max(1, i - n), i) if rows[j - 1]['close']]
    return mean(vals) if len(vals) >= n // 2 else None


def _avg_volume(rows, i, n=50):
    vols = [rows[j]['volume'] for j in range(max(0, i - n), i) if rows[j].get('volume')]
    return mean(vols) if len(vols) >= 20 else None


def _candle(r):
    return {k: _r(r.get(k), 4) for k in ('open', 'high', 'low', 'close')}


def reversal_at(rows, i, P=STOCK, need_uptrend=True):
    """천장형('TOP') 또는 바닥형('BOTTOM') 일중전환이면 설명을 돌려줍니다. 아니면 None."""
    if i < max(TOP_LOOKBACK, 51):
        return None
    r, prev = rows[i], rows[i - 1]
    h, l, c, pc = r['high'], r['low'], r['close'], prev['close']
    if not (h and l and c and pc) or h <= l:
        return None
    avg = _avg_range(rows, i)
    rng = (h - l) / pc
    if not avg or rng < avg:            # 평소보다 좁은 날은 반전이라 부르지 않음
        return None
    pos = (c - l) / (h - l)
    chg = (c / pc - 1) * 100
    closes = [x['close'] for x in rows]
    ma50 = _sma(closes, 50, i)          # 전날까지의 50일선
    vol_avg = _avg_volume(rows, i)
    vol_ratio = r['volume'] / vol_avg if vol_avg and r.get('volume') else None
    o = r.get('open')
    base = {'date': r['date'], 'closePos': _r(pos * 100, 0), 'changePct': _r(chg),
            'rangeRatio': _r(rng / avg), 'volumeRatio': _r(vol_ratio), 'candle': _candle(r),
            'prevClose': _r(pc, 4)}
    if h >= max(x['high'] for x in rows[i - TOP_LOOKBACK:i]) and c < pc and pos <= 0.5:
        if need_uptrend and (ma50 is None or pc < ma50):
            return None
        pts = []
        if rng >= 1.5 * avg:
            pts.append(_point('WIDE', r=f'{rng / avg:.1f}'))
        if vol_ratio and vol_ratio >= P['vol_mult']:
            pts.append(_point('VOLUME', v=f'{vol_ratio:.1f}'))
        if pos <= 0.2:
            pts.append(_point('NEAR_LOW', p=f'{pos * 100:.0f}'))
        if o and o > prev['high']:
            pts.append(_point('GAP_TRAP'))
        elif (h / pc - 1) * 100 >= P['swing_pct']:
            pts.append(_point('SWING', up=f'{(h / pc - 1) * 100:.1f}', chg=f'{chg:.1f}'))
        return {**base, 'kind': 'TOP', 'strong': len(pts) >= 2, 'points': pts}
    if l <= min(x['low'] for x in rows[i - BOTTOM_LOOKBACK:i]) and c > pc and pos >= 0.6:
        ma200 = _sma(closes, 200, i)
        if need_uptrend and (ma200 is None or pc < ma200):
            return None                 # 종목은 200일선 위 되돌림에서 나온 반등만
        pts = []
        if rng >= 1.5 * avg:
            pts.append(_point('WIDE', r=f'{rng / avg:.1f}'))
        if vol_ratio and vol_ratio >= P['vol_mult']:
            pts.append(_point('VOLUME', v=f'{vol_ratio:.1f}'))
        if pos >= 0.8:
            pts.append(_point('NEAR_HIGH', p=f'{pos * 100:.0f}'))
        if o and o < prev['low']:
            pts.append(_point('GAP_DOWN_TRAP'))
        elif (1 - l / pc) * 100 >= P['swing_pct']:
            pts.append(_point('SWING_UP', down=f'{(l / pc - 1) * 100:.1f}', chg=f'{chg:+.1f}'))
        return {**base, 'kind': 'BOTTOM', 'strong': len(pts) >= 2, 'points': pts}
    return None


def climax_at(rows, i, P=STOCK):
    """i일 종가 기준 클라이맥스 런 판정. 조건을 모두 채우면 met=True."""
    win = P['run_window']
    if i < 60 + win:
        return None
    closes = [x['close'] for x in rows]
    c = closes[i]
    ma50, ma200 = _sma(closes, 50, i + 1), _sma(closes, 200, i + 1)
    uptrend = ma50 is not None and c > ma50 and (ma200 is None or ma50 > ma200)
    lows = closes[i - win:i]
    lo = min(lows)
    start = i - win + lows.index(lo)
    gain = (c / lo - 1) * 100
    pts = []
    k0 = max(1, i - COMPARE_DAYS)
    recent = range(i - RECENT + 1, i + 1)
    day = lambda j: closes[j] / closes[j - 1] - 1
    prior_days = [day(j) for j in range(k0, i - RECENT + 1)]
    if len(prior_days) >= 100:
        best = max(day(j) for j in recent)
        if best > 0 and best >= max(prior_days):
            pts.append(_point('BIG_DAY'))
        wk = lambda j: closes[j] / closes[j - 5] - 1
        prior_wk = [wk(j) for j in range(max(5, k0), i - RECENT + 1)]
        if prior_wk and max(wk(j) for j in recent) >= max(prior_wk) and max(wk(j) for j in recent) > 0:
            pts.append(_point('BIG_WEEK'))
        vols = [rows[j]['volume'] or 0 for j in range(k0, i - RECENT + 1)]
        if max(vols) > 0 and max(rows[j]['volume'] or 0 for j in recent) >= max(vols):
            pts.append(_point('VOL_CLIMAX'))
    ups = sum(day(j) > 0 for j in recent)
    if ups >= 7:
        pts.append(_point('UP_DAYS', n=ups))
    for j in range(i, i - RECENT, -1):
        a, b = rows[j], rows[j - 1]
        o = a.get('open')
        gap = a['low'] > b['high'] or (P['open_gap'] and o and o >= b['high'] * 1.005 and a['close'] > b['high'])
        if gap and a['close'] > b['close']:
            pts.append(_point('GAP', d=f"{int(a['date'][5:7])}/{int(a['date'][8:10])}"))
            break
    e50 = (c / ma50 - 1) * 100 if ma50 else None
    e200 = (c / ma200 - 1) * 100 if ma200 else None
    if (e50 is not None and e50 >= P['ext50']) or (e200 is not None and e200 >= P['ext200']):
        pts.append(_point('EXTENDED', e50=f'{e50:.0f}' if e50 is not None else '—',
                          e200=f'{e200:.0f}' if e200 is not None else '—'))
    met = uptrend and gain >= P['run_pct'] and len(pts) >= P['min_points']
    return {'date': rows[i]['date'], 'met': met, 'strong': met and len(pts) >= P['min_points'] + 2, 'gainPct': _r(gain, 1),
            'runStart': rows[start]['date'], 'ext50': _r(e50, 1), 'ext200': _r(e200, 1),
            'uptrend': uptrend, 'points': pts}


def scan(rows, P=STOCK, need_uptrend=True, recent=RECENT):
    """최근 `recent` 거래일의 일중전환·클라이맥스 판정과 천장 경고."""
    n = len(rows)
    out = {'reversals': [], 'climaxDays': [], 'climax': None, 'topWarning': None}
    for i in range(max(0, n - recent - RECENT), n):
        cl = climax_at(rows, i, P)
        if cl and cl['met']:
            out['climaxDays'].append({'i': i, **cl})
    for i in range(max(0, n - recent), n):
        rv = reversal_at(rows, i, P, need_uptrend)
        if rv:
            out['reversals'].append({'i': i, **rv})
    in_window = [c for c in out['climaxDays'] if c['i'] >= n - recent]
    if in_window:
        best = max(in_window, key=lambda c: (len(c['points']), c['i']))
        peak_i = max(range(in_window[0]['i'], n), key=lambda j: rows[j]['high'])
        out['climax'] = {**best, 'lastDate': in_window[-1]['date'], 'days': len(in_window),
                         'peakDate': rows[peak_i]['date'], 'peak': _r(rows[peak_i]['high'], 4),
                         'fromPeakPct': _r((rows[-1]['close'] / rows[peak_i]['high'] - 1) * 100, 1)}
    for rv in reversed(out['reversals']):
        if rv['kind'] != 'TOP':
            continue
        cl = [c for c in out['climaxDays'] if rv['i'] - RECENT <= c['i'] <= rv['i']]
        if cl:
            out['topWarning'] = {'date': rv['date'], 'climaxDate': cl[-1]['date'],
                                 'gainPct': cl[-1]['gainPct'], 'runStart': cl[-1]['runStart'],
                                 'reversal': _strip(rv), 'daysAgo': n - 1 - rv['i']}
            break
    return out


# ------------------------------------------------------------------ 지수
def index_report(rows, symbol, label, days=260):
    """지수 1개: 현재 상태 + 지난 1년 신호 기록(5·20거래일 뒤 변화) + 차트용 캔들."""
    rows = [r for r in rows if r.get('close') and r.get('high') and r.get('low')]
    if len(rows) < 120:
        return {'symbol': symbol, 'label': label, 'status': 'UNAVAILABLE'}
    n = len(rows)
    events, last_climax, met_dates = [], -99, []
    for i in range(max(60, n - days), n):
        rv = reversal_at(rows, i, INDEX, need_uptrend=False)
        cl = climax_at(rows, i, INDEX)
        climax_recent = any((climax_at(rows, j, INDEX) or {}).get('met') for j in range(max(0, i - RECENT), i + 1)) if rv and rv['kind'] == 'TOP' else False
        if cl and cl['met'] and i - last_climax > RECENT:   # 새 클라이맥스 구간의 첫날만 기록
            events.append({'i': i, 'type': 'CLIMAX', 'date': rows[i]['date'], 'gainPct': cl['gainPct'],
                           'runStart': cl['runStart'], 'strong': cl['strong'], 'points': cl['points']})
        if cl and cl['met']:
            last_climax = i
            met_dates.append(rows[i]['date'])
        if rv:
            kind = 'TOP_WARNING' if rv['kind'] == 'TOP' and climax_recent else rv['kind']
            events.append({'i': i, 'type': kind, **{k: v for k, v in rv.items() if k != 'kind'}})
    for e in events:
        i = e.pop('i')
        e['close'] = _r(rows[i]['close'], 2)
        e['after'] = {str(h): _r((rows[i + h]['close'] / rows[i]['close'] - 1) * 100) for h in (5, 20) if i + h < n}
        e['worst20'] = _r((min(r['low'] for r in rows[i + 1:i + 21]) / rows[i]['close'] - 1) * 100) if i + 1 < n else None
    now = scan(rows, INDEX, need_uptrend=False)
    state = 'TOP_WARNING' if now['topWarning'] else 'CLIMAX' if now['climax'] else \
        ('TOP' if any(r['kind'] == 'TOP' for r in now['reversals'][-SHOW_REVERSAL:]) else
         'BOTTOM' if any(r['kind'] == 'BOTTOM' for r in now['reversals'][-SHOW_REVERSAL:]) else 'NONE')
    chart = rows[-130:]
    return {'symbol': symbol, 'label': label, 'status': 'OK', 'asOf': rows[-1]['date'], 'state': state,
            'climax': _strip(now['climax']), 'topWarning': now['topWarning'],
            'reversals': [_strip(r) for r in now['reversals'][-SHOW_REVERSAL:]],
            'events': events[::-1], 'climaxDates': [d for d in met_dates if d >= rows[-130]['date']],
            'candles': [{'d': r['date'], **{k[0]: _r(r.get(k), 2) for k in ('open', 'high', 'low', 'close')}} for r in chart],
            'ma': {str(n_): [_r(_sma([x['close'] for x in rows], n_, k + 1), 2) for k in range(n - len(chart), n)] for n_ in (20, 50)}}


def _strip(d):
    return None if d is None else {k: v for k, v in d.items() if k != 'i'}


# ------------------------------------------------------------------ 종목
RANK = {'TOP_WARNING': 0, 'CLIMAX': 1, 'TOP': 2, 'BOTTOM': 3}


def stock_items(universe, recent_reversal=SHOW_REVERSAL):
    items = []
    for s in universe:
        rows = s['rows']
        if len(rows) < 120:
            continue
        sc = scan(rows, STOCK, need_uptrend=True)
        n = len(rows)
        revs = [r for r in sc['reversals'] if r['i'] >= n - recent_reversal]
        if not (sc['climax'] or revs):
            continue
        top = [r for r in revs if r['kind'] == 'TOP']
        bottom = [r for r in revs if r['kind'] == 'BOTTOM']
        kinds = []
        if sc['topWarning']:
            kinds.append('TOP_WARNING')
        if sc['climax']:
            kinds.append('CLIMAX')
        if top:
            kinds.append('TOP')
        if bottom and not top:
            kinds.append('BOTTOM')
        if not kinds:
            continue
        lead = min(kinds, key=RANK.get)
        rv = (top or bottom)[-1] if (top or bottom) else None
        tw = sc['topWarning']
        if tw:                                # 천장 경고를 만든 천장형 반전을 보여 줌(5거래일보다 앞이어도)
            rv = {**tw['reversal'], 'i': n - 1 - tw['daysAgo']}
            tw = {k: v for k, v in tw.items() if k != 'reversal'}
        strength = (len(sc['climax']['points']) if sc['climax'] else 0) + (len(rv['points']) if rv else 0)
        closes = s['closes']
        items.append({
            'id': s['id'], 'symbol': s['symbol'], 'name': s['name'], 'industry': s['industry'],
            'marketCap': s.get('marketCap'), 'rs': s.get('rsPercentile'),
            'close': _r(closes[-1], 4), 'changePct': _r((closes[-1] / closes[-2] - 1) * 100),
            'kind': lead, 'kinds': kinds, 'strength': strength,
            'climax': _strip(sc['climax']), 'topWarning': tw, 'reversal': _strip(rv),
            'daysAgo': (n - 1 - rv['i']) if rv else None,
            'recentCandles': [_candle(r) for r in rows[-12:]],
        })
    items.sort(key=lambda x: (RANK[x['kind']], -(x['strength']), -(x['marketCap'] or 0)))
    return items


def build(universe, index_rows):
    """index_rows: [(symbol, label, rows)]. 결과는 wamo_trend.json의 'climax' 칸."""
    items = stock_items(universe)
    counts = {k: sum(k in x['kinds'] for x in items) for k in RANK}
    return {
        'status': 'OK',
        'indexes': [index_report(rows, sym, label) for sym, label, rows in index_rows if rows],
        'counts': counts, 'count': len(items), 'items': items[:120],
        'params': {'stock': STOCK, 'index': INDEX, 'topLookback': TOP_LOOKBACK,
                   'bottomLookback': BOTTOM_LOOKBACK, 'recent': RECENT},
        'note': '오닐·미너비니의 매도 규칙을 와모 기본값으로 옮긴 확인용 신호예요. 강한 주도주는 신호 뒤에도 더 오를 수 있으니 '
                '한 번에 다 팔기보다 일부 차익 실현이나 손절선 올리기로 대응하는 경우가 많아요.',
    }
