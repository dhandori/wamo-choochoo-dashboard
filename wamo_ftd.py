"""Conservative, causal FTD screening. This is not IBD's discretionary rating.

The correction gate is a WAMO policy: below SMA50 AND either 8% below
63-session closing high, or 5% below that high with SMA50 below 10 sessions
ago. All gates use information available on that session only.
"""
from datetime import date
import math

POLICY = {
    'version': 1, 'gainPct': 1.7, 'minRallyDay': 4, 'maxRallyDay': 7, 'peakWindow': 63,
    'deepDrawdownPct': 8, 'fallingMaDrawdownPct': 5, 'maDays': 50, 'slopeDays': 10,
    'correction': '종가가 50일선 아래이고, 최근 63거래일 최고 종가 대비 8% 이상 하락. 또는 5% 이상 하락하며 50일선이 10거래일 전보다 하락.',
    'confirmation': '하락 국면 이후 첫 상승 마감일을 Day 1로 계산. 그날 장중 저가를 지킨 상태에서 Day 4~7에 +1.7% 이상 상승하고 전일보다 거래량 증가.',
    'failure': '반등 Day 1 장중 저가 이탈은 반등·FTD 무효. FTD 당일 저가보다 낮은 종가는 경고로 구분.',
    'note': 'WAMO 고정 규칙에 따른 참고 신호입니다. IBD 공식 판정이 아니며 한국 시장에도 같은 규칙을 적용합니다. 상승 지속을 보장하지 않습니다.',
    'sources': [
        {'title': 'IBD · 반등 시도와 Day 4 이후 FTD', 'url': 'https://www.investors.com/how-to-invest/how-to-invest-in-stocks-in-market-correction-follow-through-day/'},
        {'title': 'Morales·Kacher · 1.5%·거래량 기준과 한계', 'url': 'https://www.virtueofselfishinvesting.com/reports/view/mlr-time-to-buy-qe3-and-its-effect-on-the-general-market-and-gold/amp'},
    ],
}


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
    events=[]; cycle=None; rally=None; ftd=None; phase='OUTSIDE'; prev_gate=False

    def event(kind, row, **extra):
        events.append(dict(type=kind, date=row['date'], close=row['close'], **extra))

    for i,r in enumerate(rows):
        if i<62:
            continue
        peak=max(closes[i-62:i+1]); ma=sum(closes[i-49:i+1])/50
        oldma=sum(closes[i-59:i-9])/50
        dd=(1-r['close']/peak)*100
        gate=r['close']<ma and (dd>=8-1e-9 or (dd>=5-1e-9 and ma<oldma))
        change=(r['close']/closes[i-1]-1)*100
        ratio=r['volume']/rows[i-1]['volume']
        start_new = cycle is None and gate
        # After a confirmed rally has left the gate, a fresh correction starts
        # a new search even if its low remains above the much older Day 1 low.
        if ftd and gate and not prev_gate:
            if r['low']<rally['low']:
                event('FTD_FAILED',r,rallyDate=rally['date'],rallyLow=rally['low'],ftdDate=ftd['date'])
            event('NEW_CORRECTION',r,previousFtdDate=ftd['date'])
            start_new=True
        if start_new:
            cycle=dict(date=r['date'], peak=peak, drawdownPct=round(dd,2), ma50=round(ma,2))
            rally=ftd=None; phase='CORRECTION'; event('CORRECTION',r,**{'drawdownPct':round(dd,2)})
        elif cycle is not None:
            if rally and r['low']<rally['low']:
                event('FTD_FAILED' if ftd else 'RALLY_FAILED',r,
                      rallyDate=rally['date'], rallyLow=rally['low'], ftdDate=ftd['date'] if ftd else None)
                rally=ftd=None; phase='CORRECTION'
            if rally is None and change>0:
                rally=dict(date=r['date'], low=r['low'], startIndex=i, day=1)
                phase='RALLY';event('RALLY',r,low=r['low'])
            elif rally:
                rally['day']=i-rally['startIndex']+1
                if ftd is None and 4<=rally['day']<=7 and change>=1.7-1e-9 and ratio>1:
                    ftd=dict(date=r['date'], low=r['low'], close=r['close'], changePct=round(change,4),
                             volumeRatio=round(ratio,4), rallyDay=rally['day'])
                    phase='CONFIRMED';event('FTD',r,**{k:v for k,v in ftd.items() if k not in ('date','close')})
                elif ftd is None and rally['day']>7:
                    phase='EXPIRED'
                    if rally['day']==8:
                        event('WINDOW_EXPIRED',r,rallyDate=rally['date'])
                elif ftd:
                    phase='PRESSURE' if r['close']<ftd['low'] else 'CONFIRMED'
            # The index can recover without a qualifying FTD. Do not carry an
            # unconfirmed attempt into a new high and flag a later bull-market day.
            if ftd is None and r['close']>=cycle['peak']:
                event('RECOVERED',r); cycle=rally=None; phase='OUTSIDE'
        prev_gate=gate
        result['series'].append(dict(date=r['date'],close=r['close'],low=r['low'],volume=r['volume'],
                                     ma50=round(ma,4),phase=phase))
        result['latest']=dict(close=r['close'],changePct=round(change,4),volumeRatio=round(ratio,4),
                              drawdownPct=round(dd,2),ma50=round(ma,2),belowMa50=r['close']<ma,
                              ma50Falling=ma<oldma,correctionGate=gate)
    result.update(phase=phase,correction=cycle,rally={k:v for k,v in rally.items() if k!='startIndex'} if rally else None,
                  ftd=ftd,events=events[-60:],series=result['series'][-140:])
    if ftd:
        result['ftdAge']=sum(r['date']>ftd['date'] for r in rows)
    if expected and asof!=expected:
        result.update(status='STALE',note=f'{asof}까지의 과거 판정입니다. 최근 마감 거래일 {expected} 자료를 기다립니다.')
    return result


def for_market(rows, market, asof, *, synthetic=False, expected=None):
    """Validate actual exchange sessions before counting Day 1, 2, 3, 4."""
    sessions=None
    if rows and not synthetic:
        try:
            sessions=market_sessions(market, rows[0]['date'], asof)
        except (ValueError, KeyError, TypeError):
            return {'status':'UNAVAILABLE','phase':'UNAVAILABLE','asOf':asof,'expectedSession':expected,
                    'policy':POLICY,'note':'거래일 달력 범위를 확인할 수 없습니다.','events':[],'series':[]}
    return analyze(rows,asof=asof,expected=expected,synthetic=synthetic,sessions=sessions)


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
