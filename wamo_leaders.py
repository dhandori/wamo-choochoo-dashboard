"""Leading stocks and leading industries, built from the verified daily histories.

Everything here is causal (uses closes up to the shown session) and uses only
price/volume already embedded in index.html / us.html plus the index history
fetched for the trend module. No extra network calls.

Definitions are WAMO screening rules adapted from O'Neil/Minervini practice;
thresholds marked "제안 기본값" are WAMO defaults, not official IBD values.
"""
from __future__ import annotations

from statistics import median

from wamo_trend import (close_high_at, oneil_raw, percentile_map, rnd, rs_high_at, sma, stage_at)

POLICY = {
    'version': 1,
    'rsMin': 80, 'rsCore': 90, 'highDistanceMax': 15,
    'minCapUsd': 1e9, 'minTradingValueKrw': 5e9, 'tradingValueDays': 20,
    'breakoutVolume': 1.5, 'breakoutLookback': 10, 'rsLeadDistance': 3,
    'heldUpWindow': 63, 'heldUpMinIndexDrawdown': 5,
    'emergenceWindow': 10, 'emergenceMin': {'US': 10, 'KR': 5},
    'industryMinMembers': 3, 'industryTopPct': 80, 'industryNewHighsMin': 3,
    'industryAbove50Min': 60, 'industryPersistDays': 10, 'industryPersistMin': 5,
    'industryLookback': 20,
    # FTD 대기 워치리스트 (블로그 체크리스트 6문항을 숫자로 바꾼 와모 기본값)
    'watchRsMin': 60, 'watchRsRise': 10, 'watchNearHighMin': 2, 'watchAbove50Min': 50,
    'watchNearHighPctMin': 30, 'reclaimLookback': 10, 'reclaimVolume': 1.4, 'reclaimMin': 2,
    'growthPctMin': 40, 'growthKnownMin': 2, 'candidateRsMin': 70, 'watchScoreMin': 4,
    'heldUpGood': 0.8, 'leadsIndexMax': 5, 'leadsIndexBenchMin': 5,
}

WATCH_CHECKS = [
    ('rsRising', '상대강도가 높아지는 중', '업종 RS 80 이상이거나, 60 이상이면서 20거래일 전보다 10점 넘게 올랐어요.'),
    ('nearHigh', '신고가 근처 종목 있음', '52주 신고가 15% 이내 종목이 2개 이상이거나, 최근 10거래일 안에 신고가를 낸 종목이 있어요.'),
    ('together', '여러 종목이 함께 강함', '50일선 위 종목이 50% 이상이고, 신고가 15% 이내 종목이 30% 이상이에요.'),
    ('reclaim', '이평선 회복 + 거래량', '최근 10거래일 안에 50일선을 거래량 1.4배 이상으로 회복했거나 신고가를 거래량 돌파한 종목이 2개 이상이에요.'),
    ('growth', '실적이 뒷받침', '실적 자료가 있는 종목 중 40% 이상이 EPS 성장(CAN SLIM C 통과) 또는 매출 +20% 이상이에요.'),
    ('candidate', '매수 후보 종목 있음', 'RS 70 이상 · 신고가 15% 이내 · 50일선 위인 종목이 1개 이상 있어요. FTD가 나오면 바로 볼 종목이에요.'),
]

STAGE_TEXT = {
    'NEW': ('🆕', '신규 진입', 'RS 순위가 5거래일 전에는 상위 20% 밖이었다가 이번에 안으로 들어왔어요.'),
    'UP': ('⬆', '강화', '5거래일 전보다 RS 순위와 50일선 위 종목 비율이 모두 올라갔어요.'),
    'HOLD': ('➡', '유지', '상위 20% 안에서 큰 변화 없이 자리를 지키고 있어요.'),
    'DOWN': ('⬇', '약화', '순위는 아직 높지만 5거래일 전보다 순위가 10점 이상 떨어졌거나, 50일선 위 비율이 10%p 이상 줄었거나, 신고가 종목 수가 줄었어요.'),
    'OUT': ('↘', '상위권 이탈', '5거래일 전에는 상위 20% 안이었지만 지금은 밖으로 밀려났어요.'),
}


def _drawdown(closes):
    """Largest peak-to-trough close decline in the window, in %."""
    peak, worst = None, 0.0
    for c in closes:
        peak = c if peak is None or c > peak else peak
        worst = max(worst, (1 - c / peak) * 100)
    return worst


def _breakout(rows, closes, i):
    """Close above the prior 251-session closing high on >= 1.5x 50-day volume."""
    if i < 251 or i < 50:
        return False
    if not close_high_at(closes, i):
        return False
    vols = [r['volume'] for r in rows[i - 50:i]]
    avg = sum(vols) / 50 if vols else 0
    return avg > 0 and rows[i]['volume'] >= POLICY['breakoutVolume'] * avg


def _liquidity(s, market):
    if market == 'US':
        cap = s.get('marketCap')
        return (None if cap is None else cap >= POLICY['minCapUsd']), cap
    rows = s['rows'][-POLICY['tradingValueDays']:]
    values = [r['close'] * r['volume'] for r in rows if r.get('volume')]
    if len(values) < POLICY['tradingValueDays'] * 0.8:
        return None, None
    avg = sum(values) / len(values)
    return avg >= POLICY['minTradingValueKrw'], avg


def _ftd_anchor(ftd_indexes):
    """Earliest still-open FTD among the market's indexes (CONFIRMED or PRESSURE)."""
    live = [x for x in ftd_indexes or [] if x.get('status') == 'OK' and x.get('ftd')
            and x.get('phase') in ('CONFIRMED', 'PRESSURE')]
    if not live:
        return None
    first = min(live, key=lambda x: x['ftd']['date'])
    return {'date': first['ftd']['date'], 'index': first.get('label'), 'late': bool(first['ftd'].get('late')),
            'qualityScore': first['ftd'].get('qualityScore')}


def analyze(universe, bench, market, ftd_indexes=None):
    bench_by_date = {r['date']: r['close'] for r in bench}
    bench_closes = [r['close'] for r in bench]
    window = POLICY['heldUpWindow']
    index_dd = _drawdown(bench_closes[-window:]) if len(bench_closes) >= window else None

    # ---- stock RS percentiles now and for the industry lookback window
    lookback = POLICY['industryLookback']
    raw = {k: {} for k in range(lookback + 1)}
    for s in universe:
        n = len(s['closes'])
        for k in range(lookback + 1):
            raw[k][s['id']] = oneil_raw(s['closes'], n - k) if n - k > 127 else None
    rs_now = percentile_map(raw[0])

    # ---- per-stock leader screen
    stocks = []
    dates = [r['date'] for r in bench]
    for s in universe:
        rows, closes = s['rows'], s['closes']
        n = len(closes)
        i = n - 1
        high252 = max(r['high'] for r in rows[-252:]) if n >= 252 else None
        dist = (1 - closes[i] / high252) * 100 if high252 else None
        stage = stage_at(closes, i)
        rs = rs_now.get(s['id'])
        liquid, liq_value = _liquidity(s, market)
        ma50 = sma(closes, 50, n)
        breakout_dates = [rows[j]['date'] for j in range(max(0, i - POLICY['breakoutLookback'] + 1), i + 1)
                          if _breakout(rows, closes, j)]
        # RS line = close / index, as in the trend module.
        pts = [r['close'] / bench_by_date[r['date']] for r in rows if bench_by_date.get(r['date'])]
        rs_high_recent = None
        if len(pts) >= 200:
            rs_high_recent = next((b for b in range(5) if rs_high_at(pts, len(pts) - 1 - b)), None)
        rs_lead = rs_high_recent is not None and dist is not None and dist >= POLICY['rsLeadDistance']
        held = None
        if index_dd is not None and index_dd >= POLICY['heldUpMinIndexDrawdown'] and n >= window:
            held = rnd(_drawdown(closes[-window:]) / index_dd, 2)
        checks = {
            'rs': rs is not None and rs >= POLICY['rsMin'],
            'nearHigh': dist is not None and dist <= POLICY['highDistanceMax'],
            'stage2': stage == 2,
            'liquidity': liquid is not False,
        }
        s['_rsNow'] = rs
        s['_breakouts'] = breakout_dates
        s['_above50'] = None if ma50 is None else closes[i] > ma50
        s['_nearHigh'] = checks['nearHigh']
        s['_leader'] = all(checks.values())
        s['_dist'] = dist
        s['_reclaim'] = bool(breakout_dates) or _reclaim(rows, closes)
        s['_candidate'] = (rs is not None and rs >= POLICY['candidateRsMin'] and dist is not None
                           and dist <= POLICY['highDistanceMax'] and s['_above50'] is True)
        stocks.append({
            'id': s['id'], 'symbol': s['symbol'], 'name': s['name'], 'industry': s['industry'],
            'close': rnd(closes[i], 4), 'changePct': rnd((closes[i] / closes[i - 1] - 1) * 100) if n > 1 else None,
            'rs': rs, 'highDistancePct': rnd(dist), 'stage': stage, 'marketCap': s.get('marketCap'),
            'liquidityValue': rnd(liq_value, 0) if liq_value is not None else None, 'liquidityKnown': liquid is not None,
            'checks': checks, 'leader': s['_leader'], 'core': s['_leader'] and rs >= POLICY['rsCore'],
            'rsLineHigh': rs_high_recent is not None, 'rsHighDaysAgo': rs_high_recent, 'rsLead': rs_lead,
            'breakoutDates': breakout_dates, 'heldUpRatio': held,
            'heldUp': held is not None and held < 1,
            'growth': s.get('growth'),
        })
    leaders = [x for x in stocks if x['leader']]
    leaders.sort(key=lambda x: (not x['core'], not x['rsLead'], not x['breakoutDates'], -(x['rs'] or 0)))

    # ---- leader emergence after the FTD
    anchor = _ftd_anchor(ftd_indexes)
    need = POLICY['emergenceMin'].get(market, 10)
    win = POLICY['emergenceWindow']
    if anchor and anchor['date'] in dates:
        start = dates.index(anchor['date'])
        window_dates = set(dates[start:start + win + 1])
        age = len(dates) - 1 - start
        rs_ok = {x['id'] for x in stocks if (x['rs'] or 0) >= POLICY['rsMin']}
        hit_ids = {u['id'] for u in universe if u['id'] in rs_ok and any(
            u['rows'][j]['date'] in window_dates and _breakout(u['rows'], u['closes'], j)
            for j in range(len(u['rows'])))}
        hits = [x for x in stocks if x['id'] in hit_ids]
        count = len(hits)
        status = 'CONFIRMED' if count >= need else 'WATCHING' if age < win else 'WEAK'
        emergence = {'status': status, 'ftd': anchor, 'count': count, 'need': need, 'age': min(age, win), 'window': win,
                     'items': [{'id': x['id'], 'name': x['name'], 'symbol': x['symbol'], 'industry': x['industry'], 'rs': x['rs']}
                               for x in sorted(hits, key=lambda x: -(x['rs'] or 0))[:20]]}
    else:
        recent = [x for x in stocks if (x['rs'] or 0) >= POLICY['rsMin'] and x['breakoutDates']]
        emergence = {'status': 'NO_FTD', 'ftd': None, 'count': len(recent), 'need': need, 'age': None, 'window': win,
                     'items': [{'id': x['id'], 'name': x['name'], 'symbol': x['symbol'], 'industry': x['industry'], 'rs': x['rs']}
                               for x in sorted(recent, key=lambda x: -(x['rs'] or 0))[:20]]}

    industries = _industries(universe, bench, raw, index_dd)
    return {
        'policy': POLICY,
        'asOf': dates[-1] if dates else None,
        'universe': len(universe),
        'indexDrawdown63': rnd(index_dd),
        'leaders': {'count': len(leaders), 'coreCount': sum(x['core'] for x in leaders),
                    'rsLeadCount': sum(x['rsLead'] for x in leaders),
                    'breakoutCount': sum(bool(x['breakoutDates']) for x in leaders),
                    'items': leaders[:60]},
        'emergence': emergence,
        'phase': _phase(ftd_indexes),
        'watchChecks': [{'key': k, 'label': a, 'meaning': b} for k, a, b in WATCH_CHECKS],
        'industries': industries,
        'stageText': {k: {'icon': v[0], 'label': v[1], 'meaning': v[2]} for k, v in STAGE_TEXT.items()},
    }


def _reclaim(rows, closes):
    """Crossed back above the 50-day average on >= 1.4x 50-day volume within the lookback."""
    n = len(closes)
    for i in range(max(51, n - POLICY['reclaimLookback']), n):
        ma, ma_prev = sma(closes, 50, i + 1), sma(closes, 50, i)
        if ma is None or ma_prev is None or not (closes[i] > ma and closes[i - 1] <= ma_prev):
            continue
        vols = [r['volume'] for r in rows[i - 50:i]]
        avg = sum(vols) / 50 if vols else 0
        if avg > 0 and rows[i]['volume'] >= POLICY['reclaimVolume'] * avg:
            return True
    return False


def _phase(ftd_indexes):
    """Which job the screen should do now: verify leaders after an FTD, or prepare the watchlist before one."""
    phases = [x.get('phase') for x in ftd_indexes or [] if x.get('status') == 'OK']
    if any(p in ('CONFIRMED', 'PRESSURE') for p in phases):
        mode = 'FTD'
    elif any(p in ('CORRECTION', 'RALLY', 'LATE') for p in phases):
        mode = 'WAIT'
    elif phases:
        mode = 'UPTREND'
    else:
        mode = 'UNKNOWN'
    return {'mode': mode, 'indexes': [{'label': x.get('label'), 'phase': x.get('phase')}
                                      for x in ftd_indexes or [] if x.get('status') == 'OK']}


def _industries(universe, bench, raw, index_dd=None):
    lookback = POLICY['industryLookback']
    groups = {}
    for s in universe:
        if s['industry'] and s['industry'] != '미분류':
            groups.setdefault(s['industry'], []).append(s)
    groups = {k: v for k, v in groups.items() if len(v) >= POLICY['industryMinMembers']}
    if len(groups) < 3:
        return {'status': 'UNAVAILABLE', 'items': [], 'note': '비교할 수 있는 업종이 3개보다 적습니다.'}

    # Industry raw RS = median of member O'Neil raw scores (equal weight).
    pct_k, rank_k = {}, {}
    for k in range(lookback + 1):
        vals = {}
        for name, members in groups.items():
            got = [raw[k].get(m['id']) for m in members if raw[k].get(m['id']) is not None]
            vals[name] = median(got) if len(got) >= POLICY['industryMinMembers'] else None
        pct_k[k] = percentile_map(vals)
        ordered = sorted((v, n) for n, v in vals.items() if v is not None)
        rank_k[k] = {n: len(ordered) - idx for idx, (v, n) in enumerate(ordered)}

    bench_dates = [r['date'] for r in bench]
    bench_close = {r['date']: r['close'] for r in bench}
    top = POLICY['industryTopPct']
    items = []
    for name, members in groups.items():
        pct = pct_k[0].get(name)
        if pct is None:
            continue
        persist = sum(1 for k in range(POLICY['industryPersistDays']) if (pct_k[k].get(name) or 0) >= top)
        breadth_now = _breadth(members, 0)
        breadth_ago = _breadth(members, 5)
        rs_line_high, levels = _industry_rs_high(members, bench_dates, bench_close)
        pct5, pct20 = pct_k[5].get(name), pct_k[lookback].get(name)
        r0, r5, r20 = rank_k[0].get(name), rank_k[5].get(name), rank_k[lookback].get(name)
        conds = {
            'rsTop': pct >= top,
            'newHighs': breadth_now['newHighs'] >= POLICY['industryNewHighsMin'],
            'above50': breadth_now['above50Pct'] is not None and breadth_now['above50Pct'] >= POLICY['industryAbove50Min'],
            'persist': persist >= POLICY['industryPersistMin'],
        }
        stage = None
        if pct >= top:
            if pct5 is not None and pct5 < top:
                stage = 'NEW'
            elif ((pct5 is not None and pct5 - pct >= 10)
                  or (breadth_now['above50Pct'] is not None and breadth_ago['above50Pct'] is not None
                      and breadth_now['above50Pct'] - breadth_ago['above50Pct'] <= -10)
                  # A drop of one new high (1 -> 0) is noise; only a real fade counts.
                  or (breadth_ago['newHighs'] >= 2 and breadth_now['newHighs'] <= breadth_ago['newHighs'] / 2)):
                stage = 'DOWN'
            elif (pct5 is not None and pct > pct5 and breadth_now['above50Pct'] is not None
                  and breadth_ago['above50Pct'] is not None and breadth_now['above50Pct'] > breadth_ago['above50Pct']):
                stage = 'UP'
            else:
                stage = 'HOLD'
        elif pct5 is not None and pct5 >= top:
            stage = 'OUT'
        # Blog checklist: is this industry ready for the next FTD?
        near_count = sum(1 for m in members if m.get('_nearHigh'))
        known = [m['growth']['ok'] for m in members if isinstance(m.get('growth'), dict) and m['growth'].get('ok') is not None]
        growth_pct = rnd(sum(known) / len(known) * 100, 1) if known else None
        cands = [m for m in members if m.get('_candidate')]
        reclaim_count = sum(1 for m in members if m.get('_reclaim'))
        watch = {
            'rsRising': pct >= top or (pct >= POLICY['watchRsMin'] and pct20 is not None and pct - pct20 >= POLICY['watchRsRise']),
            'nearHigh': near_count >= POLICY['watchNearHighMin'] or breadth_now['newHighs'] >= 1,
            'together': (breadth_now['above50Pct'] or 0) >= POLICY['watchAbove50Min']
                        and (breadth_now['nearHighPct'] or 0) >= POLICY['watchNearHighPctMin'],
            'reclaim': reclaim_count >= POLICY['reclaimMin'],
            'growth': growth_pct is not None and len(known) >= POLICY['growthKnownMin'] and growth_pct >= POLICY['growthPctMin'],
            'candidate': len(cands) >= 1,
        }
        ind_dd = _drawdown(levels[-POLICY['heldUpWindow']:]) if len(levels) >= POLICY['heldUpWindow'] else None
        held = rnd(ind_dd / index_dd, 2) if ind_dd is not None and index_dd and index_dd >= POLICY['heldUpMinIndexDrawdown'] else None
        ind_dist = (1 - levels[-1] / max(levels[-252:])) * 100 if levels else None
        bench_tail = [bench_close[d] for d in bench_dates[-252:]]
        bench_dist = (1 - bench_tail[-1] / max(bench_tail)) * 100 if bench_tail else None
        leads_index = (ind_dist is not None and bench_dist is not None and ind_dist <= POLICY['leadsIndexMax']
                       and bench_dist >= POLICY['leadsIndexBenchMin'])
        cand_items = [{'id': m['id'], 'name': m['name'], 'symbol': m['symbol'], 'rs': m.get('_rsNow'),
                       'dist': rnd(m.get('_dist'), 1), 'leader': bool(m.get('_leader')),
                       'growth': (m.get('growth') or {}).get('text') if (m.get('growth') or {}).get('ok') else None}
                      for m in sorted(cands, key=lambda m: -(m.get('_rsNow') or 0))[:5]]
        reps = sorted(members, key=lambda m: (not m.get('_leader'), -(m.get('_rsNow') or 0)))[:3]
        items.append({
            'name': name, 'members': len(members), 'rs': pct, 'rank': r0,
            'rankChange5': (r5 - r0) if r5 and r0 else None, 'rankChange20': (r20 - r0) if r20 and r0 else None,
            'rs5': pct5, 'rs20': pct20, 'persist': persist, 'stage': stage,
            'leading': all(conds.values()), 'conditions': conds,
            'newHighs': breadth_now['newHighs'], 'newHighsAgo': breadth_ago['newHighs'],
            'above50Pct': breadth_now['above50Pct'], 'above50PctAgo': breadth_ago['above50Pct'],
            'nearHighPct': breadth_now['nearHighPct'], 'rsLineHigh': rs_line_high,
            'leaderCount': sum(1 for m in members if m.get('_leader')),
            'nearHighCount': near_count, 'reclaimCount': reclaim_count, 'growthPct': growth_pct, 'growthKnown': len(known),
            'watch': watch, 'watchScore': sum(watch.values()), 'candidates': cand_items, 'candidateCount': len(cands),
            'heldUpRatio': held, 'heldUp': held is not None and held < POLICY['heldUpGood'],
            'leadsIndex': leads_index, 'distFromHigh': rnd(ind_dist, 1),
            'representatives': [{'id': m['id'], 'name': m['name'], 'symbol': m['symbol'], 'rs': m.get('_rsNow'),
                                 'leader': bool(m.get('_leader'))} for m in reps],
        })
    items.sort(key=lambda x: (-x['rs'], x['name']))
    dropped = [x for x in items if x['stage'] == 'OUT']
    # Relative strength is the entry ticket: a weak-RS industry is not 'getting ready'.
    watchlist = [x for x in items if not x['leading'] and x['watch'].get('rsRising') and x['watchScore'] >= POLICY['watchScoreMin']]
    watchlist.sort(key=lambda x: (-x['watchScore'], -x['rs'], x['name']))
    point_map = [{'name': x['name'], 'rs': x['rs'], 'nearHighPct': x['nearHighPct'], 'members': x['members'],
                  'leading': x['leading'], 'watchScore': x['watchScore'], 'rankChange20': x['rankChange20']} for x in items]
    return {'status': 'OK', 'count': len(items), 'leadingCount': sum(x['leading'] for x in items),
            'items': items[:15], 'dropped': dropped[:8],
            'watchlist': watchlist[:10], 'watchCount': len(watchlist), 'map': point_map,
            'checklist': [{'name': x['name'], 'rs': x['rs'], 'watch': x['watch'], 'watchScore': x['watchScore'],
                           'leading': x['leading']}
                          for x in sorted(items, key=lambda x: (-x['watchScore'], -x['rs'], x['name']))[:15]],
            'leadsIndex': [x['name'] for x in items if x['leadsIndex']][:10],
            'heldUp': [{'name': x['name'], 'ratio': x['heldUpRatio']} for x in
                       sorted((x for x in items if x['heldUp']), key=lambda x: x['heldUpRatio'])][:10]}


def _breadth(members, back):
    above = total = near = near_total = 0
    new_highs = 0
    for m in members:
        closes, rows = m['closes'], m['rows']
        i = len(closes) - 1 - back
        if i < 50:
            continue
        ma = sma(closes, 50, i + 1)
        if ma is not None:
            total += 1
            above += closes[i] > ma
        if i >= 251:
            high = max(r['high'] for r in rows[i - 251:i + 1])
            near_total += 1
            near += (1 - closes[i] / high) * 100 <= POLICY['highDistanceMax']
            if any(close_high_at(closes, j) for j in range(i - 9, i + 1)):
                new_highs += 1
    return {'above50Pct': rnd(above / total * 100, 1) if total else None,
            'nearHighPct': rnd(near / near_total * 100, 1) if near_total else None,
            'newHighs': new_highs}


def _industry_rs_high(members, bench_dates, bench_close):
    """Equal-weight industry index ÷ market index at a 252-session high within 5 sessions.

    Also returns the equal-weight industry index levels (for drawdown / distance from high)."""
    by_date = [{r['date']: r['close'] for r in m['rows']} for m in members]
    level, pts = 100.0, []
    for a, b in zip(bench_dates, bench_dates[1:]):
        rets = [d[b] / d[a] - 1 for d in by_date if d.get(a) and d.get(b)]
        if len(rets) >= max(2, len(members) // 2):
            level *= 1 + sum(rets) / len(rets)
        pts.append(level / bench_close[b])
    levels = [p * bench_close[d] for p, d in zip(pts, bench_dates[1:])]
    if len(pts) < 200:
        return None, levels
    return any(rs_high_at(pts, len(pts) - 1 - k) for k in range(5)), levels
