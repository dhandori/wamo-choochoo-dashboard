"""시장 거래대금(유동성) — 화면 속 종목들의 (종가 × 거래량) 합으로 돈의 흐름을 본다.

추세추종 관점의 여섯 가지 응용을 한 곳에서 계산한다.
 1. 시장 거래대금 추세: 오늘 거래대금을 20·50일 평균과 비교, 가격 방향과 묶어 4분면 판정
 2. 상승·하락 거래대금 비율: 오른 종목의 거래대금 합 ÷ 내린 종목의 거래대금 합 (오닐식 '돈이 드나드는 날')
 3. 돈이 몰리는 업종: 업종별 거래대금 비중(최근 5일)과 20일 전 비중의 변화
 4. 유동성 경보: 20일 평균 거래대금이 50일 평균보다 크게 낮으면 '돌파가 잘 안 먹히는 장'
 5. 셀링 클라이맥스: 급락 + 1년 중 최대 거래량 → 공포 매도가 한꺼번에 나온 바닥 후보
 6. FTD 거래량 품질은 wamo_ftd가 이미 계산(volumeVsAvg); 화면에서 드러낸다.

거래소 전체 공식 거래대금이 아니라 대시보드가 보는 종목의 합이다. 같은 기준으로 매일
비교하니 '늘었는지 줄었는지'를 보는 데는 충분하다.
"""
from __future__ import annotations

from datetime import date, timedelta

WINDOW = 60          # 그래프와 판정에 쓰는 거래일 수
AVG_SHORT, AVG_LONG = 20, 50
DRY_RATIO = 0.75     # 20일 평균 ÷ 50일 평균이 이보다 작으면 유동성 마름
FLOOD_RATIO = 1.25   # 이보다 크면 돈이 몰림
SURGE = 1.5          # 오늘 거래대금이 20일 평균의 몇 배면 '급증'
SELLING_CLIMAX_DROP = -3.0   # 지수(종목 중앙값) 하루 등락이 이보다 낮고
SELLING_CLIMAX_LOOKBACK = 250  # 그날 거래대금이 최근 1년 최대면 셀링 클라이맥스
TOP_INDUSTRIES = 8


def _r(v, d=1):
    return None if v is None else round(v, d)


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def daily_turnover(universe, sessions=WINDOW + SELLING_CLIMAX_LOOKBACK):
    """날짜별 {거래대금 합, 상승 종목 거래대금, 하락 종목 거래대금, 종목 수, 등락률 중앙값용 목록}."""
    by_date = {}
    for s in universe:
        rows = s['rows']
        n = len(rows)
        for i in range(max(1, n - sessions), n):
            r, p = rows[i], rows[i - 1]
            vol = r.get('volume')
            if not vol or vol <= 0 or not r.get('close') or not p.get('close'):
                continue
            value = r['close'] * vol
            slot = by_date.setdefault(r['date'], {'value': 0.0, 'up': 0.0, 'down': 0.0, 'n': 0, 'chg': [],
                                                  'industry': {}})
            slot['value'] += value
            slot['n'] += 1
            chg = r['close'] / p['close'] - 1
            slot['chg'].append(chg)
            if chg > 0:
                slot['up'] += value
            elif chg < 0:
                slot['down'] += value
            ind = s.get('industry') or '산업 미분류'
            slot['industry'][ind] = slot['industry'].get(ind, 0.0) + value
    if not by_date:
        return []
    top = max(v['n'] for v in by_date.values())
    # 종목 대부분이 거래한 날만 (임시 휴장·자료 누락일 제외)
    days = [dict(v, date=d) for d, v in sorted(by_date.items()) if v['n'] >= top * 0.8]
    for d in days:
        chg = sorted(d['chg'])
        m = len(chg)
        d['medianChg'] = (chg[(m - 1) // 2] + chg[m // 2]) / 2 * 100 if m else None
        del d['chg']
    return days


def _quadrant(price_dir, flow_dir):
    """가격 방향(up/down) × 거래대금(up/down)의 네 가지 뜻."""
    key = (price_dir, flow_dir)
    return {
        ('up', 'up'): ('HEALTHY', '건강한 상승', '최근 5일 동안 지수가 오르면서 거래량도 늘었어요. 돈이 들어오는 상승이라 돌파 매매에 좋은 환경이에요.', 1),
        ('up', 'down'): ('THIN', '힘 빠진 상승', '최근 5일 동안 지수는 올랐는데 거래량은 줄었어요. 돈이 따라오지 않는 상승이라 신고가 돌파가 잘 실패해요. 비중을 천천히 늘리세요.', 0),
        ('down', 'up'): ('DISTRIBUTION', '분산 (큰손 매도)', '최근 5일 동안 지수가 내리면서 거래량이 늘었어요. 큰손이 팔고 있다는 뜻이라 새로 사지 않고 지키는 게 먼저예요.', -1),
        ('down', 'down'): ('EXHAUSTION', '매도세 소진', '최근 5일 동안 지수가 내렸지만 거래량도 줄었어요. 팔 사람이 줄어드는 중이라 바닥을 다지는 후보 구간이에요. FTD를 기다리세요.', 0),
    }.get(key, ('FLAT', '뚜렷한 방향 없음', '지수와 거래량 모두 5일 동안 큰 변화가 없어요.', 0))


def analyze(universe, bench=None, asof=None):
    days = daily_turnover(universe)
    if len(days) < AVG_LONG + 5:
        return {'status': 'UNAVAILABLE', 'note': '거래대금을 계산할 날이 아직 부족해요.'}
    values = [d['value'] for d in days]
    n = len(values)
    avg20 = [_avg(values[max(0, i - AVG_SHORT + 1):i + 1]) if i >= AVG_SHORT - 1 else None for i in range(n)]
    avg50 = [_avg(values[max(0, i - AVG_LONG + 1):i + 1]) if i >= AVG_LONG - 1 else None for i in range(n)]
    last = days[-1]
    today, a20, a50 = values[-1], avg20[-1], avg50[-1]
    # ---- 1. 거래대금 추세 × 가격 방향 (최근 5거래일 기준: 하루 소음을 피한다)
    bench_by_date = {r['date']: r['close'] for r in (bench or [])}
    closes = [bench_by_date.get(d['date']) for d in days]
    if closes[-1] is None or closes[-6] is None:
        # 지수가 없으면 종목 중앙값 등락의 5일 누적으로 대신
        price_chg5 = sum((d['medianChg'] or 0) for d in days[-5:])
    else:
        price_chg5 = (closes[-1] / closes[-6] - 1) * 100
    flow5 = _avg(values[-5:])
    flow_prev = _avg(values[-10:-5])
    flow_chg = (flow5 / flow_prev - 1) * 100 if flow_prev else 0.0
    price_dir = 'up' if price_chg5 > 0.3 else 'down' if price_chg5 < -0.3 else 'flat'
    flow_dir = 'up' if flow_chg > 5 else 'down' if flow_chg < -5 else 'flat'
    if price_dir == 'flat' or flow_dir == 'flat':
        quad = _quadrant('x', 'x')
    else:
        quad = _quadrant(price_dir, flow_dir)
    # ---- 2. 상승·하락 거래대금 비율 (오늘과 최근 10일 중 '들어온 날' 수)
    ud_today = (last['up'] / last['down']) if last['down'] > 0 else None
    ud_series = [((d['up'] / d['down']) if d['down'] > 0 else None) for d in days[-WINDOW:]]
    inflow_days = sum(1 for x in ud_series[-10:] if x is not None and x > 1)
    ud10 = _avg([x for x in ud_series[-10:] if x is not None])
    # ---- 4. 유동성 경보
    liq_ratio = (a20 / a50) if a20 and a50 else None
    if liq_ratio is None:
        liq = ('UNKNOWN', '판단 보류', '평균을 계산할 날이 부족해요.', 0)
    elif liq_ratio < DRY_RATIO:
        liq = ('DRY', '유동성 마름', f'최근 20일 평균 거래대금이 50일 평균의 {liq_ratio * 100:.0f}%예요. 돈이 빠져나간 장에서는 돌파가 잘 안 먹히니 새로 사는 비중을 줄이세요.', -1)
    elif liq_ratio > FLOOD_RATIO:
        liq = ('FLOOD', '돈이 몰림', f'최근 20일 평균 거래대금이 50일 평균의 {liq_ratio * 100:.0f}%예요. 시장에 돈이 많이 들어와 있어요. 방향(위 판정)과 함께 보세요.', 1)
    else:
        liq = ('NORMAL', '보통', f'최근 20일 평균 거래대금이 50일 평균의 {liq_ratio * 100:.0f}%로 평소 수준이에요.', 0)
    # ---- 5. 셀링 클라이맥스: 급락 + 거래대금이 최근 1년 최대
    climax = None
    for k in range(max(1, n - 10), n):
        d = days[k]
        chg = (closes[k] / closes[k - 1] - 1) * 100 if closes[k] and closes[k - 1] else d['medianChg']
        if chg is None or chg > SELLING_CLIMAX_DROP:
            continue
        window = values[max(0, k - SELLING_CLIMAX_LOOKBACK):k]
        if window and values[k] > max(window):
            after = (closes[-1] / closes[k] - 1) * 100 if closes[-1] and closes[k] else None
            climax = {'date': d['date'], 'changePct': _r(chg), 'valueVsAvg50': _r(values[k] / avg50[k], 2) if avg50[k] else None,
                      'sinceThenPct': _r(after), 'daysAgo': n - 1 - k}
    # ---- 3. 돈이 몰리는 업종 (최근 5일 비중 vs 그 전 20일 비중)
    recent, prior = days[-5:], days[-25:-5]
    tot_r = sum(d['value'] for d in recent) or 1
    tot_p = sum(d['value'] for d in prior) or 1
    # 기간 전체 거래대금에서 업종이 차지한 비중(%)
    share_r, share_p = {}, {}
    for d in recent:
        for ind, v in d['industry'].items():
            share_r[ind] = share_r.get(ind, 0.0) + v / tot_r * 100
    for d in prior:
        for ind, v in d['industry'].items():
            share_p[ind] = share_p.get(ind, 0.0) + v / tot_p * 100
    # 업종별 가격(5일 수익률 중앙값)
    ind_ret = {}
    for s in universe:
        rows = s['rows']
        if len(rows) >= 6 and rows[-6].get('close'):
            ind_ret.setdefault(s.get('industry') or '산업 미분류', []).append((rows[-1]['close'] / rows[-6]['close'] - 1) * 100)
    industries = []
    for ind in set(share_r) | set(share_p):
        share = share_r.get(ind, 0.0)
        if ind == '산업 미분류':
            continue
        rets = sorted(ind_ret.get(ind, []))
        m = len(rets)
        med = (rets[(m - 1) // 2] + rets[m // 2]) / 2 if m else None
        industries.append({'name': ind, 'sharePct': _r(share, 2), 'shareChangePp': _r(share - share_p.get(ind, 0.0), 2),
                           'return5d': _r(med), 'members': m})
    inflow = sorted([x for x in industries if (x['shareChangePp'] or 0) > 0], key=lambda x: -(x['shareChangePp'] or 0))[:TOP_INDUSTRIES]
    outflow = sorted([x for x in industries if (x['shareChangePp'] or 0) < 0], key=lambda x: (x['shareChangePp'] or 0))[:TOP_INDUSTRIES]
    # 날씨 점수용 종합 (-1/0/1): 4분면 점수 + 유동성 점수를 합쳐 -1~1로 자른다
    raw = quad[3] + liq[3]
    score = 1 if raw >= 1 else -1 if raw <= -1 else 0
    return {
        'status': 'OK', 'asOf': last['date'], 'universe': last['n'],
        'today': {'value': today, 'vsAvg20': _r(today / a20, 2) if a20 else None, 'vsAvg50': _r(today / a50, 2) if a50 else None,
                  'surge': bool(a20 and today / a20 >= SURGE), 'upDownRatio': _r(ud_today, 2)},
        'trend': {'key': quad[0], 'label': quad[1], 'text': quad[2], 'score': quad[3],
                  'priceChange5dPct': _r(price_chg5), 'flowChange5dPct': _r(flow_chg)},
        'liquidity': {'key': liq[0], 'label': liq[1], 'text': liq[2], 'score': liq[3], 'ratio20to50': _r(liq_ratio, 2)},
        'upDown': {'today': _r(ud_today, 2), 'avg10': _r(ud10, 2), 'inflowDays10': inflow_days,
                   'note': '오른 종목들의 거래대금 ÷ 내린 종목들의 거래대금. 1보다 크면 돈이 들어온 날, 작으면 나간 날이에요.'},
        'sellingClimax': climax,
        'industries': {'inflow': inflow, 'outflow': outflow,
                       'note': '최근 5거래일 거래대금 비중이 그 전 20거래일보다 얼마나 늘었는지(%p). 비중이 늘면서 값도 오르는 업종이 돈이 몰리는 선도업종 후보예요.'},
        'series': [{'date': d['date'], 'value': d['value'], 'avg20': avg20[i], 'avg50': avg50[i],
                    'upDown': _r(ud_series[i - (n - WINDOW)], 2) if i >= n - WINDOW else None,
                    'up': closes[i] is not None and closes[i - 1] is not None and closes[i] >= closes[i - 1] if closes[i] is not None else (d['medianChg'] or 0) >= 0}
                   for i, d in enumerate(days) if i >= n - WINDOW],
        'score': score,
        'note': '거래대금 = 화면 속 종목들의 (종가 × 거래량) 합. 거래소 전체 공식 숫자와는 다르지만 같은 기준으로 매일 비교해요.',
    }


# ------------------------------------------------------------------ 지수 전체 거래량 (거래소 공식 숫자)
def _judge(price_chg5, flow_chg, liq_ratio):
    price_dir = 'up' if price_chg5 > 0.3 else 'down' if price_chg5 < -0.3 else 'flat'
    flow_dir = 'up' if flow_chg > 5 else 'down' if flow_chg < -5 else 'flat'
    quad = _quadrant('x', 'x') if 'flat' in (price_dir, flow_dir) else _quadrant(price_dir, flow_dir)
    if liq_ratio is None:
        liq = ('UNKNOWN', '판단 보류', '평균을 계산할 날이 부족해요.', 0)
    elif liq_ratio < DRY_RATIO:
        liq = ('DRY', '유동성 마름', f'최근 20일 평균 거래량이 50일 평균의 {liq_ratio * 100:.0f}%예요. 거래가 말라 돌파가 잘 안 먹히는 장이니 새로 사는 비중을 줄이세요.', -1)
    elif liq_ratio > FLOOD_RATIO:
        liq = ('FLOOD', '거래 몰림', f'최근 20일 평균 거래량이 50일 평균의 {liq_ratio * 100:.0f}%예요. 시장에 거래가 많이 몰려 있어요. 방향(위 판정)과 함께 보세요.', 1)
    else:
        liq = ('NORMAL', '보통', f'최근 20일 평균 거래량이 50일 평균의 {liq_ratio * 100:.0f}%로 평소 수준이에요.', 0)
    raw = quad[3] + liq[3]
    return quad, liq, (1 if raw >= 1 else -1 if raw <= -1 else 0)


def index_flow(rows, key, label, symbol):
    """한 지수의 공식 거래량(거래소 전체)으로 4분면·유동성·셀링 클라이맥스를 판정한다."""
    rows = [r for r in (rows or []) if r.get('volume') and r.get('close')]
    if len(rows) < AVG_LONG + 10:
        return None
    vols = [r['volume'] for r in rows]
    closes = [r['close'] for r in rows]
    n = len(rows)
    avg20 = [_avg(vols[max(0, i - AVG_SHORT + 1):i + 1]) if i >= AVG_SHORT - 1 else None for i in range(n)]
    avg50 = [_avg(vols[max(0, i - AVG_LONG + 1):i + 1]) if i >= AVG_LONG - 1 else None for i in range(n)]
    price_chg5 = (closes[-1] / closes[-6] - 1) * 100
    flow_chg = (_avg(vols[-5:]) / _avg(vols[-10:-5]) - 1) * 100
    liq_ratio = avg20[-1] / avg50[-1] if avg20[-1] and avg50[-1] else None
    quad, liq, score = _judge(price_chg5, flow_chg, liq_ratio)
    climax = None
    for k in range(max(1, n - 10), n):
        chg = (closes[k] / closes[k - 1] - 1) * 100
        window = vols[max(0, k - SELLING_CLIMAX_LOOKBACK):k]
        if chg <= SELLING_CLIMAX_DROP and len(window) >= 120 and vols[k] > max(window):
            climax = {'date': rows[k]['date'], 'changePct': _r(chg), 'valueVsAvg50': _r(vols[k] / avg50[k], 2) if avg50[k] else None,
                      'sinceThenPct': _r((closes[-1] / closes[k] - 1) * 100), 'daysAgo': n - 1 - k}
    a20, a50 = avg20[-1], avg50[-1]
    return {
        'key': key, 'label': label, 'symbol': symbol, 'asOf': rows[-1]['date'], 'source': 'index',
        'today': {'vsAvg20': _r(vols[-1] / a20, 2) if a20 else None, 'vsAvg50': _r(vols[-1] / a50, 2) if a50 else None,
                  'surge': bool(a20 and vols[-1] / a20 >= SURGE), 'changePct': _r((closes[-1] / closes[-2] - 1) * 100)},
        'trend': {'key': quad[0], 'label': quad[1], 'text': quad[2], 'score': quad[3],
                  'priceChange5dPct': _r(price_chg5), 'flowChange5dPct': _r(flow_chg)},
        'liquidity': {'key': liq[0], 'label': liq[1], 'text': liq[2], 'score': liq[3], 'ratio20to50': _r(liq_ratio, 2)},
        'sellingClimax': climax, 'score': score,
        # 차트는 50일 평균 대비 배수로 그린다 (지수마다 거래량 단위가 달라서)
        'series': [{'date': rows[i]['date'], 'x50': _r(vols[i] / avg50[i], 3) if avg50[i] else None,
                    'avg20x50': _r(avg20[i] / avg50[i], 3) if avg20[i] and avg50[i] else None,
                    'up': closes[i] >= closes[i - 1]} for i in range(max(1, n - WINDOW), n)],
    }


def split(universe, groups):
    """groups: [(key, label, symbol, members_filter, index_rows)].

    시장 판정은 각 지수의 공식 거래량(거래소 전체)으로 한다. 상승·하락 거래대금 비율은
    종목별 자료가 있어야 하므로 그 지수에 상장된 화면 속 종목으로 계산해 보조로 붙인다.
    """
    out = []
    for key, label, symbol, keep, rows in groups:
        g = index_flow(rows, key, label, symbol)
        if not g:
            continue
        members = [s for s in universe if keep(s)]
        stock = analyze(members) if len(members) >= 15 else {}
        g['members'] = len(members)
        g['upDown'] = stock.get('upDown') if stock.get('status') == 'OK' else None
        out.append(g)
    combined = analyze(universe)
    if not out:
        return {'status': 'UNAVAILABLE', 'note': '지수 거래량 자료가 아직 부족해요.'}
    return {
        'status': 'OK', 'asOf': out[0]['asOf'], 'score': min(g['score'] for g in out), 'groups': out,
        'industries': combined.get('industries') if combined.get('status') == 'OK' else None,
        'summary': ' · '.join(f"{g['label']} {g['trend']['label']}{'' if g['liquidity']['key'] == 'NORMAL' else ' · ' + g['liquidity']['label']}" for g in out),
        'note': '시장 판정은 지수 공식 거래량(거래소 전체)으로 해요. 상승·하락 비율과 업종 돈 흐름은 종목별 자료가 필요해서 화면 속 종목 기준이에요.',
    }
