#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, math, re, statistics, sys, time, urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'movers.html'
CACHE=ROOT/'wamo_movers_cache.json'
KST=timezone(timedelta(hours=9))
KR_MIN_CAP=1_000_000_000_000
US_MIN_CAP=2_000_000_000
SCREEN_META={}

import wamo_update_business_dart as kr
import wamo_update_us_sec as us


def num(v):
    if v is None: return None
    s=str(v).strip().replace(',','').replace('$','').replace('%','').replace('+','')
    if not s or s.lower() in {'n/a','na','none','-','--'}: return None
    try:
        x=float(s); return x if math.isfinite(x) else None
    except Exception: return None


def ma(vals,n):
    return sum(vals[-n:])/n if len(vals)>=n else None


def trend_info(hist):
    hist=sorted(hist,key=lambda x:x['date'])
    closes=[float(x['close']) for x in hist if x.get('close') is not None]
    if not closes: return {}
    c=closes[-1]
    m20,m60,m120,m200=(ma(closes,n) for n in (20,60,120,200))
    enough=m200 is not None
    aligned=bool(enough and c>m20>m60>m120>m200)
    high52=max(closes[-252:]) if closes else c
    vol=[float(x.get('volume') or 0) for x in hist]
    v20=statistics.mean(vol[-20:]) if len(vol)>=20 else None
    vr=(vol[-1]/v20) if v20 and vol else None
    return {
        'close':round(c,2),'ma20':round(m20,2) if m20 else None,'ma60':round(m60,2) if m60 else None,
        'ma120':round(m120,2) if m120 else None,'ma200':round(m200,2) if m200 else None,
        'aligned':aligned,'alignmentStatus':'YES' if aligned else ('NO' if enough else 'PENDING'),
        'high52Ratio':round(c/high52*100,1) if high52 else None,'volumeRatio20':round(vr,2) if vr else None,
        'historyDays':len(closes),
        'history':[{'date':x['date'],'close':round(float(x['close']),2)} for x in hist[-260:] if x.get('close') is not None]
    }


def load_payload(path):
    try:
        return kr.extract_old_payload(Path(path).read_text(encoding='utf-8'))
    except Exception: return {}


def profile_maps():
    kp=load_payload(ROOT/'index.html'); up=load_payload(ROOT/'us.html')
    km={str(x.get('stock_code') or str(x.get('ticker',''))[:6]):x for x in kp.get('stocks',[])}
    um={str(x.get('ticker','')).upper():x for x in up.get('stocks',[])}
    return km,um


def parse_naver_rise(sosok, market, limit=45):
    rows=[]
    for page in range(1,5):
        txt=kr.http_text(f'https://finance.naver.com/sise/sise_rise.naver?sosok={sosok}&page={page}',encoding='euc-kr')
        for tr in re.findall(r'<tr[^>]*>(.*?)</tr>',txt,flags=re.S|re.I):
            m=re.search(r'href="/item/main\.naver\?code=(\d{6})"[^>]*>(.*?)</a>',tr,flags=re.S|re.I)
            if not m: continue
            code=m.group(1); name=re.sub(r'<.*?>','',m.group(2)).strip()
            tds=[re.sub(r'\s+',' ',re.sub(r'<.*?>',' ',x).replace('&nbsp;',' ')).strip() for x in re.findall(r'<td[^>]*>(.*?)</td>',tr,flags=re.S|re.I)]
            # Locate change percentage defensively: first explicit % after stock name.
            pct=None; price=None
            for x in tds:
                if '%' in x:
                    v=num(x)
                    if v is not None: pct=v; break
            try:
                ni=next(i for i,x in enumerate(tds) if name.replace(' ','') in x.replace(' ',''))
                for x in tds[ni+1:ni+4]:
                    v=num(x)
                    if v is not None and v>0: price=v; break
            except Exception: pass
            if pct is None: continue
            rows.append({'ticker':code,'name':name,'market':market,'changePct':pct,'quotedPrice':price})
        if len(rows)>=limit: break
        time.sleep(.08)
    ded={x['ticker']:x for x in rows}
    return sorted(ded.values(),key=lambda x:x['changePct'],reverse=True)


def _daily_change(stock):
    value=num(stock.get('chg1d'))
    if value is not None: return value
    hist=stock.get('history') or []
    closes=[num(x.get('close')) for x in hist if isinstance(x,dict) and num(x.get('close')) is not None]
    return (closes[-1]/closes[-2]-1)*100 if len(closes)>=2 and closes[-2]>0 else None


def _kr_health(code, stock, corp_map):
    old=stock.get('financialHealth') or {}
    if old.get('status') in ('PASS','FAIL'): return old
    corp=corp_map.get(code)
    if not corp: return {'status':'UNAVAILABLE','source':'OpenDART','criterion':'DART 종목 연결 없음'}
    try:
        for year,report in kr.current_report_candidates():
            rows,_=kr.dart_statement(corp,year,report)
            if rows:
                return kr.financial_health_from_dart_rows(rows,stock.get('sector') or stock.get('detailSector') or '')
    except Exception as exc:
        return {'status':'UNAVAILABLE','source':'OpenDART','criterion':kr.compact_provider_error(str(exc))}
    return {'status':'UNAVAILABLE','source':'OpenDART','criterion':'비교 가능한 최신 재무제표 없음'}


def korea_top30(km):
    candidates=[]
    for code,old in km.items():
        cap=num(old.get('market_cap_krw')) or 0
        pct=_daily_change(old)
        if cap<KR_MIN_CAP or pct is None or kr.classify_instrument(old.get('name'))!='COMPANY': continue
        if len(old.get('history') or [])<20: continue
        candidates.append((pct,code,old))
    candidates.sort(reverse=True,key=lambda x:x[0])
    try: corp_map=kr.dart_corp_map()
    except Exception: corp_map={}
    out=[]; failed=0; unavailable=0
    for pct,code,old in candidates:
        if len(out)>=30: break
        health=_kr_health(code,old,corp_map)
        if health.get('status')!='PASS':
            unavailable += health.get('status')=='UNAVAILABLE'; failed += health.get('status')=='FAIL'; continue
        hist=old.get('history') or []; t=trend_info(hist)
        sector=old.get('sector') or old.get('detailSector') or '업종 확인 필요'
        prof=old.get('businessProfile') or {}; name=old.get('name') or code
        desc=prof.get('summary') or old.get('businessModelEasy') or f'{name}은(는) {sector}에 속하는 상장기업입니다.'
        out.append({'ticker':code,'name':name,'market':old.get('krx_market') or old.get('market') or 'KR',
                    'changePct':round(pct,3),'quotedPrice':old.get('close'),'marketCapKRW':old.get('market_cap_krw'),
                    **t,'sector':sector,'industry':old.get('krxSector') or sector,'description':desc,
                    'financialHealth':health,'source':'한국 중대형주 정밀계산 유니버스 + OpenDART 공식 재무'})
    SCREEN_META['korea']={'candidateCount':len(candidates),'financialFailCount':failed,
                          'financialUnavailableCount':unavailable,'minMarketCap':KR_MIN_CAP,
                          'universe':'시총 1조원 이상·50일 평균 거래대금 1억원 이상 일반기업'}
    return out


def us_rows_all():
    """Nasdaq 전체 스크리너에서 당일 상승률 후보를 만든다.

    Nasdaq의 download 응답은 시점에 따라 exchange 열을 생략할 수 있다.
    종전 코드는 그 경우 모든 행을 버려 TOP30이 0개가 되는 버그가 있었다.
    exchange는 표시용 정보일 뿐 랭킹 판정 필수값이 아니므로 누락을 허용한다.
    pctchange가 빠진 경우에는 lastsale/netchange로 상승률을 복원한다.
    """
    q=urllib.parse.urlencode({'tableonly':'true','limit':'5000','offset':'0','download':'true'})
    payload=us._get_json(
        'https://api.nasdaq.com/api/screener/stocks?'+q,
        headers={'Referer':'https://www.nasdaq.com/market-activity/stocks/screener'}
    )
    rows=us._nasdaq_screener_rows(payload)
    if len(rows)<1000:
        raise RuntimeError(f'Nasdaq screener rows too small: {len(rows)}')
    out=[]
    for r in rows:
        raw_symbol=str(r.get('symbol') or '').strip().upper()
        name=str(r.get('name') or r.get('companyName') or '').strip()
        if not raw_symbol or us._is_excluded_security(raw_symbol,name):
            continue

        price=num(r.get('lastsale') or r.get('lastSalePrice') or r.get('lastSale') or r.get('last'))
        net=num(r.get('netchange') or r.get('netChange') or r.get('change'))
        pct=num(r.get('pctchange') or r.get('percentchange') or r.get('percentChange') or r.get('changePercent'))
        if pct is None and price is not None and net is not None:
            prev=price-net
            if prev>0:
                pct=(net/prev)*100

        cap=num(r.get('marketCap') or r.get('marketcap'))
        if pct is None or cap is None or cap<=0:
            continue

        exchange=str(r.get('exchange') or r.get('market') or '').strip().upper()
        if 'NASDAQ' in exchange:
            exch='NASDAQ'
        elif 'AMEX' in exchange or 'NYSE AMERICAN' in exchange:
            exch='NYSE AMERICAN'
        elif 'NYSE' in exchange:
            exch='NYSE'
        else:
            # Nasdaq screener의 download=true 응답은 exchange 열을 생략하는 경우가 있다.
            # 미국 전체 상장종목 랭킹에서 이를 이유로 종목을 삭제하면 안 된다.
            exch='US'

        # Yahoo는 BRK.B 같은 점 표기를 BRK-B로 사용한다.
        yahoo_symbol=raw_symbol.replace('.','-').replace('/','-')
        out.append({
            'ticker':yahoo_symbol,'displayTicker':raw_symbol,'name':name or raw_symbol,
            'market':exch,'changePct':pct,'quotedPrice':price,'marketCapUSD':cap,
            'sectorRaw':str(r.get('sector') or '').strip(),
            'industryRaw':str(r.get('industry') or '').strip()
        })
    return sorted(out,key=lambda x:x['changePct'],reverse=True)


def _us_fallback_candidates(um):
    """Nasdaq 당일 변화율 열이 일시적으로 비정상일 때의 안전망.

    방금 갱신된 us.html의 정밀계산 유니버스에서 마지막 두 일봉으로 당일
    등락률을 직접 계산한다. 전체시장 API가 정상일 때는 사용하지 않는다.
    """
    rows=[]
    for ticker,old in um.items():
        hist=old.get('history') or []
        closes=[]
        for h in hist:
            c=num(h.get('close')) if isinstance(h,dict) else None
            if c is not None:
                closes.append(c)
        if len(closes)<2 or closes[-2]<=0:
            continue
        pct=(closes[-1]/closes[-2]-1)*100
        rows.append({
            'ticker':ticker,'displayTicker':old.get('displayTicker') or ticker,
            'name':old.get('name') or ticker,'market':old.get('exchange') or old.get('krx_market') or 'US',
            'changePct':pct,'quotedPrice':closes[-1],
            'marketCapUSD':num(old.get('market_cap_usd') or old.get('marketCapUSD')) or 0,
            'sectorRaw':old.get('sectorEn') or '', 'industryRaw':old.get('industryEn') or '',
            '_fallback':True
        })
    return sorted(rows,key=lambda x:x['changePct'],reverse=True)


def _us_health(ticker,stock,mapping):
    old=stock.get('financialHealth') or {}
    if old.get('status') in ('PASS','FAIL'): return old
    item=mapping.get(str(stock.get('displayTicker') or ticker).upper()) or mapping.get(ticker.upper())
    if not item: return {'status':'UNAVAILABLE','source':'SEC EDGAR','criterion':'SEC 티커 연결 없음'}
    try: return us.sec_financial_health_for_mapping(item,stock.get('sector') or stock.get('krxSector') or '')
    except Exception as exc: return {'status':'UNAVAILABLE','source':'SEC EDGAR','criterion':us.core.compact_provider_error(str(exc))}


def us_top30(um):
    candidates=[]
    for ticker,old in um.items():
        cap=num(old.get('market_cap_usd') or old.get('marketCapUSD')) or 0
        pct=_daily_change(old)
        if cap<US_MIN_CAP or pct is None or old.get('instrumentType') in ('ETF_ETN','SPAC','REIT'): continue
        if len(old.get('history') or [])<20: continue
        candidates.append((pct,ticker,old))
    candidates.sort(reverse=True,key=lambda x:x[0])
    try: mapping=us.sec_company_map()
    except Exception: mapping={}
    out=[]; failed=0; unavailable=0
    for pct,ticker,old in candidates:
        if len(out)>=30: break
        health=_us_health(ticker,old,mapping)
        if health.get('status')!='PASS':
            unavailable += health.get('status')=='UNAVAILABLE'; failed += health.get('status')=='FAIL'; continue
        hist=old.get('history') or []; t=trend_info(hist)
        sector=old.get('sector') or '산업 미분류'; industry=old.get('industry') or sector
        prof=old.get('businessProfile') or {}; name=old.get('name') or ticker
        desc=prof.get('summary') or old.get('businessModelEasy') or f'{name}은(는) {industry}에 속하는 미국 상장기업입니다.'
        out.append({'ticker':ticker,'displayTicker':old.get('displayTicker') or ticker,'name':name,
                    'market':old.get('exchange') or old.get('market') or 'US','changePct':round(pct,3),
                    'quotedPrice':old.get('close'),'marketCapUSD':old.get('market_cap_usd') or old.get('marketCapUSD'),
                    **t,'sector':sector,'industry':industry,'description':desc,'financialHealth':health,
                    'source':'미국 중대형주 정밀계산 유니버스 + SEC EDGAR XBRL','priceProvider':old.get('priceProvider') or old.get('dataSource')})
    SCREEN_META['us']={'candidateCount':len(candidates),'financialFailCount':failed,
                       'financialUnavailableCount':unavailable,'minMarketCap':US_MIN_CAP,
                       'universe':'시총 20억달러 이상·50일 평균 거래대금 1천만달러 이상 일반기업'}
    return out


def read_cache():
    if CACHE.exists():
        try: return json.loads(CACHE.read_text(encoding='utf-8'))
        except Exception: pass
    return {'korea':[],'us':[],'meta':{}}


def html(payload):
    data=json.dumps(payload,ensure_ascii=False,separators=(',',':')).replace('</','<\\/')
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>WAMO 오늘의 급등주 30</title>
<style>
:root{{--bg:#07101d;--p:#0e192b;--p2:#101f34;--line:#263a57;--t:#f3f7ff;--m:#91a4bf;--g:#45d6a0;--r:#ff7e86;--b:#69adff;--y:#f1cb6c}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 90% -10%,#173868 0,transparent 32%),#07101d;color:var(--t);font-family:Inter,system-ui,-apple-system,"Noto Sans KR",sans-serif}}.app{{max-width:1480px;margin:auto;padding:22px}}nav{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:20px}}nav a{{text-decoration:none;color:#aebdd1;border:1px solid var(--line);padding:8px 12px;border-radius:10px;background:#0b1728}}nav a.active{{background:#24466f;color:white;border-color:#4f7db5}}h1{{margin:4px 0 7px;font-size:30px}}.sub{{color:var(--m);font-size:12px;line-height:1.6}}.bar{{display:flex;justify-content:space-between;gap:12px;align-items:end;margin:18px 0 12px}}.tabs{{display:flex;gap:7px}}button{{border:1px solid var(--line);background:#0d1a2d;color:#aebdd1;padding:8px 12px;border-radius:9px;cursor:pointer}}button.on{{background:#21456f;color:#fff;border-color:#5682b6}}.meta{{font-size:11px;color:var(--m);text-align:right}}.summary{{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin-bottom:12px}}.kpi{{border:1px solid var(--line);background:var(--p);border-radius:13px;padding:12px}}.kpi span{{font-size:10px;color:var(--m)}}.kpi b{{display:block;font-size:22px;margin-top:4px}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}}.card{{border:1px solid var(--line);background:linear-gradient(155deg,#0f1d31,#0a1423);border-radius:15px;padding:12px;min-width:0}}.top{{display:flex;justify-content:space-between;gap:8px}}.rank{{font-size:11px;color:var(--m)}}.name{{font-weight:900;font-size:15px;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.ticker{{font-size:10px;color:var(--m);margin-top:2px}}.gain{{font-size:21px;color:var(--r);font-weight:950;white-space:nowrap}}.badges{{display:flex;gap:5px;flex-wrap:wrap;margin:9px 0}}.badge{{font-size:9px;border:1px solid #3a516f;border-radius:999px;padding:4px 6px;color:#a9bad0}}.badge.y{{border-color:#2f8063;color:#83e6bd;background:#0e2b24}}.badge.n{{border-color:#79404a;color:#ffadb3;background:#2b171c}}.badge.p{{border-color:#725f34;color:#f5d987;background:#2b2415}}.sector{{font-size:11px;font-weight:850;color:#d6e6fa}}.desc{{font-size:10px;color:#9eb0c8;line-height:1.55;height:48px;overflow:hidden;margin-top:5px}}canvas{{display:block;width:100%;height:142px;margin-top:9px;border:1px solid #21344e;border-radius:9px;background:#07111e}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:5px;margin-top:8px}}.metrics div{{background:#081522;border-radius:7px;padding:6px}}.metrics span{{display:block;color:#7589a4;font-size:8px}}.metrics b{{display:block;font-size:10px;margin-top:2px}}.note{{margin-top:14px;color:#7f93ad;font-size:10px;line-height:1.6;border-top:1px solid var(--line);padding-top:11px}}@media(max-width:1050px){{.grid{{grid-template-columns:repeat(2,1fr)}}}}@media(max-width:700px){{.app{{padding:13px}}h1{{font-size:24px}}.grid{{grid-template-columns:1fr}}.summary{{grid-template-columns:repeat(2,1fr)}}.bar{{align-items:flex-start;flex-direction:column}}.meta{{text-align:left}}}}
</style></head><body><div class="app"><nav><a href="./">🇰🇷 한국 대시보드</a><a href="us.html">🇺🇸 미국 대시보드</a><a class="active" href="movers.html">🚀 오늘의 상승률 TOP 30</a></nav><div class="sub">WAMO DAILY MOVERS · 재무가 확인된 중형·대형 일반기업 상승률 순위</div><div class="sub"><b>자동갱신(KST) · 한국 12:00 / 16:00 · 미국 00:00 / 05:00 · 시장별 하루 2회</b></div><h1>재무안정 중대형주 상승률 TOP 30</h1><div class="sub">한국은 시총 1조원, 미국은 시총 20억달러 이상에서 시작합니다. 공식 재무제표로 자본·순이익이 양수인지 확인하고, 비금융사는 부채비율 300% 이하를 추가 확인합니다. 조건 통과 종목이 30개보다 적으면 억지로 채우지 않습니다.</div><div class="bar"><div class="tabs"><button id="krBtn" onclick="setMarket('korea')">한국</button><button id="usBtn" onclick="setMarket('us')">미국</button></div><div class="meta" id="meta"></div></div><div class="summary" id="summary"></div><div class="grid" id="grid"></div><div class="note">정배열 = 종가 &gt; 20일선 &gt; 60일선 &gt; 120일선 &gt; 200일선. 재무안정 PASS는 투자등급이나 부도 가능성 보증이 아니라 최신 공식 재무제표의 최소 안전장치입니다. 금융사는 업종 특성상 일반기업의 부채비율·유동비율을 적용하지 않고 자본과 순이익만 확인합니다. 한국은 OpenDART, 미국은 SEC EDGAR XBRL을 사용하며, 재무를 확인할 수 없는 종목은 제외합니다.</div></div>
<script>const D={data};let M='korea';const fmt=(x,d=1)=>x==null?'—':Number(x).toLocaleString(undefined,{{maximumFractionDigits:d}});const capText=x=>x.marketCapKRW!=null?`시총 ${{fmt(x.marketCapKRW/1e12,2)}}조원`:x.marketCapUSD!=null?`시총 ${{fmt(x.marketCapUSD/1e9,2)}}B달러`:'시총 —';function setMarket(m){{M=m;document.getElementById('krBtn').classList.toggle('on',m==='korea');document.getElementById('usBtn').classList.toggle('on',m==='us');render()}}function render(){{const a=D[M]||[];const md=(D.meta||{{}})[M]||{{}};const dateLabel=M==='us'?'미국 현지 장 마감 기준일':'기준일';document.getElementById('meta').textContent=`${{dateLabel}} ${{md.asOf||'—'}} · 갱신 ${{md.updatedAt||'—'}} · ${{a.length}}종목`;const aligned=a.filter(x=>x.alignmentStatus==='YES').length;document.getElementById('summary').innerHTML=`<div class=kpi><span>중대형 후보</span><b>${{md.candidateCount||0}}</b></div><div class=kpi><span>재무안정 TOP</span><b>${{a.length}}</b></div><div class=kpi><span>재무기준 탈락</span><b>${{md.financialFailCount||0}}</b></div><div class=kpi><span>정배열</span><b>${{aligned}}</b></div>`;document.getElementById('grid').innerHTML=a.map((x,i)=>{{const f=x.financialHealth||{{}};const ftitle=esc(f.criterion||'공식 재무제표 최소기준 통과');return `<article class=card><div class=top><div><div class=rank>#${{i+1}}</div><div class=name>${{esc(x.name)}}</div><div class=ticker>${{esc(x.ticker)}} · ${{esc(x.market)}}</div></div><div class=gain>+${{fmt(x.changePct,2)}}%</div></div><div class=badges><span class="badge y" title="${{ftitle}}">재무안정 PASS</span><span class=badge>${{capText(x)}}</span><span class="badge ${{x.alignmentStatus==='YES'?'y':x.alignmentStatus==='PENDING'?'p':'n'}}">${{x.alignmentStatus==='YES'?'정배열':x.alignmentStatus==='PENDING'?'정배열 판정보류':'정배열 아님'}}</span><span class=badge>52주 고점 ${{fmt(x.high52Ratio)}}%</span><span class=badge>거래량 ${{fmt(x.volumeRatio20,2)}}x</span></div><div class=sector>${{esc(x.sector||'—')}}</div><div class=desc>${{esc(x.description||'')}}</div><canvas id="c${{i}}" width="430" height="142"></canvas><div class=metrics><div><span>종가</span><b>${{fmt(x.close,2)}}</b></div><div><span>20일선</span><b>${{fmt(x.ma20,2)}}</b></div><div><span>부채비율</span><b>${{fmt(f.debtRatioPct)}}%</b></div><div><span>순이익</span><b>${{f.profitPositive?'양수':'—'}}</b></div></div></article>`}}).join('');requestAnimationFrame(()=>a.forEach((x,i)=>draw(document.getElementById('c'+i),x.history||[])))}}function esc(s){{return String(s??'').replace(/[&<>\"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]))}}function draw(c,h){{if(!c||h.length<2)return;const ctx=c.getContext('2d'),w=c.width,hg=c.height,p=8,vals=h.map(x=>x.close),mn=Math.min(...vals),mx=Math.max(...vals),span=mx-mn||1;ctx.clearRect(0,0,w,hg);ctx.strokeStyle='#21344e';ctx.lineWidth=1;for(let j=1;j<4;j++){{let y=p+(hg-2*p)*j/4;ctx.beginPath();ctx.moveTo(p,y);ctx.lineTo(w-p,y);ctx.stroke()}}ctx.strokeStyle='#69adff';ctx.lineWidth=2;ctx.beginPath();vals.forEach((v,j)=>{{let x=p+(w-2*p)*j/(vals.length-1),y=hg-p-(v-mn)/span*(hg-2*p);j?ctx.lineTo(x,y):ctx.moveTo(x,y)}});ctx.stroke()}}setMarket('korea');</script></body></html>'''


def patch_nav(path):
    p=Path(path); s=p.read_text(encoding='utf-8')
    if 'href="movers.html"' in s: return
    # Insert beside market links in both dashboard variants.
    anchor='<a class="active" href="us.html" aria-current="page">🇺🇸 미국</a>'
    if anchor in s: s=s.replace(anchor,anchor+'\n    <a href="movers.html">🚀 상승률 TOP 30</a>',1)
    else:
        # Korea page has the active class on Korea; insert after US link.
        m=re.search(r'(<a[^>]+href="us\.html"[^>]*>.*?</a>)',s,flags=re.S)
        if m: s=s[:m.end()]+ '\n    <a href="movers.html">🚀 상승률 TOP 30</a>'+s[m.end():]
    p.write_text(s,encoding='utf-8')


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--market',choices=['korea','us','both'],default='both'); args=ap.parse_args()
    km,um=profile_maps(); cache=read_cache(); now=datetime.now(KST).isoformat(timespec='minutes')
    markets=['korea','us'] if args.market=='both' else [args.market]
    for m in markets:
        print('Updating movers:',m)
        arr=korea_top30(km) if m=='korea' else us_top30(um)
        if not arr:
            raise RuntimeError(f'{m} 재무안정 중대형 상승 종목을 한 종목도 확인하지 못했습니다')
        if len(arr)<30: print(f'WARN {m} strict financial-health screen returned {len(arr)} stocks; not padding')
        cache[m]=arr
        asof=max((x['history'][-1]['date'] for x in arr if x.get('history')),default='—')
        cache.setdefault('meta',{})[m]={'asOf':asof,'updatedAt':now,'count':len(arr),**SCREEN_META.get(m,{})}
    CACHE.write_text(json.dumps(cache,ensure_ascii=False,indent=2),encoding='utf-8')
    OUT.write_text(html(cache),encoding='utf-8')
    patch_nav(ROOT/'index.html'); patch_nav(ROOT/'us.html')
    print('Done:',OUT)

if __name__=='__main__': main()
