"""Optional KIS read-only quotes and estimate endpoint availability.

Official schema: koreainvestment/open-trading-api/examples_llm/domestic_stock.
Secrets and bearer tokens stay in process memory; only selected quote fields are saved.
"""
from datetime import datetime, timedelta
from pathlib import Path
from functools import lru_cache
import math
import json
import io
import os
import re
import time
import urllib.request
import zipfile
import requests
from wamo_runtime import KST, write_json

BASE = 'https://openapi.koreainvestment.com:9443'


def kospi200_master_members():
    """Read KIS's public symbol master to cross-check a non-200 KRX count.

    Field layout from the official stocks_info/kis_kospi_code_mst.py.
    This public reference file requires no account key and is not a REST login.
    """
    url = 'https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip'
    with urllib.request.urlopen(url, timeout=25) as response:
        raw = response.read()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        text = archive.read('kospi_code.mst').decode('cp949')
    members = set()
    for row in text.splitlines(keepends=True):
        if len(row) <= 249:
            continue
        head, tail = row[:-228], row[-228:]
        code = head[:9].strip()
        if tail[:2] == 'ST' and tail[18:19] in '123456789AB' and re.fullmatch(r'[0-9A-Z]{6}', code):
            members.add(code)
    if len(members) < 180:
        raise RuntimeError('한국투자 종목 마스터 형식 또는 구성 검증 실패')
    return members


def parse_us_master(text, exchange):
    records = {}
    for line in text.splitlines():
        parts = [v.strip() for v in line.split('\t')]
        if len(parts) < 10 or parts[2].upper() != exchange:
            continue
        symbol = parts[4].upper()
        if not re.fullmatch(r'[A-Z0-9][A-Z0-9./-]{0,14}', symbol):
            continue
        records[symbol.replace('.', '-').replace('/', '-')] = {'symbol':symbol, 'exchange':exchange}
    return records


@lru_cache(maxsize=1)
def us_master_records():
    records = {}
    for exchange in ('NAS', 'NYS', 'AMS'):
        name = exchange.lower() + 'mst.cod'
        with urllib.request.urlopen('https://new.real.download.dws.co.kr/common/master/' + name + '.zip', timeout=15) as response:
            raw = response.read()
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            matches = [entry for entry in archive.namelist() if Path(entry).name.lower() == name]
            if len(matches) != 1:
                raise RuntimeError('MASTER_FILE_UNVERIFIED')
            rows = parse_us_master(archive.read(matches[0]).decode('cp949'), exchange)
        if len(rows) < 50:
            raise RuntimeError('MASTER_FORMAT_UNVERIFIED')
        records.update(rows)
    return records


def number(value):
    try:
        result = float(str(value).replace(',', ''))
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


class KIS:
    def __init__(self):
        self.key = os.getenv('KIS_APP_KEY', '').strip()
        self.secret = (os.getenv('KIS_APP_SEC') or os.getenv('KIS_APP_SECRET') or '').strip()
        self.token = None
        self.session = requests.Session()
        self.last = 0

    @property
    def configured(self):
        return bool(self.key and self.secret)

    def authenticate(self):
        response = self.session.post(BASE + '/oauth2/tokenP', json={
            'grant_type': 'client_credentials', 'appkey': self.key, 'appsecret': self.secret}, timeout=15)
        if response.status_code != 200:
            raise RuntimeError(f'KIS 인증 HTTP {response.status_code}')
        self.token = response.json().get('access_token')
        if not self.token:
            raise RuntimeError('KIS 인증 응답에 접근토큰 없음')

    def get(self, path, transaction, params):
        if not self.token:
            self.authenticate()
        for attempt in range(3):
            # Conservative spacing also leaves headroom for other uses of the key.
            time.sleep(max(0, 2.0 - (time.monotonic() - self.last)))
            try:
                response = self.session.get(BASE + path, params=params, headers={
                    'authorization': 'Bearer ' + self.token, 'appkey': self.key,
                    'appsecret': self.secret, 'tr_id': transaction, 'custtype': 'P',
                    'content-type': 'application/json; charset=utf-8'}, timeout=15)
                self.last = time.monotonic()
                try:
                    data = response.json()
                except ValueError:
                    data = {}
                if response.status_code == 200 and str(data.get('rt_cd')) == '0':
                    return data
                code = str(data.get('msg_cd', ''))
                code = code if re.fullmatch(r'[A-Za-z0-9_-]{1,30}', code) else 'UNKNOWN'
                transient = response.status_code in (429, 500, 502, 503, 504) or code == 'EGW00201'
                if transient and attempt < 2:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise RuntimeError(f'HTTP_{response.status_code}_{code}')
            except requests.RequestException:
                self.last = time.monotonic()
                if attempt == 2:
                    raise RuntimeError('NETWORK_TIMEOUT') from None
                time.sleep(2 ** (attempt + 1))
        raise RuntimeError('RETRY_EXHAUSTED')

    def quote(self, code):
        return self.get('/uapi/domestic-stock/v1/quotations/inquire-price', 'FHKST01010100',
                        {'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': code}).get('output') or {}

    def overseas_quote(self, symbol, exchange):
        exchanges = {'NASDAQ':'NAS', 'NYSE':'NYS', 'NYSE AMERICAN':'AMS', 'AMEX':'AMS',
                     'NAS':'NAS', 'NYS':'NYS', 'AMS':'AMS'}
        primary = exchanges.get(str(exchange).upper())
        # The existing screener sometimes guesses an exchange when its field is empty.
        for excd in dict.fromkeys([primary, 'NAS', 'NYS', 'AMS']):
            if not excd:
                continue
            data = self.get('/uapi/overseas-price/v1/quotations/price-detail', 'HHDFS76200200',
                            {'AUTH':'', 'EXCD':excd, 'SYMB':symbol}).get('output') or {}
            if (number(data.get('last')) or 0) > 0:
                return data, excd
        raise RuntimeError('EMPTY_PRICE')

    def estimate(self, code):
        return self.get('/uapi/domestic-stock/v1/quotations/estimate-perform', 'HHKST668300C0', {'SHT_CD': code})

    def estimate_available(self, code):
        return public_estimate(self.estimate(code), datetime.now(KST).isoformat())['status'] == 'RESPONSE_AVAILABLE_UNMAPPED'

def public_estimate(data, checked_at):
    """Keep only documented public response fields, without inferring row units."""
    result = {'checkedAt': checked_at, 'status': 'EMPTY', 'periods': [],
              'mappingStatus': 'UNVERIFIED', 'forwardMetricsConnected': False}
    for key in ('output2', 'output3'):
        rows = data.get(key) or []
        if isinstance(rows, dict):
            rows = [rows]
        result[key] = [{f'data{i}': number(row.get(f'data{i}')) for i in range(1, 6)}
                       for row in rows if isinstance(row, dict)]
    periods = data.get('output4') or []
    if isinstance(periods, dict):
        periods = [periods]
    result['periods'] = [str(row.get('dt', '')) for row in periods if isinstance(row, dict)
                         and re.fullmatch(r'\d{4}\.\d{2}E?', str(row.get('dt', '')))]
    # An output1 header alone, or all-zero placeholders, is not estimate coverage.
    useful = any(v not in (None, 0) for key in ('output2', 'output3')
                 for row in result[key] for v in row.values())
    if useful and result['periods']:
        result['status'] = 'RESPONSE_AVAILABLE_UNMAPPED'
    header = data.get('output1') or {}
    if isinstance(header, list):
        header = header[0] if header else {}
    vendor_date = str(header.get('estdate', '')) if isinstance(header, dict) else ''
    # Preserve a date only if it is actually formatted as a date.
    if re.fullmatch(r'\d{8}|\d{4}[-.]\d{2}[-.]\d{2}', vendor_date):
        result['vendorDateRaw'] = vendor_date
    return result


def read_cache(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def recent(record, now, days):
    try:
        age = now - datetime.fromisoformat(record['checkedAt'])
        return timedelta(0) <= age <= timedelta(days=days)
    except (ValueError, KeyError, TypeError):
        return False


def enrich(stocks):
    root = Path(__file__).resolve().parent
    api = KIS()
    # Never retain a prior payload's "LIVE" stamp when no new request succeeded.
    for stock in stocks:
        stock.pop('kisQuote', None)
        stock.pop('kisEstimate', None)
    if not api.configured:
        return {'status': 'NOT_CONFIGURED', 'connected': False,
                'message': '한국투자 API 미연결 · GitHub Secret KIS_APP_KEY / KIS_APP_SEC 확인 필요'}
    try:
        api.authenticate()
    except (requests.RequestException, RuntimeError, ValueError):
        return {'status': 'AUTH_FAILED', 'connected': False,
                'message': '한국투자 API 인증 실패 · 기존 가격 경로 유지'}
    now = datetime.now(KST)
    cache_path = root / 'wamo_kis_cache.json'
    history_path = root / 'wamo_kis_estimate_history.json'
    records, history = read_cache(cache_path), read_cache(history_path)
    targets = sorted(stocks, key=lambda s: (bool(s.get('trendTemplate')), s.get('score') or 0), reverse=True)[:20]
    count = cached_count = errors = consecutive = estimate_errors = estimate_count = 0
    diagnostics = []
    started = time.monotonic()
    for stock in targets:
        code = str(stock.get('stock_code') or stock.get('ticker', '').split('.')[0]).upper()
        old = records.get(code) or {}
        if time.monotonic() - started > 240 or consecutive >= 5:
            stock['kisQuote'] = {'status': 'SKIPPED', 'message': '일시적인 조회 장애로 다음 갱신에서 재시도'}
            continue
        if not re.fullmatch(r'[0-9A-Z]{6}', code):
            errors += 1
            stock['kisQuote'] = {'status': 'FAILED', 'message': '종목코드 확인 필요'}
            continue
        try:
            quote = api.quote(code)
            price = number(quote.get('stck_prpr'))
            if not price or price <= 0:
                raise RuntimeError('EMPTY_PRICE')
            record = {'source': '한국투자증권 Open API', 'checkedAt': datetime.now(KST).isoformat(timespec='seconds'),
                      'status': 'LIVE', 'currency': 'KRW', 'industry': str(quote.get('bstp_kor_isnm') or '').strip(),
                      'high52': number(quote.get('w52_hgpr')), 'low52': number(quote.get('w52_lwpr')),
                      'price': price, 'per': number(quote.get('per')), 'eps': number(quote.get('eps')),
                      'pbr': number(quote.get('pbr')), 'label': '현재가 조회 응답 · Forward 지표 아님'}
            # Negative/zero earnings do not support a meaningful positive valuation multiple.
            if record['eps'] is not None and record['eps'] <= 0:
                record['per'] = None
            for field in ('per', 'pbr'):
                if record[field] is not None and record[field] <= 0:
                    record[field] = None
            stock['kisQuote'] = record
            records[code] = dict(old, **record)
            count += 1
            consecutive = 0
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            errors += 1
            consecutive += 1
            safe_code = str(exc) if re.fullmatch(r'[A-Z0-9_]{1,60}', str(exc)) else 'QUERY_FAILED'
            diagnostics.append({'code': code, 'error': safe_code})
            if old.get('price') and recent(old, now, 3):
                stock['kisQuote'] = dict(old, status='CACHED', message='이번 조회 실패 · 이전 확인값')
                cached_count += 1
            else:
                stock['kisQuote'] = {'status': 'FAILED', 'message': '현재가 조회 실패'}
        # Independent optional endpoint: a quote failure must not mislabel estimate availability.
        estimate = old.get('estimate') or {}
        if not recent(estimate, now, 3):
            try:
                estimate = public_estimate(api.estimate(code), datetime.now(KST).isoformat(timespec='seconds'))
                records.setdefault(code, {})['estimate'] = estimate
                day = now.date().isoformat()
                history.setdefault(code, {})[day] = estimate
            except (requests.RequestException, RuntimeError, ValueError):
                estimate_errors += 1
                estimate = {'status': 'UNAVAILABLE', 'forwardMetricsConnected': False}
        stock['kisEstimate'] = {k: v for k, v in estimate.items() if k not in ('output2', 'output3')}
        if estimate.get('status') == 'RESPONSE_AVAILABLE_UNMAPPED':
            estimate_count += 1
    cutoff = (now - timedelta(days=100)).date().isoformat()
    history = {code: {day: item for day, item in rows.items() if day >= cutoff}
               for code, rows in history.items() if isinstance(rows, dict)}
    write_json(cache_path, records)
    write_json(history_path, {code: rows for code, rows in history.items() if rows})
    skipped = len(targets) - count - errors
    return {'status': 'LIVE' if count == len(targets) and count else 'PARTIAL' if count else 'FAILED',
            'connected': bool(count), 'quoteCount': count, 'targetCount': len(targets), 'cachedCount': cached_count,
            'errorCount': errors, 'skippedCount': skipped, 'errors': diagnostics,
            'estimateEndpoint': 'RESPONSE_AVAILABLE_UNMAPPED' if estimate_count else 'UNAVAILABLE',
            'estimateCount': estimate_count, 'estimateErrorCount': estimate_errors,
            'forwardMetricsConnected': False,
            'message': f'한국투자 시세 {count}/{len(targets)}종목 새로 확인 · 이전값 {cached_count} · 실패 {errors} · 미조회 {skipped} · 추정실적 응답 {estimate_count}종목(단위·항목 검증 대기)'}


def enrich_us(stocks):
    """Candidate US quotes and provider industries; no inferred forecasts or SEC data."""
    for stock in stocks:
        stock.pop('kisQuote', None)
    api = KIS()
    if not api.configured:
        return {'status':'NOT_CONFIGURED', 'connected':False, 'message':'미국 한국투자 API 미연결'}
    try:
        api.authenticate()
    except (requests.RequestException, RuntimeError, ValueError):
        return {'status':'AUTH_FAILED', 'connected':False, 'message':'미국 한국투자 API 인증 실패'}
    try:
        symbols = us_master_records()
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile):
        symbols = {}
    path = Path(__file__).resolve().parent / 'wamo_kis_us_cache.json'
    cache = read_cache(path)
    now = datetime.now(KST)
    targets = sorted(stocks, key=lambda s:(bool(s.get('trendTemplate')), s.get('score') or 0), reverse=True)[:20]
    count = errors = cached_count = skipped = consecutive = 0
    diagnostics = []
    started = time.monotonic()
    for stock in targets:
        symbol = str(stock.get('ticker') or '').upper()
        old = cache.get(symbol) or {}
        if time.monotonic() - started > 240 or consecutive >= 5:
            stock['kisQuote'] = {'status':'SKIPPED', 'message':'일시적인 조회 장애로 다음 갱신에서 재시도'}
            skipped += 1
            continue
        try:
            if not re.fullmatch(r'[A-Z0-9][A-Z0-9.-]{0,14}', symbol):
                raise RuntimeError('INVALID_SYMBOL')
            master = symbols.get(symbol) or {}
            quote, exchange = api.overseas_quote(master.get('symbol', symbol), master.get('exchange', stock.get('exchange')))
            currency = str(quote.get('curr') or '').strip()
            if currency != 'USD':
                raise RuntimeError('CURRENCY_UNVERIFIED')
            record = {'status':'LIVE', 'source':'한국투자증권 해외주식 현재가상세',
                      'checkedAt':datetime.now(KST).isoformat(timespec='seconds'),
                      'price':number(quote.get('last')), 'per':number(quote.get('perx')),
                      'eps':number(quote.get('epsx')), 'pbr':number(quote.get('pbrx')),
                      'currency':currency, 'exchange':exchange,
                      'industry':str(quote.get('e_icod') or '').strip(),
                      'high52':number(quote.get('h52p')), 'low52':number(quote.get('l52p')),
                      'label':'현재가상세 API 조회값 · Forward 지표 아님'}
            if not record['price'] or record['price'] <= 0:
                raise RuntimeError('EMPTY_PRICE')
            if record['eps'] is not None and record['eps'] <= 0:
                record['per'] = None
            for field in ('per','pbr','high52','low52'):
                if record[field] is not None and record[field] <= 0:
                    record[field] = None
            stock['kisQuote'] = record
            cache[symbol] = record
            count += 1
            consecutive = 0
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            errors += 1
            consecutive += 1
            code = str(exc) if re.fullmatch(r'[A-Z0-9_]{1,60}', str(exc)) else 'QUERY_FAILED'
            diagnostics.append({'code':symbol,'error':code})
            if old.get('price') and recent(old, now, 3):
                stock['kisQuote'] = dict(old, status='CACHED', message='이번 조회 실패 · 이전 확인값')
                cached_count += 1
            else:
                stock['kisQuote'] = {'status':'FAILED','message':'미국 현재가 조회 실패'}
    write_json(path, cache)
    industries = sum(bool(s.get('kisQuote',{}).get('industry')) and s['kisQuote']['status']=='LIVE' for s in targets)
    return {'status':'LIVE' if count == len(targets) and count else 'PARTIAL' if count else 'FAILED',
            'connected':bool(count),'quoteCount':count,'targetCount':len(targets),'errorCount':errors,
            'cachedCount':cached_count,'skippedCount':skipped,'errors':diagnostics,'industryCount':industries,
            'forwardMetricsConnected':False,
            'message':f'미국 한국투자 시세 {count}/{len(targets)}종목 신규 확인 · 업종 {industries} · 이전값 {cached_count} · 실패 {errors} · 미조회 {skipped}'}
