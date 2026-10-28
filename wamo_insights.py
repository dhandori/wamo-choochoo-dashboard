"""Offline, deterministic market insight projection from verified daily histories."""
import argparse
import copy
import json
import math
import os
import re
import tempfile
from collections import defaultdict
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from statistics import median
from urllib.parse import quote, urlparse
from wamo_patterns import compact as compact_patterns, detect_patterns
from wamo_patterns import meta as pattern_meta
from wamo_runtime import calendar, expected_session, next_slot


def number(value):
    if isinstance(value, bool): return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError): return None


def day(value):
    try:
        value = str(value)
        if re.fullmatch(r'\d{8}', value): value = f'{value[:4]}-{value[4:6]}-{value[6:]}'
        return date.fromisoformat(value).isoformat()
    except (ValueError, TypeError): return None


def history(raw):
    if not isinstance(raw, list) or not raw: return None
    rows=[]; seen=set()
    for row in raw:
        if not isinstance(row, dict): return None
        d=day(row.get('date'))
        values={key:number(row.get(key)) for key in ('close','high','low','volume')}
        if not d or d in seen or any(v is None for v in values.values()): return None
        if min(values[k] for k in ('close','high','low'))<=0 or values['volume']<0: return None
        if not values['low']<=values['close']<=values['high']: return None
        if 'open' in row:
            opening=number(row['open'])
            if opening is None or not values['low']<=opening<=values['high']: return None
        rows.append({'date':d,**values}); seen.add(d)
    return sorted(rows,key=lambda r:r['date'])


def catalysts(raw):
    result=[]
    for item in raw if isinstance(raw,list) else []:
        if not isinstance(item,dict): continue
        url=item.get('url','')
        try:
            parsed=urlparse(url) if isinstance(url,str) else None
            hostname=parsed.hostname if parsed else None
        except (ValueError,TypeError):
            continue
        d=day(item.get('date')); title=item.get('title') or item.get('report')
        if (parsed and parsed.scheme=='https' and hostname in
            {'sec.gov','www.sec.gov','dart.fss.or.kr','kind.krx.co.kr'} and d and title):
            result.append({'date':d,'title':str(title),'url':url})
    return result


ASIA = ('JP', 'TW')
# Lite markets (no legacy detail page): local-currency market cap, symbol shape.
CAP_KEY = {'KR': 'market_cap_krw', 'US': 'market_cap_usd', 'JP': 'market_cap_local', 'TW': 'market_cap_local'}


def naver_url(market, symbol, exchange=None):
    """Conservative Reuters identifier mapping for verified Naver destinations."""
    if market == 'JP':
        return f'https://m.stock.naver.com/worldstock/stock/{symbol}.T/total' if re.fullmatch(r'[0-9][0-9A-Z]{3}', symbol) else None
    if market == 'TW':
        return None  # Naver does not carry Taiwan listings (checked 2026-10-20).
    if market == 'KR':
        return f'https://m.stock.naver.com/domestic/stock/{symbol}/total' if re.fullmatch(r'[A-Z0-9]{6}', symbol) else None
    venue = str(exchange or '').strip().upper()
    suffix = {'NASDAQ': '.O', 'NYSE': ''}.get(venue)
    if venue in {'AMEX', 'NYSEAMERICAN', 'NYSE AMERICAN'}:
        code = {'IMO': 'IMO', 'EQX': 'EQX', 'CBOE': 'CBOE.K'}.get(symbol)
        return f'https://m.stock.naver.com/worldstock/stock/{code}/total' if code else None
    if suffix is None:
        return None
    classes = {'BRK-A': 'BRKa', 'BRK.A': 'BRKa', 'BRK-B': 'BRKb', 'BRK.B': 'BRKb'}
    if symbol in classes:
        code = classes[symbol] if venue == 'NYSE' else None
    else:
        code = symbol + suffix if re.fullmatch(r'[A-Z][A-Z0-9]{0,19}', symbol) else None
    return f'https://m.stock.naver.com/worldstock/stock/{code}/total' if code else None


@lru_cache(maxsize=32)
def recent_sessions(market, asof):
    sessions = calendar(market).sessions
    if not asof or not calendar(market).is_session(asof):
        return ()
    end = sessions.get_loc(asof) + 1
    return tuple(d.date().isoformat() for d in sessions[max(0, end-55):end])


def trend_position(rows, market, asof):
    sessions = recent_sessions(market, asof)
    def above(offset):
        count = 50 + offset
        if len(rows) < count or len(sessions) < count:
            return None
        if tuple(r['date'] for r in rows[-count:]) != sessions[-count:]:
            return None
        window = rows[-count:-offset] if offset else rows[-50:]
        return window[-1]['close'] > sum(r['close'] for r in window) / 50
    return {'aboveMa50': above(0), 'aboveMa50Ago5': above(5)}


def comparison_snapshot(market, asof, previous=None, baseline=None):
    """Keep one actual prior session, including across same-session rebuilds.

    Never infer a transition from a missing symbol, missing value, or skipped day.
    The dated seed is a one-time migration from previously published snapshots.
    """
    sessions = recent_sessions(market, asof)
    expected = sessions[-2] if len(sessions) >= 2 else None
    result = {'expectedSession': expected, 'asOf': None, 'stocks': []}
    if not expected:
        return result
    previous = previous or {}
    candidates = [previous, previous.get('comparison') or {}, baseline or {}]
    for candidate in candidates:
        if candidate.get('asOf') != expected or not isinstance(candidate.get('stocks'), list):
            continue
        stocks = []; seen = set()
        for s in candidate['stocks']:
            if not isinstance(s, dict): continue
            ident = s.get('id')
            if not isinstance(ident, str) or not ident.startswith(market+':') or ident in seen: continue
            if s.get('asOf', expected) != expected: continue
            distance = number(s.get('highDistancePct')); rs = number(s.get('rs'))
            stocks.append({'id': ident, 'highDistancePct': distance if distance is not None and distance >= 0 else None,
                           'rs': rs if rs is not None and 0 <= rs <= 100 else None,
                           'stage2': s.get('stage2') if isinstance(s.get('stage2'), bool) else None})
            seen.add(ident)
        if stocks:
            result.update(asOf=expected, stocks=stocks)
            if candidate.get('sourceRevision'): result['sourceRevision'] = candidate['sourceRevision']
            break
    return result


@lru_cache(maxsize=1)
def industry_map_kr():
    # WAMO 산업 분류표(제품 단위). 없거나 깨져 있으면 빈 표: 기존 분류를 그대로 쓴다.
    try:
        raw=json.loads(Path(__file__).resolve().parent.joinpath('wamo_industry_map_KR.json').read_text(encoding='utf-8'))
        table=raw.get('map') if isinstance(raw,dict) else None
        return {k:v for k,v in table.items() if isinstance(v,dict) and isinstance(v.get('industry'),str) and v['industry'].strip()} if isinstance(table,dict) else {}
    except (OSError,ValueError,AttributeError):
        return {}


def investor_flow(stock):
    """KR 갱신기가 붙인 외국인·기관 추정 순매수(원)를 검증된 숫자만 통과시킨다."""
    fields={}
    for who,out in (('foreign','foreign'),('institution','inst')):
        for n in (5,20,60):
            value=number(stock.get(f'{who}_netbuy_{n}d'))
            if value is not None: fields[f'{out}Net{n}d']=round(value)
            pct=number(stock.get(f'{who}_netbuy_{n}d_pct_mcap'))
            if pct is not None: fields[f'{out}Pct{n}d']=round(pct,3)
    if not fields: return None
    asof=day(stock.get('flow_asof'))
    if asof: fields['asOf']=asof
    sessions=number(stock.get('flow_sessions'))
    if sessions and sessions>0: fields['sessions']=int(sessions)
    hold=number(stock.get('foreign_hold_pct'))
    if hold is not None and 0<=hold<=100: fields['foreignHoldPct']=round(hold,2)
    fields['estimate']=True
    return fields


def project(stock,market,asof):
    if not isinstance(stock,dict): return None
    symbol=str(stock.get('stock_code') or stock.get('ticker') or '').strip()
    if market=='US': symbol=str(stock.get('ticker') or symbol).strip().upper()
    pattern={'KR':r'[A-Z0-9]{6}','JP':r'[0-9][0-9A-Z]{3}','TW':r'[0-9]{4,6}[A-Z]?'}.get(market,r'[A-Z0-9][A-Z0-9.^=-]{0,19}')
    if not re.fullmatch(pattern,symbol): return None
    rows=history(stock.get('history'))
    if not rows or rows[-1]['date']!=asof or day(stock.get('date'))!=asof: return None
    if stock.get('isStale') or stock.get('dataStatus') in ('STALE','FAILED','ERROR'): return None
    provided_close=number(stock.get('close'))
    close=rows[-1]['close']
    if provided_close is not None and not math.isclose(close,provided_close,rel_tol=1e-5,abs_tol=1e-6): return None
    def ret(n): return round((close/rows[-n-1]['close']-1)*100,6) if len(rows)>n else None
    high=max(r['high'] for r in rows[-252:]) if len(rows)>=252 else None
    volume=None
    if len(rows)>=51:
        avg=sum(r['volume'] for r in rows[-51:-1])/50
        volume=round(rows[-1]['volume']/avg,6) if avg>0 else None
    # US display labels can be translated selectively; group by one source taxonomy.
    classification_keys=('industryEn','industry','sector') if market=='US' else ('industry','industryEn','sector')
    classification=next(((stock[k],k) for k in classification_keys if isinstance(stock.get(k),str) and stock[k].strip()),('Unclassified','unknown'))
    name,classification_source=classification
    industry_group=None;also=[]
    if market=='KR':
        mapped=industry_map_kr().get(symbol)
        if mapped:
            name,classification_source,industry_group=mapped['industry'],'WAMO 분류표',mapped.get('group')
            also=[a.strip() for a in mapped.get('also') or [] if isinstance(a,str) and a.strip() and a.strip()!=name.strip()]
    reasons=[]
    change=ret(1)
    if change is not None and change>=3: reasons.append('UP')
    if change is not None and change<=-3: reasons.append('DOWN')
    if volume is not None and volume>=1.5: reasons.append('VOLUME')
    def breakout(key,n=252):
        if len(rows)<n:return None
        prior=max(r[key] for r in rows[-n:-1]); latest=rows[-1][key]
        return latest>prior+max(abs(prior)*1e-8,1e-8)
    # 60일 신고가: 직전 59거래일의 최고가(장중) 또는 최고 종가를 넘긴 날. 52주 신고가보다 짧은 돌파.
    high60=max(r['high'] for r in rows[-60:]) if len(rows)>=60 else None
    ticker=stock.get('ticker') or symbol
    market_cap=number(stock.get(CAP_KEY.get(market,'market_cap_usd')))
    flow=investor_flow(stock) if market=='KR' else None
    patterns=compact_patterns(detect_patterns(rows,stock.get('vcp'),stock.get('stage2Checks'),stock.get('stage2Core')))
    return {'id':f'{market}:{symbol}','symbol':symbol,'ticker':ticker,'name':stock.get('name') or symbol,'market':market,
            **({'investorFlow':flow} if flow else {}),
            **({'patterns':patterns} if patterns else {}),
            'marketCap':market_cap if market_cap is not None and market_cap>0 else None,
            'industry':name.strip(),**({'alsoIndustries':also} if also else {}),'industrySource':'sector (broad)' if classification_source=='sector' else classification_source,'industryGroup':industry_group,
            'asOf':asof,'close':close,'changePct':change,'return5d':ret(5),'return20d':ret(20),'return60d':ret(60),'return120d':ret(120),'return200d':ret(200),'high52':high,
            'highDistancePct':round(max(0,(1-close/high)*100),6) if high else None,
            'intradayHigh52':breakout('high'),'closeHigh52':breakout('close'),
            'high60':high60,'high60DistancePct':round(max(0,(1-close/high60)*100),6) if high60 else None,
            'intradayHigh60':breakout('high',60),'closeHigh60':breakout('close',60),'volumeRatio':volume,
            'rs':number(stock.get('rsPercentile')),'stage2':stock.get('stage2Core'),
            **trend_position(rows,market,asof),
            'source':stock.get('dataSource') or stock.get('priceProvider') or 'verified local history',
            'exchange':stock.get('exchange'), 'naverUrl':naver_url(market,symbol,stock.get('exchange')),
            'detailUrl':None if market in ASIA else ('index.html' if market=='KR' else 'us.html')+'?stock='+quote(str(ticker))+'&legacy=1',
            'chartUrl':'https://finance.yahoo.com/quote/'+quote(str(ticker))+'/',
            'sparkline':[r['close'] for r in rows[-60:]],'catalysts':catalysts(stock.get('catalysts')),'moverReasons':reasons}


def pattern_summary(stocks):
    """셋업 집계. 단계별·셋업별 종목 수만 센다(한 종목이 여러 셋업에 들어갈 수 있다)."""
    stages=defaultdict(int);keys=defaultdict(int);breakouts=[]
    for stock in stocks:
        found=stock.get('patterns')
        if not found:continue
        stages[found['status']]+=1
        for setup in found['setups']:
            keys[setup['key']]+=1
            if setup['status']=='BREAKOUT':breakouts.append(stock['id'])
    return {'withSetup':sum(stages.values()),'byStage':dict(stages),'bySetup':dict(keys),
            'breakoutStocks':sorted(set(breakouts))}


def industries(stocks,market):
    groups=defaultdict(list)
    # 한 종목이 겹쳐 소속된 산업(alsoIndustries)에도 들어간다. 같은 산업에 두 번 들어가지는 않는다.
    for stock in stocks:
        for name in dict.fromkeys([stock['industry'],*(stock.get('alsoIndustries') or [])]): groups[name].append(stock)
    result=[]
    for name,members in sorted(groups.items()):
        count=len(members);near=sum(s['highDistancePct'] is not None and s['highDistancePct']<=10 for s in members)
        advancing=sum(s['changePct'] is not None and s['changePct']>0 for s in members)
        def med(key):
            nums=[s[key] for s in members if s[key] is not None]
            return median(nums) if nums else None
        advancing5d=sum(s['return5d'] is not None and s['return5d']>0 for s in members)
        volume_count=sum(s['volumeRatio'] is not None and s['volumeRatio']>=1.5 for s in members)
        confirmed=(count>=3 and advancing>=2 and advancing5d>=2 and advancing5d/count>=0.5 and med('return5d') is not None and med('return5d')>0 and (near>=2 or volume_count>=2) and name not in {'Unclassified','산업 미분류','KRX 업종 미분류'})
        result.append({'id':f'{market}:{name}','name':name,'memberCount':count,'nearHighCount':near,'nearHighPct':round(near/count*100,2),
            'newHighCount':sum(s['intradayHigh52'] is True for s in members),'advancingCount':advancing,'advancingPct':round(advancing/count*100,2),
            'medianReturn5d':med('return5d'),'medianReturn20d':med('return20d'),'medianChangePct':med('changePct'),'volumeConfirmedCount':sum(s['volumeRatio'] is not None and s['volumeRatio']>=1.5 for s in members),
            'advancing5dCount':advancing5d,'advancing5dPct':round(advancing5d/count*100,2),'confirmed':confirmed,'leaders':[s['id'] for s in sorted(members,key=lambda s:s['return5d'] if s['return5d'] is not None else -math.inf,reverse=True)[:3]],'members':[s['id'] for s in members]})
    return result


def build_market(market,payload,now,previous_market=None,baseline_market=None):
    """One market block of the radar snapshot (shared by KR/US pages and the Lite JP/TW engine)."""
    expected=expected_session(market,now);scheduled=next_slot(market,now).isoformat()
    if not isinstance(payload,dict):payload={}
    meta=payload.get('meta') or {}
    if not isinstance(meta,dict):meta={}
    asof=day(meta.get('asOf'))
    raw=payload.get('stocks') or []
    if not isinstance(raw,list):raw=[]
    stocks=[];seen=set()
    if asof and asof<=expected:
        for item in raw:
            s=project(item,market,asof)
            if s and s['id'] not in seen:stocks.append(s);seen.add(s['id'])
    prior_count=len(previous_market.get('stocks') or []) if previous_market else 0
    coverage_collapse=bool(prior_count and len(stocks)<prior_count*0.9)
    if previous_market and previous_market.get('stocks') and (not stocks or coverage_collapse or (raw and len(stocks)/len(raw)<0.9) or (previous_market.get('asOf') or '')>(asof or '')):
        m=copy.deepcopy(previous_market);m.update(expectedSession=expected,nextScheduledFor=scheduled)
        m['status']='STALE' if m.get('asOf')!=expected else 'PARTIAL'
        reason='대체 가격의 종목 수가 기존 검증 유니버스의 90%보다 적어 기존 가격을 유지합니다.' if coverage_collapse else '대체 가격의 유효 범위가 부족하거나 더 오래되어 기존 검증 가격을 유지합니다.'
        m['warnings']=list(dict.fromkeys(list(m.get('warnings') or [])+[reason]))
        return m
    excluded=len(raw)-len(stocks); warnings=['스크리닝 유니버스 기준이며 전체 시장의 종목 수나 확산 지표가 아닙니다.']
    recovery=meta.get('universeRecovery')
    if recovery:warnings.append('유니버스 복구 정보: '+(json.dumps(recovery,ensure_ascii=False) if isinstance(recovery,dict) else str(recovery)))
    if excluded:warnings.append(f'날짜 불일치·중복·가격 오류 {excluded}종목을 제외했습니다.')
    if any(s['industrySource']=='sector (broad)' for s in stocks):warnings.append('일부 그룹은 세부 산업 대신 넓은 섹터 분류를 사용합니다.')
    warnings.extend(w for w in (meta.get('warnings') or []) if isinstance(w,str))
    return {'asOf':asof if stocks else None,'expectedSession':expected,'status':('STALE' if asof!=expected else 'PARTIAL' if excluded else 'CURRENT') if stocks else 'UNAVAILABLE',
           'nextScheduledFor':scheduled,'source':meta.get('source') or 'verified local history',
           'coverage':{'total':len(raw),'valid':len(stocks),'excluded':excluded,'eligible52w':sum(s['high52'] is not None for s in stocks)},
           'patternSummary':pattern_summary(stocks),
           'stocks':stocks,'industries':industries(stocks,market),'movers':[s for s in stocks if s['moverReasons']],'warnings':warnings,
           'comparison':comparison_snapshot(market,asof if stocks else None,previous_market,baseline_market)}


def refresh_market_status(block,market,now):
    """Stored JP/TW blocks age between their own runs: re-derive freshness at publish time."""
    m=copy.deepcopy(block);expected=expected_session(market,now)
    m.update(expectedSession=expected,nextScheduledFor=next_slot(market,now).isoformat())
    if m.get('stocks') and m.get('asOf')!=expected:m['status']='STALE'
    return m


def build_insights(payloads,now,previous=None,baseline=None,markets=('KR','US')):
    if isinstance(now,str):now=datetime.fromisoformat(now.replace('Z','+00:00'))
    if now.tzinfo is None:now=now.replace(tzinfo=timezone.utc)
    result={'schemaVersion':1,'generatedAt':now.isoformat(),'patternMeta':pattern_meta(),'markets':{}}
    for market in markets:
        result['markets'][market]=build_market(market,payloads.get(market) or {},now,
            ((previous or {}).get('markets') or {}).get(market),((baseline or {}).get('markets') or {}).get(market))
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kr',default='index.html');parser.add_argument('--us',default='us.html');parser.add_argument('--output',default='wamo_insights.json');parser.add_argument('--now')
    args=parser.parse_args(argv)
    from wamo_update import extract_old_payload
    payloads={}
    for market,path in [('KR',args.kr),('US',args.us)]:
        try:payloads[market]=extract_old_payload(Path(path).read_text())
        except (OSError,ValueError):payloads[market]={}
    output=Path(args.output)
    try:previous=json.loads(output.read_text())
    except (OSError,ValueError):previous=None
    snapshot=build_insights(payloads,args.now or datetime.now(timezone.utc),previous)
    output.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=output.name+'.',dir=output.parent)
    try:
        with os.fdopen(fd,'w') as stream:json.dump(snapshot,stream,ensure_ascii=False,allow_nan=False,separators=(',',':'));stream.flush();os.fsync(stream.fileno())
        os.replace(tmp,output)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return snapshot

if __name__=='__main__':main()
