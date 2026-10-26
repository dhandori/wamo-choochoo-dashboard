"""S&P 500 상승·하락 상위 종목 (무료 공개 자료).

- 구성 종목: 위키백과 'List of S&P 500 companies' 표 (GICS 섹터 포함). 7일마다 한 번만 새로 받는다.
- 시세: Yahoo Finance spark (20종목씩 묶어 약 25번 호출). 마지막 두 거래일 종가로 등락률 계산.
- 결과: wamo_sp500_movers.json  (특징주 탭 맨 위 카드가 읽음)

가격이 450종목 미만으로 모이면 이전 결과를 그대로 둔다. 실패해도 다른 갱신은 막지 않는다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
MEMBERS = 'wamo_sp500.json'
OUTPUT = 'wamo_sp500_movers.json'
WIKI = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
UA = 'Mozilla/5.0 (compatible; WAMO-dashboard)'
TOP = 12
MIN_PRICED = 450
SECTOR_KO = {
    'Information Technology': '정보기술', 'Health Care': '헬스케어', 'Financials': '금융',
    'Consumer Discretionary': '경기소비재', 'Communication Services': '커뮤니케이션',
    'Industrials': '산업재', 'Consumer Staples': '필수소비재', 'Energy': '에너지',
    'Utilities': '유틸리티', 'Real Estate': '부동산', 'Materials': '소재',
}


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': '*/*'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8')


class _Constituents(HTMLParser):
    """id="constituents" 표의 행을 셀 텍스트 목록으로 모은다."""

    def __init__(self):
        super().__init__()
        self.depth = 0          # >0 이면 대상 표 안
        self.rows, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'table':
            if self.depth:
                self.depth += 1
            elif a.get('id') == 'constituents':
                self.depth = 1
        elif self.depth == 1 and tag == 'tr':
            self.row = []
        elif self.depth == 1 and tag in ('td', 'th') and self.row is not None:
            self.cell = []

    def handle_endtag(self, tag):
        if tag == 'table' and self.depth:
            self.depth -= 1
        elif self.depth == 1 and tag in ('td', 'th') and self.cell is not None:
            self.row.append(' '.join(''.join(self.cell).split()))
            self.cell = None
        elif self.depth == 1 and tag == 'tr' and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def parse_members(html):
    p = _Constituents()
    p.feed(html)
    if not p.rows:
        raise ValueError('구성 종목 표를 찾지 못함')
    head = [h.lower() for h in p.rows[0]]
    col = lambda *names: next(i for i, h in enumerate(head) if any(n in h for n in names))
    i_sym, i_name, i_sector = col('symbol'), col('security'), col('gics sector')
    i_sub = next((i for i, h in enumerate(head) if 'sub-industry' in h), None)
    out = []
    for r in p.rows[1:]:
        if len(r) <= max(i_sym, i_name, i_sector):
            continue
        sym = r[i_sym].strip()
        if not sym:
            continue
        out.append({'ticker': sym, 'name': r[i_name], 'sector': r[i_sector],
                    'subIndustry': r[i_sub] if i_sub is not None and i_sub < len(r) else ''})
    if not 480 <= len(out) <= 520:
        raise ValueError(f'구성 종목 수 이상: {len(out)}')
    return out


def load_members(root, now, fetch=True):
    cache = read_json(root / MEMBERS, {})
    fresh = cache.get('members') and cache.get('updatedAt') and \
        now - datetime.fromisoformat(cache['updatedAt']) < timedelta(days=7)
    if fresh or not fetch:
        return cache.get('members') or []
    try:
        members = parse_members(get(WIKI))
        write_json(root / MEMBERS, {'updatedAt': now.isoformat(), 'source': WIKI, 'members': members})
        return members
    except Exception as exc:  # noqa: BLE001 - 지난 목록으로 계속
        print(f'S&P 500 구성 종목 갱신 실패 · 지난 목록 사용: {type(exc).__name__}: {exc}', flush=True)
        return cache.get('members') or []


def yahoo_symbol(t):
    return t.replace('.', '-')


def fetch_spark(symbols):
    """{원래 티커: (마지막 거래일, 종가, 전일 종가)}"""
    out, ny = {}, ZoneInfo('America/New_York')
    for k in range(0, len(symbols), 20):
        chunk = symbols[k:k + 20]
        q = urllib.parse.urlencode({'symbols': ','.join(yahoo_symbol(s) for s in chunk), 'range': '5d', 'interval': '1d'})
        data = None
        for host in ('query1.finance.yahoo.com', 'query2.finance.yahoo.com'):
            try:
                data = json.loads(get(f'https://{host}/v7/finance/spark?{q}'))
                break
            except Exception as exc:  # noqa: BLE001
                print(f'spark {k // 20 + 1}번째 묶음 {host} 실패: {type(exc).__name__}', flush=True)
        if not data:
            continue
        back = {yahoo_symbol(s): s for s in chunk}
        for res in (data.get('spark') or {}).get('result') or []:
            try:
                r = res['response'][0]
                ts = r.get('timestamp') or []
                closes = (r['indicators']['quote'][0].get('close') or [])
                pairs = [(t, c) for t, c in zip(ts, closes) if isinstance(c, (int, float)) and c > 0]
                if len(pairs) < 2:
                    continue
                (t1, c1), (_, c0) = pairs[-1], pairs[-2]
                day = datetime.fromtimestamp(t1, tz=timezone.utc).astimezone(ny).date().isoformat()
                out[back.get(res.get('symbol'), res.get('symbol'))] = (day, c1, c0)
            except (KeyError, IndexError, TypeError):
                continue
        time.sleep(0.25)
    return out


def build(root=None, now=None, fetch=True, prices=None):
    root = Path(root or ROOT)
    now = now or datetime.now(timezone.utc)
    members = load_members(root, now, fetch)
    if not members:
        raise RuntimeError('S&P 500 구성 종목 없음')
    prices = prices if prices is not None else fetch_spark([m['ticker'] for m in members])
    days = {}
    for d, _, _ in prices.values():
        days[d] = days.get(d, 0) + 1
    if not days:
        raise RuntimeError('S&P 500 시세를 받지 못함')
    asof = max(days, key=lambda d: (days[d], d))
    rows = []
    for m in members:
        p = prices.get(m['ticker'])
        if not p or p[0] != asof:
            continue
        _, close, prev = p
        rows.append({'ticker': m['ticker'], 'name': m['name'], 'sector': SECTOR_KO.get(m['sector'], m['sector']),
                     'subIndustry': m.get('subIndustry', ''), 'close': round(close, 2),
                     'changePct': round((close / prev - 1) * 100, 3) + 0.0})
    if len(rows) < MIN_PRICED:
        raise RuntimeError(f'S&P 500 시세 부족: {len(rows)}종목 · 이전 결과 유지')
    rows.sort(key=lambda r: r['changePct'], reverse=True)
    up = sum(r['changePct'] > 0 for r in rows)
    result = {
        'schemaVersion': 1, 'generatedAt': now.isoformat(), 'asOf': asof,
        'count': len(rows), 'members': len(members), 'advancers': up, 'decliners': sum(r['changePct'] < 0 for r in rows),
        'gainers': rows[:TOP], 'losers': list(reversed(rows[-TOP:])),
        'source': 'Yahoo Finance 종가 · 구성 종목은 위키백과 S&P 500 목록(GICS 섹터)',
    }
    write_json(root / OUTPUT, result)
    print(f'S&P 500 상승·하락 상위 저장 · {asof} · {len(rows)}/{len(members)}종목', flush=True)
    return result


if __name__ == '__main__':
    try:
        build(fetch=os.getenv('WAMO_OFFLINE') != '1')
    except Exception as exc:  # noqa: BLE001
        print(f'S&P 500 상승·하락 상위 미완료: {exc}', flush=True)
        sys.exit(1)
