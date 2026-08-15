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
from bs4 import BeautifulSoup
import html as html_lib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.html"
KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (WAMO-Market-Radar/29.0)"
MIN_MARKET_CAP = 1_000_000_000_000       # 1조원
MIN_AVG_VALUE_50D = 10_000_000_000       # 100억원
MAX_WORKERS = 10
DART_KEY = os.getenv("OPENDART_API_KEY", "").strip()
DART_TARGET_MAX = 48
PROFILE_CACHE = ROOT / "wamo_business_profiles.json"
PROFILE_TARGET_MAX = 18
PROFILE_WORKERS = 4
DART_WORKERS = 5
DART_FRESH_CALL_MAX = 18
DART_BACKFILL_PER_RUN = 4
DART_REFRESH_DAYS = 3
PROFILE_PRIORITY_MAX = 10
PROFILE_BACKFILL_PER_RUN = 8
PROFILE_RETRY_DAYS = 3
CONSENSUS_HISTORY = ROOT / "wamo_consensus_history.json"
CONSENSUS_TARGET_MAX = 100
CONSENSUS_WORKERS = 6

FUND_PREFIXES = (
    "KODEX", "TIGER", "ACE", "RISE", "SOL", "PLUS", "HANARO",
    "KBSTAR", "KOSEF", "ARIRANG", "TIMEFOLIO", "WOORI", "1Q",
    "KINDEX", "KIWOOM", "FOCUS", "BNK ", "KOACT", "TIME ",
    "히어로즈 ", "마이티 ", "에셋플러스 ", "파워 ", "TREX",
    "UNICORN", "TRUSTON", "MASTER", "WON ", "KCGI ", "VITA ", "HK ",
)

def classify_instrument(name):
    """기업 스크리닝과 ETF/ETN을 같은 기준으로 섞지 않도록 증권 유형을 구분합니다."""
    n = str(name or "").strip().upper()
    if n.startswith(FUND_PREFIXES) or " ETF" in n or " ETN" in n or n.endswith("ETN"):
        return "ETF_ETN"
    if "스팩" in n or "SPAC" in n:
        return "SPAC"
    if "리츠" in n or "REIT" in n:
        return "REIT"
    return "COMPANY"

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

def fetch_yahoo_history(ticker: str, years=None):
    """GitHub Actions에서 사용할 1순위 가격 경로.
    query1/query2를 순차 시도하고, 수정주가 비율로 OHLC를 보정합니다.
    """
    errors = []
    now = int(time.time())
    # years=None이면 Yahoo가 보유한 상장 이후 전체 수정주가 이력을 사용합니다.
    start = 0 if years is None else now - years * 370 * 86400
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
                # 신규 상장주도 후보에서 통째로 빠지지 않게 60거래일부터 허용합니다.
                # 200일선·Stage 2·정배열은 calc_raw에서 이력 부족으로 별도 표시합니다.
                if len(rows) < 60:
                    raise RuntimeError(f"Yahoo 가격 이력 부족: {len(rows)}일")
                return rows, host

            except Exception as e:
                errors.append(f"{host} attempt {attempt+1}: {e}")
                time.sleep(1.0 + attempt * 0.8)

    raise RuntimeError(" / ".join(errors[-6:]))

def fetch_naver_history(code: str, count=10000):
    url = f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count={count}&requestType=0"
    raw = http_bytes(url)
    # 네이버 fchart는 XML 선언에 EUC-KR/CP949를 명시하는 경우가 있다.
    # ElementTree에 원시 바이트를 바로 넘기면 GitHub Actions에서
    # "multi-byte encodings are not supported"가 발생할 수 있으므로 먼저 해석한다.
    text = None
    for enc in ("euc-kr", "cp949", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("euc-kr", errors="replace")
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text, count=1, flags=re.I)
    root = ET.fromstring(text)
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
    if len(rows) < 60:
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

def alignment_history(rows):
    """현재가 > MA20 > MA60 > MA120 > MA200 정배열의 연속 구간을 계산합니다.

    과거 최초 1회가 아니라, 중간 이탈을 반영한 '현재 연속 구간 시작일'을 우선합니다.
    현재 정배열이 아니면 가장 최근 정배열 구간을 반환합니다.
    """
    closes = [float(r["close"]) for r in rows]
    n = len(closes)
    cumulative = [0.0]
    for value in closes:
        cumulative.append(cumulative[-1] + value)

    def rolling_mean(i, window):
        if i + 1 < window:
            return None
        return (cumulative[i + 1] - cumulative[i + 1 - window]) / window

    aligned = [False] * n
    ma20_series = [None] * n
    ma60_series = [None] * n
    ma120_series = [None] * n
    ma200_series = [None] * n
    for i in range(199, n):
        ma20_i = rolling_mean(i, 20)
        ma60_i = rolling_mean(i, 60)
        ma120_i = rolling_mean(i, 120)
        ma200_i = rolling_mean(i, 200)
        ma20_series[i] = ma20_i
        ma60_series[i] = ma60_i
        ma120_series[i] = ma120_i
        ma200_series[i] = ma200_i
        aligned[i] = bool(closes[i] > ma20_i > ma60_i > ma120_i > ma200_i)

    def break_reason(i):
        ma20_i, ma60_i = ma20_series[i], ma60_series[i]
        ma120_i, ma200_i = ma120_series[i], ma200_series[i]
        if ma200_i is None:
            return "200일 이력 부족"
        if closes[i] <= ma20_i:
            return "현재가가 20일선 이하로 이탈"
        if ma20_i <= ma60_i:
            return "20일선이 60일선 이하로 하락"
        if ma60_i <= ma120_i:
            return "60일선이 120일선 이하로 하락"
        if ma120_i <= ma200_i:
            return "120일선이 200일선 이하로 하락"
        return "정배열 순서 이탈"

    def run_bounds(end_idx):
        if end_idx is None or end_idx < 0 or not aligned[end_idx]:
            return None
        start_idx = end_idx
        while start_idx > 0 and aligned[start_idx - 1]:
            start_idx -= 1
        return start_idx, end_idx

    asof = date.fromisoformat(rows[-1]["date"])
    current = bool(aligned[-1])
    bounds = run_bounds(n - 1) if current else None
    if not bounds:
        latest_end = next((i for i in range(n - 1, -1, -1) if aligned[i]), None)
        bounds = run_bounds(latest_end)

    result = {
        "isAligned": current,
        "criterion": "현재가 > MA20 > MA60 > MA120 > MA200",
        "firstAlignedDate": None,
        "initialPreparationStartDate": rows[199]["date"] if n >= 200 else None,
        "initialPreparationTradingDays": 0,
        "initialPreparationWeeks": 0.0,
        "activePreparationStartDate": None,
        "activePreparationTradingDays": 0,
        "activePreparationWeeks": 0.0,
        "currentStartDate": None,
        "currentTradingDays": 0,
        "currentCalendarDays": 0,
        "currentWeeks": 0.0,
        "lastStartDate": None,
        "lastEndDate": None,
        "lastTradingDays": 0,
        "daysSinceLastEnd": None,
        "events": [],
    }

    first_idx = next((i for i, value in enumerate(aligned) if value), None)
    if first_idx is not None:
        result["firstAlignedDate"] = rows[first_idx]["date"]
        prep_start_dt = date.fromisoformat(rows[199]["date"])
        first_dt = date.fromisoformat(rows[first_idx]["date"])
        result["initialPreparationTradingDays"] = first_idx - 199 + 1
        result["initialPreparationWeeks"] = round((first_dt - prep_start_dt).days / 7, 1)

    events = []
    last_break_idx = None
    previous = False
    for i in range(199, n):
        if aligned[i] and not previous:
            event_type = "FIRST" if not events else "RECOVER"
            gap_days = (i - last_break_idx) if last_break_idx is not None else None
            prep_start_idx = 199 if event_type == "FIRST" else last_break_idx
            prep_calendar_days = None
            if prep_start_idx is not None:
                prep_calendar_days = (date.fromisoformat(rows[i]["date"]) - date.fromisoformat(rows[prep_start_idx]["date"])).days
            events.append({
                "type": event_type,
                "date": rows[i]["date"],
                "label": "최초 정배열 도달" if event_type == "FIRST" else "정배열 회복",
                "gapTradingDays": gap_days,
                "preparationStartDate": rows[prep_start_idx]["date"] if prep_start_idx is not None else None,
                "preparationTradingDays": (i - prep_start_idx + 1) if prep_start_idx is not None else None,
                "preparationWeeks": round(prep_calendar_days / 7, 1) if prep_calendar_days is not None else None,
                "shortBreak": bool(gap_days is not None and gap_days <= 5),
            })
        elif not aligned[i] and previous:
            last_break_idx = i
            events.append({
                "type": "BREAK",
                "date": rows[i]["date"],
                "label": "정배열 이탈",
                "reason": break_reason(i),
            })
        previous = aligned[i]
    result["events"] = events[-20:]
    active_prep_idx = None
    if not current:
        if last_break_idx is not None:
            active_prep_idx = last_break_idx
        elif n >= 200 and first_idx is None:
            active_prep_idx = 199
    if active_prep_idx is not None:
        result["activePreparationStartDate"] = rows[active_prep_idx]["date"]
        result["activePreparationTradingDays"] = n - active_prep_idx
        result["activePreparationWeeks"] = round(
            (asof - date.fromisoformat(rows[active_prep_idx]["date"])).days / 7, 1
        )
    if not bounds:
        return result

    start_idx, end_idx = bounds
    start_dt = date.fromisoformat(rows[start_idx]["date"])
    end_dt = date.fromisoformat(rows[end_idx]["date"])
    trading_days = end_idx - start_idx + 1
    result.update({
        "lastStartDate": rows[start_idx]["date"],
        "lastEndDate": rows[end_idx]["date"],
        "lastTradingDays": trading_days,
        "daysSinceLastEnd": (asof - end_dt).days,
    })
    if current:
        calendar_days = (asof - start_dt).days
        result.update({
            "currentStartDate": rows[start_idx]["date"],
            "currentTradingDays": trading_days,
            "currentCalendarDays": calendar_days,
            "currentWeeks": round(calendar_days / 7, 1),
        })
    return result

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
            "note": "주가·거래량·6조건·Stage 2·RS·OpenDART 재무/공시 + 섹터 대장주/주요기업 + OpenDART 사업보고서 원문만 사용해 핵심사업·제품·고객·수익구조를 투자용으로 구조화해 자동갱신합니다. 수급·컨센서스는 사용자 설정에 따라 사용하지 않습니다.",
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
    historical_high = max(highs)
    historical_high_idx = max(i for i, h in enumerate(highs) if h >= historical_high * 0.999999)
    high3 = max(highs[-min(756, len(highs)):])
    idx3 = max(i for i, h in enumerate(highs) if h >= high3 * 0.999999)
    since3 = len(rows) - 1 - idx3
    high_ratio = closes[-1] / high52
    historical_high_ratio = closes[-1] / historical_high
    alignment = alignment_history(rows)
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
        "52주 신고가권(7% 이내)": bool(high_ratio >= 0.93),
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
        "historyTradingDays": len(rows),
        "historyReady200": len(rows) >= 200,
        "high52WindowDays": min(252, len(rows)),
        "technicalCoverage": "FULL" if len(rows) >= 260 else "SHORT_HISTORY",
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
        "historicalHighRatio": historical_high_ratio * 100,
        "historicalHighDate": rows[historical_high_idx]["date"],
        "historyStartDate": rows[0]["date"],
        "alignment": alignment,
        "drawdown": (high_ratio - 1) * 100,
        "ma20": ma20, "ma50": ma50, "ma60": ma60, "ma120": ma120,
        "ma150": ma150, "ma200": ma200, "ma20m": ma20m,
        "avgTradingValue50d": avg_value_50d,
        "conditions": cond,
        "conditionCount": sum(cond.values()),
        "stage2Checks": st,
        "stage2Core": all(st.values()),
        # MA200과 정배열 시작 표시를 위해 최근 260거래일을 차트에 전달합니다.
        "history": rows[-260:],
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

def _copy_dart_cache(x, old):
    """Reuse quarterly/annual DART facts already embedded in yesterday's index.
    DART statements do not need a full-company refetch on every market-data run.
    """
    if not isinstance(old, dict):
        return False
    if old.get("dartStatus") not in ("LIVE", "CACHED", "PARTIAL"):
        return False

    exact = {
        "dartCorpCode","dartSource","dartReportYear","dartReportCode","dartFsDiv",
        "eps_cur","eps_prev","eps_yoy","eps_growth_mode",
        "sales_cur","sales_prev","sales_yoy","sales_growth_mode",
        "annualEpsSeries","latest_annual_eps_yoy","annual_eps_cagr_3y","annual_growth_mode",
        "latest_annual_proxy_yoy","annual_proxy_growth_mode","annual_proxy_cagr_3y",
        "annual_proxy_metric","annual_proxy_series","roe",
        "issued_shares","share_growth_yoy","share_report_year","share_report_code",
        "catalysts","new_catalyst","new_catalyst_note",
        "dilution_events_365d","dilution_filing_365d","dilution_note",
        "businessReportRceptNo","businessReportDate","dartFetchedAt",
    }
    copied = False
    for k in exact:
        if k in old:
            x[k] = old[k]
            copied = True
    if copied:
        x["dartStatus"] = "CACHED"
        x["dartSource"] = old.get("dartSource") or "OpenDART official"
    return copied

def _dart_cache_fresh(old, today):
    if not isinstance(old, dict) or old.get("dartStatus") not in ("LIVE","CACHED","PARTIAL"):
        return False
    s = old.get("dartFetchedAt")
    if not s:
        # Legacy dashboard data is accepted once, then stamped today by V26.
        return True
    try:
        d = date.fromisoformat(str(s)[:10])
        return (today - d).days < DART_REFRESH_DAYS
    except Exception:
        return False

def _dart_priority_key(x, old_by_ticker):
    old = old_by_ticker.get(x.get("ticker"), {})
    is_new = 1 if not old else 0
    watch_like = 1 if (x.get("conditionCount",0) >= 4 and x.get("rsPercentile",0) >= 60) else 0
    stage2 = 1 if x.get("trendTemplate") else 0
    near_high = 1 if x.get("high52Ratio",0) >= 93 else 0
    old_live = 1 if old.get("signal") in ("BUY","HOLD","WATCH") else 0
    return (
        is_new,
        watch_like,
        stage2,
        near_high,
        old_live,
        x.get("conditionCount",0),
        x.get("rsPercentile",0),
        x.get("high52Ratio",0),
    )

def dart_enrich(raw, old_by_ticker):
    meta = {
        "connected": False,
        "targetCount": 0,
        "successCount": 0,
        "errorCount": 0,
        "cachedCount": 0,
        "fetchedCount": 0,
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

    today = datetime.now(KST).date()
    errors = []

    # First reuse all available stock-level DART data from the prior dashboard.
    cached = []
    missing_or_stale = []
    for x in raw:
        code = x.get("stock_code") or str(x.get("ticker","")).split(".")[0]
        corp = cmap.get(code)
        if corp:
            x["dartCorpCode"] = corp
        old = old_by_ticker.get(x.get("ticker"), {})
        if corp and _dart_cache_fresh(old, today) and _copy_dart_cache(x, old):
            x["dartFetchedAt"] = today.isoformat()
            cached.append(x)
        elif corp:
            # Copy stale values too, so a temporary refresh failure never blanks the UI.
            _copy_dart_cache(x, old)
            missing_or_stale.append(x)

    # Candidate-driven priority. New/strong names are refreshed first.
    ordered = sorted(
        missing_or_stale,
        key=lambda x: _dart_priority_key(x, old_by_ticker),
        reverse=True,
    )
    priority = [
        x for x in ordered
        if (
            (x.get("conditionCount",0) >= 4 and x.get("rsPercentile",0) >= 60)
            or x.get("trendTemplate")
            or x.get("high52Ratio",0) >= 93
            or old_by_ticker.get(x.get("ticker"), {}).get("signal") in ("BUY","HOLD","WATCH")
            or not old_by_ticker.get(x.get("ticker"))
        )
    ]

    selected = priority[:DART_FRESH_CALL_MAX]
    selected_ids = {x.get("ticker") for x in selected}

    # Small background fill only; never refetch the whole universe in one run.
    if len(selected) < DART_FRESH_CALL_MAX:
        for x in ordered:
            if x.get("ticker") in selected_ids:
                continue
            selected.append(x)
            selected_ids.add(x.get("ticker"))
            if len(selected) >= min(DART_FRESH_CALL_MAX, len(priority) + DART_BACKFILL_PER_RUN):
                break

    tasks = []
    with ThreadPoolExecutor(max_workers=DART_WORKERS) as ex:
        for x in selected:
            corp = x.get("dartCorpCode")
            if not corp:
                continue
            fut = ex.submit(enrich_one_dart, x, corp, old_by_ticker.get(x.get("ticker"), {}))
            tasks.append((fut, x, corp))

        for i, (fut, x, corp) in enumerate(tasks, 1):
            try:
                d = fut.result()
                d["dartFetchedAt"] = today.isoformat()
                x.update(d)
            except Exception as e:
                # Keep copied stale values if present.
                if x.get("dartStatus") not in ("LIVE","CACHED","PARTIAL"):
                    x["dartStatus"] = "ERROR"
                errors.append({"ticker": x.get("ticker"), "error": str(e)})
            if i % 8 == 0 or i == len(tasks):
                print("  DART 재무/공시", i, "/", len(tasks))

    # ROE only for the small freshly processed set.
    by_report = {}
    for x in selected:
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
    for x in selected:
        corp = x.get("dartCorpCode")
        if corp in roe_map:
            x["roe"] = roe_map[corp]

    usable = sum(x.get("dartStatus") in ("LIVE","CACHED","PARTIAL") for x in raw)
    fresh = sum(x.get("dartStatus") == "LIVE" and x.get("dartFetchedAt") == today.isoformat() for x in selected)
    meta.update({
        "targetCount": len(selected),
        "successCount": usable,
        "cachedCount": len(cached),
        "fetchedCount": fresh,
        "errorCount": len(errors),
        "message": f"OpenDART 사용가능 {usable}/{len(raw)}개 · 캐시 {len(cached)} · 이번 조회 {len(selected)}개",
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



PROFILE_SCHEMA_VERSION = 9

def _fetch_naver_company_overview(code):
    """Company-specific overview from NAVER Finance.
    NAVER's 기업개요 is sourced from FnGuide and is on the same domain already used
    successfully by this updater for Korean market data.
    """
    code = str(code).zfill(6)
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    html = http_text(url, timeout=20, encoding="euc-kr")
    soup = BeautifulSoup(html, "html.parser")

    # Preferred legacy DOM: summary_info contains the company overview paragraphs.
    overview = []
    box = soup.select_one(".summary_info")
    if box:
        for p in box.find_all(["p","li"]):
            s = re.sub(r"\s+", " ", p.get_text(" ", strip=True)).strip()
            if len(s) >= 25 and "출처" not in s:
                overview.append(s)

    # Robust fallback: text between 기업개요 and FnGuide source / MY STOCK.
    if not overview:
        lines = [
            re.sub(r"\s+", " ", x).strip()
            for x in soup.get_text("\n", strip=True).splitlines()
        ]
        lines = [x for x in lines if x]
        idx = next((i for i,x in enumerate(lines) if x == "기업개요"), None)
        if idx is not None:
            for x in lines[idx+1:idx+15]:
                if "출처" in x or "MY STOCK" in x or "네이버 주식거래연결" in x:
                    break
                if len(x) >= 25:
                    overview.append(x)
                if len(overview) >= 4:
                    break

    # Naver also exposes a more useful peer-industry name than KRX's legal industry.
    page_text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    industry = None
    m = re.search(r"동종업종비교\s*\(\s*업종명\s*:\s*([^｜\|\)]+)", page_text)
    if m:
        industry = m.group(1).strip()

    if not overview:
        raise RuntimeError("NAVER 기업개요를 찾지 못했습니다.")

    return overview[:4], industry, url


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
    sector_name = str(sector or "").strip()
    return {
        "summary": (f"{name}의 기업개요에서 확인되는 주요 사업은 {seg_txt}입니다. " if seg_txt else f"{name}는 {sector_name or '해당'} 업종에서 사업을 영위합니다. ") + tpl["summary"],
        "products": f"기업개요에서 확인되는 주요 사업영역: {seg_txt}" if seg_txt else f"{sector_name or '해당 업종'}의 제품·서비스",
        "customers": tpl["customers"],
        "revenue": tpl["revenue"],
        "drivers": tpl["drivers"],
        "segments": segs,
    }



def _latest_business_report_plain(corp_code):
    """Find the latest annual business report using the same OpenDART list API
    already proven to work in this dashboard. No fragile detail-type filters.
    """
    end = datetime.now(KST).date()
    begin = end - timedelta(days=900)
    d = dart_json("list.json", {
        "corp_code": corp_code,
        "bgn_de": begin.strftime("%Y%m%d"),
        "end_de": end.strftime("%Y%m%d"),
        "sort": "date",
        "sort_mth": "desc",
        "page_count": "100",
    })
    if str(d.get("status", "000")) != "000":
        raise RuntimeError(f"공시검색 실패 {d.get('status')} {d.get('message','')}")
    rows = d.get("list") or []
    annuals = []
    for r in rows:
        nm = str(r.get("report_nm") or "")
        if "사업보고서" not in nm:
            continue
        # Prefer the original filing over correction filings.
        penalty = 1 if "정정" in nm else 0
        annuals.append((
            penalty,
            str(r.get("rcept_dt") or ""),
            {
                "rcept_no": str(r.get("rcept_no") or ""),
                "date": str(r.get("rcept_dt") or ""),
                "report": nm,
            }
        ))
    if not annuals:
        raise RuntimeError("최근 900일 사업보고서 없음")
    annuals.sort(key=lambda z: (z[0], -int(z[1] or 0)))
    return annuals[0][2]

def _dart_document_text_robust(rcept_no):
    """Download OpenDART original filing.
    Official response is ZIP binary. If OpenDART returns an XML error body,
    surface the exact status/message instead of silently falling back.
    """
    if not DART_KEY:
        raise RuntimeError("DART API KEY 없음")
    if not rcept_no:
        raise RuntimeError("사업보고서 접수번호 없음")

    url = "https://opendart.fss.or.kr/api/document.xml?" + urllib.parse.urlencode({
        "crtfc_key": DART_KEY,
        "rcept_no": rcept_no,
    })
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=8) as r:
        raw = r.read()

    # Normal path: ZIP file.
    if zipfile.is_zipfile(io.BytesIO(raw)):
        parts = []
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = [
                n for n in z.namelist()
                if n.lower().endswith((".xml", ".html", ".htm"))
            ]
            if not names:
                raise RuntimeError("원문 ZIP 안에 XML/HTML 없음")

            # Largest document files first; annual reports often span multiple XML files.
            names.sort(key=lambda n: z.getinfo(n).file_size, reverse=True)
            for name in names[:12]:
                try:
                    body = z.read(name)
                    html = _decode_doc(body)
                    soup = BeautifulSoup(html, "html.parser")
                    t = soup.get_text("\n", strip=True)
                    if len(t) >= 100:
                        parts.append(t)
                except Exception:
                    pass

        joined = "\n".join(parts)
        if len(joined) < 500:
            raise RuntimeError(f"사업보고서 원문 텍스트 너무 짧음({len(joined)})")
        return joined

    # Error path: OpenDART may return plain XML instead of ZIP.
    err = _decode_doc(raw)
    status = ""
    message = ""
    try:
        root = ET.fromstring(err)
        status = (root.findtext("status") or "").strip()
        message = (root.findtext("message") or "").strip()
    except Exception:
        pass
    if status or message:
        raise RuntimeError(f"원문 API 오류 {status} {message}")
    raise RuntimeError("원문 응답이 ZIP이 아님")

def _business_section_robust(text):
    """Extract the annual report '사업의 내용' area while preserving enough context."""
    if not text:
        return ""
    t = text.replace("\r", "\n")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)

    patterns = [
        r"Ⅱ\s*[\.\-]?\s*사업의\s*내용",
        r"II\s*[\.\-]?\s*사업의\s*내용",
        r"2\s*[\.\-]?\s*사업의\s*내용",
        r"사업의\s*내용",
    ]
    starts = []
    for p in patterns:
        for m in re.finditer(p, t, re.I):
            starts.append(m.end())
    if not starts:
        # Whole filing still contains useful product/customer/business sentences.
        return t[:70000]

    start = min(starts)
    tail = t[start:start+90000]

    end_patterns = [
        r"Ⅲ\s*[\.\-]?\s*재무에\s*관한\s*사항",
        r"III\s*[\.\-]?\s*재무에\s*관한\s*사항",
        r"3\s*[\.\-]?\s*재무에\s*관한\s*사항",
        r"재무에\s*관한\s*사항",
    ]
    ends = []
    for p in end_patterns:
        m = re.search(p, tail, re.I)
        if m:
            ends.append(m.start())
    if ends:
        tail = tail[:min(ends)]
    return tail

def _best_sentence(section, weighted_words, bad_words=None):
    bad_words = bad_words or []
    if not section:
        return ""
    # Keep paragraph boundaries; filings mix prose and table text.
    chunks = re.split(r"\n+|(?<=[다함됨임\.])\s+", section)
    ranked = []
    for raw in chunks:
        s = re.sub(r"\s+", " ", str(raw or "")).strip(" -·ㆍ|")
        if len(s) < 28 or len(s) > 320:
            continue
        score = 0
        for word, weight in weighted_words:
            if word.lower() in s.lower():
                score += weight
        for word in bad_words:
            if word in s:
                score -= 5
        if 40 <= len(s) <= 200:
            score += 2
        if score >= 5:
            ranked.append((score, -len(s), s))
    ranked.sort(reverse=True)
    return ranked[0][2] if ranked else ""

def _profile_from_dart_report(name, sector, report_text):
    section = _business_section_robust(report_text)
    whole = re.sub(r"\s+", " ", section)
    tpl = _sector_profile_template(sector)
    segs = _detected_segments(whole)

    # High-confidence business-model special cases.
    head = whole[:25000].lower()
    nlow = str(name or "").lower()
    ksec = str(sector or "").lower()

    if (
        nlow.endswith("금융지주") or nlow.endswith("지주")
        or "금융지주회사로서" in head
        or ("금융지주회사" in head and any(w in head for w in ("은행","증권","카드")))
    ):
        return {
            "summary": f"{name}는 은행·증권·카드·보험 등 금융계열사를 보유·관리하는 금융지주회사입니다.",
            "products": "은행·증권·카드·보험·자산관리 등 자회사 금융서비스",
            "customers": "직접 영업보다 자회사들이 개인·기업·기관 고객에게 금융서비스를 제공합니다.",
            "revenue": "자회사 이익이 연결 실적에 반영되고, 지주회사는 배당·브랜드·용역 등에서도 수익을 얻습니다.",
            "drivers": "핵심 자회사 순이자마진·대손비용·비이자이익, 자본비율, 자회사 배당과 주주환원이 중요합니다.",
            "segments": segs or ["금융지주"],
        }

    if (
        "회사 본부" in ksec or nlow.endswith("홀딩스")
        or ("지주회사로서" in head and "자회사" in head)
        or ("순수지주회사" in head and "자회사" in head)
        or (
            "지주회사" in head
            and any(w in head for w in ("자회사", "계열사", "지분", "배당", "보유"))
        )
    ):
        return {
            "summary": f"{name}는 여러 자회사의 지분을 보유·관리하는 지주회사입니다. 투자 판단에서는 지주회사 자체 매출보다 핵심 자회사들의 사업가치와 실적을 봐야 합니다.",
            "products": "핵심 자회사 지분 보유·관리, 브랜드·경영지원 등 지주회사 기능",
            "customers": "일반 제조업처럼 최종 고객에게 단일 제품을 파는 구조가 아니라 자회사 사업을 통해 최종 시장에 노출됩니다.",
            "revenue": "자회사 배당, 상표권·용역·임대수익과 연결·지분법 실적이 핵심입니다.",
            "drivers": "핵심 자회사 이익과 배당, 자산가치(NAV), 지주회사 할인율, 자본배분·주주환원이 중요합니다.",
            "segments": segs or ["지주회사"],
        }

    if "화장품" in whole and _contains_any(whole, ["odm", "oem"]):
        extras = []
        if _contains_any(whole, ["전문의약품", "제약"]):
            extras.append("전문의약품")
        if _contains_any(whole, ["h&b", "헬스앤뷰티"]):
            extras.append("H&B")
        if _contains_any(whole, ["화장품 용기", "용기 제조", "연우"]):
            extras.append("화장품 용기")
        extra_txt = f" 자회사·계열사를 통해 {'·'.join(extras)} 사업도 함께 영위합니다." if extras else ""
        return {
            "summary": f"{name}는 화장품 ODM/OEM이 핵심입니다. 고객 브랜드 대신 제품을 개발·제형화·생산해 납품합니다.{extra_txt}",
            "products": "스킨케어·선케어·메이크업 등 화장품의 처방·제형 개발과 생산" + (f", 그리고 {'·'.join(extras)}" if extras else ""),
            "customers": "국내외 화장품 브랜드사가 핵심 고객입니다. 브랜드사는 기획·마케팅·판매를 맡고 회사는 연구개발과 생산을 담당합니다.",
            "revenue": "화장품 부문은 고객 주문량 × 납품단가가 기본 매출 구조입니다. 수주와 가동률이 높아질수록 고정비가 분산돼 수익성이 개선될 수 있습니다." + (" 제약·H&B·용기 사업의 제품 매출도 연결 실적에 더해집니다." if extras else ""),
            "drivers": "신규·글로벌 브랜드 수주, 해외 고객 확대, 고부가 제품 믹스, 공장 가동률, 원재료비와 신규 생산능력이 핵심입니다.",
            "segments": segs or ["화장품 ODM/OEM"],
        }

    if _contains_any(whole, ["손해보험", "생명보험", "보험료"]):
        return {
            "summary": f"{name}는 보험료를 받고 위험을 인수하는 보험사업이 핵심입니다.",
            "products": "개인·기업 대상 보험상품과 보험자산 운용",
            "customers": "개인·기업 보험계약자가 고객이며 설계사·대리점·온라인 채널 등을 통해 판매합니다.",
            "revenue": "보험료에서 지급보험금과 사업비를 뺀 보험손익과 보험자산 운용수익으로 돈을 법니다.",
            "drivers": "손해율, 신계약 판매, 보험계약마진(CSM), 해지율, 금리와 운용수익률이 중요합니다.",
            "segments": segs or ["보험"],
        }

    if _contains_any(whole, ["dram", "nand", "메모리 반도체"]):
        return {
            "summary": f"{name}는 DRAM·NAND 등 메모리 반도체 생산·판매가 핵심입니다.",
            "products": "DRAM·NAND와 HBM 등 메모리 반도체",
            "customers": "데이터센터·서버, PC, 스마트폰 등 전자기기·클라우드 기업이 주요 수요처입니다.",
            "revenue": "출하량 × 평균판매가격(ASP)이 매출을 좌우하고, HBM 등 고부가 제품 비중과 공정 원가가 이익률을 결정합니다.",
            "drivers": "메모리 가격, HBM 믹스, 고객 재고, 웨이퍼 투입량, 수율과 공정 전환이 핵심입니다.",
            "segments": segs or ["메모리 반도체"],
        }

    # Generic DART-backed structured extraction.
    products = _best_sentence(section, [
        ("주요 제품", 10), ("제품", 5), ("상품", 5), ("서비스", 5),
        ("브랜드", 4), ("생산", 3), ("제조", 3), ("판매", 2)
    ], ["시장규모", "산업의 특성", "경기변동"])

    customers = _best_sentence(section, [
        ("주요 고객", 10), ("고객", 7), ("거래처", 7), ("납품", 5),
        ("수출", 4), ("브랜드사", 6), ("완성차", 5), ("병원", 4),
        ("정부", 3), ("공공기관", 3)
    ], ["경쟁요소", "시장규모"])

    revenue = _best_sentence(section, [
        ("매출", 8), ("판매", 5), ("수수료", 7), ("보험료", 7),
        ("수주", 6), ("계약", 4), ("공급", 4), ("납품", 4),
        ("이자", 5), ("임대", 4)
    ], ["연결재무제표", "별도재무제표"])

    core = _best_sentence(section, [
        ("주요 사업", 10), ("영위", 7), ("주력", 7), ("사업부문", 6),
        ("제조", 4), ("생산", 4), ("판매", 4), ("서비스", 4), ("개발", 3)
    ], ["산업의 특성", "시장규모", "경기변동"])

    seg_txt = " · ".join(segs)
    summary = core or ((f"{name}의 사업보고서에서 확인되는 주요 사업영역은 {seg_txt}입니다." if seg_txt else tpl["summary"]))
    if len(summary) > 220:
        summary = summary[:217] + "…"

    return {
        "summary": summary,
        "products": products or (f"사업보고서에서 확인되는 주요 사업영역: {seg_txt}" if seg_txt else "핵심 제품·서비스 세부 확인 필요"),
        "customers": customers or tpl["customers"],
        "revenue": revenue or tpl["revenue"],
        "drivers": tpl["drivers"],
        "segments": segs,
    }




def _load_fixed_business_db_from_index():
    """Read the already-reviewed fixed company DB embedded in index.html.
    These companies should never consume DART profile calls again.
    """
    try:
        h = INDEX.read_text(encoding="utf-8")
        m = re.search(
            r"const\s+WAMO_BUSINESS_DB\s*=\s*(\{.*?\})\s*;\s*\n\s*function\s+wamoBusinessFor",
            h,
            re.S,
        )
        if not m:
            return {}
        d = json.loads(m.group(1))
        return d if isinstance(d, dict) else {}
    except Exception as e:
        print("  고정 기업설명 DB 읽기 실패:", e)
        return {}

def _fixed_profile_detail_sector(name, krx_sector, profile):
    """Classify the reviewed fixed profile with the same taxonomy used for DART text."""
    if not isinstance(profile, dict):
        return _normalize_krx_sector(krx_sector), [_normalize_krx_sector(krx_sector)], "LOW"
    text = " ".join([
        str(profile.get("summary") or ""),
        str(profile.get("products") or ""),
        str(profile.get("customers") or ""),
        str(profile.get("revenue") or ""),
        str(profile.get("drivers") or ""),
        " ".join(str(x) for x in (profile.get("segments") or [])),
    ])
    return _detail_sector_from_business(name, krx_sector, text)


def _is_etf_name(name):
    return classify_instrument(name) == "ETF_ETN"

def _decode_markup_bytes(raw):
    """Respect XML/HTML encoding declaration before trying common Korean encodings."""
    head = raw[:500].decode("ascii", errors="ignore")
    m = re.search(r'encoding=["\']([^"\']+)["\']', head, re.I)
    encs = []
    if m:
        encs.append(m.group(1))
    encs += ["utf-8", "euc-kr", "cp949"]
    seen = set()
    for enc in encs:
        if enc.lower() in seen:
            continue
        seen.add(enc.lower())
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("utf-8", errors="replace")

def _markup_to_text(raw):
    """DART XML can vary by filing generation.
    Try several independent strategies and keep the longest readable result.
    """
    markup = _decode_markup_bytes(raw)
    candidates = []

    # Strategy A: BeautifulSoup with multiple parsers.
    for parser in ("lxml", "html.parser", "xml"):
        try:
            soup = BeautifulSoup(markup, parser)
            for tag in soup(["script", "style"]):
                tag.decompose()
            t = soup.get_text("\n", strip=True)
            if t:
                candidates.append(t)
        except Exception:
            pass

    # Strategy B: raw tag stripping. This also survives malformed legacy DART markup.
    try:
        x = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", markup)
        x = re.sub(r"(?i)<(?:br|p|div|tr|td|th|li|section|title|h[1-6])\b[^>]*>", "\n", x)
        x = re.sub(r"(?s)<[^>]+>", " ", x)
        x = html_lib.unescape(x)
        if x:
            candidates.append(x)
    except Exception:
        pass

    # Strategy C: some legacy documents store visible strings in attributes/CDATA.
    try:
        attrs = re.findall(
            r'(?i)(?:content|contents|value|title|text)\s*=\s*["\']([^"\']{2,500})["\']',
            markup
        )
        cdata = re.findall(r"(?s)<!\[CDATA\[(.*?)\]\]>", markup)
        extra = "\n".join(attrs + cdata)
        extra = re.sub(r"(?s)<[^>]+>", " ", html_lib.unescape(extra))
        if extra:
            candidates.append(extra)
    except Exception:
        pass

    if not candidates:
        return ""

    best = max(candidates, key=len)
    best = best.replace("\x00", " ")
    best = re.sub(r"[ \t]+", " ", best)
    best = re.sub(r"\n\s*\n\s*\n+", "\n\n", best)
    return best.strip()

def _dart_document_text_v3(rcept_no):
    """Official OpenDART original document ZIP -> readable filing text."""
    if not DART_KEY:
        raise RuntimeError("DART API KEY 없음")
    if not rcept_no:
        raise RuntimeError("접수번호 없음")

    url = "https://opendart.fss.or.kr/api/document.xml?" + urllib.parse.urlencode({
        "crtfc_key": DART_KEY,
        "rcept_no": rcept_no,
    })
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=45) as r:
        raw = r.read()

    if not zipfile.is_zipfile(io.BytesIO(raw)):
        err = _decode_markup_bytes(raw)
        status = message = ""
        try:
            root = ET.fromstring(err)
            status = (root.findtext("status") or "").strip()
            message = (root.findtext("message") or "").strip()
        except Exception:
            pass
        if status or message:
            raise RuntimeError(f"원문 API 오류 {status} {message}")
        raise RuntimeError("원문 응답이 ZIP이 아님")

    parts = []
    member_debug = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        names.sort(key=lambda n: z.getinfo(n).file_size, reverse=True)
        for name in names[:10]:
            try:
                body = z.read(name)
                member_debug.append(f"{name}:{len(body)}")
                # Skip obvious images/fonts/binaries.
                if name.lower().endswith((".jpg",".jpeg",".png",".gif",".bmp",".pdf",".woff",".ttf",".zip")):
                    continue
                t = _markup_to_text(body)
                if len(t) >= 50:
                    parts.append(t)
            except Exception:
                pass

    joined = "\n".join(parts)
    joined = re.sub(r"\n{3,}", "\n\n", joined).strip()
    if len(joined) < 500:
        raise RuntimeError(
            f"사업보고서 원문 텍스트 너무 짧음({len(joined)})"
            + (f" / ZIP:{' | '.join(member_debug[:6])}" if member_debug else "")
        )
    return joined

def _periodic_report_candidates(corp_code, max_count=3):
    """Use the latest periodic filing, not only the annual report.
    Business descriptions are often updated in quarterly/semiannual filings.
    """
    end = datetime.now(KST).date()
    begin = end - timedelta(days=1000)
    d = dart_json("list.json", {
        "corp_code": corp_code,
        "bgn_de": begin.strftime("%Y%m%d"),
        "end_de": end.strftime("%Y%m%d"),
        "pblntf_ty": "A",
        "sort": "date",
        "sort_mth": "desc",
        "page_count": "100",
    })
    if str(d.get("status","000")) != "000":
        raise RuntimeError(f"정기공시 검색 실패 {d.get('status')} {d.get('message','')}")
    rows = []
    for r in d.get("list") or []:
        nm = str(r.get("report_nm") or "").strip()
        if not any(k in nm for k in ("사업보고서","반기보고서","분기보고서")):
            continue
        # Avoid amendment wrappers first; keep them as last-resort candidates.
        penalty = 1 if ("정정" in nm or "첨부정정" in nm) else 0
        rows.append({
            "rcept_no": str(r.get("rcept_no") or ""),
            "date": str(r.get("rcept_dt") or ""),
            "report": nm,
            "_penalty": penalty,
        })
    # Latest original first, then corrections / older filings as fallback.
    rows.sort(key=lambda r: (r["_penalty"], -int(r["date"] or 0)))
    out = []
    seen = set()
    for r in rows:
        if not r["rcept_no"] or r["rcept_no"] in seen:
            continue
        seen.add(r["rcept_no"])
        out.append(r)
        if len(out) >= max_count:
            break
    if not out:
        raise RuntimeError("최근 정기보고서 없음")
    return out

def _dart_public_viewer_text(rcept_no):
    """Free official DART viewer fallback.
    Parse the document tree on the report's official page and fetch the '사업의 내용' section.
    """
    main_url = "https://dart.fss.or.kr/dsaf001/main.do?" + urllib.parse.urlencode({"rcpNo": rcept_no})
    html = http_text(main_url, timeout=6)

    # DART's document tree calls viewDoc(rcpNo,dcmNo,eleId,offset,length,dtd).
    calls = []
    for m in re.finditer(
        r"viewDoc\s*\(\s*['\"]?(\d+)['\"]?\s*,\s*['\"]?(\d+)['\"]?\s*,\s*['\"]?(\d+)['\"]?\s*,\s*['\"]?(\d+)['\"]?\s*,\s*['\"]?(\d+)['\"]?\s*,\s*['\"]([^'\"]+)['\"]\s*\)",
        html, re.I
    ):
        before = html[max(0, m.start()-1800):m.start()]
        label = re.sub(r"<[^>]+>", " ", html_lib.unescape(before))
        label = re.sub(r"\s+", " ", label)[-500:]
        calls.append((label, m.groups()))

    ranked = []
    for label, args in calls:
        score = 0
        if "사업의 내용" in label:
            score += 100
        if "사업" in label:
            score += 20
        if "재무에 관한 사항" in label:
            score -= 30
        ranked.append((score, label, args))
    ranked.sort(key=lambda z: z[0], reverse=True)

    for score, label, args in ranked[:8]:
        if score <= 0:
            continue
        rcp, dcm, ele, offset, length, dtd = args
        url = "https://dart.fss.or.kr/report/viewer.do?" + urllib.parse.urlencode({
            "rcpNo": rcp, "dcmNo": dcm, "eleId": ele,
            "offset": offset, "length": length, "dtd": dtd,
        })
        try:
            body = http_bytes(url, timeout=6)
            t = _markup_to_text(body)
            if len(t) >= 500:
                return t
        except Exception:
            pass
    raise RuntimeError("DART 공시뷰어 사업의 내용 추출 실패")

def _fetch_best_business_text(corp_code, preferred_rcept=None, preferred_date=None):
    """Fast path.
    1) Use the receipt number already obtained by DART financial/disclosure enrichment.
    2) Only if that fails, search at most the latest 2 periodic filings.
    3) For each filing, try ZIP first and official DART viewer second.
    This bounds one company's worst-case network work instead of walking 12 filings.
    """
    attempts = []
    candidates = []
    seen = set()

    if preferred_rcept:
        r = {
            "rcept_no": str(preferred_rcept),
            "date": str(preferred_date or ""),
            "report": "정기보고서",
        }
        candidates.append(r)
        seen.add(r["rcept_no"])

    # If preferred receipt works, no list.json request is needed at all.
    if candidates:
        r = candidates[0]
        try:
            t = _dart_document_text_v3(r["rcept_no"])
            return t, r, "OpenDART 원문 ZIP"
        except Exception as e:
            attempts.append(f"{r.get('date','')} {r['rcept_no']} ZIP:{e}")
        try:
            t = _dart_public_viewer_text(r["rcept_no"])
            return t, r, "DART 공시뷰어"
        except Exception as e:
            attempts.append(f"{r.get('date','')} {r['rcept_no']} VIEW:{e}")

    # Only after the preferred receipt failed, query a very small fallback set.
    try:
        for r in _periodic_report_candidates(corp_code, 1):
            if r["rcept_no"] not in seen:
                candidates.append(r)
                seen.add(r["rcept_no"])
    except Exception as e:
        attempts.append("목록:" + str(e))

    # At most two fallback filings.
    fallback_rows = candidates[1:] if preferred_rcept else candidates
    for r in fallback_rows[:1]:
        try:
            t = _dart_document_text_v3(r["rcept_no"])
            return t, r, "OpenDART 원문 ZIP"
        except Exception as e:
            attempts.append(f"{r.get('date','')} {r['rcept_no']} ZIP:{e}")
        try:
            t = _dart_public_viewer_text(r["rcept_no"])
            return t, r, "DART 공시뷰어"
        except Exception as e:
            attempts.append(f"{r.get('date','')} {r['rcept_no']} VIEW:{e}")

    raise RuntimeError(" / ".join(attempts[-5:]) or "정기보고서 본문 확보 실패")

def _normalize_krx_sector(s):
    s = str(s or "")
    # Last-resort categories only. DART business text takes priority.
    rules = [
        (["보험"], "보험"),
        (["은행","금융"], "금융"),
        (["증권"], "증권"),
        (["화장품"], "화장품"),
        (["반도체"], "반도체"),
        (["의약","제약","바이오"], "제약·바이오"),
        (["자동차"], "자동차·부품"),
        (["건설"], "건설"),
        (["조선"], "조선"),
        (["통신"], "통신"),
        (["소프트웨어","IT","정보"], "IT서비스·소프트웨어"),
        (["화학"], "화학"),
        (["식품","음료"], "식품"),
        (["운송","해운","항공"], "운송"),
        (["전기","전력"], "전력·전기장비"),
        (["유통","소매","도매"], "유통"),
    ]
    for keys, label in rules:
        if any(k in s for k in keys):
            return label
    return s if s and s != "KRX 업종 미분류" else "기타"

def _detail_sector_from_business(name, krx_sector, report_text):
    """Primary business-sector taxonomy from DART narrative.
    Order matters: narrow sectors before broad sectors.
    """
    n = str(name or "")
    t = re.sub(r"\s+", " ", str(report_text or "")).lower()
    k = str(krx_sector or "").lower()

    if _is_etf_name(n):
        return "ETF", ["ETF"], "HIGH"

    # Holding companies must not be mistaken for operating subsidiaries.
    head = t[:25000]
    if (
        n.endswith("금융지주") or n.endswith("지주")
        or "금융지주회사로서" in head
        or ("금융지주회사" in head and any(w in head for w in ("은행","증권","카드")))
    ):
        return "금융지주", ["금융지주"], "HIGH"
    if (
        "회사 본부" in k or n.endswith("홀딩스")
        or ("지주회사로서" in head and "자회사" in head)
        or ("순수지주회사" in head and "자회사" in head)
        or (
            "지주회사" in head
            and any(w in head for w in ("자회사", "계열사", "지분", "배당", "보유"))
        )
    ):
        return "지주회사", ["지주회사"], "HIGH"

    # K-뷰티는 같은 산업 안에서도 돈 버는 방식이 크게 다르므로 우선 분리한다.
    # 단순히 '유통채널'이라는 단어가 있다는 이유로 자체 브랜드사를 유통사로
    # 오분류하지 않도록 ODM → 자체 브랜드 → 전문 유통 순서로 판정한다.
    if "화장품" in t:
        if any(w in t for w in ("odm", "oem", "제조자개발생산", "주문자상표부착생산")):
            return "화장품 ODM/OEM", ["화장품 ODM/OEM", "K-뷰티"], "HIGH"
        if any(w in t for w in ("자체 브랜드", "브랜드 회사", "브랜딩", "최종 소비자", "자체 제품")):
            return "화장품 브랜드", ["화장품 브랜드", "K-뷰티"], "HIGH"
        if any(w in t for w in ("유통 플랫폼", "수출 유통", "해외 유통", "유통회사", "브랜드 유통")):
            return "K-뷰티 유통", ["K-뷰티 유통", "K-뷰티"], "HIGH"

    narrow = [
        ("화장품 ODM/OEM", ["화장품"], ["odm","oem"]),
        ("K-뷰티 유통", ["화장품"], ["유통","플랫폼","수출"]),
        ("손해보험", ["손해보험"], []),
        ("생명보험", ["생명보험"], []),
        ("증권", ["증권"], ["위탁매매","ib","브로커리지","자산관리"]),
        ("메모리 반도체", ["반도체"], ["dram","nand","hbm"]),
        ("반도체 검사·테스트", ["반도체"], ["검사","테스트","프로브","소켓"]),
        ("반도체 장비", ["반도체"], ["장비","식각","증착","세정"]),
        ("반도체 소재·부품", ["반도체"], ["소재","부품","sic","쿼츠","세라믹"]),
        ("타이어", ["타이어"], []),
        ("자동차 부품", ["자동차"], ["부품","제동","조향","현가","adas"]),
        ("방산", ["방산"], ["유도무기","감시정찰","군수","방위"]),
        ("조선", ["선박"], ["건조","조선"]),
        ("해운", ["해운"], ["선박","운송","벌크"]),
        ("항공", ["항공"], ["여객","화물"]),
        ("정유", ["정유"], ["휘발유","경유","항공유","정제"]),
        ("석유화학", ["석유화학"], ["합성고무","합성수지","화학"]),
        ("이차전지 소재", ["이차전지"], ["양극재","음극재","전구체","분리막","전해액"]),
        ("이차전지", ["이차전지"], ["배터리","전지"]),
        ("전력기기·전선", ["전력"], ["전선","케이블","변압기","차단기"]),
        ("신재생에너지", ["태양광","풍력","신재생"], ["발전","ess"]),
        ("플랜트 EPC", ["플랜트"], ["설계","조달","시공","epc"]),
        ("건설·주택", ["건설"], ["주택","건축","토목","시공"]),
        ("미용의료·의료기기", ["의료기기"], ["미용","피부","시술","재생"]),
        ("제약·바이오", ["의약품"], ["제약","바이오","신약","임상"]),
        ("게임", ["게임"], []),
        ("인터넷 플랫폼", ["플랫폼"], ["광고","커머스","검색","핀테크"]),
        ("AI·클라우드·IT서비스", ["클라우드"], ["it","ai","시스템","데이터센터"]),
        ("통신", ["통신"], ["이동통신","5g","lte","인터넷"]),
        ("로봇·모션", ["로봇"], ["감속기","모터","자동화"]),
        ("건설기계", ["건설기계"], ["굴착기","로더"]),
        ("물류", ["물류"], ["운송","창고","완성차"]),
        ("편의점·리테일", ["편의점"], ["소매","가맹"]),
        ("식품", ["식품"], ["제과","음료","스낵"]),
        ("화장품 브랜드", ["화장품"], ["브랜드","소비자","온라인"]),
    ]

    tags = []
    for label, must_any, support_any in narrow:
        if must_any and not any(w in t for w in must_any):
            continue
        if support_any and not any(w in t for w in support_any):
            continue
        tags.append(label)
    if tags:
        return tags[0], tags[:4], "HIGH"

    broad = [
        ("반도체", ["반도체"]),
        ("자동차·부품", ["자동차"]),
        ("제약·바이오", ["제약","바이오","의약"]),
        ("화장품", ["화장품"]),
        ("금융", ["은행","대출","예금"]),
        ("보험", ["보험"]),
        ("IT서비스·소프트웨어", ["소프트웨어","정보기술","it서비스"]),
        ("유통", ["유통","소매"]),
        ("화학", ["화학"]),
        ("에너지", ["에너지","발전","가스"]),
        ("기계·장비", ["기계","장비"]),
    ]
    for label, words in broad:
        if any(w in t for w in words):
            return label, [label], "MEDIUM"

    fallback = _normalize_krx_sector(krx_sector)
    return fallback, [fallback], "LOW"

def _is_holding_sector_name(name):
    """지주사 군집은 영업 섹터가 아니라 지배구조·정책 동조로 따로 본다."""
    s = str(name or "").replace(" ", "")
    return any(k in s for k in ("지주회사", "금융지주", "지주사"))


def _build_sector_stats(raw):
    groups = {}
    for x in raw:
        groups.setdefault(x.get("sector") or "기타", []).append(x)

    sectors = []
    for name, members in groups.items():
        ar = statistics.mean(x["rsPercentile"] for x in members)
        b60 = statistics.mean(1 if x["ma60"] and x["close"] > x["ma60"] else 0 for x in members)
        nh = statistics.mean(1 if x["high52Ratio"] >= 93 else 0 for x in members)
        vh = statistics.mean(min(x["volumeRatio"] / 2, 1) for x in members)
        st = statistics.mean(1 if x["trendTemplate"] else 0 for x in members)
        detail_count = sum(1 for x in members if x.get("sectorConfidence") in ("HIGH", "MEDIUM"))
        raw_score = 0.35 * ar + 20 * b60 + 20 * nh + 15 * vh + 10 * st
        member_count = len(members)
        # 한 종목만 강한 경우를 '섹터 액션'으로 과대평가하지 않는다.
        # 3개 이상이 함께 움직여야 정식 섹터 액션으로 인정하고,
        # 1~2개 그룹의 점수는 중립값(50) 쪽으로 축소한다.
        breadth_confidence = min(member_count / 3, 1.0)
        score = 50 + (raw_score - 50) * breadth_confidence
        is_holding_theme = _is_holding_sector_name(name)
        group_status = "HOLDING_THEME" if is_holding_theme else "SECTOR_ACTION" if member_count >= 3 else "REFERENCE" if member_count == 2 else "SINGLE"
        if group_status == "HOLDING_THEME":
            action = "동조" if member_count >= 3 and score >= 58 else "참고"
        elif group_status == "SECTOR_ACTION":
            action = "강화" if score >= 72 else "상승" if score >= 58 else "중립" if score >= 45 else "약화"
        else:
            action = "참고" if group_status == "REFERENCE" else "단일"
        sectors.append({
            "name": name,
            "score": round(score, 1),
            "rsPercentile": round(ar, 1),
            "breadth60": round(b60 * 100, 1),
            "newHighPct": round(nh * 100, 1),
            "volumeHeat": round(vh * 100, 1),
            "stage2Pct": round(st * 100, 1),
            "action": action,
            "groupStatus": group_status,
            "groupStatusLabel": (f"지주사 {member_count}개 동조 · 참고" if is_holding_theme else f"{member_count}개 동조" if member_count >= 3 else f"{member_count}개 · 참고용"),
            "detailClassifiedCount": detail_count,
            "classificationCoveragePct": round(detail_count / member_count * 100, 1) if member_count else 0,
            "classificationLabel": f"사업내용 세부분류 {detail_count}/{member_count}" if detail_count else "KRX 업종 기준 · 세부분류 대기",
            "leaders": [m["name"] for m in sorted(members, key=lambda z: z["rsPercentile"], reverse=True)[:3]],
            "memberCount": member_count,
        })
    status_rank = {"SECTOR_ACTION": 3, "HOLDING_THEME": 2, "REFERENCE": 1, "SINGLE": 0}
    sectors.sort(key=lambda s: (status_rank.get(s.get("groupStatus"), 0), s["score"]), reverse=True)
    return sectors


def _sector_action_overlay(sector):
    """종목 선별과 섞지 않는 독립 보조신호. 모든 종목에 동일한 형식으로 붙인다."""
    group_status = sector.get("groupStatus")
    action = sector.get("action")
    if group_status == "HOLDING_THEME":
        status, label = "HOLDING_THEME", "지주사 동조 · 참고"
    elif group_status == "SECTOR_ACTION" and action in ("강화", "상승"):
        status, label = "CONFIRMED", "섹터 동반강세"
    elif group_status == "SECTOR_ACTION":
        status, label = "WATCH", "섹터 동반확인 중"
    else:
        status, label = "NONE", "섹터 동반신호 없음"
    return {
        "status": status,
        "label": label,
        "action": action,
        "score": sector.get("score"),
        "memberCount": sector.get("memberCount", 0),
        "breadth60": sector.get("breadth60"),
        "newHighPct": sector.get("newHighPct"),
        "stage2Pct": sector.get("stage2Pct"),
    }


def _profile_is_priority(x, old_by_ticker):
    old = old_by_ticker.get(x.get("ticker"), {})
    return bool(
        (x.get("conditionCount",0) >= 4 and x.get("rsPercentile",0) >= 60)
        or x.get("trendTemplate")
        or x.get("high52Ratio",0) >= 93
        or x.get("historicalHighRatio",0) >= 93
        or old.get("signal") in ("BUY","HOLD","WATCH")
    )

def _profile_priority_key(x, old_by_ticker):
    old = old_by_ticker.get(x.get("ticker"), {})
    return (
        1 if not old else 0,  # truly new stock on dashboard first
        1 if (x.get("conditionCount",0) >= 4 and x.get("rsPercentile",0) >= 60) else 0,
        1 if x.get("trendTemplate") else 0,
        1 if (x.get("high52Ratio",0) >= 93 or x.get("historicalHighRatio",0) >= 93) else 0,
        x.get("conditionCount",0),
        x.get("rsPercentile",0),
        x.get("high52Ratio",0),
        x.get("volumeRatio",0),
    )

def profile_enrich(raw, old_by_ticker=None):
    old_by_ticker = old_by_ticker or {}
    cache = _load_profile_cache()
    fixed_db = _load_fixed_business_db_from_index()
    today = datetime.now(KST).date()

    if not DART_KEY:
        for x in raw:
            x["krxSector"] = x.get("sector")
            x["detailSector"] = _normalize_krx_sector(x.get("sector"))
            x["sector"] = x["detailSector"]
        return {
            "status":"NO_KEY","targetCount":0,"coveredCount":0,"fetchedCount":0,
            "source":"OpenDART","message":"DART API KEY 없음","errors":[]
        }

    try:
        cmap = dart_corp_map()
    except Exception as e:
        raise RuntimeError("기업설명용 DART 고유번호 목록 실패: " + str(e))

    errors = []
    missing = []
    covered = 0
    cached_count = 0
    retry_wait = 0

    for x in raw:
        original_sector = x.get("sector")
        x["krxSector"] = original_sector

        if _is_etf_name(x.get("name")):
            x["detailSector"] = "ETF"
            x["sectorTags"] = ["ETF"]
            x["sectorConfidence"] = "HIGH"
            x["sector"] = "ETF"
            x["businessProfile"] = {
                "summary": f"{x.get('name')}은 개별 기업이 아니라 여러 자산을 묶어 운용하는 ETF입니다.",
                "products":"기초지수 또는 정해진 운용전략을 추종하는 상장지수펀드",
                "customers":"증권시장에서 ETF를 매매하는 투자자",
                "revenue":"기업 매출이 아니라 보유자산 가격변동과 분배금이 투자수익을 결정합니다.",
                "drivers":"기초지수 수익률, 환율(해외형), 운용보수, 추적오차와 분배정책",
                "segments":["ETF"],
            }
            x["businessModelEasy"] = x["businessProfile"]["summary"]
            x["businessModelSource"] = "ETF 자동분류"
            covered += 1
            continue

        code = x.get("stock_code") or str(x.get("ticker","")).split(".")[0]
        corp = x.get("dartCorpCode") or cmap.get(code)
        if corp:
            x["dartCorpCode"] = corp

        old = cache.get(code) or cache.get(x.get("ticker")) or {}
        fresh = False
        try:
            d = date.fromisoformat(str(old.get("updatedAt")))
            fresh = (
                (today - d).days <= 180
                and old.get("schemaVersion") == PROFILE_SCHEMA_VERSION
                and isinstance(old.get("businessProfile"), dict)
                and old.get("detailSector")
                and not old.get("failed")
            )
        except Exception:
            fresh = False

        fixed = fixed_db.get(str(code))
        if isinstance(fixed, dict) and fixed.get("summary"):
            detail, tags, confidence = _fixed_profile_detail_sector(
                x.get("name"), original_sector, fixed
            )
            x["businessProfile"] = fixed
            x["businessModelEasy"] = fixed.get("summary") or ""
            x["businessModelSource"] = "WAMO 검수 고정 DB"
            x["detailSector"] = detail
            x["sectorTags"] = tags
            x["sectorConfidence"] = confidence
            x["sector"] = detail
            covered += 1
            continue

        if fresh:
            x["businessProfile"] = old["businessProfile"]
            x["businessModelEasy"] = old["businessProfile"].get("summary") or ""
            x["businessModelSource"] = old.get("businessModelSource") or "OpenDART 사업보고서 직접추출"
            x["businessModelReportDate"] = old.get("businessModelReportDate")
            x["businessModelUrl"] = old.get("businessModelUrl")
            # 저장된 사업설명은 재사용하되, 세부섹터는 최신 분류법으로 매번 다시 계산한다.
            detail, tags, confidence = _fixed_profile_detail_sector(
                x.get("name"), original_sector, old["businessProfile"]
            )
            x["detailSector"] = detail
            x["sectorTags"] = tags
            x["sectorConfidence"] = confidence
            x["sector"] = x["detailSector"]
            covered += 1
            cached_count += 1
            continue

        # Failed records get a cooldown so one problematic filing cannot consume minutes every day.
        retry_after = old.get("retryAfter")
        if old.get("failed") and retry_after:
            try:
                if today < date.fromisoformat(str(retry_after)):
                    x["detailSector"] = old.get("detailSector") or _normalize_krx_sector(original_sector)
                    x["sectorTags"] = old.get("sectorTags") or [x["detailSector"]]
                    x["sectorConfidence"] = old.get("sectorConfidence") or "LOW"
                    x["sector"] = x["detailSector"]
                    x["businessProfile"] = {
                        "summary":"DART 원문 재시도 대기 중입니다. 업종명만 보고 사업모델을 임의 생성하지 않습니다.",
                        "products":"—","customers":"—","revenue":"—","drivers":"—","segments":[]
                    }
                    x["businessModelEasy"] = x["businessProfile"]["summary"]
                    x["businessModelSource"] = "DART 재시도 대기"
                    retry_wait += 1
                    continue
            except Exception:
                pass

        if corp:
            missing.append(x)
        else:
            x["detailSector"] = _normalize_krx_sector(original_sector)
            x["sectorTags"] = [x["detailSector"]]
            x["sectorConfidence"] = "LOW"
            x["sector"] = x["detailSector"]
            x["businessProfile"] = {
                "summary":"DART 고유번호를 확인할 수 없어 회사별 사업설명을 자동 생성하지 않았습니다.",
                "products":"—","customers":"—","revenue":"—","drivers":"—","segments":[]
            }
            x["businessModelEasy"] = x["businessProfile"]["summary"]
            x["businessModelSource"] = "DART 연결 불가"

    # Strong/new names first.
    missing.sort(key=lambda x: _profile_priority_key(x, old_by_ticker), reverse=True)
    priority = [x for x in missing if _profile_is_priority(x, old_by_ticker)]
    selected = priority[:PROFILE_PRIORITY_MAX]
    selected_ids = {x.get("ticker") for x in selected}

    # Only a small background batch fills the long-tail company DB each day.
    for x in missing:
        if x.get("ticker") in selected_ids:
            continue
        selected.append(x)
        selected_ids.add(x.get("ticker"))
        if len(selected) >= min(PROFILE_TARGET_MAX, len(priority[:PROFILE_PRIORITY_MAX]) + PROFILE_BACKFILL_PER_RUN):
            break

    # Everything not selected uses a conservative broad sector until its turn.
    for x in missing:
        if x.get("ticker") in selected_ids:
            continue
        x["detailSector"] = _normalize_krx_sector(x.get("krxSector"))
        x["sectorTags"] = [x["detailSector"]]
        x["sectorConfidence"] = "LOW"
        x["sector"] = x["detailSector"]
        x["businessProfile"] = {
            "summary":"DART 회사별 사업내용 DB가 순차 구축 중입니다.",
            "products":"—","customers":"—",
            "revenue":"업종명만 보고 사업모델을 임의 생성하지 않습니다.",
            "drivers":"신규·강한 후보는 우선 처리하고 나머지는 매일 자동으로 추가합니다.",
            "segments":[]
        }
        x["businessModelEasy"] = x["businessProfile"]["summary"]
        x["businessModelSource"] = "DART 백로그"

    def one(x):
        report_text, report, route = _fetch_best_business_text(
            x.get("dartCorpCode"),
            x.get("businessReportRceptNo"),
            x.get("businessReportDate"),
        )
        section = _business_section_robust(report_text)
        if len(section) < 400:
            section = report_text[:90000]

        profile = _profile_from_dart_report(
            x.get("name"), x.get("krxSector") or x.get("sector"), report_text
        )
        detail, tags, confidence = _detail_sector_from_business(
            x.get("name"), x.get("krxSector"), section
        )
        return x, report, route, profile, detail, tags, confidence

    fetched = 0
    with ThreadPoolExecutor(max_workers=PROFILE_WORKERS) as ex:
        fut_map = {ex.submit(one, x): x for x in selected}
        for i, fut in enumerate(as_completed(fut_map), 1):
            x0 = fut_map[fut]
            code = x0.get("stock_code") or str(x0.get("ticker","")).split(".")[0]
            try:
                x, report, route, prof, detail, tags, confidence = fut.result()
                x["businessProfile"] = prof
                x["businessModelEasy"] = prof.get("summary") or ""
                x["businessModelSource"] = f"OpenDART 정기보고서 직접추출 · {route}"
                x["businessModelReportDate"] = report.get("date")
                x["businessModelUrl"] = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + report.get("rcept_no","")
                x["detailSector"] = detail
                x["sectorTags"] = tags
                x["sectorConfidence"] = confidence
                x["sector"] = detail

                cache[code] = {
                    "schemaVersion": PROFILE_SCHEMA_VERSION,
                    "businessProfile": prof,
                    "businessModelSource": x["businessModelSource"],
                    "businessModelReportDate": report.get("date"),
                    "businessModelUrl": x["businessModelUrl"],
                    "detailSector": detail,
                    "sectorTags": tags,
                    "sectorConfidence": confidence,
                    "updatedAt": today.isoformat(),
                    "failed": False,
                }
                fetched += 1
                covered += 1
            except Exception as e:
                detail = _normalize_krx_sector(x0.get("krxSector"))
                x0["detailSector"] = detail
                x0["sectorTags"] = [detail]
                x0["sectorConfidence"] = "LOW"
                x0["sector"] = detail
                x0["businessProfile"] = {
                    "summary":"이번 자동갱신에서 DART 사업내용 추출에 실패했습니다. 업종명만 보고 사업모델을 임의 생성하지 않습니다.",
                    "products":"—","customers":"—","revenue":"—","drivers":"—","segments":[]
                }
                x0["businessModelEasy"] = x0["businessProfile"]["summary"]
                x0["businessModelSource"] = "DART 추출 실패"
                msg = f"{x0.get('name')}({x0.get('stock_code')}): {e}"
                errors.append(msg)

                # Persist failure cooldown. Strong candidates retry sooner.
                retry_days = 1 if _profile_is_priority(x0, old_by_ticker) else PROFILE_RETRY_DAYS
                cache[code] = {
                    "schemaVersion": PROFILE_SCHEMA_VERSION,
                    "failed": True,
                    "lastError": str(e)[:1000],
                    "retryAfter": (today + timedelta(days=retry_days)).isoformat(),
                    "updatedAt": today.isoformat(),
                    "detailSector": detail,
                    "sectorTags": [detail],
                    "sectorConfidence": "LOW",
                }
                if len(errors) <= 20:
                    print("  기업설명 실패:", msg)

            if i % 8 == 0 or i == len(selected):
                print("  DART 기업설명", i, "/", len(selected), "이번 연결", fetched)

    _save_profile_cache(cache)
    target_count = sum(not _is_etf_name(x.get("name")) for x in raw)
    actual = sum(
        str(x.get("businessModelSource","")).startswith("OpenDART")
        for x in raw if not _is_etf_name(x.get("name"))
    )
    pending = sum(
        x.get("businessModelSource") in ("DART 백로그","DART 재시도 대기","DART 추출 실패")
        for x in raw
    )
    fixed_count = sum(x.get("businessModelSource") == "WAMO 검수 고정 DB" for x in raw)
    print(
        f"  회사별 사업모델: DART {actual}개 · 고정DB {fixed_count}개"
        f" · DART이번조회 {len(selected)} · DART신규연결 {fetched} · 백로그/대기 {pending}"
    )
    if selected:
        print("  이번 DART 자동처리 종목:", ", ".join(x.get("name","") for x in selected[:20]))

    return {
        "status": "LIVE" if target_count and actual / target_count >= 0.70 else "PARTIAL",
        "targetCount": target_count,
        "coveredCount": actual,
        "cachedCount": cached_count,
        "selectedCount": len(selected),
        "fetchedCount": fetched,
        "pendingCount": pending,
        "retryWaitCount": retry_wait,
        "source": "OpenDART 정기보고서 원문 + DART 공시뷰어 fallback",
        "message": f"DART {actual}개 · 고정DB {fixed_count}개 · 이번 DART조회 {len(selected)} · 백로그 {pending}",
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
    operating_sector_action = sector.get("groupStatus") == "SECTOR_ACTION" and sector.get("action") in ("강화", "상승")
    sector_value = "지주사 동조 · 참고" if sector.get("groupStatus") == "HOLDING_THEME" else f"섹터 {ss:.0f}"
    items["L"] = cs_item("PASS" if ors >= 80 and operating_sector_action else "FAIL", f"오닐식 RS {ors:.0f} · {sector_value}", "RS ≥80 + 영업 세부섹터 동반강세", "시장 내 주도주인지 봅니다. 지주사 동조는 서로 다른 자회사 산업을 묶으므로 영업 세부섹터 강세로 인정하지 않습니다.")

    # I — Institutional Sponsorship
    # 사용자 설정: 외국인/기관 수급을 사용하지 않음.
    # 오닐의 I를 억지 대체하지 않고 명시적으로 '사용 안 함' 처리한다.
    items["I"] = cs_item(
        "DISABLED",
        "사용 안 함",
        "기관 보유·후원 데이터",
        "사용자 설정에 따라 외국인·기관 수급을 사용하지 않습니다. CAN SLIM 자동판정에서는 I를 제외하고 C·A·N·S·L·M 6개만 별도로 집계합니다."
    )

    if mkt.get("pass") is None:
        items["M"] = cs_item("UNKNOWN", "시장 데이터 확인 불가", "시장 상승추세", mkt.get("note", ""))
    else:
        items["M"] = cs_item("PASS" if mkt["pass"] else "FAIL", "시장 상승" if mkt["pass"] else "시장 비우호", "KOSPI >50일선 >200일선 + 200일선 상승", mkt.get("note", ""))

    # Original CAN SLIM has 7 letters, but I is intentionally disabled.
    # Automated dashboard score therefore uses C/A/N/S/L/M only and labels it clearly.
    auto_keys = ("C","A","N","S","L","M")
    auto_sts = [items[k]["status"] for k in auto_keys]
    auto_pass = auto_sts.count("PASS")
    auto_measured = sum(s in ("PASS","FAIL") for s in auto_sts)
    auto_unknown = auto_sts.count("UNKNOWN")

    auto_full = (auto_measured == 6 and auto_pass == 6)
    auto_key = all(items[k]["status"] == "PASS" for k in ("C","A","L","M"))
    auto_strong = (not auto_full) and auto_pass >= 5 and auto_measured >= 5 and auto_key
    auto_preliminary = (not auto_full) and (not auto_strong) and items["L"]["status"] == "PASS" and auto_pass >= 3

    x["canSlim"] = {
        "items": items,
        "passCount": auto_pass,
        "measuredCount": auto_measured,
        "unknownCount": auto_unknown,
        "autoKeys": list(auto_keys),
        "institutionalDisabled": True,
        "fullMatch": auto_full,
        "strongCandidate": auto_strong,
        "preliminary": auto_preliminary,
        "label": "CAN SLIM 자동판정(I 제외)",
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


def validate_payload_integrity(payload):
    """핵심 카드·필터·표의 숫자가 서로 맞을 때만 사이트를 갱신합니다."""
    issues = []
    checks = 0
    stocks = payload.get("stocks") or []
    sectors = payload.get("sectors") or []
    meta = payload.get("meta") or {}
    funnel = meta.get("universeFunnel") or {}

    tickers = [x.get("ticker") for x in stocks]
    checks += 1
    if len(tickers) != len(set(tickers)):
        issues.append("중복 티커 존재")

    for x in stocks:
        ticker = x.get("ticker") or x.get("name") or "알 수 없음"
        checks += 1
        if x.get("instrumentType") in ("ETF_ETN", "SPAC"):
            issues.append(f"{ticker}: 기업 후보에 ETF·ETN·스팩 혼입")
        cond = x.get("conditions") or {}
        checks += 1
        if x.get("conditionCount") != sum(bool(v) for v in cond.values()):
            issues.append(f"{ticker}: 6조건 합계 불일치")

        stage = x.get("stage2Checks") or {}
        expected_stage = bool(stage and all(bool(v) for v in stage.values()) and (x.get("rsPercentile") or 0) >= 70)
        checks += 1
        if bool(x.get("trendTemplate")) != expected_stage:
            issues.append(f"{ticker}: Stage 2 불일치")

        hist = x.get("history") or []
        hist_dates = [r.get("date") for r in hist]
        checks += 1
        if not hist or hist_dates != sorted(set(hist_dates)) or hist_dates[-1] != x.get("date"):
            issues.append(f"{ticker}: 차트 이력 날짜 불일치")
        if hist:
            recent = hist[-min(252, len(hist)):]
            high52 = max(float(r.get("high") or 0) for r in recent)
            calc_ratio = float(x.get("close") or 0) / high52 * 100 if high52 else 0
            checks += 1
            if abs(calc_ratio - float(x.get("high52Ratio") or 0)) > 0.15:
                issues.append(f"{ticker}: 52주 고점비 불일치")

        a = x.get("alignment") or {}
        ma_values = [x.get("ma20"), x.get("ma60"), x.get("ma120"), x.get("ma200")]
        expected_alignment = bool(all(v is not None for v in ma_values) and x.get("close") > ma_values[0] > ma_values[1] > ma_values[2] > ma_values[3])
        checks += 1
        if bool(a.get("isAligned")) != expected_alignment:
            issues.append(f"{ticker}: 현재 정배열 불일치")

        overlay = x.get("sectorAction") or {}
        sec = next((s for s in sectors if s.get("name") == x.get("sector")), None)
        checks += 1
        if not sec:
            issues.append(f"{ticker}: 섹터 보조정보 원본 없음")
        elif _is_holding_sector_name(x.get("sector")) and overlay.get("status") == "CONFIRMED":
            issues.append(f"{ticker}: 지주사를 산업 섹터동반강세로 오분류")
        elif overlay.get("status") != _sector_action_overlay(sec).get("status"):
            issues.append(f"{ticker}: 섹터 보조정보 불일치")

    checks += 3
    if funnel.get("deepScanned") != len(stocks):
        issues.append("정밀계산 종목 수 불일치")
    if funnel.get("growth4plus") != sum((x.get("conditionCount") or 0) >= 4 for x in stocks):
        issues.append("성장주 후보 수 불일치")
    if funnel.get("stage2") != sum(bool(x.get("trendTemplate")) for x in stocks):
        issues.append("Stage 2 후보 수 불일치")
    checks += 1
    if funnel.get("highZone") != sum((x.get("high52Ratio") or 0) >= 93 or (x.get("historicalHighRatio") or 0) >= 93 for x in stocks):
        issues.append("신고가권 후보 수 불일치")

    health = meta.get("dataHealth") or {}
    attempted = int(health.get("priceAttemptedCount") or 0)
    fetched = int(health.get("priceFetchedCount") or 0)
    failed = int(health.get("failedCount") or 0)
    checks += 2
    if attempted and attempted != fetched + failed:
        issues.append("기업 가격수집 시도·성공·실패 합계 불일치")
    if failed != len(payload.get("errors") or []):
        issues.append("가격수집 실패 건수 불일치")

    sector_counts = {}
    for x in stocks:
        sector_counts[x.get("sector") or "기타"] = sector_counts.get(x.get("sector") or "기타", 0) + 1
    for sec in sectors:
        checks += 1
        if sec.get("memberCount") != sector_counts.get(sec.get("name"), 0):
            issues.append(f"{sec.get('name')}: 섹터 인원 불일치")
        leader = sec.get("leaderStock") or {}
        if leader and leader.get("name") not in [x.get("name") for x in stocks if x.get("sector") == sec.get("name")]:
            issues.append(f"{sec.get('name')}: 대장주 섹터 불일치")

    if issues:
        raise RuntimeError("정합성 검사 실패: " + " / ".join(issues[:12]))
    return {
        "status": "PASS",
        "checks": checks,
        "checkedAt": datetime.now(KST).isoformat(timespec="minutes"),
        "note": "중복·6조건·Stage 2·신고가권·정배열·섹터 인원·지주사 분리·종목별 섹터 보조정보·퍼널 합계를 자동 대조했습니다.",
    }


def main():
    if not INDEX.exists():
        raise SystemExit("index.html을 찾지 못했습니다.")

    old_html = INDEX.read_text(encoding="utf-8")
    old_payload = extract_old_payload(old_html)
    old_mode = (old_payload.get("meta") or {}).get("mode")
    old_by_ticker = {x.get("ticker"): x for x in old_payload.get("stocks", []) if x.get("ticker")}

    print("1/9 코스피·코스닥 시가총액 목록 수집")
    listed = fetch_market_summary(0, ".KS", "KOSPI") + fetch_market_summary(1, ".KQ", "KOSDAQ")
    sector_map = kind_sector_map()
    for r in listed:
        r["sector"] = sector_map.get(r["stock_code"], "KRX 업종 미분류")
        r["instrumentType"] = classify_instrument(r.get("name"))

    # ETF·ETN과 스팩은 기업의 재무·사업·Stage 2 기준으로 평가하면 왜곡된다.
    # REIT는 실제 영업기업과 구분 표시하되 별도 필터로 확인할 수 있도록 남긴다.
    company_universe = [r for r in listed if r.get("instrumentType") in ("COMPANY", "REIT")]
    excluded_instruments = [r for r in listed if r.get("instrumentType") in ("ETF_ETN", "SPAC")]
    cap_pass = [r for r in company_universe if r["market_cap_krw"] >= MIN_MARKET_CAP]
    if len(cap_pass) < 80:
        raise RuntimeError(f"시가총액 1조원 통과 종목이 비정상적으로 적습니다: {len(cap_pass)}")

    print(
        "2/9 시총 1조 이상 기업 가격 이력 수집:", len(cap_pass),
        "(ETF·ETN·스팩 사전 제외", len(excluded_instruments), ")"
    )
    raw = []
    errors = []
    price_fetched_count = 0
    liquidity_rejected_count = 0
    def task(meta):
        errors_local = []
        # 1순위: Yahoo Finance. GitHub Actions 서버에서 네이버 fchart가 막히는 경우를 피함.
        try:
            rows, host = fetch_yahoo_history(meta["ticker"])
            x = calc_raw(meta, rows)
            x["dataSource"] = "Yahoo Finance"
            x["priceProvider"] = host
            return x
        except Exception as e:
            errors_local.append("Yahoo: " + str(e))

        # 2순위: Naver Finance. Yahoo가 개별 종목에서 실패할 때만 사용.
        try:
            rows = fetch_naver_history(meta["stock_code"])
            x = calc_raw(meta, rows)
            x["dataSource"] = "NAVER Finance"
            x["priceProvider"] = "fchart.stock.naver.com"
            return x
        except Exception as e:
            errors_local.append("Naver: " + str(e))

        # 두 실시간 공급자가 모두 실패하면 직전 정상 갱신값을 사용한다.
        # 후보 자체를 조용히 삭제하지 않고 CACHED로 명확히 표시한다.
        old = old_by_ticker.get(meta.get("ticker"), {}) if old_mode == "LIVE" else {}
        old_rows = old.get("history") or []
        if len(old_rows) >= 60:
            try:
                x = calc_raw(meta, old_rows)
                for key in ("historicalHighRatio", "historicalHighDate", "historyStartDate"):
                    if old.get(key) is not None:
                        x[key] = old.get(key)
                x["dataSource"] = "이전 정상값"
                x["priceProvider"] = "previous-successful-run"
                x["dataStatus"] = "CACHED"
                x["fallbackReason"] = " || ".join(errors_local)
                return x
            except Exception as e:
                errors_local.append("이전 정상값: " + str(e))

        raise RuntimeError(" || ".join(errors_local))

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(task, r): r for r in cap_pass}
        for i, fut in enumerate(as_completed(futures), 1):
            meta = futures[fut]
            try:
                x = fut.result()
                price_fetched_count += 1
                if x["avgTradingValue50d"] >= MIN_AVG_VALUE_50D:
                    raw.append(x)
                else:
                    liquidity_rejected_count += 1
            except Exception as e:
                errors.append({"ticker": meta["ticker"], "name": meta["name"], "error": str(e)})
            if i % 30 == 0:
                print("  history", i, "/", len(cap_pass))

    if len(raw) < 50:
        raise RuntimeError(f"정상 계산 종목이 너무 적어 기존 사이트를 덮어쓰지 않습니다: {len(raw)} / 가격수집 오류 {len(errors)}개. 첫 오류: {(errors[0].get('error') if errors else '없음')}")

    print("3/9 RS / Stage 2 계산")
    rsvals = [x["rsBlend"] for x in raw]
    ovals = [x["oneilRsRaw"] for x in raw]
    for x in raw:
        x["rsPercentile"] = round(percentile(rsvals, x["rsBlend"]), 1)
        x["oneilRsPercentile"] = round(percentile(ovals, x["oneilRsRaw"]), 1)
        x["trendTemplate"] = bool(x["stage2Core"] and x["rsPercentile"] >= 70)

    mkt = market_direction()

    print("4/9 OpenDART 재무·공시 — 캐시 우선 / 신규조회 최대 18개")
    dart_meta = dart_enrich(raw, old_by_ticker)
    print("  ", dart_meta.get("message"))

    print("5/9 OpenDART 사업내용 + 세부섹터 — 신규·강한 10개 우선 + 백로그 8개")
    profile_meta = profile_enrich(raw, old_by_ticker)
    print("  ", profile_meta.get("message"))

    # IMPORTANT: sector action is now calculated on DART-derived business sectors,
    # not only the broad KRX industry label.
    sectors = _build_sector_stats(raw)
    sector_by_name = {s["name"]: s for s in sectors}

    print("6/9 수급·컨센서스 생략 (사용자 설정)")
    flow_meta = {"connected": False, "coverage": 0, "source": "사용 안 함", "message": "수급 미사용"}

    print("7/9 점수 / 신호 / CAN SLIM 계산")
    today = datetime.now(KST).date().isoformat()
    for x in raw:
        sector = sector_by_name[x["sector"]]
        x["sectorAction"] = _sector_action_overlay(sector)
        add_can_slim(x, sector, mkt)
        hc = max(0, min(100, (x["high52Ratio"] - 70) / 30 * 100))
        vc = min(100, x["volumeRatio"] / 2 * 100)
        base = 0.30 * x["rsPercentile"] + 25 * x["conditionCount"] / 6 + 0.15 * hc + 0.10 * vc + 20 * (1 if x["trendTemplate"] else 0)
        # 종목 자체 점수와 섹터동반액션을 섞지 않는다.
        # 섹터동반액션은 매우 중요한 별도 보조신호로 화면에 강하게 표시한다.
        x["score"] = round(base, 1)

        if x["ma60"] and x["close"] < x["ma60"]:
            sig, reason = "EXIT", "60일선 아래 — 추세 훼손"
        elif x["trendTemplate"] and x["conditionCount"] >= 5 and x["rsPercentile"] >= 70:
            sig, reason = "BUY", "미너비니 Stage 2 + 이세무사 성장주 기준 5/6↑ + RS 70↑"
        elif x["conditionCount"] >= 4 and x["rsPercentile"] >= 60:
            sig, reason = "WATCH", "이세무사 성장주 기준 4/6↑ — 추가 확인"
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
        for k in ("chg1d","ret252","rs5","rs20","rs60","rsBlend","oneilRsRaw","demandRatio","volumeRatio","high52Ratio","historicalHighRatio","drawdown"):
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
        # 같은 세부섹터의 시총 상위기업뿐 아니라 현재 주도주도 반드시 보이게 한다.
        major_names = []
        for m in ((leader_rank[:1] if leader_rank else []) + major_rank):
            name = m.get("name")
            if name and name not in major_names:
                major_names.append(name)
            if len(major_names) >= 6:
                break
        sec["majorCompanies"] = major_names

    asof = max(x["date"] for x in raw)
    asof_date = date.fromisoformat(asof)
    for x in raw:
        stale_days = max(0, (asof_date - date.fromisoformat(x["date"])).days)
        x["staleDays"] = stale_days
        x["isStale"] = stale_days > 4
        if x["isStale"]:
            x["dataStatus"] = "STALE"
    stale_count = sum(bool(x.get("isStale")) for x in raw)
    live_count = sum(x.get("dataStatus") == "LIVE" and not x.get("isStale") for x in raw)
    cached_count = sum(x.get("dataStatus") == "CACHED" and not x.get("isStale") for x in raw)
    short_history_count = sum((x.get("historyTradingDays") or 0) < 260 for x in raw)
    source_counts = {
        "Yahoo Finance": sum(x.get("dataSource") == "Yahoo Finance" for x in raw),
        "NAVER Finance": sum(x.get("dataSource") == "NAVER Finance" for x in raw),
        "이전 정상값": sum(x.get("dataSource") == "이전 정상값" for x in raw),
    }
    coverage_pct = round(price_fetched_count / len(cap_pass) * 100, 1) if cap_pass else 0

    print("8/9 섹터 대장주 연결")
    consensus_meta = {"status": "NOT_USED", "source": "사용 안 함", "message": "컨센서스 미사용"}

    payload = {
        "meta": {
            "title": "WAMO MARKET RADAR · AUTO",
            "mode": "LIVE",
            "asOf": asof,
            "updatedAt": datetime.now(KST).isoformat(timespec="minutes"),
            "source": "Yahoo Finance price + NAVER market-cap/fallback + KRX KIND + OpenDART fundamentals/disclosures + DART business narrative + DART-derived detailed sectors",
            "universeCount": len(listed),
            "eligibleUniverseCount": len(company_universe),
            "successCount": len(raw),
            "errorCount": len(errors),
            "marketDirection": {"KOREA": mkt},
            "dataHealth": {
                "liveCount": live_count,
                "cachedCount": cached_count,
                "staleCount": stale_count,
                "failedCount": len(errors),
                "priceAttemptedCount": len(cap_pass),
                "priceFetchedCount": price_fetched_count,
                "priceCoveragePct": coverage_pct,
                "liquidityRejectedCount": liquidity_rejected_count,
                "excludedInstrumentCount": len(excluded_instruments),
                "shortHistoryCount": short_history_count,
                "sourceCounts": source_counts,
                "dartConnected": bool(dart_meta.get("connected")),
                "flowConnected": False,
                "consensusConnected": False,
                "message": f"상장 {len(listed):,}개 중 ETF·ETN·스팩 {len(excluded_instruments):,}개 분리 → 기업·리츠 {len(company_universe):,}개 → 시총 1조 이상 기업 {len(cap_pass):,}개 중 가격수집 {price_fetched_count:,}개({coverage_pct:.1f}%) → 거래대금 기준 통과 {len(raw):,}개 · 실패 {len(errors):,}개",
            },
            "universeFunnel": {
                "listed": len(listed),
                "companyUniverse": len(company_universe),
                "excludedInstruments": len(excluded_instruments),
                "liquidityPass": len(raw),
                "marketCapPass": len(cap_pass),
                "deepScanned": len(raw),
                "growth4plus": sum(x["conditionCount"] >= 4 for x in raw),
                "stage2": sum(bool(x["trendTemplate"]) for x in raw),
                "highZone": sum((x.get("high52Ratio") or 0) >= 93 or (x.get("historicalHighRatio") or 0) >= 93 for x in raw),
                "buy": sum(x["signal"] in ("BUY", "HOLD") for x in raw),
                "liquidityThresholdKRW": MIN_AVG_VALUE_50D,
                "marketCapThresholdKRW": MIN_MARKET_CAP,
                "marketCapEnforced": True,
            },
            "dartMeta": dart_meta,
            "profileMeta": profile_meta,
            "marketContextMeta": {"status": "NOT_USED", "source": "사용 안 함", "message": "수급 미사용(사용자 설정)"},
            "flowMeta": flow_meta,
            "consensusMeta": consensus_meta,
            "catalystMeta": {"status": "LIVE" if dart_meta.get("successCount",0) > 0 else "NOT_CONNECTED", "source": "OpenDART official"},
            "note": "후보 스크리닝 대시보드입니다. 검수 기업설명 DB와 OpenDART를 함께 사용하며, 재무·공시는 캐시 우선으로 갱신합니다. 수급·컨센서스·리비전은 사용자 설정에 따라 사용하지 않습니다. 신규 상장주는 60거래일부터 포함하되 200일선·Stage 2·정배열의 이력 부족을 별도 표시합니다.",
        },
        "sectors": sectors,
        "stocks": raw,
        "errors": errors,
    }

    payload["meta"]["qa"] = validate_payload_integrity(payload)

    print("9/9 index.html 갱신")
    new_html = replace_payload(old_html, payload)
    new_html = patch_index_health_ui(new_html)
    new_html = patch_leader_profile_ui(new_html)
    tmp = INDEX.with_suffix(".html.tmp")
    tmp.write_text(new_html, encoding="utf-8")
    tmp.replace(INDEX)
    print("완료:", asof, "종목", len(raw), "오류", len(errors))

if __name__ == "__main__":
    main()
