"""Bounded, optional related-headline enrichment; never a price dependency."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse
import json
import re
import xml.etree.ElementTree as ET
import requests
from wamo_runtime import write_json
ROOT = Path(__file__).resolve().parent


def parse_feed(xml, now):
    rows=[]
    for item in ET.fromstring(xml).findall('./channel/item')[:12]:
        try:
            title=(item.findtext('title') or '').strip()
            url=(item.findtext('link') or '').strip()
            parsed=urlparse(url)
            published=parsedate_to_datetime(item.findtext('pubDate') or '')
            if published.tzinfo is None:published=published.replace(tzinfo=timezone.utc)
            if not title or parsed.scheme!='https' or parsed.hostname!='news.google.com':continue
            if published > now + timedelta(minutes=5) or published < now-timedelta(days=4):continue
            rows.append({'title':title[:300],'url':url,'date':published.isoformat(),'publisher':item.findtext('source') or ''})
        except (ValueError,TypeError,OverflowError):continue
    return sorted(rows,key=lambda n:n['date'],reverse=True)[:3]


def fetch(stock,now):
    name=re.sub(r'\s+(?:Class [A-Z] )?(?:Common Stock|Ordinary Shares).*','',stock['name'],flags=re.I).strip()
    kr=stock['market']=='KR'
    query='"'+name+'" '+('주가' if kr else 'stock')+' when:3d'
    response=requests.get('https://news.google.com/rss/search',params={'q':query,'hl':'ko' if kr else 'en-US','gl':'KR' if kr else 'US','ceid':'KR:ko' if kr else 'US:en'},timeout=(4,8),headers={'User-Agent':'WAMO public market dashboard/1.0'})
    response.raise_for_status()
    if len(response.content)>1000000:raise ValueError('oversize feed')
    return parse_feed(response.content,now)


def attach_news(snapshot,cache,now):
    entries=cache.get('stocks',{}) if isinstance(cache,dict) else {}
    for market in snapshot['markets'].values():
        for stock in market['stocks']:
            rows=[]
            for n in entries.get(stock['id'],{}).get('items',[]):
                try:
                    date=datetime.fromisoformat(n['date'].replace('Z','+00:00'))
                    url=urlparse(n['url'])
                    if now-timedelta(days=4)<=date<=now+timedelta(minutes=5) and url.scheme=='https' and url.hostname=='news.google.com':rows.append(n)
                except (ValueError,KeyError,TypeError):continue
            stock['news']=rows[:3]
    return snapshot


def main():
    now=datetime.now(timezone.utc)
    snapshot=json.loads((ROOT/'wamo_insights.json').read_text())
    try:cache=json.loads((ROOT/'wamo_news.json').read_text())
    except (OSError,ValueError):cache={'stocks':{}}
    selected=[]
    for market in snapshot['markets'].values():
        candidates=sorted(market['stocks'],key=lambda s:abs(s.get('changePct') or 0),reverse=True)
        selected.extend([s for s in candidates if s['moverReasons']][:12])
    due=[]
    for s in selected:
        try:
            checked=datetime.fromisoformat(cache['stocks'][s['id']]['checkedAt'])
            if now-checked<timedelta(hours=2):continue
        except (KeyError,ValueError,TypeError):pass
        due.append(s)
    failures=0
    with ThreadPoolExecutor(max_workers=6) as pool:
        tasks={pool.submit(fetch,s,now):s for s in due}
        for task in as_completed(tasks):
            stock=tasks[task]
            try:cache.setdefault('stocks',{})[stock['id']]={'checkedAt':now.isoformat(),'items':task.result()}
            except (requests.RequestException,ValueError,ET.ParseError):failures+=1
    # Keep a small rolling cache. Old headlines are never re-dated on failure.
    cutoff=(now-timedelta(days=4)).isoformat()
    cache['stocks']={k:v for k,v in cache.get('stocks',{}).items() if v.get('checkedAt','')>=cutoff}
    cache['checkedAt']=now.isoformat()
    write_json(ROOT/'wamo_news.json',cache)
    print(f'Related headlines: requested={len(due)}, unavailable={failures}, cached={len(cache["stocks"])}; prices unaffected')

if __name__=='__main__':main()
