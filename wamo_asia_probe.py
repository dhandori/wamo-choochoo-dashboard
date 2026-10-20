"""일본·대만 시세 출처 점검 (GitHub 서버에서 1회 실행). 결과는 실행 요약 알림으로만 남긴다."""
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = {'User-Agent': 'Mozilla/5.0 (WAMO asia probe)', 'Accept': 'application/json,text/plain,*/*'}


def note(level, text):
    print(f'::{level}::{text}', flush=True)


def get(url, timeout=25):
    t = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            body = r.read()
            return r.status, body, time.time() - t
    except urllib.error.HTTPError as e:
        return e.code, b'', time.time() - t
    except Exception as e:  # noqa: BLE001
        return type(e).__name__, b'', time.time() - t


def yahoo(sym):
    now = int(time.time())
    code, body, dt = get(f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={now - 400 * 86400}&period2={now + 86400}&interval=1d')
    rows = 0
    if code == 200:
        try:
            rows = len(json.loads(body)['chart']['result'][0]['timestamp'])
        except Exception:  # noqa: BLE001
            rows = -1
    return sym, code, rows, round(dt, 2)


JP = '7203 6758 8306 9984 6861 8035 9983 4063 6501 7974 8058 6098 4519 8316 6902 7741 6367 9432 8001 4568 6981 7267 6273 6594 8766 4502 6503 2914 6954 7751 5108 8411 4901 6702 6762 3382 9433 8031 6857 6146'.split()
TW = '2330 2317 2454 2308 2382 3711 2412 2881 2882 1303 2891 2303 2886 3008 2357 1216 2002 2884 3045 5880 2892 2885 1301 2207 3034 2379 2395 6669 3231 2327 3017 2345 4938 2603 1326 2880 2912 6505 3661 2301'.split()

# 1) 한 종목씩 (야후)
for sym in ('7203.T', '2330.TW', '6488.TWO'):
    s, code, rows, dt = yahoo(sym)
    note('notice', f'야후 {s}: HTTP {code} · 일봉 {rows}개 · {dt}초')

# 2) 몰아서 받기: 일본 40 + 대만 40, 동시 8개
t0 = time.time()
with ThreadPoolExecutor(8) as pool:
    res = list(pool.map(yahoo, [s + '.T' for s in JP] + [s + '.TW' for s in TW]))
ok = sum(1 for r in res if r[1] == 200 and r[2] > 200)
codes = {}
for r in res:
    codes[str(r[1])] = codes.get(str(r[1]), 0) + 1
note('notice', f'야후 80종목 연속: 성공 {ok}/80 · 응답 {codes} · {round(time.time() - t0, 1)}초')

# 3) 거래소 공식 일괄 자료
for label, url in [
    ('대만 TWSE 전종목 당일시세', 'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL'),
    ('대만 TWSE 상장사 기본정보', 'https://openapi.twse.com.tw/v1/opendata/t187ap03_L'),
    ('대만 TPEx 장외 전종목 시세', 'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes'),
    ('일본 JPX 상장종목 목록(xls)', 'https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls'),
    ('네이버 해외 7203.T', 'https://api.stock.naver.com/stock/7203.T/basic'),
    ('네이버 해외 2330.TW', 'https://api.stock.naver.com/stock/2330.TW/basic'),
]:
    code, body, dt = get(url, timeout=40)
    extra = ''
    if code == 200 and body[:1] in (b'[', b'{'):
        try:
            data = json.loads(body)
            extra = f' · 항목 {len(data)}개'
        except Exception:  # noqa: BLE001
            pass
    note('notice', f'{label}: HTTP {code} · {len(body)} bytes{extra} · {round(dt, 1)}초')
