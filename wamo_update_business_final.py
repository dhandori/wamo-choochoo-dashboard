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
UA = "Mozilla/5.0 (WAMO-Market-Radar/20.0)"
MIN_MARKET_CAP = 1_000_000_000_000       # 1조원
MIN_AVG_VALUE_50D = 10_000_000_000       # 100억원
MAX_WORKERS = 10
DART_KEY = os.getenv("OPENDART_API_KEY", "").strip()
DART_TARGET_MAX = 120
PROFILE_CACHE = ROOT / "wamo_business_profiles.json"
PROFILE_TARGET_MAX = 100
PROFILE_WORKERS = 4
DART_WORKERS = 6
CONSENSUS_HISTORY = ROOT / "wamo_consensus_history.json"
CONSENSUS_TARGET_MAX = 100
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
            "note": "주가·거래량·6조건·Stage 2·RS·OpenDART 재무/공시 + 섹터 대장주/주요기업 + 회사별 기업요약과 사업보고서를 바탕으로 핵심사업·제품·고객·수익구조를 투자용으로 구조화해 자동갱신합니다. 수급·컨센서스는 사용자 설정에 따라 사용하지 않습니다.",
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
    annual = _latest_business_report_from_discs(all_discs)
    if annual:
        result["businessReportRceptNo"] = annual.get("rcept_no")
        result["businessReportDate"] = annual.get("date")
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


# ---------------- Investor-flow enrichment ----------------
# KRX's website/login changes can break pykrx scraping on cloud runners.
# For a reliable unattended dashboard, use NAVER's public per-stock
# foreign/institution net-buy SHARES and convert them to an approximate
# KRW value using that day's close.  Never present this as official KRX value.

def _naver_num(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("+", "")
    if not s or s in ("-", "nan", "NaN"):
        return None
    try:
        return float(s)
    except Exception:
        return None

def _naver_flow_rows(code, pages=5):
    """Return newest-first daily rows: date, close, institution shares, foreign shares."""
    rows = {}
    for page in range(1, pages + 1):
        url = f"https://finance.naver.com/item/frgn.naver?code={code}&page={page}"
        try:
            html = http_text(url, timeout=18, encoding="euc-kr")
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            continue

        table = soup.find("table", class_="type2")
        if table is None:
            continue

        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 7:
                continue
            vals = [td.get_text(" ", strip=True) for td in tds]
            ds = vals[0].replace(".", "-")
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", ds):
                continue

            close = _naver_num(vals[1])
            inst_shares = _naver_num(vals[5])
            foreign_shares = _naver_num(vals[6])
            if close is None:
                continue

            rows[ds] = {
                "date": ds,
                "close": close,
                "institution_shares": inst_shares,
                "foreign_shares": foreign_shares,
            }

    return [rows[k] for k in sorted(rows, reverse=True)]

def _naver_flow_one(code):
    daily = _naver_flow_rows(code, pages=5)
    if len(daily) < 20:
        return {}

    out = {
        "flow_source": "NAVER 순매수수량×종가 추정",
        "flow_is_estimate": True,
        "flow_sessions": len(daily),
    }
    for n in (5, 20, 60):
        chunk = daily[: min(n, len(daily))]
        f = 0.0
        inst = 0.0
        f_ok = 0
        i_ok = 0
        for r in chunk:
            c = r["close"]
            if r.get("foreign_shares") is not None:
                f += r["foreign_shares"] * c
                f_ok += 1
            if r.get("institution_shares") is not None:
                inst += r["institution_shares"] * c
                i_ok += 1
        # Require most sessions to be present so a partial page is not mistaken for full data.
        req = max(3, int(len(chunk) * 0.8))
        if f_ok >= req:
            out[f"foreign_netbuy_{n}d"] = f
        if i_ok >= req:
            out[f"institution_netbuy_{n}d"] = inst

    return out

def investor_flow_enrich(raw):
    """Attach foreign/institution 5/20/60-session approximate net-buy values."""
    meta = {
        "connected": False,
        "source": "NAVER investor net-buy shares × daily close (approximate KRW)",
        "coverage": 0,
        "targetCount": 0,
        "message": "수급 데이터 미연결",
        "exactOfficial": False,
        "note": "외국인·기관 순매수수량에 당일 종가를 곱한 추정금액입니다. 연기금·사모 세부수급은 공식 KRX Open API 키가 없으므로 임의 생성하지 않습니다.",
    }
    if not raw:
        return meta

    # Only stocks relevant to the screener need expensive investor-flow calls.
    candidates = sorted(
        raw,
        key=lambda x: (
            1 if x.get("conditionCount", 0) >= 4 else 0,
            1 if x.get("trendTemplate") else 0,
            x.get("rsPercentile", 0),
            x.get("score", 0),
        ),
        reverse=True,
    )
    target = [
        x for x in candidates
        if x.get("conditionCount", 0) >= 4
        or x.get("trendTemplate")
        or x.get("rsPercentile", 0) >= 70
    ][:140]
    if len(target) < min(60, len(candidates)):
        target = candidates[: min(100, len(candidates))]

    errors = []

    def one(x):
        code = str(x.get("stock_code") or "").zfill(6)
        return x, _naver_flow_one(code)

    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(one, x) for x in target]
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                x, vals = fut.result()
                x.update(vals)
            except Exception as e:
                errors.append(str(e))
            if i % 25 == 0:
                print("  수급", i, "/", len(target))

    core_cov = sum(
        x.get("foreign_netbuy_20d") is not None
        and x.get("institution_netbuy_20d") is not None
        for x in target
    )

    # Derived ratios versus market cap.
    for x in target:
        cap = x.get("market_cap_krw")
        if cap:
            for who in ("foreign", "institution"):
                for n in (5, 20, 60):
                    k = f"{who}_netbuy_{n}d"
                    v = x.get(k)
                    if v is not None:
                        x[f"{who}_netbuy_{n}d_pct_mcap"] = float(v) / float(cap) * 100.0

    required = max(10, int(len(target) * 0.60)) if target else 10
    meta.update({
        "connected": core_cov >= required,
        "coverage": core_cov,
        "targetCount": len(target),
        "message": f"외국인·기관 20일 추정수급 {core_cov}/{len(target)}개 후보 연결",
        "errors": errors[:20],
    })
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
    """Current forward estimates from NAVER CompanyInfo/WiseReport (FnGuide data).
    Uses the explicit annual estimate row, e.g. 2026(E).
    """
    import pandas as pd

    code = str(code).zfill(6)
    url = f"https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={code}&cn="
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Referer": "https://finance.naver.com/",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        },
    )
    with urllib.request.urlopen(req, timeout=22) as r:
        raw = r.read()

    html = None
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            html = raw.decode(enc)
            if "추정실적" in html or "Financial" in html or "EPS" in html:
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
        if len(df.columns) < 4 or len(df) == 0:
            continue

        # Locate the period column and estimate rows such as 2026(E).
        period_col = df.columns[0]
        coltext = " | ".join(str(c) for c in df.columns)
        if "EPS" not in coltext or "영업이익" not in coltext:
            continue

        sales_cols = [c for c in df.columns if "매출액" in str(c) and "YoY" not in str(c)]
        op_cols = [c for c in df.columns if "영업이익" in str(c)]
        eps_cols = [c for c in df.columns if re.search(r"(^|\s)EPS($|\s|\()", str(c), re.I)]

        for _, row in df.iterrows():
            period = str(row.get(period_col, "")).strip()
            m = re.search(r"(20\d{2}).*?\(E\)", period, re.I)
            if not m:
                continue
            yr = int(m.group(1))
            if yr < this_year:
                continue

            def first_value(cols):
                for c in cols:
                    v = _consensus_clean(row.get(c))
                    if v is not None:
                        return v
                return None

            sales = first_value(sales_cols)
            op = first_value(op_cols)
            eps = first_value(eps_cols)
            if sales is None and op is None and eps is None:
                continue

            candidates.append((yr, period, sales, op, eps))

    if not candidates:
        raise RuntimeError("WiseReport/FnGuide 연간 추정실적 행을 찾지 못했습니다.")

    candidates.sort(key=lambda z: z[0])
    yr, period, sales, op, eps = candidates[0]
    return {
        "forecast_period": period,
        "forecast_year": yr,
        "forecast_sales": sales,
        "forecast_op": op,
        "forecast_eps": eps,
        "consensus_source": "FnGuide via NAVER CompanyInfo/WiseReport",
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
        "source": "FnGuide via NAVER CompanyInfo/WiseReport",
        "message": f"현재 컨센서스 {covered}/{len(target)}종목 · 4주 비교 가능 {mature}종목",
        "note": "현재 추정치는 즉시 표시됩니다. 4주 리비전은 이 대시보드가 매일 저장한 동일 추정치를 28일 전과 비교하므로 최초 4주 동안 UNKNOWN이 정상입니다.",
        "errors": errors[:20],
    }



# ---------------- Business-model profile from OpenDART ----------------

def _load_profile_cache():
    if not PROFILE_CACHE.exists():
        return {}
    try:
        d = json.loads(PROFILE_CACHE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def _save_profile_cache(cache):
    tmp = PROFILE_CACHE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PROFILE_CACHE)

def _decode_doc(raw):
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("utf-8", errors="replace")

def _dart_document_text(rcept_no):
    if not DART_KEY or not rcept_no:
        return ""
    url = "https://opendart.fss.or.kr/api/document.xml?" + urllib.parse.urlencode({
        "crtfc_key": DART_KEY,
        "rcept_no": rcept_no,
    })
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=35) as r:
        raw = r.read()

    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = [n for n in z.namelist() if n.lower().endswith((".xml",".html",".htm"))]
        if not names:
            return ""
        names.sort(key=lambda n: z.getinfo(n).file_size, reverse=True)
        pieces = []
        for name in names[:5]:
            try:
                html = _decode_doc(z.read(name))
                soup = BeautifulSoup(html, "html.parser")
                t = soup.get_text(" ", strip=True)
                if t:
                    pieces.append(t)
            except Exception:
                pass
        return "\n".join(pieces)

def _business_section(text):
    if not text:
        return ""
    t = re.sub(r"\s+", " ", text)
    starts = [
        r"Ⅱ\.?\s*사업의\s*내용", r"II\.?\s*사업의\s*내용",
        r"사업의\s*내용"
    ]
    start = None
    for p in starts:
        m = re.search(p, t, re.I)
        if m:
            start = m.end()
            break
    if start is None:
        return t[:18000]

    tail = t[start:start+30000]
    ends = [
        r"Ⅲ\.?\s*재무에\s*관한\s*사항",
        r"III\.?\s*재무에\s*관한\s*사항",
        r"재무에\s*관한\s*사항"
    ]
    end = len(tail)
    for p in ends:
        m = re.search(p, tail, re.I)
        if m:
            end = min(end, m.start())
    return tail[:end]

def _clean_business_sentence(s):
    s = re.sub(r"\s+", " ", str(s or "")).strip(" -·ㆍ")
    s = re.sub(r"^\(?\d+\)?\s*", "", s)
    s = re.sub(r"^(당사|동사|회사는|회사는)\s*", "", s)
    s = re.sub(r"\[[^\]]{0,80}\]", "", s)
    s = re.sub(r"\([^)]{0,80}\)", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > 210:
        s = s[:207].rstrip() + "…"
    return s

def _extract_business_sentences(section):
    if not section:
        return []
    # Corporate filings often use both periods and table-style separators.
    parts = re.split(r"(?<=[\.다함됨임])\s+|[•●■◆▶]\s*", section)
    good_words = [
        "주요 사업","주력","영위","제조","생산","판매","공급","개발","운영",
        "서비스","플랫폼","수주","시공","제품","매출","고객","유통","임대",
        "보험","대출","반도체","화장품","의약품","자동차","기계","소프트웨어"
    ]
    bad_words = ["산업의 특성","시장 규모","시장규모","경기변동","경쟁요소","관련 법령","위험관리"]
    scored = []
    for raw in parts:
        s = _clean_business_sentence(raw)
        if len(s) < 28 or len(s) > 220:
            continue
        score = 0
        for w in good_words:
            if w in s:
                score += 2
        if "주요 사업" in s or "주력" in s or "영위" in s:
            score += 3
        if any(w in s for w in ("제조","생산","판매","공급","서비스","운영")):
            score += 2
        if any(w in s for w in bad_words):
            score -= 4
        if re.search(r"\d{4}년|설립|상장", s) and not any(w in s for w in ("사업","제조","판매","공급")):
            score -= 3
        if score >= 4:
            scored.append((score, len(s), s))
    scored.sort(key=lambda z: (-z[0], z[1]))

    out = []
    for _, _, s in scored:
        if any(s[:45] in x or x[:45] in s for x in out):
            continue
        out.append(s)
        if len(out) >= 2:
            break
    return out

def _sector_profile_template(sector):
    s = str(sector or "")
    rules = [
        (["화장품"], {
            "summary":"화장품 브랜드에 제품을 직접 판매하거나, 브랜드사를 대신해 제품을 개발·생산하는 사업입니다.",
            "customers":"국내외 화장품 브랜드, 유통사, 소비자가 주요 고객입니다.",
            "revenue":"자체 브랜드는 제품 판매액, ODM/OEM은 고객 주문량 × 납품단가가 매출의 핵심입니다.",
            "drivers":"수주 증가, 해외 고객 확대, 공장 가동률 상승, 고마진 제품 비중 상승이 실적 개선에 중요합니다."
        }),
        (["보험"], {
            "summary":"고객에게 보험상품을 판매해 보험료를 받고 위험을 인수하는 금융사업입니다.",
            "customers":"개인·기업 보험계약자가 고객이며 설계사·대리점·온라인 채널을 통해 판매합니다.",
            "revenue":"보험료에서 보험금·사업비를 뺀 보험손익과 채권·주식 등 자산운용 수익으로 돈을 법니다.",
            "drivers":"손해율 하락, 신계약 증가, 금리·운용수익률 개선, 비용 효율화가 핵심입니다."
        }),
        (["은행","금융"], {
            "summary":"자금을 조달해 대출·투자하고 금융서비스를 제공하는 사업입니다.",
            "customers":"개인, 중소기업, 대기업 등 예금·대출·결제·자산관리 고객이 핵심입니다.",
            "revenue":"예대금리차에서 나오는 이자이익과 카드·자산관리·IB 등 수수료이익으로 돈을 법니다.",
            "drivers":"대출 성장, 순이자마진, 대손비용, 연체율, 수수료 수익 증가가 실적을 좌우합니다."
        }),
        (["증권"], {
            "summary":"주식·채권 중개, 자산관리, 투자은행(IB), 자기자본 운용을 하는 금융사업입니다.",
            "customers":"개인·기관 투자자와 자금조달이 필요한 기업이 주요 고객입니다.",
            "revenue":"위탁매매·자산관리·IB 수수료와 채권·주식 운용손익에서 수익을 냅니다.",
            "drivers":"거래대금, IPO·회사채 발행, 시장금리, 보유자산 평가손익이 중요합니다."
        }),
        (["반도체"], {
            "summary":"반도체 또는 반도체 제조에 필요한 소재·부품·장비를 개발·생산해 공급하는 사업입니다.",
            "customers":"메모리·파운드리·팹리스·OSAT 등 반도체 기업이 주요 고객입니다.",
            "revenue":"칩·장비·부품의 출하량 × 판매단가가 기본 매출 구조이며 유지보수·소모품 매출이 붙기도 합니다.",
            "drivers":"고객 CAPEX, 가동률, 신규 공정 채택, 제품 믹스와 ASP 상승이 핵심입니다."
        }),
        (["의약","제약","바이오"], {
            "summary":"의약품·바이오 제품을 연구개발하고 생산·판매하거나 기술을 이전하는 사업입니다.",
            "customers":"병원·약국·유통사·글로벌 제약사 등이 주요 고객 또는 파트너입니다.",
            "revenue":"제품 판매, 위탁생산, 기술이전 계약금·마일스톤·로열티에서 수익을 냅니다.",
            "drivers":"허가·임상 성과, 처방·판매량 증가, 신제품 출시, 생산능력 확대가 중요합니다."
        }),
        (["자동차"], {
            "summary":"완성차 또는 자동차 부품을 개발·생산해 판매하는 제조업입니다.",
            "customers":"완성차 업체, 딜러·소비자 또는 1차 협력사가 주요 고객입니다.",
            "revenue":"차량·부품 출하량 × 판매단가가 핵심이며 고사양 제품 비중이 수익성에 영향을 줍니다.",
            "drivers":"완성차 생산량, 신차 사이클, 전기차 믹스, 원재료·환율이 중요합니다."
        }),
        (["건설"], {
            "summary":"주택·건축·토목·플랜트 프로젝트를 수주해 설계·시공하는 사업입니다.",
            "customers":"정부·공공기관, 시행사, 기업, 주택 수요자가 주요 발주처입니다.",
            "revenue":"수주한 공사의 진행률에 따라 공사매출을 인식하고 개발사업에서는 분양이익도 얻습니다.",
            "drivers":"신규 수주, 원가율, 공사 진행률, 분양률과 미분양, 자재비가 핵심입니다."
        }),
        (["조선"], {
            "summary":"선박·해양설비를 수주한 뒤 설계·건조해 인도하는 장기 수주산업입니다.",
            "customers":"글로벌 해운사·에너지 기업이 주요 고객입니다.",
            "revenue":"선가 × 수주량이 매출 기반이며 공정 진행에 따라 매출과 이익을 인식합니다.",
            "drivers":"신조선가, 수주잔고, 고선가 물량 비중, 후판가격, 생산성 개선이 중요합니다."
        }),
        (["기계","장비","전기"], {
            "summary":"산업용 기계·장비·전기제품을 제조해 기업 고객에게 공급하는 B2B 제조업입니다.",
            "customers":"제조공장, 건설·에너지·반도체 등 설비투자를 하는 기업이 주요 고객입니다.",
            "revenue":"장비·부품 판매와 설치·유지보수 서비스에서 매출을 올립니다.",
            "drivers":"고객 설비투자, 수주잔고, 가동률, 고사양 장비 믹스가 중요합니다."
        }),
        (["소프트웨어","IT","정보"], {
            "summary":"기업·소비자에게 소프트웨어, 플랫폼, 데이터 또는 IT 서비스를 제공하는 사업입니다.",
            "customers":"기업·공공기관·개인 사용자가 주요 고객입니다.",
            "revenue":"구독료, 라이선스, 광고, 거래수수료, 구축·운영비 등 반복 매출이 핵심입니다.",
            "drivers":"사용자·고객 수 증가, ARPU, 재계약률, 클라우드 비용과 영업레버리지가 중요합니다."
        }),
        (["통신"], {
            "summary":"유무선 통신망을 구축·운영해 개인과 기업에 통신서비스를 제공하는 사업입니다.",
            "customers":"휴대전화·인터넷 가입자와 기업·공공기관이 주요 고객입니다.",
            "revenue":"월 통신요금, 기업 네트워크·IDC·부가서비스 사용료가 핵심 매출입니다.",
            "drivers":"가입자 수, ARPU, 해지율, 설비투자 부담, 기업서비스 성장률이 중요합니다."
        }),
        (["유통","소매","도매"], {
            "summary":"상품을 매입하거나 중개해 소비자·기업에 판매하는 유통사업입니다.",
            "customers":"최종 소비자 또는 소매·기업 고객이 주요 고객입니다.",
            "revenue":"판매가와 매입가의 차이인 유통마진, 입점·중개 수수료 등으로 돈을 법니다.",
            "drivers":"점포·플랫폼 거래액, 객단가, 재고회전, 매입단가와 판촉비가 중요합니다."
        }),
        (["화학"], {
            "summary":"화학 소재·원료·제품을 생산해 다른 제조업체나 소비자에게 공급하는 사업입니다.",
            "customers":"전자·자동차·건설·생활소비재 등 다양한 제조업체가 주요 고객입니다.",
            "revenue":"판매물량 × 제품가격에서 원재료·에너지 비용을 뺀 스프레드가 핵심입니다.",
            "drivers":"제품가격, 원재료 가격, 가동률, 증설·수급 사이클이 중요합니다."
        }),
        (["식품","음료"], {
            "summary":"식품·음료를 개발·생산해 유통채널과 소비자에게 판매하는 사업입니다.",
            "customers":"대형마트·편의점·온라인몰·외식업체와 최종 소비자가 주요 고객입니다.",
            "revenue":"제품 판매량 × 판매단가가 매출의 핵심이고 브랜드력과 유통망이 마진을 좌우합니다.",
            "drivers":"판매량, 가격 인상, 원재료비, 신제품·해외매출 비중이 중요합니다."
        }),
        (["운송","해운","항공"], {
            "summary":"사람이나 화물을 운송하고 운임을 받는 사업입니다.",
            "customers":"화주·여행객·물류기업이 주요 고객입니다.",
            "revenue":"운송량 × 운임이 핵심 매출이며 연료비와 선박·항공기 가동률이 수익성에 영향을 줍니다.",
            "drivers":"운임, 물동량, 유가, 환율, 공급능력과 가동률이 중요합니다."
        }),
        (["에너지","전력","가스"], {
            "summary":"전력·가스·에너지를 생산·유통하거나 관련 설비를 운영하는 사업입니다.",
            "customers":"가정·기업·발전사·정부·공공기관 등이 주요 고객입니다.",
            "revenue":"에너지 판매량 × 판매단가 또는 장기 공급계약·설비 이용료로 수익을 냅니다.",
            "drivers":"에너지 가격, 발전·가동률, 연료비, 규제요금과 신규설비가 중요합니다."
        }),
    ]
    for keys, p in rules:
        if any(k in s for k in keys):
            return p
    return {
        "summary":f"{s or '해당'} 업종에서 제품·서비스를 개발·공급하는 기업입니다.",
        "customers":"주요 고객과 판매채널은 회사별 사업보고서에서 확인합니다.",
        "revenue":"제품·서비스 판매량과 판매단가, 계약·수수료 구조가 매출을 결정합니다.",
        "drivers":"수요 증가, 가격·제품 믹스, 가동률과 비용 구조가 실적을 좌우합니다."
    }

def _sector_easy_model(sector):
    return _sector_profile_template(sector)["summary"]


def _latest_business_report_from_discs(discs):
    rows = []
    for d in discs or []:
        nm = str(d.get("report",""))
        if "사업보고서" not in nm:
            continue
        penalty = 1 if ("정정" in nm or "첨부정정" in nm) else 0
        rows.append((penalty, str(d.get("date","")), d))
    if not rows:
        return None
    rows.sort(key=lambda z: (z[0], z[1]), reverse=False)
    # Prefer non-correction, and among same penalty choose latest date.
    best_penalty = rows[0][0]
    same = [r for r in rows if r[0] == best_penalty]
    same.sort(key=lambda z: z[1], reverse=True)
    return same[0][2]


def _latest_business_report_direct(corp_code):
    """Directly query A001 (business report), instead of hoping it appears in the general disclosure list."""
    end = datetime.now(KST).date()
    begin = end - timedelta(days=800)
    d = dart_json("list.json", {
        "corp_code": corp_code,
        "bgn_de": begin.strftime("%Y%m%d"),
        "end_de": end.strftime("%Y%m%d"),
        "last_reprt_at": "Y",
        "pblntf_ty": "A",
        "pblntf_detail_ty": "A001",
        "sort": "date",
        "sort_mth": "desc",
        "page_count": "10",
    })
    if str(d.get("status","000")) != "000":
        return None
    rows = d.get("list") or []
    if not rows:
        return None
    r = rows[0]
    return {
        "rcept_no": str(r.get("rcept_no") or ""),
        "date": str(r.get("rcept_dt") or ""),
        "report": str(r.get("report_nm") or "사업보고서"),
    }

def _score_sentence_for_bucket(s, words):
    if not s:
        return -999
    score = 0
    for w, wt in words:
        if w in s:
            score += wt
    if 35 <= len(s) <= 180:
        score += 2
    if len(s) > 240:
        score -= 3
    return score

def _pick_bucket_sentence(section, words, exclude=None):
    exclude = exclude or []
    parts = re.split(r"(?<=[\.다함됨임])\s+|[•●■◆▶]\s*|\n+", section or "")
    ranked = []
    for raw in parts:
        s = _clean_business_sentence(raw)
        if len(s) < 25 or len(s) > 260:
            continue
        if any(e in s for e in exclude):
            continue
        sc = _score_sentence_for_bucket(s, words)
        if sc >= 4:
            ranked.append((sc, -len(s), s))
    ranked.sort(reverse=True)
    return ranked[0][2] if ranked else ""

def _make_business_profile(section, sector):
    tpl = _sector_profile_template(sector)

    core = _pick_bucket_sentence(section, [
        ("주요 사업",8),("주력",7),("영위",6),("사업부문",5),("제조",4),("생산",4),("판매",4),("서비스",4),("공급",3)
    ], ["산업의 특성","시장 규모","경기변동"])

    products = _pick_bucket_sentence(section, [
        ("주요 제품",9),("제품",5),("상품",5),("서비스",5),("브랜드",4),("품목",4),("매출",2),("생산",2)
    ], ["산업의 특성","시장 규모"])

    customers = _pick_bucket_sentence(section, [
        ("주요 고객",9),("고객",6),("거래처",6),("납품",5),("수출",4),("국내외",3),("브랜드사",5),
        ("완성차",4),("제약사",4),("병원",4),("정부",3),("공공기관",3),("해운사",4)
    ], ["경쟁요소","시장 규모"])

    revenue = _pick_bucket_sentence(section, [
        ("매출",7),("판매",5),("수수료",6),("보험료",6),("이자",5),("수주",5),("계약",4),("공급",4),("납품",4)
    ], ["매출액 표","연결재무제표"])

    # Avoid repeating identical source sentences across rows.
    seen = set()
    def unique_or(value, fallback):
        v = value.strip() if value else ""
        key = v[:80]
        if not v or key in seen:
            return fallback
        seen.add(key)
        return v

    return {
        "summary": unique_or(core, tpl["summary"]),
        "products": unique_or(products, "사업보고서에서 핵심 제품·서비스 문장을 자동 추출하지 못했습니다."),
        "customers": unique_or(customers, tpl["customers"]),
        "revenue": unique_or(revenue, tpl["revenue"]),
        "drivers": tpl["drivers"],
    }



PROFILE_SCHEMA_VERSION = 4

def _fetch_fnguide_business_summary(code):
    """Company-specific Business Summary only; no consensus/revision data."""
    code = str(code).zfill(6)
    urls = [
        f"https://wcomp.fnguide.com/CompanyInfo/Snapshot?cmp_cd={code}",
        f"https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?gicode=A{code}",
    ]
    last = None
    for url in urls:
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
                    "Referer": "https://finance.naver.com/",
                },
            )
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()

            html = None
            for enc in ("utf-8", "euc-kr", "cp949"):
                try:
                    html = raw.decode(enc)
                    break
                except Exception:
                    pass
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")

            # Preferred: list items in Business Summary area.
            bullets = [
                re.sub(r"\s+", " ", li.get_text(" ", strip=True)).strip()
                for li in soup.find_all("li")
            ]
            bullets = [
                b for b in bullets
                if len(b) >= 45 and any(k in b for k in ("동사는", "회사는", "사업", "매출액", "영업이익"))
            ]
            # Keep business/operating sentences and reject menus/navigation.
            bullets = [
                b for b in bullets
                if not any(k in b for k in ("로그인", "회원가입", "다운로드", "이용약관"))
            ]
            if bullets:
                return bullets[:3], url

            # Fallback: extract text following "Business Summary".
            lines = [re.sub(r"\s+", " ", x).strip()
                     for x in soup.get_text("\n", strip=True).splitlines()]
            lines = [x for x in lines if x]
            idx = next((i for i, x in enumerate(lines) if "Business Summary" in x), None)
            if idx is not None:
                cand = []
                for x in lines[idx+1:idx+35]:
                    if any(stop in x for stop in ("업종 비교", "Band Chart", "PER Band", "Financial Highlight")):
                        break
                    if len(x) >= 45:
                        cand.append(x)
                    if len(cand) >= 3:
                        break
                if cand:
                    return cand, url
        except Exception as e:
            last = e
    if last:
        raise last
    raise RuntimeError("Business Summary 없음")

def _contains_any(t, words):
    tl = str(t or "").lower()
    return any(str(w).lower() in tl for w in words)

def _detected_segments(t):
    specs = [
        ("화장품 ODM/OEM", ["화장품 odm","화장품 oem","odm사업","odm 방식","odm방식"]),
        ("전문의약품", ["전문의약품","제약 부문","제약사업"]),
        ("H&B", ["h&b","헬스앤뷰티"]),
        ("화장품 용기", ["화장품 용기","용기 제조","연우"]),
        ("메모리 반도체", ["dram","nand","메모리 반도체"]),
        ("HBM", ["hbm"]),
        ("파운드리", ["파운드리"]),
        ("반도체 장비", ["반도체 장비","증착장비","식각장비","세정장비"]),
        ("반도체 소재·부품", ["반도체 소재","반도체 부품","웨이퍼","포토레지스트"]),
        ("2차전지", ["2차전지","배터리"]),
        ("양극재", ["양극재"]),
        ("음극재", ["음극재"]),
        ("전해액", ["전해액"]),
        ("자동차 부품", ["자동차 부품","전장부품"]),
        ("완성차", ["완성차"]),
        ("조선", ["선박","조선"]),
        ("건설·플랜트", ["건설","플랜트","토목","주택사업"]),
        ("소프트웨어·플랫폼", ["소프트웨어","플랫폼","클라우드","saas"]),
        ("보험", ["보험료","손해보험","생명보험"]),
        ("은행", ["예대마진","은행업","대출"]),
        ("증권·IB", ["증권업","투자은행","ib사업","위탁매매"]),
        ("유통·커머스", ["유통","커머스","온라인몰","도소매"]),
        ("식품·음료", ["식품","음료"]),
        ("바이오·신약", ["신약","바이오","임상"]),
        ("통신", ["이동통신","통신서비스","5g"]),
        ("전력·에너지", ["발전","전력","에너지","도시가스"]),
    ]
    out = []
    for label, words in specs:
        if _contains_any(t, words):
            out.append(label)
    return out[:7]

def _profile_from_business_summary(name, sector, bullets):
    t = " ".join(bullets or [])
    tpl = _sector_profile_template(sector)
    segs = _detected_segments(t)

    # Cosmetics ODM: highly specific model.
    if "화장품" in t and _contains_any(t, ["odm","oem"]):
        extras = []
        if _contains_any(t, ["전문의약품","제약"]):
            extras.append("전문의약품")
        if _contains_any(t, ["h&b","헬스앤뷰티"]):
            extras.append("H&B")
        if _contains_any(t, ["화장품 용기","용기 제조","연우"]):
            extras.append("화장품 용기")
        extra = f" 자회사·계열사를 통해 {'·'.join(extras)} 사업도 함께 영위합니다." if extras else ""
        return {
            "summary": f"{name}는 화장품 ODM/OEM이 핵심입니다. 고객 브랜드 대신 제품을 개발·제형화·생산해 납품합니다.{extra}",
            "products": "스킨케어·선케어·메이크업 등 화장품의 처방·제형 개발과 생산" + (f", 그리고 {'·'.join(extras)}" if extras else ""),
            "customers": "국내외 화장품 브랜드사가 핵심 고객입니다. 브랜드사는 기획·마케팅·판매를 맡고, 회사는 연구개발과 생산을 담당합니다.",
            "revenue": "화장품 부문은 고객사의 주문량 × 납품단가가 기본 매출 구조입니다. 수주가 늘고 공장 가동률이 높아질수록 고정비가 분산돼 수익성이 개선될 수 있습니다." + (" 제약·H&B·용기 사업의 제품 판매매출도 연결 실적에 더해집니다." if extras else ""),
            "drivers": "인디·글로벌 브랜드 수주, 해외 고객 확대, 선케어 등 고부가 제품 믹스, 공장 가동률, 원재료비와 신규 생산능력이 핵심입니다.",
            "segments": segs or ["화장품 ODM/OEM"],
        }

    if _contains_any(t, ["손해보험","생명보험","보험료"]):
        return {
            "summary": f"{name}는 보험료를 받고 위험을 인수하는 보험사업이 핵심입니다.",
            "products": "개인·기업 대상 보험상품과 보험자산 운용",
            "customers": "개인·기업 보험계약자가 고객이며 설계사·대리점·온라인 채널 등을 통해 판매합니다.",
            "revenue": "보험료에서 보험금·사업비를 뺀 보험손익과 채권·주식 등 보험자산의 운용수익으로 돈을 법니다.",
            "drivers": "손해율, 신계약 판매, 보험계약마진(CSM), 해지율, 금리와 운용수익률이 핵심입니다.",
            "segments": segs or ["보험"],
        }

    if _contains_any(t, ["dram","nand","메모리 반도체"]):
        return {
            "summary": f"{name}는 DRAM·NAND 등 메모리 반도체를 생산·판매하는 사업이 핵심입니다.",
            "products": "DRAM·NAND와 HBM 등 고부가 메모리 제품",
            "customers": "데이터센터·서버, PC, 스마트폰 등 전자기기·클라우드 기업이 주요 수요처입니다.",
            "revenue": "출하량 × 메모리 평균판매가격(ASP)이 매출을 좌우하고, HBM 등 고부가 제품 비중과 원가가 이익률을 결정합니다.",
            "drivers": "메모리 가격, HBM 믹스, 고객 재고, 웨이퍼 투입량, 수율과 신규 공정 전환 속도가 중요합니다.",
            "segments": segs or ["메모리 반도체"],
        }

    if _contains_any(t, ["반도체 장비","증착장비","식각장비","세정장비"]):
        return {
            "summary": f"{name}는 반도체 제조공정에 필요한 장비를 고객사에 공급하는 B2B 장비업체입니다.",
            "products": "반도체 공정 장비와 부품·유지보수 서비스",
            "customers": "반도체 제조사의 신규 팹·공정 전환 투자가 주요 수요원입니다.",
            "revenue": "신규 장비 수주·납품 매출과 설치 후 부품·서비스 매출로 돈을 법니다.",
            "drivers": "고객 CAPEX, 신규 공정 채택, 수주잔고, 장비 가동률과 서비스 매출이 중요합니다.",
            "segments": segs or ["반도체 장비"],
        }

    if _contains_any(t, ["2차전지","배터리","양극재","음극재","전해액"]):
        return {
            "summary": f"{name}는 2차전지 또는 배터리 소재·부품을 생산해 배터리·완성차 고객에 공급하는 사업입니다.",
            "products": "배터리 셀 또는 양극재·음극재·전해액 등 관련 소재·부품",
            "customers": "배터리 제조사와 완성차 업체가 주요 고객입니다.",
            "revenue": "출하량 × 판매단가가 핵심이며 원재료 가격 연동과 제품 믹스가 수익성에 영향을 줍니다.",
            "drivers": "전기차·ESS 수요, 고객 가동률, 원재료 가격, 신규 공장 램프업과 수율이 중요합니다.",
            "segments": segs or ["2차전지"],
        }

    seg_txt = " · ".join(segs)
    return {
        "summary": (f"{name}의 주요 사업영역은 {seg_txt}입니다. " if seg_txt else "") + tpl["summary"],
        "products": f"주요 사업영역: {seg_txt}" if seg_txt else "회사별 핵심 제품·서비스는 기업요약·사업보고서를 함께 확인합니다.",
        "customers": tpl["customers"],
        "revenue": tpl["revenue"],
        "drivers": tpl["drivers"],
        "segments": segs,
    }


def profile_enrich(raw):
    cache = _load_profile_cache()
    today = datetime.now(KST).date()

    candidates = sorted(
        raw,
        key=lambda x: (
            1 if x.get("conditionCount",0) >= 4 else 0,
            1 if x.get("trendTemplate") else 0,
            x.get("score",0),
            x.get("rsPercentile",0),
        ),
        reverse=True,
    )
    target = [
        x for x in candidates
        if x.get("conditionCount",0) >= 4
        or x.get("trendTemplate")
        or x.get("signal") in ("BUY","HOLD","WATCH")
    ][:PROFILE_TARGET_MAX]

    # Useful fallback on every stock.
    for x in raw:
        tpl = _sector_profile_template(x.get("sector"))
        x["businessProfile"] = {
            "summary": tpl["summary"],
            "products": "회사별 핵심 제품·서비스 정보 확인 필요",
            "customers": tpl["customers"],
            "revenue": tpl["revenue"],
            "drivers": tpl["drivers"],
            "segments": [],
        }
        x["businessModelEasy"] = tpl["summary"]
        x["businessModel"] = ""
        x["businessModelSource"] = "업종 템플릿"

    jobs = []
    for x in target:
        old = cache.get(x.get("ticker")) or {}
        fresh = False
        try:
            d = date.fromisoformat(str(old.get("updatedAt")))
            fresh = (
                (today - d).days <= 90
                and old.get("schemaVersion") == PROFILE_SCHEMA_VERSION
                and isinstance(old.get("businessProfile"), dict)
            )
        except Exception:
            fresh = False

        if fresh:
            x["businessProfile"] = old["businessProfile"]
            x["businessModelEasy"] = old["businessProfile"].get("summary") or x["businessModelEasy"]
            x["businessModelSource"] = old.get("businessModelSource","기업요약")
            x["businessModelReportDate"] = old.get("businessModelReportDate")
        else:
            jobs.append(x)

    def one(x):
        code = str(x.get("stock_code") or "").zfill(6)

        # Primary: company-specific FnGuide Business Summary, classified into
        # an investment-facing business model instead of copying the text verbatim.
        try:
            bullets, _ = _fetch_fnguide_business_summary(code)
            prof = _profile_from_business_summary(x.get("name"), x.get("sector"), bullets)
            return x, prof, "FnGuide 기업요약 기반 구조화", None
        except Exception:
            pass

        # Secondary: official OpenDART annual report.
        corp = x.get("dartCorpCode")
        if corp:
            annual = _latest_business_report_direct(corp)
            if annual and annual.get("rcept_no"):
                txt = _dart_document_text(annual["rcept_no"])
                sec = _business_section(txt)
                if len(sec) >= 300:
                    prof = _make_business_profile(sec, x.get("sector"))
                    prof.setdefault("segments", _detected_segments(sec))
                    return x, prof, "OpenDART 사업보고서 원문", annual.get("date")

        raise RuntimeError(f"{x.get('name')}: 회사별 사업설명 소스 확보 실패")

    errors = []
    fetched = 0
    with ThreadPoolExecutor(max_workers=PROFILE_WORKERS) as ex:
        futs = [ex.submit(one, x) for x in jobs]
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                x, prof, source, report_date = fut.result()
                x["businessProfile"] = prof
                x["businessModelEasy"] = prof.get("summary") or x.get("businessModelEasy")
                x["businessModelSource"] = source
                x["businessModelReportDate"] = report_date
                cache[x.get("ticker")] = {
                    "schemaVersion": PROFILE_SCHEMA_VERSION,
                    "businessProfile": prof,
                    "businessModelSource": source,
                    "businessModelReportDate": report_date,
                    "updatedAt": today.isoformat(),
                }
                fetched += 1
            except Exception as e:
                errors.append(str(e))
            if i % 15 == 0:
                print("  기업설명", i, "/", len(jobs))

    _save_profile_cache(cache)
    covered = sum(x.get("businessModelSource") != "업종 템플릿" for x in target)
    return {
        "status": "LIVE" if covered >= max(10, int(len(target)*0.60)) else "PARTIAL",
        "targetCount": len(target),
        "coveredCount": covered,
        "fetchedCount": fetched,
        "source": "FnGuide 기업요약 + OpenDART fallback",
        "message": f"회사별 투자용 비즈니스모델 {covered}/{len(target)}개 후보 연결",
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
        # PASS requires positive combined 20d demand plus breadth or positive 60d persistence.
        i_pass = core_positive and (breadth20 == 2 or medium_positive)

        val = f"외인20 {_flow_uk(f20)} · 기관20 {_flow_uk(i20)}"
        if combined60 is not None:
            val += f" · 외인+기관60 {_flow_uk(combined60)}"

        items["I"] = cs_item(
            "PASS" if i_pass else "FAIL",
            val,
            "외국인+기관 20일 추정 순매수 > 0 + 60일 지속성 확인",
            "오닐의 I(기관 후원)를 한국시장 수급으로 근사합니다. 외국인·기관의 일별 순매수수량×종가 추정금액을 사용해 20일을 중심으로 60일 지속성을 확인합니다. 공식 KRX 순매수거래대금과는 차이가 날 수 있습니다."
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



def patch_leader_profile_ui(html):
    css = """
.sector-leader{margin-top:9px;padding:8px 9px;border-radius:9px;background:#0a1424;border:1px solid #223754;font-size:10px;line-height:1.55}
.sector-leader b{color:#7fbeff}.sector-major{margin-top:5px;color:#91a3bc;font-size:9px;line-height:1.45}
.biz-pro-head{font-size:13px;font-weight:900;line-height:1.65;color:#eff6ff;margin:2px 0 10px}
.biz-pro-grid{display:grid;grid-template-columns:1fr;gap:7px}
.biz-pro-row{background:#091728;border:1px solid #25415f;border-radius:9px;padding:9px 10px}
.biz-pro-label{font-size:9px;font-weight:900;color:#75b9f4;margin-bottom:3px}
.biz-pro-value{font-size:11px;line-height:1.62;color:#c9d7e9}
.biz-pro-segments{font-size:9px;color:#88a0bd;margin-top:8px;line-height:1.5}
"""
    if ".biz-pro-grid{" not in html:
        html = html.replace("</style>", css + "\n</style>", 1)

    # Existing sector-card upgrade.
    old = """<div class="leaders">${(s.leaders||[]).slice(0,3).join(' · ')||'—'}</div>`;"""
    new = """<div class="sector-leader"><b>현재 대장 · ${(s.leaderStock&&s.leaderStock.name)||((s.leaders||[])[0]||'—')}</b><div>${s.leaderStock?`WAMO ${fmt(s.leaderStock.score,0)} · RS ${fmt(s.leaderStock.rs,0)} · ${s.leaderStock.conditions}/6${s.leaderStock.stage2?' · Stage 2':''}`:'—'}</div></div><div class="sector-major">주요기업(시총) · ${(s.majorCompanies||[]).slice(0,5).join(' · ')||'—'}</div>`;"""
    if old in html:
        html = html.replace(old, new, 1)

    # Add renderer. It reuses the OLD box's #businessEasy/#businessModel/#businessSource,
    # so it works even when previous dashboard versions already patched the HTML.
    js = r"""
  function escBiz(v){
    return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
  }
  function renderBusinessProfilePro(x){
    const be=document.getElementById('businessEasy');
    const bm=document.getElementById('businessModel');
    const bs=document.getElementById('businessSource');
    if(!be) return;
    const p=x.businessProfile||{};
    const row=(label,val)=>`<div class="biz-pro-row"><div class="biz-pro-label">${escBiz(label)}</div><div class="biz-pro-value">${escBiz(val||'확인 필요')}</div></div>`;
    be.innerHTML=`<div class="biz-pro-head">${escBiz(p.summary||x.businessModelEasy||'회사 사업구조 확인 필요')}</div>
      <div class="biz-pro-grid">
        ${row('주요 제품·서비스',p.products)}
        ${row('고객·판매처',p.customers)}
        ${row('돈 버는 구조',p.revenue)}
        ${row('실적이 좋아지는 조건',p.drivers)}
      </div>
      ${Array.isArray(p.segments)&&p.segments.length?`<div class="biz-pro-segments">사업영역 · ${p.segments.map(escBiz).join(' · ')}</div>`:''}`;
    if(bm) bm.style.display='none';
    if(bs) bs.textContent=`근거: ${x.businessModelSource||'업종 템플릿'}${x.businessModelReportDate?' · '+x.businessModelReportDate:''}`;
  }
"""
    if "function renderBusinessProfilePro(x)" not in html:
        html = html.replace("  function drawChart(x){", js + "\n  function drawChart(x){", 1)

    # Critical fix: old renderer runs first, then this renderer overwrites it.
    # Supports the exact one-line layout in the existing index.
    old_call = """$('#detailWhy').textContent=x.signalReason||'—'; updateDetailWatch();
    const list=$('#conditions');"""
    new_call = """$('#detailWhy').textContent=x.signalReason||'—'; updateDetailWatch();
    renderBusinessProfilePro(x);
    const list=$('#conditions');"""
    if old_call in html and "renderBusinessProfilePro(x);\n    const list=$('#conditions');" not in html:
        html = html.replace(old_call, new_call, 1)

    # Also support a line-broken layout, if a future index has it.
    marker = """    updateDetailWatch();
    const list=$('#conditions');"""
    repl = """    updateDetailWatch();
    renderBusinessProfilePro(x);
    const list=$('#conditions');"""
    if marker in html and "renderBusinessProfilePro(x);\n    const list=$('#conditions');" not in html:
        html = html.replace(marker, repl, 1)

    html = re.sub(
        r'\s*<span class="health-pill \$\{h\.flowConnected\?\'good\':\'warn\'\}">수급 .*?</span>',
        '', html
    )
    html = re.sub(
        r'\s*<span class="health-pill \$\{h\.consensusConnected\?\'good\':\'warn\'\}">컨센 .*?</span>',
        '', html
    )
    return html


def patch_index_health_ui(html):
    """One-time safe UI enhancement: show DART / 수급 / 컨센서스 connection pills."""
    old = """`<span class="health-pill ${h.dartConnected?'good':'warn'}">DART ${h.dartConnected?'연결':'미연결'}</span>`"""
    new = """`<span class="health-pill ${h.dartConnected?'good':'warn'}">DART ${h.dartConnected?'연결':'미연결'}</span>`"""
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

    print("5/8 수급·컨센서스 생략 (사용자 설정)")
    flow_meta = {"connected": False, "coverage": 0, "source": "사용 안 함", "message": "수급 미사용"}

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

    # Rebuild sector leadership AFTER WAMO score is finalized.
    for sec in sectors:
        members = [x for x in raw if x.get("sector") == sec.get("name")]
        leader_rank = sorted(
            members,
            key=lambda x: (
                1 if x.get("trendTemplate") else 0,
                x.get("conditionCount",0),
                x.get("score",0),
                x.get("rsPercentile",0),
                x.get("volumeRatio",0),
            ),
            reverse=True,
        )
        major_rank = sorted(
            members,
            key=lambda x: x.get("market_cap_krw") or 0,
            reverse=True,
        )
        sec["leaderStock"] = ({
            "name": leader_rank[0].get("name"),
            "ticker": leader_rank[0].get("ticker"),
            "score": leader_rank[0].get("score"),
            "rs": leader_rank[0].get("rsPercentile"),
            "stage2": bool(leader_rank[0].get("trendTemplate")),
            "conditions": leader_rank[0].get("conditionCount",0),
        } if leader_rank else None)
        sec["leaderCandidates"] = [
            {
                "name": m.get("name"), "ticker": m.get("ticker"),
                "score": m.get("score"), "rs": m.get("rsPercentile"),
                "stage2": bool(m.get("trendTemplate")),
                "conditions": m.get("conditionCount",0),
            }
            for m in leader_rank[:4]
        ]
        sec["leaders"] = [m.get("name") for m in leader_rank[:4]]
        sec["majorCompanies"] = [m.get("name") for m in major_rank[:5]]

    profile_meta = profile_enrich(raw)
    print("  ", profile_meta.get("message"))

    asof = max(x["date"] for x in raw)

    print("7/8 섹터 대장주 + 기업 비즈니스모델 연결")
    consensus_meta = {"status": "NOT_USED", "source": "사용 안 함", "message": "컨센서스 미사용"}

    payload = {
        "meta": {
            "title": "WAMO MARKET RADAR · AUTO",
            "mode": "LIVE",
            "asOf": asof,
            "updatedAt": datetime.now(KST).isoformat(timespec="minutes"),
            "source": "Yahoo Finance price + NAVER market-cap/fallback + KRX KIND industry + OpenDART official fundamentals/disclosures + OpenDART fundamentals/disclosures + sector leadership + OpenDART business-profile summary",
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
                "flowConnected": False,
                "consensusConnected": False,
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
            "profileMeta": profile_meta,
            "marketContextMeta": {"status": "LIVE" if flow_meta.get("connected") else "PARTIAL", "source": flow_meta.get("source"), "message": flow_meta.get("message")},
            "flowMeta": flow_meta,
            "consensusMeta": consensus_meta,
            "catalystMeta": {"status": "LIVE" if dart_meta.get("successCount",0) > 0 else "NOT_CONNECTED", "source": "OpenDART official"},
            "note": "최종 통합 자동갱신: 주가·거래량·시총·6조건·Stage 2·RS·CAN SLIM·OpenDART EPS/매출/ROE/공시/희석·NAVER 외국인/기관 추정수급·시장방향·FnGuide 현재 컨센서스까지 연결합니다. 4주 리비전은 매일 저장한 컨센서스 스냅샷이 28일 쌓인 뒤 자동 계산됩니다.",
        },
        "sectors": sectors,
        "stocks": raw,
        "errors": errors[:100],
    }

    print("8/8 index.html 갱신")
    new_html = replace_payload(old_html, payload)
    new_html = patch_index_health_ui(new_html)
    new_html = patch_leader_profile_ui(new_html)
    tmp = INDEX.with_suffix(".html.tmp")
    tmp.write_text(new_html, encoding="utf-8")
    tmp.replace(INDEX)
    print("완료:", asof, "종목", len(raw), "오류", len(errors))

if __name__ == "__main__":
    main()
