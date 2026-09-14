"""Korean universe provider boundary; validate complete identity coverage."""
import json
import re


def fetch_naver_universe(http_text, sosok, suffix, market):
    expected = {'KOSPI': (0, '.KS'), 'KOSDAQ': (1, '.KQ')}
    if expected.get(market) != (sosok, suffix):
        raise ValueError('시장과 티커 접미사 불일치')
    records, total = {}, None
    # Market-value order can change between requests. Reconcile at most two
    # complete sweeps, retaining strict identity, market, cap and count checks.
    for attempt in range(2):
        for number in range(1, 81):
            url = f'https://m.stock.naver.com/api/stocks/marketValue/{market}?page={number}&pageSize=100'
            payload = json.loads(http_text(url, encoding='utf-8'))
            if not isinstance(payload, dict):
                raise RuntimeError(f'{market} 종목 목록 응답 형식 변경')
            count = payload.get('totalCount')
            if (payload.get('stockListCategoryType') != market
                    or payload.get('page') != number or payload.get('pageSize') != 100
                    or type(count) is not int or not 100 <= count <= 8000
                    or (total is not None and total != count)):
                raise RuntimeError(f'{market} 종목 목록 메타데이터 불일치 · 페이지 {number}')
            total = count
            stocks = payload.get('stocks')
            if not isinstance(stocks, list) or len(stocks) != min(100, total - (number - 1) * 100):
                raise RuntimeError(f'{market} 종목 목록 페이지 누락 · 페이지 {number}')
            page_codes = set()
            for item in stocks:
                if not isinstance(item, dict):
                    raise RuntimeError(f'{market} 종목 행 응답 형식 변경')
                code, name = item.get('itemCode'), item.get('stockName')
                cap = item.get('marketValueRaw')
                if (not isinstance(code, str) or not re.fullmatch(r'[0-9A-Z]{6}', code)
                        or not isinstance(name, str) or not name.strip()
                        or str(item.get('sosok')) != str(sosok)):
                    raise RuntimeError(f'{market} 종목 식별자·시장 검증 실패 · 페이지 {number}')
                if code in page_codes:
                    raise RuntimeError(f'{market} 동일 페이지 중복 종목 · 페이지 {number} · 코드 {code}')
                if not isinstance(cap, str) or not re.fullmatch(r'[0-9]+', cap) or int(cap) <= 0:
                    raise RuntimeError(f'{market} 시가총액 검증 실패 · 페이지 {number} · 코드 {code}')
                page_codes.add(code)
                # These are listing/capitalization inputs, not price histories.
                # The latest valid occurrence supersedes the earlier one.
                records[code] = dict(ticker=code + suffix, stock_code=code, name=name.strip(),
                                     market='KOREA', krx_market=market, market_cap_krw=int(cap))
            if len(records) > total:
                raise RuntimeError(f'{market} 조회 도중 구성종목 변경 · 전체 종목 수 초과')
            if number * 100 >= total:
                break
        if len(records) == total:
            rows = []
            for item in records.values():
                compact = item['name'].replace(' ', '')
                if re.search(r'(스팩|SPAC)$', compact, flags=re.I):
                    continue
                if re.search(r'(?:\d*우[A-Z\d]*(?:\(전환\))?)$', compact):
                    continue
                rows.append(item)
            if len(rows) < 100:
                raise RuntimeError(f'{market} 적격 종목 목록 부족')
            return rows
        print(f'{market} 순위 변동 재확인 · 고유 종목 {len(records)}/{total} · 조회 {attempt + 1}/2', flush=True)
    raise RuntimeError(f'{market} 고유 종목 수 부족 {len(records)}/{total} · 2회 전체 조회 후 중단')
