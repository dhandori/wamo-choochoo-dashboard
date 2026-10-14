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
from pathlib import Path
from statistics import median
from urllib.parse import quote, urlparse
from wamo_runtime import expected_session, next_slot


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


def project(stock,market,asof):
    if not isinstance(stock,dict): return None
    symbol=str(stock.get('stock_code') or stock.get('ticker') or '').strip()
    if market=='US': symbol=str(stock.get('ticker') or symbol).strip().upper()
    if not (re.fullmatch(r'[A-Z0-9]{6}',symbol) if market=='KR' else re.fullmatch(r'[A-Z0-9][A-Z0-9.^=-]{0,19}',symbol)): return None
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
    reasons=[]
    change=ret(1)
    if change is not None and change>=3: reasons.append('UP')
    if change is not None and change<=-3: reasons.append('DOWN')
    if volume is not None and volume>=1.5: reasons.append('VOLUME')
    def breakout(key):
        if len(rows)<252:return None
        prior=max(r[key] for r in rows[-252:-1]); latest=rows[-1][key]
        return latest>prior+max(abs(prior)*1e-8,1e-8)
    ticker=stock.get('ticker') or symbol
    return {'id':f'{market}:{symbol}','symbol':symbol,'ticker':ticker,'name':stock.get('name') or symbol,'market':market,
            'industry':name.strip(),'industrySource':'sector (broad)' if classification_source=='sector' else classification_source,
            'asOf':asof,'close':close,'changePct':change,'return5d':ret(5),'return20d':ret(20),'high52':high,
            'highDistancePct':round(max(0,(1-close/high)*100),6) if high else None,
            'intradayHigh52':breakout('high'),'closeHigh52':breakout('close'),'volumeRatio':volume,
            'rs':number(stock.get('rsPercentile')),'stage2':stock.get('stage2Core'),
            'source':stock.get('dataSource') or stock.get('priceProvider') or 'verified local history',
            'detailUrl':('index.html' if market=='KR' else 'us.html')+'?stock='+quote(str(ticker))+'&legacy=1',
            'chartUrl':'https://finance.yahoo.com/quote/'+quote(str(ticker))+'/',
            'sparkline':[r['close'] for r in rows[-60:]],'catalysts':catalysts(stock.get('catalysts')),'moverReasons':reasons}


def industries(stocks,market):
    groups=defaultdict(list)
    for stock in stocks: groups[stock['industry']].append(stock)
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
            'medianReturn5d':med('return5d'),'medianChangePct':med('changePct'),'volumeConfirmedCount':sum(s['volumeRatio'] is not None and s['volumeRatio']>=1.5 for s in members),
            'advancing5dCount':advancing5d,'advancing5dPct':round(advancing5d/count*100,2),'confirmed':confirmed,'leaders':[s['id'] for s in sorted(members,key=lambda s:s['return5d'] if s['return5d'] is not None else -math.inf,reverse=True)[:3]],'members':[s['id'] for s in members]})
    return result


def build_insights(payloads,now,previous=None):
    if isinstance(now,str):now=datetime.fromisoformat(now.replace('Z','+00:00'))
    if now.tzinfo is None:now=now.replace(tzinfo=timezone.utc)
    result={'schemaVersion':1,'generatedAt':now.isoformat(),'markets':{}}
    for market in ('KR','US'):
        expected=expected_session(market,now);scheduled=next_slot(market,now).isoformat()
        payload=payloads.get(market) or {}
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
        previous_market=((previous or {}).get('markets') or {}).get(market)
        prior_count=len(previous_market.get('stocks') or []) if previous_market else 0
        coverage_collapse=bool(prior_count and len(stocks)<prior_count*0.9)
        if previous_market and previous_market.get('stocks') and (not stocks or coverage_collapse or (raw and len(stocks)/len(raw)<0.9) or (previous_market.get('asOf') or '')>(asof or '')):
            m=copy.deepcopy(previous_market);m.update(expectedSession=expected,nextScheduledFor=scheduled)
            m['status']='STALE' if m.get('asOf')!=expected else 'PARTIAL'
            reason='대체 가격의 종목 수가 기존 검증 유니버스의 90%보다 적어 기존 가격을 유지합니다.' if coverage_collapse else '대체 가격의 유효 범위가 부족하거나 더 오래되어 기존 검증 가격을 유지합니다.'
            m['warnings']=list(dict.fromkeys(list(m.get('warnings') or [])+[reason]))
        else:
            excluded=len(raw)-len(stocks); warnings=['스크리닝 유니버스 기준이며 전체 시장의 종목 수나 확산 지표가 아닙니다.']
            recovery=meta.get('universeRecovery')
            if recovery:warnings.append('유니버스 복구 정보: '+(json.dumps(recovery,ensure_ascii=False) if isinstance(recovery,dict) else str(recovery)))
            if excluded:warnings.append(f'날짜 불일치·중복·가격 오류 {excluded}종목을 제외했습니다.')
            if any(s['industrySource']=='sector (broad)' for s in stocks):warnings.append('일부 그룹은 세부 산업 대신 넓은 섹터 분류를 사용합니다.')
            m={'asOf':asof if stocks else None,'expectedSession':expected,'status':('STALE' if asof!=expected else 'PARTIAL' if excluded else 'CURRENT') if stocks else 'UNAVAILABLE',
               'nextScheduledFor':scheduled,'source':meta.get('source') or 'verified local history',
               'coverage':{'total':len(raw),'valid':len(stocks),'excluded':excluded,'eligible52w':sum(s['high52'] is not None for s in stocks)},
               'stocks':stocks,'industries':industries(stocks,market),'movers':[s for s in stocks if s['moverReasons']],'warnings':warnings}
        result['markets'][market]=m
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
