#!/usr/bin/env python3
# WAMO Market Radar - GitHub cloud updater
# Price/volume screening works without any API key.
# OpenDART official fundamentals/disclosures integrated through OPENDART_API_KEY.

from __future__ import annotations

import io
import json
import os
import zipfile
import math
import re
import statistics
import time
import subprocess
import sys
import importlib
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.html"
KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (WAMO-Market-Radar/16.0)"
MIN_MARKET_CAP = 1_000_000_000_000       # 1조원
MIN_AVG_VALUE_50D = 10_000_000_000       # 100억원
MAX_WORKERS = 10
DART_KEY = os.getenv("OPENDART_API_KEY", "").strip()
DART_TARGET_MAX = 120
DART_WORKERS = 6
CONSENSUS_HISTORY = ROOT / "wamo_consensus_history.json"
CONSENSUS_TARGET_MAX = 140
CONSENSUS_WORKERS = 6

def http_bytes(url: str, timeout=25) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Referer": "https://finance.naver.com/",
            "Accept": "*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def http_text(url: str, timeout=25, encoding=None) -> str:
    raw = http_bytes(url, timeout)
    if encoding:
        return raw.decode(encoding, errors="replace")
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("utf-8", errors="replace")

def extract_old_payload(html: str) -> dict:
    m = re.search(r"window\.WAMO_DATA\s*=\s*(\{.*?\})\s*;", html, flags=re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except Exception:
        return {}

def replace_payload(html: str, payload: dict) -> str:
    js = "window.WAMO_DATA = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";"
    out, n = re.subn(r"window\.WAMO_DATA\s*=\s*\{.*?\}\s*;", lambda _: js, html, count=1, flags=re.S)
    if n != 1:
        raise RuntimeError("index.html 안에서 WAMO_DATA 영역을 찾지 못했습니다.")
    return out

def clean_num(x):
    s = str(x or "").strip().replace(",", "").replace("%", "")
    if not s or s in {"-", "N/A", "nan"}:
        return None
    try:
        v = float(s)
        return v if math.isfinite(v) else None
    except Exception:
        return None

def fetch_market_summary(sosok: int, suffix: str, krx_market: str):
    # Naver market-cap summary: market cap is displayed in 억원.
    rows = []
    for page in range(1, 80):
        url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
        txt = http_text(url, encoding="euc-kr")
        # Each stock row has a link with code=XXXXXX, followed by TD values.
        stock_rows = re.findall(r"<tr[^>]*>(.*?)</tr>", txt, flags=re.S | re.I)
        page_count = 0
        for tr in stock_rows:
            m = re.search(r'href="/item/main\.naver\?code=(\d{6})"[^>]*>(.*?)</a>', tr, flags=re.S | re.I)
            if not m:
                continue
            code = m.group(1)
            name = re.sub(r"<.*?>", "", m.group(2)).strip()
            tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S | re.I)
            values = []
            for td in tds:
                v = re.sub(r"<.*?>", "", td)
                v = v.replace("&nbsp;", " ").strip()
                values.append(v)
            # Expected Naver columns after the leading rank cell:
            # 종목명, 현재가, 전일비, 등락률, 액면가, 시가총액(억원), 상장주식수...
            # Find market cap defensively by locating name cell position.
            texts = [re.sub(r"\s+", "", x) for x in values]
            try:
                ni = next(i for i, x in enumerate(texts) if name.replace(" ", "") in x)
            except StopIteration:
                ni = 1
            mcap_100m = None
            # Standard layout market cap is 5 numeric cells after name.
            for idx in (ni + 5, ni + 6):
                if 0 <= idx < len(values):
                    v = clean_num(values[idx])
                    if v is not None and v > 0:
                        mcap_100m = v
                        break
            if mcap_100m is None:
                continue
            market_cap = mcap_100m * 100_000_000
            # Avoid preferred shares / SPAC for this growth-stock screen.
            lname = name.replace(" ", "")
            if re.search(r"(스팩|SPAC)$", lname, flags=re.I):
                continue
            if re.search(r"우([A-Z]|\d|B)?$", lname):
                continue
            rows.append(
                {
                    "ticker": code + suffix,
                    "stock_code": code,
                    "name": name,
                    "market": "KOREA",
                    "krx_market": krx_market,
                    "market_cap_krw": market_cap,
                }
            )
            page_count += 1
        if page_count == 0:
            break
        time.sleep(0.05)
    if len(rows) < 100:
        raise RuntimeError(f"{krx_market} 시가총액 목록 수집이 비정상적으로 적습니다: {len(rows)}")
    return rows

def kind_sector_map():
    # Best-effort KRX KIND industry mapping. Failure is allowed.
    try:
        import pandas as pd
        out = {}
        for market_type in ("stockMkt", "kosdaqMkt"):
            q = urllib.parse.urlencode({"method": "download", "searchType": "13", "marketType": market_type})
            raw = http_bytes("https://kind.krx.co.kr/corpgeneral/corpList.do?" + q)
            df = None
            try:
                df = pd.read_excel(io.BytesIO(raw), dtype=str)
            except Exception:
                try:
                    tables = pd.read_html(io.BytesIO(raw))
                    df = tables[0] if tables else None
                except Exception:
                    pass
            if df is None:
                continue
            df.columns = [str(c).strip() for c in df.columns]
            code_col = next((c for c in df.columns if "종목코드" in c or "주식코드" in c), None)
            sector_col = next((c for c in df.columns if "업종" in c), None)
            if not code_col or not sector_col:
                continue
            for _, r in df.iterrows():
                code = re.sub(r"\D", "", str(r.get(code_col, "")))[-6:].zfill(6)
                sec = str(r.get(sector_col, "")).strip()
                if re.fullmatch(r"\d{6}", code) and sec and sec.lower() != "nan":
                    out[code] = sec
        return out
    except Exception as e:
        print("KIND sector mapping skipped:", e)
        return {}

def fetch_yahoo_history(ticker: str, years=5):
    """GitHub Actions에서 사용할 1순위 가격 경로.
    query1/query2를 순차 시도하고, 수정주가 비율로 OHLC를 보정합니다.
    """
    errors = []
    now = int(time.time())
    start = now - years * 370 * 86400
    qs = urllib.parse.urlencode({
        "period1": start,
        "period2": now + 86400,
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    })

    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = f"https://{host}/v8/finance/chart/{urllib.parse.quote(ticker, safe='.^=')}?{qs}"
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": UA,
                        "Accept": "application/json,text/plain,*/*",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                )
                with urllib.request.urlopen(req, timeout=25) as r:
                    d = json.loads(r.read().decode("utf-8"))

                result = d.get("chart", {}).get("result")
                if not result:
                    raise RuntimeError(str(d.get("chart", {}).get("error") or "Yahoo result 없음"))

                r0 = result[0]
                ts = r0.get("timestamp") or []
                quote = (r0.get("indicators", {}).get("quote") or [{}])[0]
                adj = (r0.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose") or []

                closes = quote.get("close") or []
                highs = quote.get("high") or []
                lows = quote.get("low") or []
                vols = quote.get("volume") or []

                rows = []
                for i, t in enumerate(ts):
                    try:
                        raw_close = float(closes[i])
                        raw_high = float(highs[i]) if highs[i] is not None else raw_close
                        raw_low = float(lows[i]) if lows[i] is not None else raw_close
                        volume = float(vols[i] or 0) if i < len(vols) else 0.0
                        adj_close = float(adj[i]) if i < len(adj) and adj[i] is not None else raw_close
                    except Exception:
                        continue

                    if raw_close <= 0 or not math.isfinite(raw_close):
                        continue

                    # Split/dividend-adjusted close와 OHLC의 스케일을 맞춤.
                    ratio = adj_close / raw_close if raw_close else 1.0
                    if not math.isfinite(ratio) or ratio <= 0:
                        ratio = 1.0

                    rows.append({
                        "date": datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat(),
                        "close": adj_close,
                        "high": raw_high * ratio,
                        "low": raw_low * ratio,
                        "volume": volume,
                    })

                # Duplicate date 제거 후 정렬.
                dedup = {r["date"]: r for r in rows}
                rows = [dedup[k] for k in sorted(dedup)]
                if len(rows) < 260:
                    raise RuntimeError(f"Yahoo 가격 이력 부족: {len(rows)}일")
                return rows, host

            except Exception as e:
                errors.append(f"{host} attempt {attempt+1}: {e}")
                time.sleep(1.0 + attempt * 0.8)

    raise RuntimeError(" / ".join(errors[-6:]))

def fetch_naver_history(code: str, count=900):
    url = f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count={count}&requestType=0"
    raw = http_bytes(url)
    root = ET.fromstring(raw)
    rows = []
    for item in root.findall(".//item"):
        p = (item.attrib.get("data") or "").split("|")
        if len(p) < 6:
            continue
        try:
            dt = datetime.strptime(p[0], "%Y%m%d").date().isoformat()
            o, h, l, c, v = map(float, p[1:6])
        except Exception:
            continue
        if c > 0:
            rows.append({"date": dt, "close": c, "high": h, "low": l, "volume": v})
    rows.sort(key=lambda r: r["date"])
    if len(rows) < 260:
        raise RuntimeError(f"가격 이력 부족: {len(rows)}일")
    return rows

def fetch_yahoo_index(symbol="^KS11", years=2):
    now = int(time.time())
    start = now - years * 370 * 86400
    q = urllib.parse.urlencode(
        {"period1": start, "period2": now + 86400, "interval": "1d", "events": "history"}
    )
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol, safe='^')}?{q}"
    d = json.loads(http_text(url))
    result = d.get("chart", {}).get("result")
    if not result:
        raise RuntimeError("KOSPI 지수 데이터 수집 실패")
    r0 = result[0]
    ts = r0.get("timestamp") or []
    q0 = (r0.get("indicators", {}).get("quote") or [{}])[0]
    closes = q0.get("close") or []
    rows = []
    for i, t in enumerate(ts):
        try:
            c = float(closes[i])
        except Exception:
            continue
        rows.append((datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat(), c))
    if len(rows) < 220:
        raise RuntimeError("KOSPI 지수 이력 부족")
    return rows

def mean_tail(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None

def pct_change(xs, n):
    if len(xs) <= n or not xs[-n - 1]:
        return None
    return xs[-1] / xs[-n - 1] - 1

def range_return(xs, older_ago, newer_ago):
    oi = len(xs) - 1 - older_ago
    ni = len(xs) - 1 - newer_ago
    if oi < 0 or ni < 0 or ni <= oi or not xs[oi]:
        return None
    return xs[ni] / xs[oi] - 1

def percentile(values, x):
    vals = sorted(v for v in values if v is not None and math.isfinite(v))
    if not vals:
        return 50.0
    below = sum(v < x for v in vals)
    equal = sum(v == x for v in vals)
    return 100 * (below + 0.5 * equal) / len(vals)

def monthly_closes(rows):
    d = {}
    for r in rows:
        d[r["date"][:7]] = r["close"]
    return [d[k] for k in sorted(d)]

def demand_ratio(rows, n=50):
    recent = rows[-min(n + 1, len(rows)) :]
    up = down = 0.0
    for i in range(1, len(recent)):
        if recent[i]["close"] > recent[i - 1]["close"]:
            up += recent[i]["volume"]
        elif recent[i]["close"] < recent[i - 1]["close"]:
            down += recent[i]["volume"]
    if down <= 0:
        return 99.0 if up > 0 else 1.0
    return up / down

def oneil_rs_raw(closes):
    return (
        0.40 * (range_return(closes, 63, 0) or 0)
        + 0.20 * (range_return(closes, 126, 63) or 0)
        + 0.20 * (range_return(closes, 189, 126) or 0)
        + 0.20 * (range_return(closes, 252, 189) or 0)
    )

def market_direction():
    try:
        rows = fetch_yahoo_index()
        c = [x[1] for x in rows]
        ma50 = mean_tail(c, 50)
        ma200 = mean_tail(c, 200)
        prev200 = sum(c[-220:-20]) / 200 if len(c) >= 220 else ma200
        ok = bool(ma50 and ma200 and prev200 and c[-1] > ma50 > ma200 and ma200 > prev200)
        return {
            "pass": ok,
            "close": c[-1],
            "ma50": ma50,
            "ma200": ma200,
            "note": "KOSPI 50일선·200일선 기반 시장 방향 대체지표",
        }
    except Exception as e:
        print("Market direction unavailable:", e)
        return {"pass": None, "note": "시장지수 데이터 수집 실패"}

def calc_raw(meta, rows):
    closes = [r["close"] for r in rows]
    highs = [r["high"] for r in rows]
    lows = [r["low"] for r in rows]
    vols = [r["volume"] for r in rows]
    ma20 = mean_tail(closes, 20)
    ma50 = mean_tail(closes, 50)
    ma60 = mean_tail(closes, 60)
    ma120 = mean_tail(closes, 120)
    ma150 = mean_tail(closes, 150)
    ma200 = mean_tail(closes, 200)
    ma200_prev = sum(closes[-220:-20]) / 200 if len(closes) >= 220 else ma200

    high52 = max(highs[-252:])
    low52 = min(lows[-252:])
    high3 = max(highs[-min(756, len(highs)):])
    idx3 = max(i for i, h in enumerate(highs) if h >= high3 * 0.999999)
    since3 = len(rows) - 1 - idx3
    high_ratio = closes[-1] / high52
    avg_value_50d = sum(r["close"] * r["volume"] for r in rows[-50:]) / min(50, len(rows))
    vol20 = mean_tail(vols, 20) or 1
    vol_ratio = vols[-1] / vol20 if vol20 else 0
    mcl = monthly_closes(rows)
    ma20m = mean_tail(mcl, 20)

    ret = {n: (pct_change(closes, n) or 0) for n in (1, 5, 20, 60, 120, 252)}
    rs_blend = 0.20 * ret[5] + 0.40 * ret[20] + 0.40 * ret[60]

    cond = {
        "장기 우상향": bool(ma200 and ma200_prev and closes[-1] > ma200 > ma200_prev and ret[252] > 0),
        "최근 1년 내 장기 신고가": bool(since3 <= 252),
        "고점 대비 -30% 이내": bool(high_ratio >= 0.70),
        "52주 신고가권(3% 이내)": bool(high_ratio >= 0.97),
        "월봉 20개월선 위": bool(ma20m and mcl[-1] > ma20m),
        "일봉 정배열": bool(ma20 and ma60 and ma120 and ma200 and closes[-1] > ma20 > ma60 > ma120 > ma200),
    }
    st = {
        "주가>150·200일선": bool(ma150 and ma200 and closes[-1] > ma150 and closes[-1] > ma200),
        "150일선>200일선": bool(ma150 and ma200 and ma150 > ma200),
        "200일선 상승": bool(ma200 and ma200_prev and ma200 > ma200_prev),
        "50일선>150·200일선": bool(ma50 and ma150 and ma200 and ma50 > ma150 and ma50 > ma200),
        "주가>50일선": bool(ma50 and closes[-1] > ma50),
        "52주 저점 대비 +30%": bool(closes[-1] >= low52 * 1.30),
        "52주 고점 25% 이내": bool(high_ratio >= 0.75),
    }

    return {
        **meta,
        "date": rows[-1]["date"],
        "close": closes[-1],
        "chg1d": ret[1] * 100,
        "ret252": ret[252] * 100,
        "rs5": ret[5] * 100,
        "rs20": ret[20] * 100,
        "rs60": ret[60] * 100,
        "rsBlend": rs_blend * 100,
        "oneilRsRaw": oneil_rs_raw(closes) * 100,
        "demandRatio": demand_ratio(rows),
        "volumeRatio": vol_ratio,
        "high52Ratio": high_ratio * 100,
        "drawdown": (high_ratio - 1) * 100,
        "ma20": ma20, "ma50": ma50, "ma60": ma60, "ma120": ma120,
        "ma150": ma150, "ma200": ma200, "ma20m": ma20m,
        "avgTradingValue50d": avg_value_50d,
        "conditions": cond,
        "conditionCount": sum(cond.values()),
        "stage2Checks": st,
        "stage2Core": all(st.values()),
        "history": rows[-180:],
        "dataSource": "NAVER Finance",
        "dataStatus": "LIVE",
        "foreign_netbuy_20d": None,
        "institution_netbuy_20d": None,
        "pension_netbuy_20d": None,
        "eps_revision_4w": None,
        "op_revision_4w": None,
        "revision_signal": "UNKNOWN",
        "catalysts": [],
    }


# ---------------- OpenDART official enrichment ----------------

def dart_json(endpoint: str, params: dict, timeout=25):
    if not DART_KEY:
        return {"status": "NO_KEY", "message": "OPENDART_API_KEY not configured"}
    q = dict(params)
    q["crtfc_key"] = DART_KEY
    url = "https://opendart.fss.or.kr/api/" + endpoint + "?" + urllib.parse.urlencode(q)
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept": "application/json,text/plain,*/*",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
            status = str(d.get("status", "000"))
            if status in ("000", "013"):
                return d
            if status in ("020", "800", "900"):
                last = RuntimeError(f"DART {endpoint}: {status} {d.get('message','')}")
                time.sleep(1.5 + attempt)
                continue
            raise RuntimeError(f"DART {endpoint}: {status} {d.get('message','')}")
        except Exception as e:
            last = e
            time.sleep(1.0 + attempt)
    raise last or RuntimeError("DART request failed")

def dart_corp_map():
    if not DART_KEY:
        return {}
    url = "https://opendart.fss.or.kr/api/corpCode.xml?" + urllib.parse.urlencode({"crtfc_key": DART_KEY})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        xml = z.read(z.namelist()[0])
    root = ET.fromstring(xml)
    out = {}
    for item in root.findall(".//list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        corp_code = (item.findtext("corp_code") or "").strip()
        if stock_code and corp_code:
            out[stock_code] = corp_code
    if len(out) < 1000:
        raise RuntimeError(f"DART corp code map suspiciously small: {len(out)}")
    return out

def dart_num(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s or s in ("-", "N/A"):
        return None
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        x = float(s)
        return x if math.isfinite(x) else None
    except Exception:
        return None

def dart_growth(cur, prev):
    if cur is None or prev is None:
        return None, None
    if prev > 0:
        return (cur / prev - 1) * 100, "PERCENT"
    if prev <= 0 and cur > 0:
        return None, "TURNAROUND"
    return None, "NONPOSITIVE"

def dart_find_row(rows, patterns, statement_only=True):
    compiled = [re.compile(p, re.I) for p in patterns]
    # Prefer consolidated IS/CIS rows.
    candidates = []
    for r in rows:
        if statement_only and r.get("sj_div") not in ("IS", "CIS"):
            continue
        nm = str(r.get("account_nm", ""))
        aid = str(r.get("account_id", ""))
        if any(rx.search(nm) or rx.search(aid) for rx in compiled):
            candidates.append(r)
    if not candidates:
        return None
    # Prefer standard XBRL IDs and shorter / exact names.
    candidates.sort(key=lambda r: (
        1 if "표준계정코드 미사용" in str(r.get("account_id","")) else 0,
        len(str(r.get("account_nm",""))),
        int(str(r.get("ord","999999")).replace(",","") or 999999) if str(r.get("ord","")).replace(",","").isdigit() else 999999
    ))
    return candidates[0]

def dart_statement(corp_code, year, reprt_code):
    # Consolidated first, standalone fallback.
    for fs_div in ("CFS", "OFS"):
        d = dart_json("fnlttSinglAcntAll.json", {
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": reprt_code,
            "fs_div": fs_div,
        })
        if str(d.get("status","000")) == "000" and d.get("list"):
            return d.get("list") or [], fs_div
    return [], None

def current_report_candidates(today=None):
    d = today or datetime.now(KST).date()
    y = d.year
    # Try only reports that can plausibly exist at this point in the year.
    if d.month >= 11:
        seq = [(y, "11014"), (y, "11012"), (y, "11013"), (y-1, "11011")]
    elif d.month >= 8:
        seq = [(y, "11012"), (y, "11013"), (y-1, "11011")]
    elif d.month >= 5:
        seq = [(y, "11013"), (y-1, "11011")]
    else:
        seq = [(y-1, "11011"), (y-2, "11011")]
    return seq

def extract_current_eps_sales(rows, reprt_code):
    eps_row = dart_find_row(rows, [
        r"^기본주당이익", r"^기본주당순이익", r"^기본주당손익",
        r"BasicEarningsLossPerShare"
    ])
    sales_row = dart_find_row(rows, [
        r"^매출액$", r"^영업수익$", r"^수익\(매출액\)$",
        r"Revenue", r"Sales"
    ])

    result = {}
    if eps_row:
        if reprt_code == "11011":
            cur = dart_num(eps_row.get("thstrm_amount"))
            prev = dart_num(eps_row.get("frmtrm_amount"))
        else:
            # OpenDART specifies 3-month amounts for quarterly/semiannual IS/CIS.
            cur = dart_num(eps_row.get("thstrm_amount"))
            prev = dart_num(eps_row.get("frmtrm_q_amount"))
        g, mode = dart_growth(cur, prev)
        result.update({"eps_cur": cur, "eps_prev": prev, "eps_yoy": g, "eps_growth_mode": mode})

    if sales_row:
        if reprt_code == "11011":
            cur = dart_num(sales_row.get("thstrm_amount"))
            prev = dart_num(sales_row.get("frmtrm_amount"))
        else:
            cur = dart_num(sales_row.get("thstrm_amount"))
            prev = dart_num(sales_row.get("frmtrm_q_amount"))
        g, mode = dart_growth(cur, prev)
        result.update({"sales_cur": cur, "sales_prev": prev, "sales_yoy": g, "sales_growth_mode": mode})
    return result

def extract_annual_eps(rows):
    r = dart_find_row(rows, [
        r"^기본주당이익", r"^기본주당순이익", r"^기본주당손익",
        r"BasicEarningsLossPerShare"
    ])
    return dart_num(r.get("thstrm_amount")) if r else None

def _annual_eps_from_report(corp_code, report_year):
    """Pull up to 3 fiscal-year EPS values from one annual report.
    OpenDART annual full-FS exposes current/prior/prior-prior amounts.
    Try consolidated first, then standalone if the EPS row is absent.
    """
    for fs_div in ("CFS", "OFS"):
        d = dart_json("fnlttSinglAcntAll.json", {
            "corp_code": corp_code,
            "bsns_year": str(report_year),
            "reprt_code": "11011",
            "fs_div": fs_div,
        })
        rows = d.get("list") or []
        if str(d.get("status","000")) != "000" or not rows:
            continue
        r = dart_find_row(rows, [
            r"^기본주당이익", r"^기본주당순이익", r"^기본주당손익",
            r"BasicEarningsLossPerShare"
        ])
        if not r:
            continue
        vals = []
        cur = dart_num(r.get("thstrm_amount"))
        prev = dart_num(r.get("frmtrm_amount"))
        prev2 = dart_num(r.get("bfefrmtrm_amount"))
        if cur is not None:
            vals.append({"year": report_year, "eps": cur, "fs": fs_div})
        if prev is not None:
            vals.append({"year": report_year - 1, "eps": prev, "fs": fs_div})
        if prev2 is not None:
            vals.append({"year": report_year - 2, "eps": prev2, "fs": fs_div})
        if vals:
            return vals
    return []



def dart_major_accounts(corp_code, year, reprt_code):
    """OpenDART 'single company major accounts' endpoint.
    This is less granular than the full FS but is more standardized across issuers.
    """
    d = dart_json("fnlttSinglAcnt.json", {
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": reprt_code,
    })
    if str(d.get("status","000")) != "000":
        return []
    return d.get("list") or []

def _major_net_income_row(rows):
    patterns = [
        r"^당기순이익",
        r"^분기순이익",
        r"^반기순이익",
        r"^연결당기순이익",
        r"^순이익",
        r"ProfitLoss",
    ]
    candidates = []
    for r in rows:
        nm = str(r.get("account_nm",""))
        if any(re.search(p, nm, re.I) for p in patterns):
            candidates.append(r)
    if not candidates:
        return None
    # Prefer consolidated statements.
    candidates.sort(key=lambda r: (
        0 if str(r.get("fs_div","")) == "CFS" else 1,
        len(str(r.get("account_nm",""))),
    ))
    return candidates[0]

def current_income_proxy(corp_code, year, reprt_code):
    """Quarterly/semiannual earnings proxy if EPS is not exposed."""
    try:
        rows = dart_major_accounts(corp_code, year, reprt_code)
    except Exception:
        return {}
    r = _major_net_income_row(rows)
    if not r:
        return {}

    cur = dart_num(r.get("thstrm_amount"))
    prev = dart_num(r.get("frmtrm_amount"))
    # Some filings expose current-period value in cumulative fields instead.
    if cur is None:
        cur = dart_num(r.get("thstrm_add_amount"))
    if prev is None:
        prev = dart_num(r.get("frmtrm_add_amount"))

    g, mode = dart_growth(cur, prev)
    return {
        "quarter_proxy_metric": str(r.get("account_nm") or "순이익"),
        "quarter_proxy_cur": cur,
        "quarter_proxy_prev": prev,
        "quarter_proxy_yoy": g,
        "quarter_proxy_growth_mode": mode,
    }

def annual_major_income_series(corp_code, latest_year):
    by_year = {}
    metric = None
    for report_year in (latest_year, latest_year - 1, latest_year - 2, latest_year - 3):
        try:
            rows = dart_major_accounts(corp_code, report_year, "11011")
        except Exception:
            rows = []
        r = _major_net_income_row(rows)
        if not r:
            continue
        metric = metric or str(r.get("account_nm") or "순이익")
        vals = [
            (report_year, dart_num(r.get("thstrm_amount"))),
            (report_year - 1, dart_num(r.get("frmtrm_amount"))),
            (report_year - 2, dart_num(r.get("bfefrmtrm_amount"))),
        ]
        for y, v in vals:
            if v is not None and latest_year - 3 <= y <= latest_year:
                by_year.setdefault(y, {
                    "year": y, "value": v,
                    "metric": metric,
                    "fs": r.get("fs_div"),
                    "source": "OpenDART 주요계정",
                })
        if all(y in by_year for y in range(latest_year - 3, latest_year + 1)):
            break
    return [by_year[y] for y in sorted(by_year)], metric


def _annual_parent_income_from_report(corp_code, report_year):
    """Fallback annual earnings series when reported EPS is not exposed in full FS.
    Uses profit attributable to owners of parent where possible; net income only as last resort.
    """
    owner_patterns = [
        r"지배기업.*소유주.*귀속.*당기순이익",
        r"지배기업.*소유주.*순이익",
        r"지배주주.*순이익",
        r"ProfitLossAttributableToOwnersOfParent",
    ]
    total_patterns = [
        r"^당기순이익",
        r"^분기순이익",
        r"^연결당기순이익",
        r"^ProfitLoss$",
    ]
    for fs_div in ("CFS", "OFS"):
        d = dart_json("fnlttSinglAcntAll.json", {
            "corp_code": corp_code,
            "bsns_year": str(report_year),
            "reprt_code": "11011",
            "fs_div": fs_div,
        })
        rows = d.get("list") or []
        if str(d.get("status","000")) != "000" or not rows:
            continue
        r = dart_find_row(rows, owner_patterns)
        metric = "지배주주순이익"
        if not r:
            r = dart_find_row(rows, total_patterns)
            metric = "당기순이익"
        if not r:
            continue

        vals = []
        cur = dart_num(r.get("thstrm_amount"))
        prev = dart_num(r.get("frmtrm_amount"))
        prev2 = dart_num(r.get("bfefrmtrm_amount"))
        if cur is not None:
            vals.append({"year": report_year, "value": cur, "fs": fs_div, "metric": metric})
        if prev is not None:
            vals.append({"year": report_year - 1, "value": prev, "fs": fs_div, "metric": metric})
        if prev2 is not None:
            vals.append({"year": report_year - 2, "value": prev2, "fs": fs_div, "metric": metric})
        if vals:
            return vals
    return []

def annual_earnings_fallback_series(corp_code, latest_year):
    by_year = {}
    metric = None
    for report_year in (latest_year, latest_year - 1, latest_year - 2, latest_year - 3):
        try:
            vals = _annual_parent_income_from_report(corp_code, report_year)
        except Exception:
            vals = []
        for v in vals:
            y = int(v["year"])
            if latest_year - 3 <= y <= latest_year:
                by_year.setdefault(y, v)
                metric = metric or v.get("metric")
        if all(y in by_year for y in range(latest_year - 3, latest_year + 1)):
            break
    return [by_year[y] for y in sorted(by_year)], metric

def calc_fallback_annual_metrics(series, metric):
    out = {}
    if not series:
        return out
    s = sorted(series, key=lambda z: z["year"])
    if len(s) >= 2:
        latest = s[-1]["value"]
        prev = s[-2]["value"]
        g, mode = dart_growth(latest, prev)
        out["latest_annual_proxy_yoy"] = g
        out["annual_proxy_growth_mode"] = mode
    if len(s) >= 4:
        start = s[-4]["value"]
        end = s[-1]["value"]
        yrs = s[-1]["year"] - s[-4]["year"]
        if start is not None and end is not None and start > 0 and end > 0 and yrs > 0:
            out["annual_proxy_cagr_3y"] = ((end / start) ** (1 / yrs) - 1) * 100
    out["annual_proxy_metric"] = metric or "순이익"
    out["annual_proxy_series"] = s
    return out

def annual_eps_series(corp_code, latest_year, old=None):
    # Reuse cache only if it is actually complete for 4 consecutive fiscal years.
    old_series = (old or {}).get("annualEpsSeries")
    if isinstance(old_series, list) and old_series:
        cleaned = {}
        for x in old_series:
            try:
                y = int(x.get("year"))
                e = dart_num(x.get("eps"))
                if e is not None:
                    cleaned[y] = {"year": y, "eps": e, "fs": x.get("fs")}
            except Exception:
                pass
        needed = set(range(latest_year - 3, latest_year + 1))
        if needed.issubset(cleaned.keys()):
            return [cleaned[y] for y in sorted(needed)]

    # One latest annual report normally supplies 3 years; one older report fills the 4th.
    by_year = {}
    for report_year in (latest_year, latest_year - 1, latest_year - 2):
        try:
            vals = _annual_eps_from_report(corp_code, report_year)
        except Exception:
            vals = []
        for v in vals:
            y = int(v["year"])
            if latest_year - 3 <= y <= latest_year:
                by_year.setdefault(y, v)
        if all(y in by_year for y in range(latest_year - 3, latest_year + 1)):
            break

    # Last-resort direct-year pulls for any missing year.
    for y in range(latest_year - 3, latest_year + 1):
        if y in by_year:
            continue
        try:
            vals = _annual_eps_from_report(corp_code, y)
        except Exception:
            vals = []
        exact = next((v for v in vals if int(v["year"]) == y), None)
        if exact:
            by_year[y] = exact

    return [by_year[y] for y in sorted(by_year)]

def calc_annual_metrics(series):
    if not series:
        return {}
    s = sorted(series, key=lambda z: z["year"])
    out = {}
    if len(s) >= 2:
        latest = s[-1]["eps"]
        prev = s[-2]["eps"]
        g, mode = dart_growth(latest, prev)
        out["latest_annual_eps_yoy"] = g
        out["annual_growth_mode"] = mode
    if len(s) >= 4:
        start = s[-4]["eps"]
        end = s[-1]["eps"]
        yrs = s[-1]["year"] - s[-4]["year"]
        if start is not None and end is not None and start > 0 and end > 0 and yrs > 0:
            out["annual_eps_cagr_3y"] = ((end / start) ** (1 / yrs) - 1) * 100
    return out

def issued_shares(corp_code, year, reprt_code):
    d = dart_json("stockTotqySttus.json", {
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": reprt_code,
    })
    if str(d.get("status","000")) != "000":
        return None

    rows = d.get("list") or []
    parsed = []
    for r in rows:
        v = dart_num(r.get("istc_totqy"))
        if v is not None:
            parsed.append((str(r.get("se","")).strip(), v))

    if not parsed:
        return None

    # DART frequently returns common/preferred rows plus a total row.
    # Prefer the explicit total so classes are not double-counted.
    for se, v in parsed:
        norm = re.sub(r"\s+", "", se).lower()
        if "합계" in norm or norm in ("계", "total"):
            return v

    # If no explicit total exists, sum security-class rows only once.
    return sum(v for _, v in parsed)

def share_growth_with_fallback(corp_code, latest_year, latest_reprt):
    """Try the freshest same-report YoY pair, then progressively safer fallbacks."""
    candidates = []
    if latest_year and latest_reprt:
        candidates.append((latest_year, latest_reprt))
    # Quarterly and annual fallbacks make insurers / late filers more robust.
    y_now = datetime.now(KST).year
    for pair in [
        (y_now, "11012"),
        (y_now, "11013"),
        (y_now - 1, "11011"),
    ]:
        if pair not in candidates:
            candidates.append(pair)

    for y, rc in candidates:
        try:
            cur = issued_shares(corp_code, y, rc)
            prev = issued_shares(corp_code, y - 1, rc)
        except Exception:
            continue
        if cur is not None and prev not in (None, 0):
            return {
                "issued_shares": cur,
                "share_growth_yoy": (cur / prev - 1) * 100,
                "share_report_year": y,
                "share_report_code": rc,
            }
    return {}

DART_POSITIVE_KEYWORDS = [
    ("공급·수주", ["단일판매", "공급계약", "수주"]),
    ("신규투자", ["신규시설투자", "시설투자", "유형자산 취득", "유형자산취득"]),
    ("인수·확장", ["영업양수", "타법인주식및출자증권취득", "합병결정", "합병 결정"]),
    ("기술·허가", ["특허", "기술이전", "품목허가", "임상", "신제품"]),
]
DART_DILUTION_KEYWORDS = ["유상증자", "전환사채", "신주인수권부사채", "교환사채", "주식관련사채"]
DART_RISK_KEYWORDS = ["영업정지", "회생절차", "상장폐지", "횡령", "배임", "감사의견"]

def classify_disclosure(report_name):
    n = re.sub(r"\s+", "", str(report_name or ""))
    for label, keys in DART_POSITIVE_KEYWORDS:
        if any(re.sub(r"\s+", "", k) in n for k in keys):
            return label, "POSITIVE"
    if any(re.sub(r"\s+", "", k) in n for k in DART_DILUTION_KEYWORDS):
        return "희석·자금조달", "DILUTION"
    if any(re.sub(r"\s+", "", k) in n for k in DART_RISK_KEYWORDS):
        return "리스크", "RISK"
    if "영업(잠정)실적" in n or "매출액또는손익구조" in n:
        return "실적", "EARNINGS"
    return "기타공시", "NEUTRAL"

def recent_disclosures(corp_code, days=45):
    end = datetime.now(KST).date()
    begin = end - timedelta(days=days)
    d = dart_json("list.json", {
        "corp_code": corp_code,
        "bgn_de": begin.strftime("%Y%m%d"),
        "end_de": end.strftime("%Y%m%d"),
        "last_reprt_at": "Y",
        "sort": "date",
        "sort_mth": "desc",
        "page_count": "100",
    })
    if str(d.get("status","000")) != "000":
        return []
    out = []
    for r in d.get("list") or []:
        cat, pol = classify_disclosure(r.get("report_nm",""))
        rno = str(r.get("rcept_no",""))
        out.append({
            "date": r.get("rcept_dt",""),
            "report": r.get("report_nm",""),
            "category": cat,
            "polarity": pol,
            "rcept_no": rno,
            "url": ("https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + rno) if rno else "",
        })
    return out[:100]

def fetch_roe_batch(corp_codes, year, reprt_code):
    out = {}
    if not corp_codes:
        return out
    for i in range(0, len(corp_codes), 100):
        chunk = corp_codes[i:i+100]
        d = dart_json("fnlttCmpnyIndx.json", {
            "corp_code": ",".join(chunk),
            "bsns_year": str(year),
            "reprt_code": reprt_code,
            "idx_cl_code": "M210000",
        })
        if str(d.get("status","000")) != "000":
            continue
        for r in d.get("list") or []:
            nm = str(r.get("idx_nm","")).lower()
            if "자기자본이익률" in nm or nm.strip() == "roe":
                v = dart_num(r.get("idx_val"))
                if v is not None:
                    # Official examples are ratio-form (e.g. 0.256), but guard percent-form too.
                    out[r.get("corp_code")] = v * 100 if abs(v) <= 2 else v
    return out

def enrich_one_dart(x, corp_code, old=None):
    result = {
        "dartStatus": "PARTIAL",
        "dartCorpCode": corp_code,
        "dartSource": "OpenDART official",
    }

    latest_rows = []
    latest_year = None
    latest_reprt = None
    latest_fs = None
    for y, rc in current_report_candidates():
        rows, fs = dart_statement(corp_code, y, rc)
        if rows:
            latest_rows, latest_year, latest_reprt, latest_fs = rows, y, rc, fs
            break

    if latest_rows:
        result.update(extract_current_eps_sales(latest_rows, latest_reprt))
        result.update({
            "dartReportYear": latest_year,
            "dartReportCode": latest_reprt,
            "dartFsDiv": latest_fs,
        })
        if result.get("eps_yoy") is None:
            result.update(current_income_proxy(corp_code, latest_year, latest_reprt))

    # Annual A: use the most recently completed fiscal year.
    annual_latest = datetime.now(KST).year - 1
    series = annual_eps_series(corp_code, annual_latest, old=old)
    result["annualEpsSeries"] = series
    result.update(calc_annual_metrics(series))

    # Some financial companies do not expose a usable 4-year EPS series in the full-FS API.
    # In that case retain strict transparency and calculate an explicitly labelled earnings proxy.
    if result.get("latest_annual_eps_yoy") is None or result.get("annual_eps_cagr_3y") is None:
        proxy_series, proxy_metric = annual_major_income_series(corp_code, annual_latest)
        if len(proxy_series) < 4:
            proxy_series2, proxy_metric2 = annual_earnings_fallback_series(corp_code, annual_latest)
            if len(proxy_series2) > len(proxy_series):
                proxy_series, proxy_metric = proxy_series2, proxy_metric2
        result.update(calc_fallback_annual_metrics(proxy_series, proxy_metric))

    # Supply dilution: freshest available same-report YoY pair with fallbacks.
    result.update(share_growth_with_fallback(corp_code, latest_year, latest_reprt))

    # One 365-day disclosure pull supports both N (fresh catalyst) and S (dilution watch).
    all_discs = recent_disclosures(corp_code, 365)
    cutoff45 = (datetime.now(KST).date() - timedelta(days=45)).strftime("%Y%m%d")
    discs45 = [d for d in all_discs if str(d.get("date","")) >= cutoff45]
    result["catalysts"] = discs45[:12]
    positive = [d for d in discs45 if d.get("polarity") == "POSITIVE"]
    result["new_catalyst"] = bool(positive)
    result["new_catalyst_note"] = positive[0]["report"] if positive else ""

    dilution = [d for d in all_discs if d.get("polarity") == "DILUTION"]
    result["dilution_events_365d"] = dilution[:10]
    result["dilution_filing_365d"] = bool(dilution)
    result["dilution_note"] = dilution[0]["report"] if dilution else ""

    result["dartStatus"] = "LIVE" if latest_rows or series or discs else "NO_DATA"
    return result

def dart_enrich(raw, old_by_ticker):
    meta = {
        "connected": False,
        "targetCount": 0,
        "successCount": 0,
        "errorCount": 0,
        "source": "OpenDART official",
        "message": "OpenDART key not connected",
    }
    if not DART_KEY:
        return meta

    try:
        cmap = dart_corp_map()
        meta["connected"] = True
    except Exception as e:
        meta["message"] = "OpenDART key validation/corp-code fetch failed: " + str(e)
        return meta

    # Prioritize actual screening candidates; cap calls to keep workflow reliable.
    candidates = sorted(
        raw,
        key=lambda x: (
            1 if x.get("conditionCount",0) >= 4 else 0,
            1 if x.get("trendTemplate") else 0,
            x.get("rsPercentile",0),
            x.get("score",0),
        ),
        reverse=True,
    )
    target = []
    for x in candidates:
        if x.get("conditionCount",0) >= 4 or x.get("trendTemplate") or x.get("rsPercentile",0) >= 70:
            target.append(x)
        if len(target) >= DART_TARGET_MAX:
            break
    # If candidate pool is unexpectedly small, still cover top names.
    if len(target) < min(50, len(candidates)):
        target = candidates[:min(DART_TARGET_MAX, len(candidates))]

    tasks = []
    errors = []
    with ThreadPoolExecutor(max_workers=DART_WORKERS) as ex:
        for x in target:
            code = x.get("stock_code") or str(x.get("ticker","")).split(".")[0]
            corp = cmap.get(code)
            if not corp:
                continue
            fut = ex.submit(enrich_one_dart, x, corp, old_by_ticker.get(x.get("ticker"), {}))
            tasks.append((fut, x, corp))

        for i, (fut, x, corp) in enumerate(tasks, 1):
            try:
                d = fut.result()
                x.update(d)
            except Exception as e:
                x["dartStatus"] = "ERROR"
                errors.append({"ticker": x.get("ticker"), "error": str(e)})
            if i % 20 == 0:
                print("  DART", i, "/", len(tasks))

    # ROE is efficient through the official multi-company endpoint (max 100 per request).
    by_report = {}
    for x in target:
        corp = x.get("dartCorpCode")
        y = x.get("dartReportYear")
        rc = x.get("dartReportCode")
        if corp and y and rc:
            by_report.setdefault((y, rc), []).append(corp)
    roe_map = {}
    for (y, rc), corps in by_report.items():
        try:
            roe_map.update(fetch_roe_batch(corps, y, rc))
        except Exception as e:
            print("DART ROE batch error:", y, rc, e)
    for x in target:
        corp = x.get("dartCorpCode")
        if corp in roe_map:
            x["roe"] = roe_map[corp]

    succ = sum(x.get("dartStatus") == "LIVE" for x in target)
    meta.update({
        "targetCount": len(target),
        "successCount": succ,
        "errorCount": len(errors),
        "message": f"OpenDART 공식 데이터 {succ}/{len(target)}개 후보 연결",
        "errors": errors[:20],
    })
    return meta


# ---------------- KRX investor-flow enrichment ----------------

def ensure_pykrx():
    """Load pykrx. GitHub Actions runners are disposable, so install only when missing."""
    try:
        from pykrx import stock as krx_stock
        return krx_stock
    except Exception:
        print("  pykrx 없음 → 자동 설치")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q", "pykrx"],
            check=True,
            timeout=180,
        )
        importlib.invalidate_caches()
        from pykrx import stock as krx_stock
        return krx_stock

def _flow_col(df):
    if df is None or getattr(df, "empty", True):
        return None
    for c in df.columns:
        s = str(c)
        if "순매수" in s and "거래대금" in s:
            return c
    for c in df.columns:
        if "순매수" in str(c):
            return c
    return None

def _history_trade_dates(raw):
    dates = set()
    for x in raw:
        for r in x.get("history") or []:
            d = str(r.get("date",""))
            if len(d) >= 10:
                dates.add(d[:10])
    return sorted(dates)

def _period_dates(raw):
    dates = _history_trade_dates(raw)
    if not dates:
        return {}
    out = {}
    for n in (5, 20, 60):
        idx = max(0, len(dates) - n)
        out[n] = (dates[idx].replace("-",""), dates[-1].replace("-",""))
    return out

def _batch_pykrx_flow(krx_stock, raw):
    periods = _period_dates(raw)
    if not periods:
        raise RuntimeError("거래일 캘린더를 만들 수 없습니다.")

    investor_map = {
        "foreign": "외국인",
        "institution": "기관합계",
        "pension": "연기금",
        "private": "사모",
    }
    flow_by_code = {str(x.get("stock_code") or "").zfill(6): {} for x in raw}

    calls = 0
    successes = 0
    errors = []

    for n, (start, end) in periods.items():
        for market in ("KOSPI", "KOSDAQ"):
            for key, investor in investor_map.items():
                calls += 1
                try:
                    df = krx_stock.get_market_net_purchases_of_equities(
                        start, end, market, investor
                    )
                    col = _flow_col(df)
                    if col is None:
                        errors.append(f"{market}/{investor}/{n}d: empty")
                        continue
                    successes += 1
                    for code, row in df.iterrows():
                        code = str(code).zfill(6)
                        if code not in flow_by_code:
                            continue
                        try:
                            v = float(row[col])
                            if math.isfinite(v):
                                flow_by_code[code][f"{key}_netbuy_{n}d"] = v
                        except Exception:
                            pass
                except Exception as e:
                    errors.append(f"{market}/{investor}/{n}d: {e}")

    return flow_by_code, {
        "calls": calls,
        "successfulCalls": successes,
        "errors": errors[:30],
        "periods": {str(k): {"from": v[0], "to": v[1]} for k,v in periods.items()},
    }

def _investor_row_value(df, labels):
    if df is None or getattr(df, "empty", True):
        return None
    for label in labels:
        if label in df.index:
            row = df.loc[label]
            # get_market_trading_value_by_investor returns KRW transaction values.
            if hasattr(row, "get"):
                v = row.get("순매수")
            else:
                v = None
            try:
                if v is not None:
                    v = float(v)
                    return v if math.isfinite(v) else None
            except Exception:
                pass
    return None

def _pykrx_flow_one(krx_stock, code, target_dates):
    """Exact per-stock KRX trading-value fallback. Never substitutes share volume for money."""
    result = {}
    for n, (start, end) in target_dates.items():
        try:
            df = krx_stock.get_market_trading_value_by_investor(start, end, code)
        except Exception:
            continue

        f = _investor_row_value(df, ["외국인합계", "외국인"])
        inst = _investor_row_value(df, ["기관합계"])
        pension = _investor_row_value(df, ["연기금 등", "연기금"])
        private = _investor_row_value(df, ["사모"])

        if f is not None:
            result[f"foreign_netbuy_{n}d"] = f
        if inst is not None:
            result[f"institution_netbuy_{n}d"] = inst
        if pension is not None:
            result[f"pension_netbuy_{n}d"] = pension
        if private is not None:
            result[f"private_netbuy_{n}d"] = private
    return result


def investor_flow_enrich(raw):
    """Attach 5/20/60 trading-day investor net-purchase values in KRW."""
    meta = {
        "connected": False,
        "source": "KRX investor trading data via pykrx",
        "coverage": 0,
        "message": "수급 데이터 미연결",
        "fallback": False,
    }
    if not raw:
        return meta

    target_dates = _period_dates(raw)
    if not target_dates:
        meta["message"] = "거래일 계산 실패"
        return meta

    flow_by_code = {}
    detail = {}
    krx_stock = None
    share_meta = {"success": False, "message": "미실행"}
    try:
        krx_stock = ensure_pykrx()
        flow_by_code, detail = _batch_pykrx_flow(krx_stock, raw)
        share_meta = krx_listed_share_fallback(krx_stock, raw)
    except Exception as e:
        detail = {"errors": [str(e)], "successfulCalls": 0}

    for x in raw:
        code = str(x.get("stock_code") or "").zfill(6)
        x.update(flow_by_code.get(code, {}))

    # Coverage of the two core I inputs.
    core_cov = sum(
        x.get("foreign_netbuy_20d") is not None and x.get("institution_netbuy_20d") is not None
        for x in raw
    )

    # If the batch ranking endpoint misses a stock, fall back to the exact
    # per-stock KRX trading-VALUE endpoint. Do not use share-volume pages as money.
    if core_cov < len(raw):
        meta["fallback"] = True
        missing = [
            x for x in raw
            if x.get("foreign_netbuy_20d") is None or x.get("institution_netbuy_20d") is None
        ]

        def one(x):
            code = str(x.get("stock_code") or "").zfill(6)
            vals = _pykrx_flow_one(krx_stock, code, target_dates) if krx_stock is not None else {}
            return x, vals

        # Keep concurrency conservative because these are KRX requests.
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(one, x) for x in missing]
            for i, fut in enumerate(as_completed(futures), 1):
                try:
                    x, vals = fut.result()
                    for k, v in vals.items():
                        if x.get(k) is None:
                            x[k] = v
                except Exception:
                    pass
                if i % 20 == 0:
                    print("  KRX 수급 정밀 fallback", i, "/", len(missing))

        core_cov = sum(
            x.get("foreign_netbuy_20d") is not None and x.get("institution_netbuy_20d") is not None
            for x in raw
        )

    # Derived ratios make flows comparable across large/small caps.
    for x in raw:
        cap = x.get("market_cap_krw")
        if cap:
            for who in ("foreign","institution","pension","private"):
                for n in (5,20,60):
                    k = f"{who}_netbuy_{n}d"
                    v = x.get(k)
                    if v is not None:
                        x[f"{who}_netbuy_{n}d_pct_mcap"] = float(v) / float(cap) * 100.0

    meta.update({
        "connected": core_cov >= max(10, int(len(raw) * 0.50)),
        "coverage": core_cov,
        "total": len(raw),
        "message": f"외국인·기관 20일 수급 {core_cov}/{len(raw)}종목 연결",
        "detail": detail,
        "shareFallback": share_meta,
    })
    if meta["fallback"]:
        meta["source"] = "KRX via pykrx (batch + per-stock trading-value fallback)"
    return meta

def _flow_uk(v):
    if v is None:
        return "—"
    av = abs(float(v))
    if av >= 100_000_000:
        return f"{v/100_000_000:+,.1f}억"
    if av >= 10_000:
        return f"{v/10_000:+,.0f}만원"
    return f"{v:+,.0f}원"



# ---------------- FnGuide consensus snapshot / self-built revision history ----------------

def _flatten_df_columns(df):
    try:
        import pandas as pd
        if isinstance(df.columns, pd.MultiIndex):
            cols = []
            for tup in df.columns:
                vals = [str(x).strip() for x in tup if str(x).strip() not in ("", "nan")]
                cols.append(" ".join(dict.fromkeys(vals)))
            df.columns = cols
        else:
            df.columns = [str(c).strip() for c in df.columns]
    except Exception:
        df.columns = [str(c).strip() for c in df.columns]
    return df

def _consensus_clean(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("원", "").replace("%","")
    if s in ("", "-", "N/A", "nan", "NaN"):
        return None
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        x = float(s)
        return x if math.isfinite(x) else None
    except Exception:
        return None

def _consensus_row_value(df, patterns, col):
    if len(df.columns) == 0:
        return None
    first = df.columns[0]
    for _, r in df.iterrows():
        lab = str(r.get(first, "")).strip()
        if any(re.search(p, lab, re.I) for p in patterns):
            return _consensus_clean(r.get(col))
    return None

def fetch_fnguide_consensus(code):
    """Current forward estimates from FnGuide public Company Guide.
    Secondary source. Ambiguous/missing tables return an error rather than fabricated values.
    """
    import pandas as pd

    url = (
        "https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?"
        + urllib.parse.urlencode({
            "pGB": "1",
            "gicode": "A" + str(code).zfill(6),
            "cID": "",
            "MenuYn": "Y",
            "ReportGB": "",
            "NewMenuID": "11",
            "stkGb": "701",
        })
    )
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Referer": "https://comp.fnguide.com/",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        },
    )
    with urllib.request.urlopen(req, timeout=22) as r:
        raw = r.read()
    # FnGuide pages are usually utf-8/euc-kr compatible through parser.
    html = None
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            html = raw.decode(enc)
            break
        except Exception:
            pass
    if html is None:
        html = raw.decode("utf-8", errors="replace")

    tables = pd.read_html(io.StringIO(html))
    this_year = datetime.now(KST).year
    candidates = []

    for raw_df in tables:
        df = _flatten_df_columns(raw_df.copy())
        if len(df.columns) < 2 or len(df) == 0:
            continue
        first = df.columns[0]
        labels = " ".join(str(x) for x in df[first].head(80).tolist())
        if "EPS" not in labels:
            continue
        if "영업이익" not in labels and "매출액" not in labels:
            continue

        for col in df.columns[1:]:
            cs = str(col).strip()
            year_match = re.search(r"(20\d{2})", cs)
            yr = int(year_match.group(1)) if year_match else None
            is_est = ("E" in cs.upper()) or ("추정" in cs) or (yr is not None and yr >= this_year)
            if not is_est:
                continue

            eps = _consensus_row_value(df, [r"^EPS", r"주당순이익"], col)
            op = _consensus_row_value(df, [r"^영업이익$", r"영업이익"], col)
            sales = _consensus_row_value(df, [r"^매출액$", r"매출액", r"영업수익"], col)
            if eps is None and op is None and sales is None:
                continue

            # Prefer the nearest explicit estimate year and rows with more data.
            filled = sum(v is not None for v in (eps, op, sales))
            explicit_e = 1 if "E" in cs.upper() or "추정" in cs else 0
            year_distance = abs((yr or this_year + 9) - this_year)
            score = explicit_e * 10 + filled * 3 - year_distance
            candidates.append((score, yr or 9999, cs, sales, op, eps))

    if not candidates:
        raise RuntimeError("FnGuide 추정 실적 표를 찾지 못했습니다.")

    candidates.sort(key=lambda z: (-z[0], z[1]))
    _, _, period, sales, op, eps = candidates[0]
    return {
        "forecast_period": period,
        "forecast_sales": sales,
        "forecast_op": op,
        "forecast_eps": eps,
        "consensus_source": "FnGuide webpage snapshot",
        "consensus_url": url,
    }

def _load_consensus_history():
    if not CONSENSUS_HISTORY.exists():
        return {}
    try:
        d = json.loads(CONSENSUS_HISTORY.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def _save_consensus_history(hist):
    tmp = CONSENSUS_HISTORY.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONSENSUS_HISTORY)

def _revision_pct(cur, old):
    if cur is None or old in (None, 0):
        return None
    return (cur / old - 1.0) * 100.0

def _previous_consensus_snapshot(hist, ticker, today):
    arr = hist.get(ticker) or []
    target = today - timedelta(days=28)
    eligible = []
    for x in arr:
        try:
            d = date.fromisoformat(str(x.get("snapshot_date")))
        except Exception:
            continue
        if d <= target:
            eligible.append((d, x))
    if not eligible:
        return None
    eligible.sort(key=lambda z: z[0], reverse=True)
    return eligible[0][1]

def consensus_enrich(raw):
    """Current consensus now; 4-week revisions after 28 days of self-built daily history."""
    hist = _load_consensus_history()
    today = datetime.now(KST).date()
    today_s = today.isoformat()

    # Priority: stronger technical candidates first, while still covering a broad universe.
    target = sorted(
        raw,
        key=lambda x: (
            x.get("conditionCount", 0),
            1 if x.get("trendTemplate") else 0,
            x.get("rsPercentile", 0),
            x.get("score", 0),
        ),
        reverse=True,
    )[: min(CONSENSUS_TARGET_MAX, len(raw))]

    errors = []
    covered = 0
    mature = 0

    def one(x):
        code = str(x.get("stock_code") or str(x.get("ticker","")).split(".")[0]).zfill(6)
        return x, fetch_fnguide_consensus(code)

    with ThreadPoolExecutor(max_workers=CONSENSUS_WORKERS) as ex:
        futures = [ex.submit(one, x) for x in target]
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                x, cur = fut.result()
                prev = _previous_consensus_snapshot(hist, x.get("ticker"), today)
                sr = _revision_pct(cur.get("forecast_sales"), prev.get("forecast_sales") if prev else None)
                opr = _revision_pct(cur.get("forecast_op"), prev.get("forecast_op") if prev else None)
                er = _revision_pct(cur.get("forecast_eps"), prev.get("forecast_eps") if prev else None)

                if prev is not None:
                    mature += 1

                vals = [v for v in (er, opr, sr) if v is not None]
                if not vals:
                    sig = "UNKNOWN"
                elif any(v >= 5 for v in vals):
                    sig = "UP"
                elif any(v <= -5 for v in vals):
                    sig = "DOWN"
                else:
                    sig = "FLAT"

                x.update(cur)
                x.update({
                    "snapshot_date": today_s,
                    "sales_revision_4w": sr,
                    "op_revision_4w": opr,
                    "eps_revision_4w": er,
                    "revision_signal": sig,
                })
                covered += 1

                ticker = x.get("ticker")
                arr = hist.setdefault(ticker, [])
                arr = [z for z in arr if z.get("snapshot_date") != today_s]
                arr.append({
                    "snapshot_date": today_s,
                    "forecast_period": cur.get("forecast_period"),
                    "forecast_sales": cur.get("forecast_sales"),
                    "forecast_op": cur.get("forecast_op"),
                    "forecast_eps": cur.get("forecast_eps"),
                })
                cutoff = today - timedelta(days=150)
                clean_arr = []
                for z in arr:
                    try:
                        if date.fromisoformat(str(z.get("snapshot_date"))) >= cutoff:
                            clean_arr.append(z)
                    except Exception:
                        pass
                hist[ticker] = clean_arr
            except Exception as e:
                errors.append(str(e))
            if i % 25 == 0:
                print("  consensus", i, "/", len(target))

    # Explicit defaults for uncovered names.
    for x in raw:
        if "revision_signal" not in x:
            x["revision_signal"] = "UNKNOWN"
            x["eps_revision_4w"] = None
            x["op_revision_4w"] = None
            x["sales_revision_4w"] = None

    _save_consensus_history(hist)

    status = "LIVE" if covered >= max(10, int(len(target) * 0.50)) else ("PARTIAL" if covered else "FAILED")
    return {
        "status": status,
        "coveredCount": covered,
        "targetCount": len(target),
        "historyMatureCount": mature,
        "errorCount": len(errors),
        "source": "FnGuide webpage snapshot",
        "message": f"현재 컨센서스 {covered}/{len(target)}종목 · 4주 비교 가능 {mature}종목",
        "note": "현재 추정치는 즉시 표시됩니다. 4주 리비전은 이 대시보드가 매일 저장한 동일 추정치를 28일 전과 비교하므로 최초 4주 동안 UNKNOWN이 정상입니다.",
        "errors": errors[:20],
    }


def cs_item(status, value, criterion, why):
    return {"status": status, "value": value, "criterion": criterion, "why": why}

def add_can_slim(x, sector, mkt):
    items = {}

    # C — Current Quarterly Earnings
    eps_yoy = x.get("eps_yoy")
    eps_mode = x.get("eps_growth_mode")
    sales_yoy = x.get("sales_yoy")
    qproxy = x.get("quarter_proxy_yoy")
    qproxy_mode = x.get("quarter_proxy_growth_mode")
    qproxy_metric = x.get("quarter_proxy_metric") or "순이익"

    if eps_mode == "TURNAROUND":
        value = "최근 3개월 EPS 흑자전환"
        if sales_yoy is not None:
            value += f" · 매출 YoY {sales_yoy:+.1f}%"
        items["C"] = cs_item("PASS", value, "최근 3개월 EPS YoY ≥ +25% 또는 적자→흑자", "OpenDART (포괄)손익계산서의 3개월 당기/전기 비교입니다.")
    elif eps_yoy is not None:
        value = f"최근 3개월 EPS YoY {eps_yoy:+.1f}%"
        if sales_yoy is not None:
            value += f" · 매출 YoY {sales_yoy:+.1f}%"
        items["C"] = cs_item("PASS" if eps_yoy >= 25 else "FAIL", value, "최근 3개월 EPS YoY ≥ +25%", "분/반기 보고서 (포괄)손익계산서의 3개월 금액을 전년 동기와 비교합니다.")
    elif qproxy_mode == "TURNAROUND":
        items["C"] = cs_item(
            "PASS",
            f"EPS 미제공 → {qproxy_metric} 흑자전환 (대체지표)",
            "최근 3개월 EPS 또는 대체 이익 성장 ≥ +25%/흑자전환",
            "EPS가 노출되지 않아 OpenDART 주요계정의 순이익을 대체지표로 사용합니다. EPS와 동일한 지표는 아닙니다."
        )
    elif qproxy is not None:
        items["C"] = cs_item(
            "PASS" if qproxy >= 25 else "FAIL",
            f"EPS 미제공 → {qproxy_metric} YoY {qproxy:+.1f}% (대체지표)",
            "최근 3개월 EPS 또는 대체 이익 YoY ≥ +25%",
            "EPS가 노출되지 않아 OpenDART 주요계정의 순이익 성장률을 대체지표로 사용합니다. EPS와 동일한 지표는 아닙니다."
        )
    else:
        items["C"] = cs_item("UNKNOWN", "EPS/대체 이익 비교 데이터 없음", "최근 3개월 EPS YoY ≥ +25%", "OpenDART에서 비교 가능한 EPS 또는 순이익을 찾지 못했습니다.")

    # A — Annual Earnings Growth
    ay = x.get("latest_annual_eps_yoy")
    cagr = x.get("annual_eps_cagr_3y")
    amode = x.get("annual_growth_mode")
    proxy_yoy = x.get("latest_annual_proxy_yoy")
    proxy_cagr = x.get("annual_proxy_cagr_3y")
    proxy_metric = x.get("annual_proxy_metric") or "순이익"

    if amode == "TURNAROUND":
        items["A"] = cs_item("FAIL", "최근 연간 EPS 흑자전환 · 3년 CAGR 확인 필요", "연간 EPS YoY ≥ +25% AND 3년 EPS CAGR ≥ +25%", "흑자전환은 강하지만 3년 복합성장률을 계산할 수 없어 엄격 기준에서는 통과시키지 않습니다.")
    elif ay is not None and cagr is not None:
        items["A"] = cs_item(
            "PASS" if ay >= 25 and cagr >= 25 else "FAIL",
            f"연간 EPS YoY {ay:+.1f}% · 3년 CAGR {cagr:+.1f}%",
            "연간 EPS YoY ≥ +25% AND 3년 EPS CAGR ≥ +25%",
            "OpenDART 사업보고서의 기본주당이익으로 계산한 정식 EPS 기준입니다."
        )
    elif proxy_yoy is not None and proxy_cagr is not None:
        items["A"] = cs_item(
            "PASS" if proxy_yoy >= 25 and proxy_cagr >= 25 else "FAIL",
            f"EPS 미제공 → {proxy_metric} YoY {proxy_yoy:+.1f}% · 3년 CAGR {proxy_cagr:+.1f}% (대체지표)",
            "연간 이익 YoY ≥ +25% AND 3년 CAGR ≥ +25%",
            "OpenDART 전체재무제표에서 4개년 EPS가 확보되지 않아 지배주주순이익(없으면 당기순이익) 성장률을 대체지표로 사용합니다. EPS와 동일한 값이라고 보지는 않습니다."
        )
    else:
        items["A"] = cs_item("UNKNOWN", "연간 EPS/이익 4개년 데이터 불충분", "연간 EPS 또는 대체 이익 YoY ≥ +25% AND 3년 CAGR ≥ +25%", "OpenDART에서 비교 가능한 4개년 연간 이익 데이터를 확보하지 못했습니다.")

    # N — New
    near = x["high52Ratio"] >= 95
    has_cat = bool(x.get("new_catalyst"))
    cat_note = x.get("new_catalyst_note") or ""
    n_value = (cat_note[:55] + ("…" if len(cat_note) > 55 else "")) if has_cat else f"52주 고점比 {x['high52Ratio']:.1f}%"
    items["N"] = cs_item(
        "PASS" if has_cat or near else "FAIL",
        n_value,
        "최근 중요 신규 공시 촉매 또는 현재가 ≥ 52주 고점의 95%",
        "OpenDART 최근 공시의 공급·수주/신규투자/인수·확장/기술·허가 신호와 새로운 가격 고점을 함께 봅니다."
    )

    # S — Supply & Demand
    dr = x["demandRatio"]
    share_g = x.get("share_growth_yoy")
    dilution = x.get("dilution_filing_365d")
    dilution_note = x.get("dilution_note") or ""

    if dr < 1.15:
        status = "FAIL"
    elif share_g is not None:
        status = "PASS" if share_g <= 5 else "FAIL"
    elif dilution is False:
        # O'Neil's S is fundamentally a supply/demand test. If the stock-total API
        # cannot be compared, use one-year official dilution filings as a transparent fallback.
        status = "PASS"
    elif dilution is True:
        status = "FAIL"
    else:
        status = "UNKNOWN"

    s_value = f"상승/하락일 거래량比 {dr:.2f}x"
    if share_g is not None:
        s_value += f" · 발행주식수 YoY {share_g:+.1f}%"
        if x.get("share_growth_source"):
            s_value += " (KRX 대체)"
    elif dilution is False:
        s_value += " · 최근 1년 희석성 공시 없음"
    elif dilution is True:
        note = dilution_note[:38] + ("…" if len(dilution_note) > 38 else "")
        s_value += f" · 희석성 공시 있음: {note}"

    items["S"] = cs_item(
        status,
        s_value,
        "거래량 수요우위 ≥1.15x + 발행주식수 증가 억제(또는 최근 1년 희석성 공시 없음)",
        "발행주식수 YoY를 우선 사용하고, 비교값이 없을 때만 OpenDART의 최근 1년 유상증자·전환사채 등 희석성 공시 여부를 대체근거로 사용합니다."
    )

    ors = x["oneilRsPercentile"]
    ss = sector["score"]
    items["L"] = cs_item("PASS" if ors >= 80 and ss >= 58 else "FAIL", f"오닐식 RS {ors:.0f} · 섹터 {ss:.0f}", "RS ≥80 + 강한 섹터", "시장 내 주도주인지 봅니다.")

    # I — Institutional Sponsorship (Korea-market flow proxy)
    f20 = x.get("foreign_netbuy_20d")
    i20 = x.get("institution_netbuy_20d")
    f60 = x.get("foreign_netbuy_60d")
    i60 = x.get("institution_netbuy_60d")
    p20 = x.get("pension_netbuy_20d")
    pr20 = x.get("private_netbuy_20d")

    flow_unit_suspect = (
        f20 is not None and i20 is not None and
        abs(f20) + abs(i20) < 1_000_000
    )

    if f20 is None or i20 is None or flow_unit_suspect:
        reason = "외국인·기관 수급 데이터 확인 필요"
        if flow_unit_suspect:
            reason = "수급 금액 단위 검증 필요"
        items["I"] = cs_item(
            "UNKNOWN",
            reason,
            "외국인+기관 20일 순매수 > 0 + 중기 수급 확인",
            "오닐의 원래 I는 기관 보유·후원을 뜻합니다. 이 대시보드는 한국시장 자동화를 위해 투자자별 순매수를 대체지표로 사용하며, 데이터가 없으면 임의 판정하지 않습니다."
        )
    else:
        combined20 = f20 + i20
        combined60 = (f60 + i60) if f60 is not None and i60 is not None else None
        core_positive = combined20 > 0
        breadth20 = int(f20 > 0) + int(i20 > 0)
        medium_positive = combined60 is None or combined60 > 0
        support_positive = ((p20 is not None and p20 > 0) or (pr20 is not None and pr20 > 0))

        # PASS requires positive combined 20d institutional demand and either
        # both core groups buying, positive 60d trend, or pension/private support.
        i_pass = core_positive and (breadth20 == 2 or medium_positive or support_positive)

        val = f"외인20 {_flow_uk(f20)} · 기관20 {_flow_uk(i20)}"
        if p20 is not None:
            val += f" · 연기금20 {_flow_uk(p20)}"
        if pr20 is not None:
            val += f" · 사모20 {_flow_uk(pr20)}"
        if combined60 is not None:
            val += f" · 외인+기관60 {_flow_uk(combined60)}"

        items["I"] = cs_item(
            "PASS" if i_pass else "FAIL",
            val,
            "외국인+기관 20일 합산 순매수 > 0 + 수급 지속성/폭 확인",
            "오닐의 I(기관 후원)를 한국시장 투자자별 순매수로 근사한 지표입니다. 20일을 중심으로 60일 지속성, 연기금·사모의 동참 여부를 함께 확인합니다."
        )

    if mkt.get("pass") is None:
        items["M"] = cs_item("UNKNOWN", "시장 데이터 확인 불가", "시장 상승추세", mkt.get("note", ""))
    else:
        items["M"] = cs_item("PASS" if mkt["pass"] else "FAIL", "시장 상승" if mkt["pass"] else "시장 비우호", "KOSPI >50일선 >200일선 + 200일선 상승", mkt.get("note", ""))

    sts = [items[k]["status"] for k in "CANSLIM"]
    pass_count = sts.count("PASS")
    measured = sum(s != "UNKNOWN" for s in sts)
    full = pass_count == 7
    key = all(items[k]["status"] == "PASS" for k in ("C","A","L","M"))
    strong = (not full) and pass_count >= 5 and measured >= 6 and key
    preliminary = (not full) and (not strong) and items["L"]["status"] == "PASS" and pass_count >= 2

    x["canSlim"] = {
        "items": items,
        "passCount": pass_count,
        "measuredCount": measured,
        "unknownCount": sts.count("UNKNOWN"),
        "fullMatch": full,
        "strongCandidate": strong,
        "preliminary": preliminary,
    }


def patch_index_health_ui(html):
    """One-time safe UI enhancement: show DART / 수급 / 컨센서스 connection pills."""
    old = """`<span class="health-pill ${h.dartConnected?'good':'warn'}">DART ${h.dartConnected?'연결':'미연결'}</span>`"""
    new = """`<span class="health-pill ${h.dartConnected?'good':'warn'}">DART ${h.dartConnected?'연결':'미연결'}</span>
      <span class="health-pill ${h.flowConnected?'good':'warn'}">수급 ${h.flowConnected?'연결':'부분/미연결'}</span>
      <span class="health-pill ${h.consensusConnected?'good':'warn'}">컨센 ${h.consensusConnected?'연결':'부분/미연결'}</span>`"""
    if old in html:
        html = html.replace(old, new, 1)
    return html


def main():
    if not INDEX.exists():
        raise SystemExit("index.html을 찾지 못했습니다.")

    old_html = INDEX.read_text(encoding="utf-8")
    old_payload = extract_old_payload(old_html)
    old_mode = (old_payload.get("meta") or {}).get("mode")
    old_by_ticker = {x.get("ticker"): x for x in old_payload.get("stocks", []) if x.get("ticker")}

    print("1/8 코스피·코스닥 시가총액 목록 수집")
    listed = fetch_market_summary(0, ".KS", "KOSPI") + fetch_market_summary(1, ".KQ", "KOSDAQ")
    sector_map = kind_sector_map()
    for r in listed:
        r["sector"] = sector_map.get(r["stock_code"], "KRX 업종 미분류")

    cap_pass = [r for r in listed if r["market_cap_krw"] >= MIN_MARKET_CAP]
    if len(cap_pass) < 80:
        raise RuntimeError(f"시가총액 1조원 통과 종목이 비정상적으로 적습니다: {len(cap_pass)}")

    print("2/8 시총 1조 이상 종목 가격 이력 수집:", len(cap_pass))
    raw = []
    errors = []
    def task(meta):
        errors_local = []
        # 1순위: Yahoo Finance. GitHub Actions 서버에서 네이버 fchart가 막히는 경우를 피함.
        try:
            rows, host = fetch_yahoo_history(meta["ticker"], 5)
            x = calc_raw(meta, rows)
            x["dataSource"] = "Yahoo Finance"
            x["priceProvider"] = host
            return x
        except Exception as e:
            errors_local.append("Yahoo: " + str(e))

        # 2순위: Naver Finance. Yahoo가 개별 종목에서 실패할 때만 사용.
        try:
            rows = fetch_naver_history(meta["stock_code"], 900)
            x = calc_raw(meta, rows)
            x["dataSource"] = "NAVER Finance"
            x["priceProvider"] = "fchart.stock.naver.com"
            return x
        except Exception as e:
            errors_local.append("Naver: " + str(e))

        raise RuntimeError(" || ".join(errors_local))

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(task, r): r for r in cap_pass}
        for i, fut in enumerate(as_completed(futures), 1):
            meta = futures[fut]
            try:
                x = fut.result()
                if x["avgTradingValue50d"] >= MIN_AVG_VALUE_50D:
                    raw.append(x)
            except Exception as e:
                errors.append({"ticker": meta["ticker"], "name": meta["name"], "error": str(e)})
            if i % 30 == 0:
                print("  history", i, "/", len(cap_pass))

    if len(raw) < 50:
        raise RuntimeError(f"정상 계산 종목이 너무 적어 기존 사이트를 덮어쓰지 않습니다: {len(raw)} / 가격수집 오류 {len(errors)}개. 첫 오류: {(errors[0].get('error') if errors else '없음')}")

    print("3/8 RS / Stage 2 / 세부섹터 계산")
    rsvals = [x["rsBlend"] for x in raw]
    ovals = [x["oneilRsRaw"] for x in raw]
    for x in raw:
        x["rsPercentile"] = round(percentile(rsvals, x["rsBlend"]), 1)
        x["oneilRsPercentile"] = round(percentile(ovals, x["oneilRsRaw"]), 1)
        x["trendTemplate"] = bool(x["stage2Core"] and x["rsPercentile"] >= 70)

    groups = {}
    for x in raw:
        groups.setdefault(x["sector"], []).append(x)

    sectors = []
    for name, members in groups.items():
        ar = statistics.mean(x["rsPercentile"] for x in members)
        b60 = statistics.mean(1 if x["ma60"] and x["close"] > x["ma60"] else 0 for x in members)
        nh = statistics.mean(1 if x["high52Ratio"] >= 97 else 0 for x in members)
        vh = statistics.mean(min(x["volumeRatio"] / 2, 1) for x in members)
        st = statistics.mean(1 if x["trendTemplate"] else 0 for x in members)
        score = 0.35 * ar + 20 * b60 + 20 * nh + 15 * vh + 10 * st
        action = "강화" if score >= 72 else "상승" if score >= 58 else "중립" if score >= 45 else "약화"
        sectors.append(
            {
                "name": name,
                "score": round(score, 1),
                "rsPercentile": round(ar, 1),
                "breadth60": round(b60 * 100, 1),
                "newHighPct": round(nh * 100, 1),
                "volumeHeat": round(vh * 100, 1),
                "stage2Pct": round(st * 100, 1),
                "action": action,
                "leaders": [m["name"] for m in sorted(members, key=lambda z: z["rsPercentile"], reverse=True)[:3]],
                "memberCount": len(members),
            }
        )
    sectors.sort(key=lambda s: s["score"], reverse=True)
    sector_by_name = {s["name"]: s for s in sectors}
    mkt = market_direction()

    print("4/8 OpenDART 공식 실적·공시 연결")
    dart_meta = dart_enrich(raw, old_by_ticker)
    print("  ", dart_meta.get("message"))

    print("5/8 외국인·기관·연기금·사모 수급 연결")
    flow_meta = investor_flow_enrich(raw)
    print("  ", flow_meta.get("message"))

    print("6/8 점수 / 신호 / CAN SLIM 계산")
    today = datetime.now(KST).date().isoformat()
    for x in raw:
        add_can_slim(x, sector_by_name[x["sector"]], mkt)
        hc = max(0, min(100, (x["high52Ratio"] - 70) / 30 * 100))
        vc = min(100, x["volumeRatio"] / 2 * 100)
        base = 0.30 * x["rsPercentile"] + 25 * x["conditionCount"] / 6 + 0.15 * hc + 0.10 * vc + 20 * (1 if x["trendTemplate"] else 0)
        x["score"] = round(0.85 * base + 0.15 * sector_by_name[x["sector"]]["score"], 1)

        if x["ma60"] and x["close"] < x["ma60"]:
            sig, reason = "EXIT", "60일선 아래 — 추세 훼손"
        elif x["trendTemplate"] and x["conditionCount"] >= 5 and x["rsPercentile"] >= 70:
            sig, reason = "BUY", "Stage 2 + 성장주 6조건 5개↑ + RS 70↑"
        elif x["conditionCount"] >= 4 and x["rsPercentile"] >= 60:
            sig, reason = "WATCH", "성장주 6조건 4개↑ — 추가 확인"
        else:
            sig, reason = "NEUTRAL", "주도 조건 부족"

        old = old_by_ticker.get(x["ticker"], {}) if old_mode == "LIVE" else {}
        if sig == "BUY" and old.get("signal") in ("BUY", "HOLD"):
            x["signal"] = "HOLD"
            x["entered"] = old.get("entered") or today
        else:
            x["signal"] = sig
            x["entered"] = today if sig == "BUY" else old.get("entered")
        x["signalReason"] = reason
        if x.get("entered"):
            try:
                x["actionAge"] = (date.fromisoformat(today) - date.fromisoformat(x["entered"])).days + 1
            except Exception:
                x["actionAge"] = None
        else:
            x["actionAge"] = None

        # keep JSON compact / stable
        for k in ("close","ma20","ma50","ma60","ma120","ma150","ma200","ma20m","avgTradingValue50d","market_cap_krw"):
            if x.get(k) is not None:
                x[k] = round(float(x[k]), 2)
        for k in ("chg1d","ret252","rs5","rs20","rs60","rsBlend","oneilRsRaw","demandRatio","volumeRatio","high52Ratio","drawdown"):
            if x.get(k) is not None:
                x[k] = round(float(x[k]), 2)
        for r in x["history"]:
            for k in ("close","high","low"):
                r[k] = round(float(r[k]), 2)
            r["volume"] = int(r["volume"])

    raw.sort(key=lambda x: x["score"], reverse=True)
    asof = max(x["date"] for x in raw)

    print("7/8 FnGuide 현재 컨센서스 + 4주 리비전 이력 연결")
    consensus_meta = consensus_enrich(raw)
    print("  ", consensus_meta.get("message"))

    payload = {
        "meta": {
            "title": "WAMO MARKET RADAR · AUTO",
            "mode": "LIVE",
            "asOf": asof,
            "updatedAt": datetime.now(KST).isoformat(timespec="minutes"),
            "source": "Yahoo Finance price + NAVER market-cap/fallback + KRX KIND industry + OpenDART official fundamentals/disclosures + KRX investor flow via pykrx + FnGuide consensus snapshot",
            "universeCount": len(listed),
            "successCount": len(raw),
            "errorCount": len(errors),
            "marketDirection": {"KOREA": mkt},
            "dataHealth": {
                "liveCount": len(raw),
                "cachedCount": 0,
                "staleCount": 0,
                "failedCount": len(errors),
                "dartConnected": bool(dart_meta.get("connected")),
                "flowConnected": bool(flow_meta.get("connected")),
                "consensusConnected": consensus_meta.get("status") in ("LIVE","PARTIAL"),
                "message": f"전체 {len(listed):,}개 → 시총 1조 이상 {len(cap_pass):,}개 → 거래대금 100억원 이상 정밀계산 {len(raw):,}개 · 수급 {flow_meta.get('coverage',0):,}개",
            },
            "universeFunnel": {
                "listed": len(listed),
                "liquidityPass": len(raw),
                "marketCapPass": len(cap_pass),
                "deepScanned": len(raw),
                "growth4plus": sum(x["conditionCount"] >= 4 for x in raw),
                "stage2": sum(bool(x["trendTemplate"]) for x in raw),
                "buy": sum(x["signal"] in ("BUY", "HOLD") for x in raw),
                "liquidityThresholdKRW": MIN_AVG_VALUE_50D,
                "marketCapThresholdKRW": MIN_MARKET_CAP,
                "marketCapEnforced": True,
            },
            "dartMeta": dart_meta,
            "marketContextMeta": {"status": "LIVE" if flow_meta.get("connected") else "PARTIAL", "source": flow_meta.get("source"), "message": flow_meta.get("message")},
            "flowMeta": flow_meta,
            "consensusMeta": consensus_meta,
            "catalystMeta": {"status": "LIVE" if dart_meta.get("successCount",0) > 0 else "NOT_CONNECTED", "source": "OpenDART official"},
            "note": "최종 통합 자동갱신: 주가·거래량·시총·6조건·Stage 2·RS·CAN SLIM·OpenDART EPS/매출/ROE/공시/희석·KRX 외국인/기관/연기금/사모 수급·시장방향·FnGuide 현재 컨센서스까지 연결합니다. 4주 리비전은 매일 저장한 컨센서스 스냅샷이 28일 쌓인 뒤 자동 계산됩니다.",
        },
        "sectors": sectors,
        "stocks": raw,
        "errors": errors[:100],
    }

    print("8/8 index.html 갱신")
    new_html = replace_payload(old_html, payload)
    new_html = patch_index_health_ui(new_html)
    tmp = INDEX.with_suffix(".html.tmp")
    tmp.write_text(new_html, encoding="utf-8")
    tmp.replace(INDEX)
    print("완료:", asof, "종목", len(raw), "오류", len(errors))

if __name__ == "__main__":
    main()
