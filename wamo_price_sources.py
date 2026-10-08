"""Independent daily-price sources, strict validation and bounded outage recovery.

Never splice differently adjusted series. Nasdaq/Naver supply their entire
available window; that window is not evidence of an all-time high. Google HTML
is a small diagnostic cross-check only, never a replacement for OHLCV history.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


@dataclass
class PriceHistory:
    rows: list
    source: str
    provider: str
    full_history: bool = False
    adjustment: str = 'PROVIDER_REPORTED'


def validate_history(rows, expected):
    """Reject corrupt/stale data, and exclude a newer unfinished session."""
    date.fromisoformat(expected)
    checked, seen = [], set()
    for row in rows:
        day = row.get('date', '')
        if date.fromisoformat(day).isoformat() != day or day in seen:
            raise ValueError('invalid or duplicate trading date')
        seen.add(day)
        if day > expected:
            continue
        values = {key: float(row[key]) for key in ('close', 'high', 'low', 'volume')}
        if not all(math.isfinite(v) for v in values.values()):
            raise ValueError('non-finite OHLCV')
        if not (0 < values['low'] <= values['close'] * 1.000001
                and values['high'] * 1.000001 >= values['close']
                and values['high'] >= values['low'] and values['volume'] >= 0):
            raise ValueError('invalid OHLCV bounds')
        checked.append(dict(date=day, **values))
    checked.sort(key=lambda r: r['date'])
    if not checked or checked[-1]['date'] != expected:
        raise ValueError(f"기대 거래일 {expected}, 응답 가격일 {checked[-1]['date'] if checked else '없음'}")
    if len(checked) < 60:
        raise ValueError(f'가격 이력 부족: {len(checked)}일')
    return checked


def get_text(url):
    for attempt in range(2):
        try:
            request = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (WAMO daily market dashboard)',
                'Accept': 'application/json,text/html,*/*',
                'Accept-Language': 'en-US,en;q=0.9',
            })
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.read().decode('utf-8')
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt:
                raise RuntimeError(f'HTTP_{exc.code}') from None
        except (OSError, UnicodeError):
            if attempt:
                raise RuntimeError('NETWORK') from None
        time.sleep(0.5)


def get_json(url):
    return json.loads(get_text(url))


def canonical_symbol(symbol):
    return re.sub(r'[-./]', '', str(symbol).upper())


def number(value):
    return float(re.sub(r'[$,\s]', '', str(value)))


def parse_nasdaq(payload, symbol, expected):
    data = payload.get('data') or {}
    if canonical_symbol(data.get('symbol')) != canonical_symbol(symbol):
        raise ValueError('Nasdaq symbol mismatch')
    source = (data.get('tradesTable') or {}).get('rows') or []
    if not source or int(data.get('totalRecords', -1)) != len(source):
        raise ValueError('Nasdaq history incomplete')
    rows = [dict(date=datetime.strptime(r['date'], '%m/%d/%Y').date().isoformat(),
                 **{key: number(r[key]) for key in ('close', 'high', 'low', 'volume')})
            for r in source]
    return validate_history(rows, expected)


def fetch_nasdaq(meta, expected):
    symbol = meta.get('displayTicker') or meta['ticker'].replace('-', '.')
    start = date.fromisoformat(expected) - timedelta(days=3*370)
    query = urllib.parse.urlencode(dict(assetclass='stocks', fromdate=start.isoformat(),
                                       todate=expected, limit=1500))
    url = f'https://api.nasdaq.com/api/quote/{urllib.parse.quote(symbol, safe="")}/historical?{query}'
    return PriceHistory(parse_nasdaq(get_json(url), symbol, expected),
                        'Nasdaq', 'api.nasdaq.com', False)


def fetch_naver_us(meta, expected):
    symbol = meta.get('displayTicker') or meta['ticker']
    # Only unambiguous ordinary symbols; class-share aliases need an explicit map.
    if not re.fullmatch(r'[A-Z]{1,6}', symbol):
        raise RuntimeError('NAVER_UNMAPPED_SYMBOL')
    suffixes = ('.O', '', '.A') if meta.get('exchange') == 'NASDAQ' else ('', '.O', '.A')
    errors = []
    for suffix in suffixes:
        code = symbol + suffix
        try:
            basic = get_json(f'https://api.stock.naver.com/stock/{code}/basic')
            if (basic.get('symbolCode') != symbol or basic.get('reutersCode') != code
                    or (basic.get('stockExchangeType') or {}).get('nationCode') != 'USA'
                    or basic.get('stockEndType') != 'stock'):
                raise ValueError('NAVER symbol/market mismatch')
            end = date.fromisoformat(expected) + timedelta(days=1)
            start = date.fromisoformat(expected) - timedelta(days=3*370)
            query = urllib.parse.urlencode(dict(startDateTime=start.strftime('%Y%m%d')+'0000',
                                                endDateTime=end.strftime('%Y%m%d')+'0000'))
            payload = get_json(f'https://api.stock.naver.com/chart/foreign/item/{code}/day?{query}')
            rows = [dict(date=datetime.strptime(r['localDate'], '%Y%m%d').date().isoformat(),
                         close=float(r['closePrice']), high=float(r['highPrice']),
                         low=float(r['lowPrice']), volume=float(r['accumulatedTradingVolume']))
                    for r in payload]
            return PriceHistory(validate_history(rows, expected), 'NAVER Finance',
                                'api.stock.naver.com', False)
        except (RuntimeError, ValueError, TypeError, KeyError) as exc:
            errors.append(str(exc))
            # An outage is shared by all aliases; do not multiply network timeouts.
            if str(exc) == 'NETWORK' or re.match(r'HTTP_(429|5\d\d)', str(exc)):
                break
    raise RuntimeError('NAVER: ' + ' / '.join(errors))


class PriceRouter:
    """Thread-safe per-run circuit breakers; primary recovers after a cooldown."""
    def __init__(self, fetchers, failure_limit=8, cooldown=60, clock=time.monotonic):
        self.fetchers = fetchers
        self.failure_limit, self.cooldown, self.clock = failure_limit, cooldown, clock
        self.lock = threading.Lock()
        self.health = {name: dict(attempts=0, successes=0, failures=0, skipped=0,
                                 consecutive=0, opened=None, probing=False)
                       for name in fetchers}

    def fetch(self, meta, expected):
        errors = []
        for name, fetcher in self.fetchers.items():
            with self.lock:
                health = self.health[name]
                if health['opened'] is not None:
                    if health['probing'] or self.clock() - health['opened'] < self.cooldown:
                        health['skipped'] += 1
                        errors.append(name + ': CIRCUIT_OPEN')
                        continue
                    health['probing'] = True
                health['attempts'] += 1
            try:
                result = fetcher(meta, expected)
                result.rows = validate_history(result.rows, expected)
            except Exception as exc:
                with self.lock:
                    health['failures'] += 1
                    health['consecutive'] += 1
                    health['probing'] = False
                    if health['consecutive'] >= self.failure_limit:
                        health['opened'] = self.clock()
                errors.append(name + ': ' + str(exc))
                continue
            with self.lock:
                health['successes'] += 1
                health['consecutive'] = 0
                health['opened'] = None
                health['probing'] = False
            return result
        raise RuntimeError(' || '.join(errors))

    def summary(self):
        with self.lock:
            return {'sources': {name: {key: val for key, val in health.items()
                                      if key in ('attempts', 'successes', 'failures', 'skipped')}
                                for name, health in self.health.items()}}


def us_router(yahoo_fetch):
    def yahoo(meta, expected):
        rows, host = yahoo_fetch(meta['ticker'], years=3, expected_date=expected)
        return PriceHistory(rows, 'Yahoo Finance', host, False, 'SPLIT_AND_DIVIDEND_ADJUSTED')
    return PriceRouter({'Yahoo Finance': yahoo, 'Nasdaq': fetch_nasdaq,
                        'NAVER Finance': fetch_naver_us})


def parse_google_close(html, expected):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    price = soup.select_one('.N6SYTe')
    stamp = re.search(r'Closed:\s*([A-Za-z]{3})\s+(\d{1,2}),\s*([\d:]+\s*[AP]M)\s+GMT([+-]\d+)\s*[·⋅]\s*USD', soup.get_text(' ', strip=True))
    if price is None or stamp is None:
        raise ValueError('Google regular-close markup unavailable')
    wanted = date.fromisoformat(expected)
    month = datetime.strptime(stamp[1], '%b').month
    if (month, int(stamp[2])) != (wanted.month, wanted.day):
        raise ValueError('Google close date mismatch')
    value = number(price.get_text('', strip=True))
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Google close price invalid')
    return dict(close=value, date=expected, source='Google Finance HTML',
                scope='DIAGNOSTIC_ONLY', yearVerified=False)


def google_close(symbol, exchange, expected):
    if not re.fullmatch(r'[A-Z0-9.\-]{1,12}', symbol) or exchange not in ('NASDAQ', 'NYSE', 'NYSEARCA', 'NYSEAMERICAN'):
        raise ValueError('unsupported Google symbol')
    url = 'https://www.google.com/finance/quote/' + urllib.parse.quote(symbol+':'+exchange, safe=':') + '?hl=en'
    return {**parse_google_close(get_text(url), expected), 'url': url}
