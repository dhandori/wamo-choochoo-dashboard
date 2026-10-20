"""일본·대만 종목 목록·업종·시가총액 출처 점검 (GitHub 서버 1회 실행). 결과는 실행 요약 알림으로만 남긴다."""
import json
import re
import time
import urllib.request

UA = {'User-Agent': 'Mozilla/5.0 (WAMO asia probe)', 'Accept': 'application/json,text/html,*/*'}


def note(level, text):
    print(f'::{level}::{text[:3500]}', flush=True)


def get(url, timeout=30):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b''
    except Exception as e:  # noqa: BLE001
        return type(e).__name__, b''


# 대만 상장사 기본정보 · 당일시세 필드
for label, url in [('TWSE 기본정보', 'https://openapi.twse.com.tw/v1/opendata/t187ap03_L'),
                   ('TWSE 당일시세', 'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL'),
                   ('TWSE 업종별 시가총액', 'https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL')]:
    code, body = get(url)
    if code == 200:
        data = json.loads(body)
        tsmc = next((x for x in data if any(str(v) == '2330' for v in x.values())), data[0])
        note('notice', f'{label}: {len(data)}개 · 2330 = {json.dumps(tsmc, ensure_ascii=False)}')
    else:
        note('error', f'{label}: HTTP {code}')

# 대만 장외(TPEx) 종목 목록 - TWSE ISIN 사이트 (big5)
code, body = get('https://isin.twse.com.tw/isin/C_public.jsp?strMode=4', timeout=40)
text = body.decode('big5', 'replace') if body else ''
rows = re.findall(r'<td bgcolor=#FAFAD2>(\d{4})　([^<]+)</td>', text)
note('notice', f'TPEx 목록(ISIN): HTTP {code} · {len(body)} bytes · 4자리 종목 {len(rows)}개 · 예 {rows[:5]}')
for url in ['https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&o=json',
            'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes']:
    code, body = get(url)
    note('notice', f'TPEx 다른 주소 {url[:70]}: HTTP {code} · {len(body)} bytes')

# 일본 종목 목록
code, body = get('https://www.jpx.co.jp/markets/statistics-equities/misc/01.html')
links = re.findall(r'href="([^"]+data_j\.xls[^"]*)"', body.decode('utf-8', 'replace')) if body else []
note('notice', f'JPX 목록 안내 페이지: HTTP {code} · 파일 링크 {links}')
if links:
    link = links[0] if links[0].startswith('http') else 'https://www.jpx.co.jp' + links[0]
    code, body = get(link, timeout=60)
    note('notice', f'JPX 목록 파일: HTTP {code} · {len(body)} bytes · 앞부분 {body[:8]!r}')

# 네이버 해외주식 (일본)
code, body = get('https://api.stock.naver.com/stock/7203.T/basic')
if code == 200:
    d = json.loads(body)
    note('notice', '네이버 7203.T 필드: ' + json.dumps({k: (v if not isinstance(v, (list, dict)) else type(v).__name__) for k, v in d.items()}, ensure_ascii=False))
for url in ['https://api.stock.naver.com/stock/exchange/TYO/marketValue?page=1&pageSize=5',
            'https://api.stock.naver.com/stock/exchange/TSE/marketValue?page=1&pageSize=5',
            'https://api.stock.naver.com/stock/exchange/TAI/marketValue?page=1&pageSize=5',
            'https://api.stock.naver.com/stock/exchange/TWO/marketValue?page=1&pageSize=5']:
    code, body = get(url)
    sample = ''
    if code == 200:
        try:
            d = json.loads(body)
            stocks = d.get('stocks') if isinstance(d, dict) else d
            sample = f" · 전체 {d.get('totalCount') if isinstance(d, dict) else '?'} · 첫 종목 {json.dumps(stocks[0], ensure_ascii=False)[:900] if stocks else '-'}"
        except Exception as e:  # noqa: BLE001
            sample = f' · 해석 실패 {e}'
    note('notice', f'네이버 순위 {url.split("/exchange/")[1][:30]}: HTTP {code}{sample}')
