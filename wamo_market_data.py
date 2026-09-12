"""Korean universe provider boundary; no files, signals or UI are changed here."""
import json
import re


def fetch_naver_universe(http_text, sosok, suffix, market):
    expected = {'KOSPI': (0, '.KS'), 'KOSDAQ': (1, '.KQ')}
    if expected.get(market) != (sosok, suffix):
        raise ValueError('시장과 티커 접미사 불일치')
    rows, seen, total = [], set(), None
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
            raise RuntimeError(f'{market} 종목 목록 메타데이터 불일치')
        total = count
        stocks = payload.get('stocks')
        if not isinstance(stocks, list) or len(stocks) != min(100, total - len(seen)):
            raise RuntimeError(f'{market} 종목 목록 페이지 누락')
        for item in stocks:
            if not isinstance(item, dict):
                raise RuntimeError(f'{market} 종목 행 응답 형식 변경')
            code, name = item.get('itemCode'), item.get('stockName')
            cap = item.get('marketValueRaw')
            # Raw is KRW; marketValue is rounded 억원 and must not substitute for it.
            if (not isinstance(code, str) or not re.fullmatch(r'[0-9A-Z]{6}', code)
                    or code in seen or not isinstance(name, str) or not name.strip()
                    or str(item.get('sosok')) != str(sosok)
                    or not isinstance(cap, str) or not re.fullmatch(r'[0-9]+', cap)
                    or int(cap) <= 0):
                raise RuntimeError(f'{market} 종목 식별자·시장·시가총액 검증 실패')
            seen.add(code)
            name = name.strip()
            compact = name.replace(' ', '')
            # Preserve the existing universe exclusion rules; ETF/REIT handling
            # remains with the caller's classify_instrument stage.
            if re.search(r'(스팩|SPAC)$', compact, flags=re.I):
                continue
            if re.search(r'(?:\d*우[A-Z\d]*(?:\(전환\))?)$', compact):
                continue
            rows.append(dict(ticker=code + suffix, stock_code=code, name=name,
                             market='KOREA', krx_market=market,
                             market_cap_krw=int(cap)))
        if len(seen) == total:
            if len(rows) < 100:
                raise RuntimeError(f'{market} 적격 종목 목록 부족')
            return rows
    raise RuntimeError(f'{market} 종목 목록 페이지 한도 초과')
