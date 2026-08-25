#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, math, re, statistics, sys, time, urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'movers.html'
CACHE=ROOT/'wamo_movers_cache.json'
KST=timezone(timedelta(hours=9))

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


def korea_top30(km):
    candidates=parse_naver_rise(0,'KOSPI',45)+parse_naver_rise(1,'KOSDAQ',45)
    candidates=sorted(candidates,key=lambda x:x['changePct'],reverse=True)
    sector_map=kr.kind_sector_map()
    out=[]
    for c in candidates:
        if len(out)>=30: break
        name=c['name']; inst=kr.classify_instrument(name)
        if inst!='COMPANY': continue
        code=c['ticker']; suffix='.KS' if c['market']=='KOSPI' else '.KQ'
        try: hist=kr.fetch_naver_history(code,count=280)
        except Exception:
            try: hist,_=kr.fetch_yahoo_history(code+suffix,years=2)
            except Exception: continue
        t=trend_info(hist)
        if len(t.get('history',[]))<20: continue
        old=km.get(code,{})
        sector=old.get('sector') or old.get('detailSector') or sector_map.get(code) or '업종 확인 필요'
        prof=old.get('businessProfile') or {}
        desc=prof.get('summary') or old.get('businessModelEasy') or f'{name}은(는) KIND 업종분류상 {sector}에 속하는 상장기업입니다.'
        out.append({**c,**t,'sector':sector,'industry':old.get('krxSector') or sector,'description':desc,'source':'NAVER 상승률 + NAVER/KIND 가격·업종'})
    return out


def us_rows_all():
    q=urllib.parse.urlencode({'tableonly':'true','limit':'5000','offset':'0','download':'true'})
    payload=us._get_json('https://api.nasdaq.com/api/screener/stocks?'+q,headers={'Referer':'https://www.nasdaq.com/market-activity/stocks/screener'})
    rows=us._nasdaq_screener_rows(payload)
    if len(rows)<1000: raise RuntimeError(f'Nasdaq screener rows too small: {len(rows)}')
    out=[]
    for r in rows:
        symbol=str(r.get('symbol') or '').strip().upper(); name=str(r.get('name') or r.get('companyName') or '').strip()
        if not symbol or us._is_excluded_security(symbol,name): continue
        pct=num(r.get('pctchange') or r.get('percentchange') or r.get('percentChange') or r.get('changePercent'))
        cap=num(r.get('marketCap') or r.get('marketcap'))
        price=num(r.get('lastsale') or r.get('lastSalePrice') or r.get('last'))
        if pct is None or cap is None or cap<=0: continue
        exchange=str(r.get('exchange') or r.get('market') or '').upper()
        if 'NASDAQ' in exchange: exch='NASDAQ'
        elif 'AMEX' in exchange or 'NYSE AMERICAN' in exchange: exch='NYSE AMERICAN'
        elif 'NYSE' in exchange: exch='NYSE'
        else: continue
        out.append({'ticker':symbol,'name':name,'market':exch,'changePct':pct,'quotedPrice':price,'marketCapUSD':cap,
                    'sectorRaw':str(r.get('sector') or '').strip(),'industryRaw':str(r.get('industry') or '').strip()})
    return sorted(out,key=lambda x:x['changePct'],reverse=True)


def us_top30(um):
    out=[]
    for c in us_rows_all():
        if len(out)>=30: break
        try: hist,provider=kr.fetch_yahoo_history(c['ticker'],years=2)
        except Exception: continue
        t=trend_info(hist)
        if len(t.get('history',[]))<20: continue
        old=um.get(c['ticker'],{})
        sector=old.get('sector') or us._bilingual(c.get('sectorRaw')) or '산업 미분류'
        industry=old.get('industry') or us.translate_industry(c.get('industryRaw'),c.get('sectorRaw'))
        prof=old.get('businessProfile') or {}
        desc=prof.get('summary') or old.get('businessModelEasy') or f"{c['name']}은(는) Nasdaq 분류상 {industry}에 속하는 미국 상장기업입니다."
        out.append({**c,**t,'sector':sector,'industry':industry,'description':desc,'source':'Nasdaq Screener 상승률 + Yahoo Finance 가격','priceProvider':provider})
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
</style></head><body><div class="app"><nav><a href="./">🇰🇷 한국 대시보드</a><a href="us.html">🇺🇸 미국 대시보드</a><a class="active" href="movers.html">🚀 오늘의 상승률 TOP 30</a></nav><div class="sub">WAMO DAILY MOVERS · 전체시장 상승률 상위 종목을 별도 스캔</div><div class="sub"><b>자동갱신(KST) · 한국 12:00 / 16:00 · 미국 00:00 / 05:00 · 시장별 하루 2회</b></div><h1>오늘 가장 강하게 오른 30종목</h1><div class="sub">상승률만 보지 않고 업종·기업설명·거래량·52주 고점 위치·정배열 여부와 1년 가격추세를 함께 봅니다.</div><div class="bar"><div class="tabs"><button id="krBtn" onclick="setMarket('korea')">한국</button><button id="usBtn" onclick="setMarket('us')">미국</button></div><div class="meta" id="meta"></div></div><div class="summary" id="summary"></div><div class="grid" id="grid"></div><div class="note">정배열 = 종가 &gt; 20일선 &gt; 60일선 &gt; 120일선 &gt; 200일선. 200거래일 이력이 없으면 판정을 보류합니다. 한국 상승률 순위는 NAVER 증권의 KOSPI·KOSDAQ 상승률 화면, 미국은 Nasdaq Stock Screener를 사용하며 차트·이동평균은 NAVER/Yahoo Finance 일봉으로 재검산합니다. ETF·ETN·SPAC 등은 기업 랭킹에서 제외합니다.</div></div>
<script>const D={data};let M='korea';const fmt=(x,d=1)=>x==null?'—':Number(x).toLocaleString(undefined,{{maximumFractionDigits:d}});function setMarket(m){{M=m;document.getElementById('krBtn').classList.toggle('on',m==='korea');document.getElementById('usBtn').classList.toggle('on',m==='us');render()}}function render(){{const a=D[M]||[];const md=(D.meta||{{}})[M]||{{}};document.getElementById('meta').textContent=`기준일 ${{md.asOf||'—'}} · 갱신 ${{md.updatedAt||'—'}} · ${{a.length}}종목`;const aligned=a.filter(x=>x.alignmentStatus==='YES').length,near=a.filter(x=>(x.high52Ratio||0)>=90).length,vol=a.filter(x=>(x.volumeRatio20||0)>=1.5).length;document.getElementById('summary').innerHTML=`<div class=kpi><span>상위 종목</span><b>${{a.length}}</b></div><div class=kpi><span>정배열</span><b>${{aligned}}</b></div><div class=kpi><span>52주 고점 90%+</span><b>${{near}}</b></div><div class=kpi><span>거래량 20일평균 1.5배+</span><b>${{vol}}</b></div>`;document.getElementById('grid').innerHTML=a.map((x,i)=>`<article class=card><div class=top><div><div class=rank>#${{i+1}}</div><div class=name>${{esc(x.name)}}</div><div class=ticker>${{esc(x.ticker)}} · ${{esc(x.market)}}</div></div><div class=gain>+${{fmt(x.changePct,2)}}%</div></div><div class=badges><span class="badge ${{x.alignmentStatus==='YES'?'y':x.alignmentStatus==='PENDING'?'p':'n'}}">${{x.alignmentStatus==='YES'?'정배열':x.alignmentStatus==='PENDING'?'정배열 판정보류':'정배열 아님'}}</span><span class=badge>52주 고점 ${{fmt(x.high52Ratio)}}%</span><span class=badge>거래량 ${{fmt(x.volumeRatio20,2)}}x</span></div><div class=sector>${{esc(x.sector||'—')}}</div><div class=desc>${{esc(x.description||'')}}</div><canvas id="c${{i}}" width="430" height="142"></canvas><div class=metrics><div><span>종가</span><b>${{fmt(x.close,2)}}</b></div><div><span>20일선</span><b>${{fmt(x.ma20,2)}}</b></div><div><span>60일선</span><b>${{fmt(x.ma60,2)}}</b></div><div><span>200일선</span><b>${{fmt(x.ma200,2)}}</b></div></div></article>`).join('');requestAnimationFrame(()=>a.forEach((x,i)=>draw(document.getElementById('c'+i),x.history||[])))}}function esc(s){{return String(s??'').replace(/[&<>\"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]))}}function draw(c,h){{if(!c||h.length<2)return;const ctx=c.getContext('2d'),w=c.width,hg=c.height,p=8,vals=h.map(x=>x.close),mn=Math.min(...vals),mx=Math.max(...vals),span=mx-mn||1;ctx.clearRect(0,0,w,hg);ctx.strokeStyle='#21344e';ctx.lineWidth=1;for(let j=1;j<4;j++){{let y=p+(hg-2*p)*j/4;ctx.beginPath();ctx.moveTo(p,y);ctx.lineTo(w-p,y);ctx.stroke()}}ctx.strokeStyle='#69adff';ctx.lineWidth=2;ctx.beginPath();vals.forEach((v,j)=>{{let x=p+(w-2*p)*j/(vals.length-1),y=hg-p-(v-mn)/span*(hg-2*p);j?ctx.lineTo(x,y):ctx.moveTo(x,y)}});ctx.stroke()}}setMarket('korea');</script></body></html>'''


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
        if len(arr)<20: raise RuntimeError(f'{m} top movers too few: {len(arr)}')
        cache[m]=arr
        asof=max((x['history'][-1]['date'] for x in arr if x.get('history')),default='—')
        cache.setdefault('meta',{})[m]={'asOf':asof,'updatedAt':now,'count':len(arr)}
    CACHE.write_text(json.dumps(cache,ensure_ascii=False,indent=2),encoding='utf-8')
    OUT.write_text(html(cache),encoding='utf-8')
    patch_nav(ROOT/'index.html'); patch_nav(ROOT/'us.html')
    print('Done:',OUT)

if __name__=='__main__': main()
