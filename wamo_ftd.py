"""Conservative, causal FTD screening. This is not IBD's discretionary rating.

The correction gate is a WAMO policy: below SMA50 AND either 8% below
63-session closing high, or 5% below that high with SMA50 below 10 sessions
ago. All gates use information available on that session only.
"""
from datetime import date
import math

POLICY = {
    'version': 2, 'gainPct': 1.7, 'minRallyDay': 4, 'preferredMaxRallyDay': 7, 'peakWindow': 63,
    'deepDrawdownPct': 8, 'fallingMaDrawdownPct': 5, 'maDays': 50, 'slopeDays': 10,
    'distributionDropPct': 0.2, 'postFtdWindow': 10, 'postFtdDistributionWarn': 3,
    'strongGainPct': 2.0, 'volumeAvgDays': 50,
    'correction': '종가가 50일선 아래이고, 최근 63거래일 최고 종가 대비 8% 이상 하락. 또는 5% 이상 하락하며 50일선이 10거래일 전보다 하락.',
    'dayOne': '하락 국면 안에서 처음으로 상승 마감한 날. 또는 장중 새 저점을 찍었지만 그날 가격 범위의 위쪽 절반(중간보다 위)에서 마감한 날.',
    'bottom': '하락 국면 시작(또는 직전 무효) 이후 Day 1까지의 장중 최저가. 이 가격을 장중에 깨면 반등 시도 무효.',
    'confirmation': 'Day 4 이후 하루 +1.7% 이상 상승하고 거래량이 전일보다 많으면 FTD. Day 4~7이 보통이고 Day 8 이후는 \'늦은 FTD\'로 표시합니다. Day 2~3 급등은 인정하지 않습니다.',
    'failure': '기준 저점 장중 이탈은 반등·FTD 무효. FTD 당일 저가보다 낮은 종가, FTD 뒤 10거래일 안에 분산일(올랐지만 아래쪽에서 끝난 위장 분산일 포함) 3번 이상은 경고.',
    'completion': 'FTD 이후 종가가 하락 전 고점을 다시 넘으면 상승 전환 완료로 보고 추적을 끝냅니다. 그 FTD는 이력으로만 남깁니다.',
    'note': 'WAMO 고정 규칙에 따른 참고 신호입니다. IBD 공식 판정이 아니며 한국 시장에도 같은 규칙을 적용합니다. 상승 지속을 보장하지 않습니다.',
    'sources': [
        {'title': 'IBD · 반등 시도와 Day 4 이후 FTD', 'url': 'https://www.investors.com/how-to-invest/how-to-invest-in-stocks-in-market-correction-follow-through-day/'},
        {'title': 'Morales·Kacher · 1.5%·거래량 기준과 한계', 'url': 'https://www.virtueofselfishinvesting.com/reports/view/mlr-time-to-buy-qe3-and-its-effect-on-the-general-market-and-gold/amp'},
    ],
}


# Where the close lands in the day's range matters as much as the volume.
# pos = (close - low) / (high - low): 0 = closed at the low, 1 = closed at the high.
DIST_RULE = {'heavyPosMax': 1 / 3, 'lightPosMin': 0.5, 'hiddenPosMax': 1 / 3, 'hiddenRangeMinPct': 0.6,
             'weights': {'HEAVY': 1.5, 'NORMAL': 1.0, 'LIGHT': 0.5, 'HIDDEN': 1.0}}
DIST_KIND_TEXT = {
    'HEAVY': '꽉 찬 음봉 · 저가 근처 마감 (위험)',
    'NORMAL': '보통 분산일',
    'LIGHT': '긴 아랫꼬리 · 저가에서 회복해 위쪽 마감 (덜 위험)',
    'HIDDEN': '위장 분산일 · 올랐지만 아래쪽 마감',
}


def close_position(row):
    high, low, close = row.get('high'), row.get('low'), row.get('close')
    if not (valid_num(high) and valid_num(low) and valid_num(close)) or high <= low:
        return None
    return min(1.0, max(0.0, (close - low) / (high - low)))


def distribution_kind(prev, cur, drop_pct=0.2):
    """None, or HEAVY / NORMAL / LIGHT (classic: down >= 0.2% on higher volume) or HIDDEN.

    HIDDEN: an up close on higher volume whose close sank into the bottom third of a real
    (>= 0.6%) range - late selling pushed it down, a disguised distribution day.
    """
    if not (valid_num(prev.get('close')) and valid_num(cur.get('close'))
            and valid_num(prev.get('volume')) and valid_num(cur.get('volume'))):
        return None
    if cur['volume'] <= prev['volume']:
        return None
    change = (cur['close'] / prev['close'] - 1) * 100
    pos = close_position(cur)
    if change <= -drop_pct + 1e-9:
        if pos is None:
            return 'NORMAL'
        return 'HEAVY' if pos <= DIST_RULE['heavyPosMax'] else 'LIGHT' if pos >= DIST_RULE['lightPosMin'] else 'NORMAL'
    if change > 0 and pos is not None and pos <= DIST_RULE['hiddenPosMax'] \
            and (cur['high'] - cur['low']) / prev['close'] * 100 >= DIST_RULE['hiddenRangeMinPct']:
        return 'HIDDEN'
    return None


def valid_num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def analyze(raw, *, asof, expected=None, synthetic=False, sessions=None):
    result = dict(status='OK', phase='OUTSIDE', asOf=asof, expectedSession=expected or asof,
                  correction=None, rally=None, ftd=None, events=[], series=[], latest=None, policy=POLICY)

    def unavailable(note):
        return {**result, 'status': 'UNAVAILABLE', 'phase': 'UNAVAILABLE', 'note': note}

    if synthetic:
        return unavailable('실제 지수가 아닌 대체 지수로는 FTD를 판정하지 않습니다.')
    if not isinstance(raw, list) or len(raw) < 70:
        return unavailable('지수의 연속된 일별 고가·저가·종가·거래량이 70거래일 이상 필요합니다.')
    rows=[]; dates=set()
    for r in raw:
        if not isinstance(r, dict):
            return unavailable('지수 이력 형식을 확인할 수 없습니다.')
        d=r.get('date')
        try:
            if date.fromisoformat(d).isoformat()!=d:
                raise ValueError('date')
        except (ValueError, TypeError):
            return unavailable('지수 날짜 형식을 확인할 수 없습니다.')
        if d>asof:
            continue  # Intraday/future sessions never enter a close-based signal.
        if d in dates or (rows and d<rows[-1]['date']):
            return unavailable('지수 날짜가 중복되거나 순서가 맞지 않습니다.')
        if not all(valid_num(r.get(k)) for k in ('close','high','low','volume')):
            return unavailable('지수 저가 또는 거래량이 누락·0·비정상입니다. 추정값으로 판정하지 않습니다.')
        if not r['low']<=r['close']<=r['high']:
            return unavailable('지수 고가·저가·종가의 관계가 맞지 않습니다.')
        rows.append({k:r[k] for k in ('date','close','high','low','volume')});dates.add(d)
    if len(rows)<70 or rows[-1]['date']!=asof:
        return unavailable('가격 기준일까지 이어지는 지수 이력이 없습니다.')
    if sessions is not None and [r['date'] for r in rows]!=[d for d in sessions if rows[0]['date']<=d<=asof]:
        return unavailable('거래일 이력이 누락되어 반등 일수를 확정할 수 없습니다.')
    closes=[r['close'] for r in rows]
    vols=[r['volume'] for r in rows]
    events=[]; cycle=None; rally=None; ftd=None; last_ftd=None; phase='OUTSIDE'; prev_gate=False
    bottom=None  # lowest intraday low since the correction started or the last reset
    warnings=[]

    def event(kind, row, **extra):
        events.append(dict(type=kind, date=row['date'], close=row['close'], **extra))

    def upper_half(row):
        span=row['high']-row['low']
        return span>0 and (row['close']-row['low'])/span>0.5

    for i,r in enumerate(rows):
        if i<62:
            continue
        peak=max(closes[i-62:i+1]); ma=sum(closes[i-49:i+1])/50
        oldma=sum(closes[i-59:i-9])/50
        dd=(1-r['close']/peak)*100
        gate=r['close']<ma and (dd>=8-1e-9 or (dd>=5-1e-9 and ma<oldma))
        change=(r['close']/closes[i-1]-1)*100
        ratio=r['volume']/rows[i-1]['volume']
        warnings=[]
        start_new = cycle is None and gate
        # After a confirmed rally has left the gate, a fresh correction starts
        # a new search even if its low remains above the much older bottom.
        if ftd and gate and not prev_gate:
            if r['low']<rally['low']:
                event('FTD_FAILED',r,rallyDate=rally['date'],rallyLow=rally['low'],ftdDate=ftd['date'])
            event('NEW_CORRECTION',r,previousFtdDate=ftd['date'])
            start_new=True
        if start_new:
            cycle=dict(date=r['date'], peak=peak, drawdownPct=round(dd,2), ma50=round(ma,2))
            rally=ftd=None; bottom=r['low']; phase='CORRECTION'; event('CORRECTION',r,**{'drawdownPct':round(dd,2)})
        elif cycle is not None:
            new_low=bottom is None or r['low']<bottom
            if rally and r['low']<rally['low']:
                event('FTD_FAILED' if ftd else 'RALLY_FAILED',r,
                      rallyDate=rally['date'], rallyLow=rally['low'], ftdDate=ftd['date'] if ftd else None)
                rally=ftd=None; phase='CORRECTION'; bottom=r['low']
            if rally is None:
                bottom=r['low'] if bottom is None else min(bottom,r['low'])
                kind='UP_CLOSE' if change>0 else 'REVERSAL' if new_low and upper_half(r) else None
                if kind:
                    rally=dict(date=r['date'], low=bottom, dayOneLow=r['low'], startIndex=i, day=1, kind=kind)
                    phase='RALLY';event('RALLY',r,low=bottom,dayOneKind=kind)
            elif rally:
                rally['day']=i-rally['startIndex']+1
                if ftd is None and rally['day']>=4 and change>=POLICY['gainPct']-1e-9 and ratio>1:
                    prior=vols[max(0,i-POLICY['volumeAvgDays']):i]
                    avg=sum(prior)/len(prior) if len(prior)>=20 else None
                    late=rally['day']>POLICY['preferredMaxRallyDay']
                    quality=dict(early=not late, strongGain=change>=POLICY['strongGainPct']-1e-9,
                                 aboveAvgVolume=None if avg is None else r['volume']>avg)
                    ftd=dict(date=r['date'], low=r['low'], close=r['close'], changePct=round(change,4),
                             volumeRatio=round(ratio,4), rallyDay=rally['day'], late=late,
                             volumeVsAvg=None if avg is None else round(r['volume']/avg,4),
                             quality=quality, qualityScore=sum(1 for v in quality.values() if v),
                             startIndex=i, postDistribution=[])
                    phase='CONFIRMED';event('FTD',r,**{k:v for k,v in ftd.items() if k not in ('date','close','startIndex','postDistribution','quality')})
                elif ftd is None:
                    if rally['day']==POLICY['preferredMaxRallyDay']+1:
                        event('LATE_WINDOW',r,rallyDate=rally['date'])
                    phase='LATE' if rally['day']>POLICY['preferredMaxRallyDay'] else 'RALLY'
                elif ftd:
                    age=i-ftd['startIndex']
                    kind=distribution_kind(rows[i-1],r,POLICY['distributionDropPct']) if age<=POLICY['postFtdWindow'] else None
                    if kind:
                        ftd['postDistribution'].append(r['date'])
                        ftd.setdefault('postDistributionKind',{})[r['date']]=kind
                    if r['close']<ftd['low']:
                        warnings.append(dict(code='BELOW_FTD_LOW', level=ftd['low']))
                    if len(ftd['postDistribution'])>=POLICY['postFtdDistributionWarn']:
                        warnings.append(dict(code='DISTRIBUTION_CLUSTER', count=len(ftd['postDistribution']),
                                             dates=list(ftd['postDistribution'])))
                    phase='PRESSURE' if warnings else 'CONFIRMED'
            # The index can recover without a qualifying FTD. Do not carry an
            # unconfirmed attempt into a new high and flag a later bull-market day.
            if ftd is None and r['close']>=cycle['peak']:
                event('RECOVERED',r); cycle=rally=None; bottom=None; phase='OUTSIDE'
            # After an FTD, a close back above the pre-correction peak completes
            # the turn. Keep the FTD as history; never show it as "valid" for months.
            elif ftd and r['date']>ftd['date'] and r['close']>=cycle['peak']:
                last_ftd=dict({k:v for k,v in ftd.items() if k!='startIndex'}, rallyDate=rally['date'], recoveredDate=r['date'], peak=cycle['peak'])
                event('UPTREND',r,ftdDate=ftd['date'],peak=cycle['peak'])
                cycle=rally=ftd=None; bottom=None; warnings=[]; phase='OUTSIDE'
        prev_gate=gate
        result['series'].append(dict(date=r['date'],close=r['close'],low=r['low'],volume=r['volume'],
                                     ma50=round(ma,4),phase=phase))
        result['latest']=dict(close=r['close'],changePct=round(change,4),volumeRatio=round(ratio,4),
                              drawdownPct=round(dd,2),ma50=round(ma,2),belowMa50=r['close']<ma,
                              ma50Falling=ma<oldma,correctionGate=gate,
                              low=r['low'],high=r['high'],upperHalf=upper_half(r))
    result.update(phase=phase,correction=cycle,rally={k:v for k,v in rally.items() if k!='startIndex'} if rally else None,
                  ftd={k:v for k,v in ftd.items() if k!='startIndex'} if ftd else None,
                  warnings=warnings,events=events[-60:],series=result['series'][-140:])
    if cycle is not None and bottom is not None:
        result['bottom']=bottom
    if ftd:
        result['ftdAge']=sum(r['date']>ftd['date'] for r in rows)
    elif last_ftd and cycle is None:
        result['lastFtd']=dict(last_ftd, age=sum(r['date']>last_ftd['date'] for r in rows))
    if expected and asof!=expected:
        result.update(status='STALE',note=f'{asof}까지의 과거 판정입니다. 최근 마감 거래일 {expected} 자료를 기다립니다.')
    return result


def for_market(rows, market, asof, *, synthetic=False, expected=None, traded=None):
    """Validate actual exchange sessions before counting Day 1, 2, 3, 4.

    traded: dates on which most screened stocks actually traded. When given (Japan/Taiwan),
    a calendar session with no trading at all is an unscheduled closure (e.g. Taiwan shut
    on 2026-07-10 for Typhoon Bavi) and is not counted as a missing index day.
    """
    sessions=None
    if rows and not synthetic:
        try:
            sessions=market_sessions(market, rows[0]['date'], asof)
        except (ValueError, KeyError, TypeError):
            return {'status':'UNAVAILABLE','phase':'UNAVAILABLE','asOf':asof,'expectedSession':expected,
                    'policy':POLICY,'note':'거래일 달력 범위를 확인할 수 없습니다.','events':[],'series':[]}
    closed=[]
    if sessions is not None and traded is not None:
        first=min(traded) if traded else asof   # only judge days the stock histories cover
        closed=[d for d in sessions if d not in traded and first<=d<asof]
        sessions=[d for d in sessions if d not in closed]
    result=analyze(rows,asof=asof,expected=expected,synthetic=synthetic,sessions=sessions)
    if closed:
        result['unscheduledClosures']=closed
    return result


def merge_history(current, stored):
    """Retain verified index history across rolling provider request windows.

    Current overlapping observations replace old ones (provider corrections).
    Invalid/duplicate input is returned unchanged so validation cannot hide it.
    """
    if not current or not isinstance(current,list):
        return current
    dates=[r.get('date') for r in current if isinstance(r,dict)]
    if len(dates)!=len(current) or any(not isinstance(d,str) for d in dates) or dates!=sorted(set(dates)):
        return current
    if not stored:
        return current
    older={r['date']:r for r in stored if isinstance(r,dict) and isinstance(r.get('date'),str)}
    for r in current:
        saved=older.get(r['date'])
        historical=r['date']<current[-1]['date']
        valid_old=saved and all(valid_num(saved.get(k)) for k in ('close','high','low','volume'))
        valid_new=all(valid_num(r.get(k)) for k in ('close','high','low','volume'))
        if historical and valid_old and not valid_new:
            continue  # Previously verified history, never fill a bad latest quote.
        older[r['date']]=r
    return [older[d] for d in sorted(older)]


def fetch_naver_index(symbol):
    import json
    import urllib.request
    code={'^KS11':'KOSPI','^KQ11':'KOSDAQ'}[symbol]
    url=f'https://api.stock.naver.com/chart/domestic/index/{code}?periodType=dayCandle'
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=15) as response:
        return json.load(response)


def repair_korean_history(rows, symbol, payload):
    """Only fill invalid Yahoo rows after cross-source identity/scale checks.

    Do not synthesize volume or use ETF data. At least 20 overlapping valid
    sessions must agree in close (0.001%) and volume units (1%).
    """
    code={'^KS11':'KOSPI','^KQ11':'KOSDAQ'}.get(symbol)
    if (not code or not isinstance(payload,dict) or payload.get('code')!=code
            or payload.get('infoType')!='index' or payload.get('stockExchangeType')!='KRX'
            or payload.get('hasVolume') is not True):
        raise ValueError('보완 원천 지수 식별자 불일치')
    replacement={}
    for raw in payload.get('priceInfos') or []:
        d=raw.get('localDate','')
        if len(d)!=8 or not d.isdigit():
            raise ValueError('보완 원천 날짜 오류')
        d=f'{d[:4]}-{d[4:6]}-{d[6:]}'
        row=dict(date=d,close=raw.get('closePrice'),high=raw.get('highPrice'),low=raw.get('lowPrice'),
                 volume=raw.get('accumulatedTradingVolume'))
        if d in replacement or not all(valid_num(row[k]) for k in ('close','high','low','volume')) or not row['low']<=row['close']<=row['high']:
            raise ValueError('보완 원천 OHLCV 오류')
        replacement[d]=row
    overlap=0
    for r in rows:
        other=replacement.get(r.get('date'))
        if other and all(valid_num(r.get(k)) for k in ('close','high','low','volume')):
            if abs(r['close']/other['close']-1)>0.00001 or abs(r['volume']/other['volume']-1)>0.01:
                raise ValueError('원천 간 종가 또는 거래량 단위 불일치')
            overlap+=1
    if overlap<20:
        raise ValueError('교차검증 가능한 관측 부족')
    repaired=[];out=[]
    for r in rows:
        invalid=not all(valid_num(r.get(k)) for k in ('close','high','low','volume'))
        if invalid and r.get('date') in replacement:
            out.append(replacement[r['date']]);repaired.append(r['date'])
        else:
            out.append(r)
    return out,repaired


# KRX official notices, checked 2026-10-18. The installed calendar omits
# local election day and the reinstated Constitution Day for 2026.
# https://strn.krx.co.kr/corebbs5/BHPSTRN0401/list (notices 132, 133)
KRX_EXTRA_CLOSURES={'2026-06-03','2026-07-17'}


def market_sessions(market, start, end):
    from wamo_runtime import calendar
    return [s.date().isoformat() for s in calendar(market).sessions_in_range(start,end)
            if market!='KR' or s.date().isoformat() not in KRX_EXTRA_CLOSURES]
