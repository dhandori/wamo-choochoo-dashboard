"""Optional KIS read-only quotes and estimate endpoint availability.

Official schema: koreainvestment/open-trading-api/examples_llm/domestic_stock.
Secrets and bearer tokens stay in process memory; only selected quote fields are saved.
"""
from datetime import datetime
from pathlib import Path
import math
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
        time.sleep(max(0, 0.55 - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        response = self.session.get(BASE + path, params=params, headers={
            'authorization': 'Bearer ' + self.token, 'appkey': self.key,
            'appsecret': self.secret, 'tr_id': transaction, 'custtype': 'P'}, timeout=15)
        if response.status_code != 200:
            raise RuntimeError(f'KIS 조회 HTTP {response.status_code}')
        data = response.json()
        if str(data.get('rt_cd')) != '0':
            raise RuntimeError('KIS 조회 실패')
        return data

    def quote(self, code):
        return self.get('/uapi/domestic-stock/v1/quotations/inquire-price', 'FHKST01010100',
                        {'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': code}).get('output') or {}

    def estimate_available(self, code):
        data = self.get('/uapi/domestic-stock/v1/quotations/estimate-perform', 'HHKST668300C0', {'SHT_CD': code})
        # DATA1..5 row semantics need verification against real responses before
        # calling any number forward EPS, consensus or forward PER.
        return any(data.get(key) for key in ('output1', 'output2', 'output3'))


def enrich(stocks):
    api = KIS()
    if not api.configured:
        return {'status': 'NOT_CONFIGURED', 'connected': False,
                'message': '한국투자 API 미연결 · GitHub Secret KIS_APP_KEY / KIS_APP_SEC 확인 필요'}
    count, errors, estimates = 0, 0, 'NOT_CHECKED'
    records = {}
    try:
        api.authenticate()
    except (requests.RequestException, RuntimeError, ValueError):
        return {'status': 'AUTH_FAILED', 'connected': False, 'message': '한국투자 API 인증 실패 · 기존 가격 경로 유지'}
    targets = sorted(stocks, key=lambda s: (bool(s.get('trendTemplate')), s.get('score') or 0), reverse=True)[:20]
    for stock in targets:
        code = stock.get('stock_code') or stock.get('ticker', '').split('.')[0]
        try:
            quote = api.quote(code)
            price = number(quote.get('stck_prpr'))
            if not price or price <= 0:
                raise RuntimeError('KIS 가격 없음')
            record = {'source': '한국투자증권 Open API', 'checkedAt': datetime.now(KST).isoformat(timespec='seconds'),
                      'price': price, 'per': number(quote.get('per')), 'eps': number(quote.get('eps')),
                      'pbr': number(quote.get('pbr')), 'label': '현재가 조회 응답 · Forward 지표 아님'}
            stock['kisQuote'] = record
            records[code] = record
            count += 1
        except (requests.RequestException, RuntimeError, ValueError):
            errors += 1
            if errors >= 3:
                break
    if targets and count:
        try:
            code = targets[0].get('stock_code') or targets[0]['ticker'].split('.')[0]
            estimates = 'RESPONSE_AVAILABLE_UNMAPPED' if api.estimate_available(code) else 'EMPTY'
        except (requests.RequestException, RuntimeError, ValueError):
            estimates = 'UNAVAILABLE'
    write_json(Path(__file__).resolve().parent / 'wamo_kis_cache.json', records)
    return {'status': 'LIVE' if count and not errors else 'PARTIAL' if count else 'FAILED',
            'connected': bool(count), 'quoteCount': count, 'errorCount': errors,
            'estimateEndpoint': estimates, 'forwardMetricsConnected': False,
            'message': f'한국투자 현재가·PER·EPS {count}종목 확인 · 추정실적 필드 검증 전, Forward PER 미표시'}
