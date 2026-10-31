#!/usr/bin/env python3
"""52주 신고가 공통 계산 (2026-10-31 사용자 결정: 노션 기록과 같은 기준 하나로 통일).

화면·노션·보고서가 각자 다른 종목 범위로 신고가를 세서 같은 날 숫자가 달랐다(미국 10/30: 노션 107, 레이더 70,
흐름판 19). 이 모듈이 한 번만 계산해 wamo_highs52.json 으로 내놓고, 다른 곳은 이 결과를 읽는다.

기준은 노션 기록(global-high-radar claude-automations/state/notion-52w/n52.py)과 같다.
- 대상: 한국 KOSPI·KOSDAQ 보통주 시총 1,000억 원 이상(우선주·스팩 제외),
        미국 NYSE·NASDAQ·AMEX 보통주 시총 20억 달러 이상(ETF·우선주·워런트·SPAC 제외,
        같은 회사의 여러 주식 종류는 티커가 가장 짧은 하나만, BRK.A 제외).
- 신고가: 그날 장중 고가 ≥ 그 전 1년(365일) 동안의 최고가 × 0.9999. 신저가는 저가로 같은 방식.
- 종목 목록: 한국은 네이버 시가총액 순 목록(1,000억 원 밑으로 내려가면 멈춤), 미국은 나스닥 공식 스크리너.
  노션 기록은 TradingView 스크리너를 쓰지만, 무료 GitHub 작업에서는 쓸 수 없어 공식 목록으로 바꿨다.
- 가격: 야후 일봉(노션 기록의 예비 경로와 같은 판정).

  python3 wamo_highs52.py --market KR|US|auto [--day YYYY-MM-DD] [--out wamo_highs52.json]

처음에는 노션 기록과 나란히 돌려(shadow) 같은 날 결과를 비교한다(RADAR_READ_TOKEN 이 있으면 노션 기록의
history.json 을 읽어 shadow 칸에 겹침·차이를 남긴다). 비교가 맞으면 다른 곳이 이 파일을 읽게 바꾼다.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'wamo_highs52.json'
UA = 'Mozilla/5.0 (WAMO daily market dashboard)'
KST = timezone(timedelta(hours=9))

KR_MIN_CAP = 1e11      # 1,000억 원
US_MIN_CAP = 2e9       # 20억 달러
TOUCH = 0.9999         # 노션 기록과 같은 허용치(소수점 반올림 차이)
MIN_PRIOR_BARS = 20
INDEX = {'KR': '^KS11', 'US': '^GSPC'}
LABEL = {'KR': '한국', 'US': '미국'}
US_EXCLUDE = {'BRK.A', 'BRK-A'}
WORKERS = 8


# ------------------------------------------------------------------ http
def get_json(url, headers=None, data=None, retries=3, timeout=25):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers={'User-Agent': UA, 'Accept': 'application/json', **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 401, 403, 404):
                raise
            last = exc
        except (OSError, ValueError) as exc:
            last = exc
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f'{type(last).__name__}: {url.split("?")[0]}')


# ------------------------------------------------------------------ universe
def kr_preferred(code, name):
    """우선주·스팩. 노션 기록과 같은 규칙(코드 끝자리 5·7·9·K)에 이름 검사를 더한다."""
    return code[-1] in '579K' or '스팩' in name or name.endswith(('우', '우B', '우C'))


def universe_kr(fetch=get_json):
    out = []
    for market, suffix in (('KOSPI', '.KS'), ('KOSDAQ', '.KQ')):
        for page in range(1, 81):
            payload = fetch(f'https://m.stock.naver.com/api/stocks/marketValue/{market}?page={page}&pageSize=100')
            stocks = payload.get('stocks') if isinstance(payload, dict) else None
            if not stocks:
                break
            below = False
            for s in stocks:
                code, name = str(s.get('itemCode') or ''), str(s.get('stockName') or '').strip()
                try:
                    cap = int(s.get('marketValueRaw'))
                except (TypeError, ValueError):
                    continue
                if cap < KR_MIN_CAP:
                    below = True  # 시가총액 순이라 여기부터는 모두 기준 밑
                    continue
                # ETF·ETN 같은 펀드는 주식이 아니다(stockEndType 'etf' 등). 노션 기록도 보통주만 본다.
                # 거래정지 종목은 야후에 같은 가격이 계속 찍혀 신고가로 잘못 잡힌다(삼부토건 10/30 등).
                halted = str(((s.get('tradeStopType') or {}).get('name')) or 'TRADING') != 'TRADING'
                if (halted or str(s.get('stockEndType') or 'stock') != 'stock'
                        or not re.fullmatch(r'[0-9A-Z]{6}', code) or kr_preferred(code, name)):
                    continue
                out.append({'code': code, 'yahoo': code + suffix, 'name': name, 'board': market, 'cap': cap})
            if below:
                break
    return out


CLASS_RE = re.compile(r'\s+(Class [A-Z]\b.*|Series [A-Z]\b.*|Common Stock.*|Ordinary Shares.*|Capital Stock.*|'
                      r'Common Shares.*|Subordinate Voting Shares.*|Voting Shares.*)$', re.I)


def company_key(name):
    """같은 회사의 여러 주식 종류를 하나로 묶는 열쇠(예: Alphabet Class A·Class C)."""
    return CLASS_RE.sub('', str(name or '')).strip().rstrip(',.').lower()


def us_excluded(symbol, name):
    symbol, upper = str(symbol or '').upper().strip(), f" {str(name or '').upper().strip()} "
    if not symbol or len(symbol) > 8 or any(c in symbol for c in '^=+/') or symbol in US_EXCLUDE:
        return True
    words = (' ETF', ' ETN', 'EXCHANGE TRADED', 'INDEX FUND', 'WARRANT', 'RIGHTS', ' ACQUISITION', 'SPAC', 'UNITS',
             ' UNIT ', 'PREFERRED', 'DEPOSITARY SHARE', 'DEPOSITARY RECEIPT', ' ADR ', ' ADS ', 'TEST STOCK', ' NOTES ')
    if any(w in upper for w in words):
        return True
    return bool(re.search(r'(?:[-./]W|WS|[-./]U)$', symbol))


def universe_us(fetch=get_json):
    payload = fetch('https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&offset=0&download=true',
                    headers={'Referer': 'https://www.nasdaq.com/market-activity/stocks/screener'})
    rows = ((payload or {}).get('data') or {}).get('rows') or []
    if len(rows) < 1000:
        raise RuntimeError(f'나스닥 종목 목록이 비정상적으로 적습니다: {len(rows)}')
    keep = {}
    for r in rows:
        # 나스닥 목록은 주식 종류를 'BRK/B' 처럼 적는다. 제외하지 말고 'BRK.B' 로 읽는다.
        symbol, name = str(r.get('symbol') or '').strip().upper().replace('/', '.'), str(r.get('name') or '').strip()
        try:
            cap = float(str(r.get('marketCap') or '0').replace(',', ''))
        except ValueError:
            continue
        if cap < US_MIN_CAP or us_excluded(symbol, name):
            continue
        item = {'code': symbol, 'yahoo': symbol.replace('.', '-'), 'name': name, 'cap': cap,
                'sector': str(r.get('sector') or '').strip(), 'industry': str(r.get('industry') or '').strip()}
        key = company_key(name) or symbol
        old = keep.get(key)
        if old is None or (len(symbol), symbol) < (len(old['code']), old['code']):
            keep[key] = item
    return list(keep.values())


# ------------------------------------------------------------------ prices
def yahoo_bars(symbol, day, fetch=get_json):
    """day 까지 약 1년치 일봉 {date: (high, low, close)}. 없으면 {}. 거래량이 0인 날(거래정지)은 뺀다."""
    end = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(days=2)
    start = end - timedelta(days=380)
    q = urllib.parse.urlencode({'period1': int(start.timestamp()), 'period2': int(end.timestamp()), 'interval': '1d'})
    last = None
    for host in ('query1', 'query2'):
        try:
            res = (fetch(f'https://{host}.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?{q}')
                   .get('chart') or {}).get('result')
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {}
            last = exc
            continue
        except RuntimeError as exc:
            last = exc
            continue
        if not res:
            return {}
        r = res[0]
        quote = (r.get('indicators') or {}).get('quote') or [{}]
        q0 = quote[0]
        off = (r.get('meta') or {}).get('gmtoffset') or 0
        bars = {}
        vols = q0.get('volume') or []
        for i, (t, h, l, c) in enumerate(zip(r.get('timestamp') or [], q0.get('high') or [], q0.get('low') or [], q0.get('close') or [])):
            v = vols[i] if i < len(vols) else None
            if h is None or c is None or v == 0:
                continue
            bars[datetime.fromtimestamp(t + off, timezone.utc).date()] = (h, l, c)
        return bars
    raise RuntimeError(f'야후 일봉 실패: {symbol} ({type(last).__name__})')


def judge(bars, day):
    """(신고가?, 신저가?, 종가, 등락률). 그날 봉이 없거나 이력이 짧으면 None."""
    if day not in bars:
        return None
    dates = sorted(bars)
    prior = [bars[d] for d in dates if day - timedelta(days=365) < d < day]
    if len(prior) < MIN_PRIOR_BARS:
        return None
    h, l, c = bars[day]
    pc = bars[[d for d in dates if d < day][-1]][2]
    hi = h >= max(p[0] for p in prior) * TOUCH
    lows = [p[1] for p in prior if p[1] is not None]
    lo = l is not None and bool(lows) and l <= min(lows) * (2 - TOUCH)
    return hi, lo, c, (c / pc - 1) * 100 if pc else None


# ------------------------------------------------------------------ session
def target_day(market, now=None, fetch=get_json):
    """그 시장의 가장 최근 '끝난' 거래일. 지수 일봉에서 마감 시각이 지난 날 중 마지막."""
    from zoneinfo import ZoneInfo
    now = now or datetime.now(timezone.utc)
    if market == 'KR':
        local, close_at = now.astimezone(KST), (15, 40)
    else:
        local, close_at = now.astimezone(ZoneInfo('America/New_York')), (16, 10)
    finished = local.date() if (local.hour, local.minute) >= close_at else local.date() - timedelta(days=1)
    bars = yahoo_bars(INDEX[market], finished, fetch)
    days = [d for d in bars if d <= finished]
    if not days:
        raise RuntimeError(f'{market} 지수 일봉을 받지 못했습니다')
    return max(days)


# ------------------------------------------------------------------ run
def scan(market, day, universe, fetch=get_json, workers=WORKERS):
    def work(item):
        try:
            return item, judge(yahoo_bars(item['yahoo'], day, fetch), day), None
        except RuntimeError as exc:
            return item, None, str(exc)
    highs, lows, failed, missing = [], 0, [], 0
    with ThreadPoolExecutor(workers) as pool:
        for item, result, error in pool.map(work, universe):
            if error:
                failed.append(item['code'])
                continue
            if result is None:
                missing += 1
                continue
            hi, lo, close, chg = result
            lows += bool(lo)
            if hi:
                row = {k: item[k] for k in ('code', 'name', 'cap') if k in item}
                row.update({k: item[k] for k in ('board', 'sector', 'industry') if item.get(k)})
                row['close'] = round(close, 4)
                row['chgPct'] = None if chg is None else round(chg, 2)
                highs.append(row)
    highs.sort(key=lambda r: -r['cap'])
    return {'asOf': day.isoformat(), 'universe': len(universe), 'failed': len(failed), 'noBar': missing,
            'failedCodes': failed[:50], 'highs': highs, 'highCount': len(highs), 'lowCount': lows}


def notion_shadow(market, day, ours, fetch=None):
    """노션 기록(history.json)과 같은 날 비교. 읽을 열쇠가 없거나 실패하면 None(비교는 선택)."""
    token = os.environ.get('RADAR_READ_TOKEN')
    if not token and fetch is None:
        return None
    try:
        if fetch is None:
            url = ('https://api.github.com/repos/dhandori/global-high-radar/contents/'
                   'claude-automations/state/notion-52w/history.json?ref=main')
            payload = get_json(url, headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'})
            hist = json.loads(base64.b64decode(payload['content']).decode('utf-8'))
        else:
            hist = fetch()
        entry = (hist.get(LABEL[market]) or {}).get(day)
    except Exception as exc:  # noqa: BLE001 - 비교는 결과를 막지 않는다
        return {'error': type(exc).__name__}
    if not entry:
        return {'notion': None, 'note': '노션 기록에 그날 자료가 아직 없음'}
    theirs = {str(r.get('코드')).upper() for r in entry.get('rows') or []}
    mine = {r['code'].upper() for r in ours['highs']}
    return {'notion': len(theirs), 'ours': len(mine), 'both': len(theirs & mine),
            'onlyOurs': sorted(mine - theirs)[:40], 'onlyNotion': sorted(theirs - mine)[:40],
            'notionLows': entry.get('lows'), 'notionSource': entry.get('source')}


def run(market, day=None, out=OUT, fetch=get_json, now=None):
    day = day or target_day(market, now, fetch)
    universe = universe_kr(fetch) if market == 'KR' else universe_us(fetch)
    result = scan(market, day, universe, fetch)
    if result['failed'] > len(universe) * 0.05:
        raise RuntimeError(f"{market} 가격 조회 실패가 너무 많습니다: {result['failed']}/{len(universe)}")
    result['generatedAt'] = datetime.now(timezone.utc).isoformat()
    result['basis'] = ('KOSPI·KOSDAQ 보통주 시총 1,000억 원 이상' if market == 'KR'
                       else 'NYSE·NASDAQ·AMEX 보통주 시총 20억 달러 이상') + ' · 장중 고가가 1년 최고가에 닿음'
    result['shadow'] = notion_shadow(market, result['asOf'], result)
    try:
        doc = json.loads(Path(out).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        doc = {}
    doc.setdefault('markets', {})[market] = result
    doc['schema'] = 1
    doc['updatedAt'] = result['generatedAt']
    tmp = Path(str(out) + '.tmp')
    tmp.write_text(json.dumps(doc, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    tmp.replace(out)
    return result


def already_done(market, day, out=OUT):
    try:
        cur = json.loads(Path(out).read_text(encoding='utf-8'))['markets'][market]
        return cur.get('asOf') == day.isoformat()
    except (OSError, ValueError, KeyError, TypeError):
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--market', choices=['KR', 'US', 'auto'], default='auto')
    ap.add_argument('--day')
    ap.add_argument('--out', default=str(OUT))
    ap.add_argument('--force', action='store_true', help='이미 계산한 날도 다시 계산')
    a = ap.parse_args(argv)
    hour = datetime.now(KST).hour
    markets = [a.market] if a.market != 'auto' else (['KR'] if 15 <= hour < 24 else ['US'])
    for market in markets:
        day = date.fromisoformat(a.day) if a.day else target_day(market)
        if not a.force and already_done(market, day, a.out):
            print(f'{market} {day}: 이미 계산함 · 건너뜀', flush=True)
            continue
        r = run(market, day, a.out)
        sh = r.get('shadow') or {}
        print(f"{market} {r['asOf']}: 신고가 {r['highCount']} · 신저가 {r['lowCount']} · 대상 {r['universe']} · "
              f"조회 실패 {r['failed']} · 노션 {sh.get('notion')} (겹침 {sh.get('both')})", flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
