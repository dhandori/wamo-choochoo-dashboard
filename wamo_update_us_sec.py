#!/usr/bin/env python3
"""WAMO 미국판 자동갱신기.

가격·거래량은 Yahoo Finance 차트 데이터로 기술지표를 직접 계산하고,
기업 공시·재무는 인증키가 필요 없는 SEC EDGAR submissions/companyfacts를 사용한다.
한국판 index.html과 데이터를 섞지 않고 us.html만 갱신한다.
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import wamo_update_business_dart as core


ROOT = Path(__file__).resolve().parent
US_HTML = ROOT / "us.html"
SEC_CACHE = ROOT / "wamo_us_sec_cache.json"
KST = timezone(timedelta(hours=9))

MIN_MARKET_CAP_USD = 1_000_000_000
MIN_AVG_VALUE_50D_USD = 10_000_000
MAX_PRICE_UNIVERSE = 900
PRICE_WORKERS = 16
SEC_TARGET_MAX = 80
SEC_WORKERS = 3
SEC_CACHE_DAYS = 7
ENERGY_HISTORY_DAYS = 140

SEC_UA = os.getenv(
    "SEC_USER_AGENT",
    "WAMO-Market-Radar/1.0 personal-research github.com/dhandori",
).strip()
_SEC_LOCK = threading.Lock()
_SEC_LAST_REQUEST = 0.0


def _get_json(url, headers=None, timeout=35, retries=3):
    base = {
        "User-Agent": "Mozilla/5.0 (WAMO-Market-Radar/US)",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if headers:
        base.update(headers)
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=base)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last = exc
            time.sleep(1.0 + attempt * 1.3)
    raise RuntimeError(f"JSON 수집 실패: {url} · {last}")


def _num(value):
    if value is None:
        return None
    text = str(value).strip().replace("$", "").replace(",", "").replace("%", "")
    if not text or text.lower() in {"n/a", "na", "none", "null", "-"}:
        return None
    multiplier = 1.0
    if text[-1:].upper() in {"K", "M", "B", "T"}:
        multiplier = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[text[-1].upper()]
        text = text[:-1]
    try:
        number = float(text) * multiplier
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _is_excluded_security(symbol, name):
    symbol = str(symbol or "").upper().strip()
    name = str(name or "").upper().strip()
    if not symbol or len(symbol) > 8 or any(c in symbol for c in "^=+"):
        return True
    excluded_words = (
        " ETF", " ETN", "EXCHANGE TRADED", "INDEX FUND", "WARRANT", "RIGHTS",
        " ACQUISITION", "ACQUISITION CORP", "SPAC", "UNITS", " UNIT ",
        "PREFERRED", "DEPOSITARY SHARES EACH", "TEST STOCK",
    )
    if any(word in f" {name} " for word in excluded_words):
        return True
    if re.search(r"(?:\-W|\.W|/W|WS)$", symbol) or re.search(r"(?:\-U|\.U|/U)$", symbol):
        return True
    return False


SECTOR_KO = {
    "Technology": "기술", "Health Care": "헬스케어", "Healthcare": "헬스케어",
    "Consumer Cyclical": "경기소비재", "Consumer Defensive": "필수소비재",
    "Consumer Discretionary": "경기소비재", "Consumer Staples": "필수소비재",
    "Consumer Services": "소비자서비스", "Consumer Durables": "내구소비재",
    "Consumer Non-Durables": "비내구소비재",
    "Finance": "금융", "Financials": "금융", "Financial Services": "금융서비스",
    "Industrials": "산업재", "Capital Goods": "자본재", "Basic Industries": "기초산업",
    "Energy": "에너지", "Utilities": "유틸리티", "Real Estate": "부동산",
    "Public Utilities": "공공 유틸리티", "Transportation": "운송",
    "Telecommunications": "통신", "Communication Services": "커뮤니케이션 서비스",
    "Basic Materials": "기초소재", "Materials": "소재", "Miscellaneous": "기타",
}

INDUSTRY_RULES = (
    (r"semiconductor", "반도체"), (r"biotech", "바이오테크"),
    (r"pharmaceutical|drug", "제약"), (r"medical|dental|hospital|health", "의료·헬스케어"),
    (r"software", "소프트웨어"), (r"computer", "컴퓨터·IT 하드웨어"),
    (r"internet|web|portal", "인터넷 서비스"), (r"electronic", "전자부품·전자기기"),
    (r"bank", "은행"), (r"insurance", "보험"), (r"broker|investment banker", "증권·투자중개"),
    (r"investment manager|asset management", "자산운용"), (r"finance|financial", "금융서비스"),
    (r"real estate|reit", "부동산·리츠"), (r"oil|gas|petroleum", "석유·가스"),
    (r"electric util", "전력 유틸리티"), (r"water supply", "수도 유틸리티"),
    (r"natural gas distribution", "도시가스 유통"), (r"renewable|solar|wind", "신재생에너지"),
    (r"auto|motor vehicle", "자동차·부품"), (r"aerospace|defense", "항공우주·방산"),
    (r"machinery|industrial equipment", "산업기계·장비"), (r"chemical", "화학"),
    (r"steel|metal|mining", "금속·광업"), (r"construction|building", "건설·건자재"),
    (r"transport|trucking|railroad", "운송"), (r"marine|shipping", "해운"),
    (r"air freight|air transportation", "항공운송"), (r"retail", "소매유통"),
    (r"wholesale|distributor", "도매·유통"), (r"food", "식품"),
    (r"beverage", "음료"), (r"restaurant", "외식"), (r"tobacco", "담배"),
    (r"apparel|clothing|shoe", "의류·신발"), (r"hotel|resort", "호텔·리조트"),
    (r"broadcast|television|radio", "방송"), (r"publishing", "출판"),
    (r"advertising", "광고"), (r"telecommunication", "통신서비스"),
    (r"business service", "기업서비스"), (r"packaging|container", "포장재·용기"),
    (r"agricultur|farming", "농업"), (r"homebuilding", "주택건설"),
)


def _bilingual(text, korean=None):
    text = str(text or "").strip()
    if not text:
        return ""
    korean = korean or SECTOR_KO.get(text)
    if korean and korean != text:
        return f"{korean} ({text})"
    return f"기타 산업 ({text})"


def translate_industry(industry, broad=""):
    original = str(industry or "").strip()
    if not original:
        return _bilingual(broad) or "산업 미분류"
    lower = original.lower()
    for pattern, korean in INDUSTRY_RULES:
        if re.search(pattern, lower):
            return _bilingual(original, korean)
    broad_ko = SECTOR_KO.get(str(broad or "").strip(), "산업")
    return f"{broad_ko} 세부산업 ({original})"


def _nasdaq_screener_rows(payload):
    """Nasdaq 스크리너의 구형·현행 JSON 배열 위치를 모두 지원한다.

    현행 응답은 data.rows를 사용하지만, 일부 캐시·배포 응답은
    data.table.rows 형태로 내려온 적이 있어 두 형식을 모두 허용한다.
    """
    root = payload if isinstance(payload, dict) else {}
    data = root.get("data") if isinstance(root.get("data"), dict) else {}
    table = data.get("table") if isinstance(data.get("table"), dict) else {}
    nested = data.get("data") if isinstance(data.get("data"), dict) else {}
    for candidate in (data.get("rows"), table.get("rows"), nested.get("rows"), root.get("rows")):
        if isinstance(candidate, list):
            return candidate
    return []


def _find_reference_cycle(value):
    """dict/list 안에서 자기 자신을 다시 참조하는 경로를 찾는다."""
    active = {}

    def visit(node, path):
        if not isinstance(node, (dict, list, tuple)):
            return None
        oid = id(node)
        if oid in active:
            return f"{active[oid]} → {path}"
        active[oid] = path
        try:
            items = node.items() if isinstance(node, dict) else enumerate(node)
            for key, child in items:
                found = visit(child, f"{path}.{key}" if isinstance(node, dict) else f"{path}[{key}]")
                if found:
                    return found
        finally:
            active.pop(oid, None)
        return None

    return visit(value, "payload")


def _assert_json_payload(payload):
    """us.html 교체 전에 순환참조와 JSON 변환 가능 여부를 선제 검사한다."""
    cycle = _find_reference_cycle(payload)
    if cycle:
        raise RuntimeError(f"미국 payload 순환참조 감지: {cycle}")
    try:
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"미국 payload JSON 검사 실패: {exc}") from exc


def fetch_us_universe():
    """Nasdaq 공식 주식 스크리너 화면이 사용하는 JSON으로 미국 상장기업을 만든다."""
    query = urllib.parse.urlencode({
        "tableonly": "true", "limit": "5000", "offset": "0", "download": "true",
    })
    payload = _get_json(
        "https://api.nasdaq.com/api/screener/stocks?" + query,
        headers={"Referer": "https://www.nasdaq.com/market-activity/stocks/screener"},
    )
    rows = _nasdaq_screener_rows(payload)
    if len(rows) < 1000:
        status = (payload or {}).get("status") if isinstance(payload, dict) else None
        message = (payload or {}).get("message") if isinstance(payload, dict) else None
        raise RuntimeError(
            f"Nasdaq 미국 종목 목록이 비정상적으로 적습니다: {len(rows)}"
            f" · 응답상태={status or '없음'} · 메시지={message or '없음'}"
        )

    listed = []
    excluded = 0
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        name = str(row.get("name") or row.get("companyName") or "").strip()
        if _is_excluded_security(symbol, name):
            excluded += 1
            continue
        cap = _num(row.get("marketCap") or row.get("marketcap"))
        if not cap or cap < MIN_MARKET_CAP_USD:
            continue
        exchange_raw = str(row.get("exchange") or row.get("market") or "").strip().upper()
        if "NASDAQ" in exchange_raw:
            exchange = "NASDAQ"
        elif "AMEX" in exchange_raw or "NYSE AMERICAN" in exchange_raw:
            exchange = "NYSE AMERICAN"
        elif "NYSE" in exchange_raw:
            exchange = "NYSE"
        else:
            # The screener occasionally omits the exchange column. This affects only
            # the comparison benchmark label, not technical calculations.
            exchange = "NASDAQ" if symbol in str(row.get("url") or "").upper() else "NYSE"
        broad = str(row.get("sector") or "").strip()
        industry = str(row.get("industry") or "").strip()
        broad_label = _bilingual(broad) or "산업 미분류"
        industry_label = translate_industry(industry, broad)
        sector = industry_label or broad_label
        yahoo_symbol = symbol.replace(".", "-").replace("/", "-")
        listed.append({
            "ticker": yahoo_symbol,
            "displayTicker": symbol,
            "stock_code": symbol,
            "name": name or symbol,
            "sector": sector,
            "krxSector": broad_label,
            "industry": industry_label,
            "sectorEn": broad,
            "industryEn": industry,
            "sectorSource": "Nasdaq Stock Screener · Sector/Industry",
            "sectorPath": f"{broad_label} → {industry_label}" if broad and industry and broad != industry else sector,
            "exchange": exchange,
            "krx_market": "NASDAQ" if exchange == "NASDAQ" else "NYSE",
            "market_cap_usd": cap,
            # Core sector routine only uses this numeric field for within-sector ranking.
            "market_cap_krw": cap,
            "instrumentType": "REIT" if "REIT" in name.upper() else "COMPANY",
            "sectorConfidence": "MEDIUM" if industry or broad else "LOW",
        })
    listed.sort(key=lambda x: x["market_cap_usd"], reverse=True)
    if len(listed) < 300:
        raise RuntimeError(f"시총 10억달러 미국 일반기업이 비정상적으로 적습니다: {len(listed)}")
    return listed, excluded


def market_direction_us():
    errors = []
    for symbol, label in (("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq Composite")):
        try:
            rows = core.fetch_yahoo_index(symbol, years=2)
            closes = [c for _, c in rows]
            ma50 = core.mean_tail(closes, 50)
            ma200 = core.mean_tail(closes, 200)
            ma200_prev = sum(closes[-220:-20]) / 200 if len(closes) >= 220 else ma200
            if symbol == "^GSPC":
                sp = (rows, closes, ma50, ma200, ma200_prev)
            errors.append({
                "symbol": symbol, "label": label, "close": closes[-1],
                "ma50": ma50, "ma200": ma200,
                "pass": bool(closes[-1] > ma50 > ma200 and ma200 > ma200_prev),
            })
        except Exception as exc:
            errors.append({"symbol": symbol, "label": label, "error": str(exc), "pass": False})
    # errors 목록 안의 원본 dict에 benchmarks=errors를 붙이면
    # dict → list → 같은 dict로 돌아오는 순환참조가 된다. 반드시 복사본을 쓴다.
    primary = dict(next((x for x in errors if x.get("symbol") == "^GSPC"), errors[0]))
    primary["note"] = (
        "S&P 500이 50일선·200일선 위이고 50일선이 200일선 위이며 "
        "200일선이 상승하는지 확인합니다. Nasdaq Composite는 보조 확인입니다."
    )
    primary["benchmarks"] = [dict(item) for item in errors]
    return primary


def sector_flow_benchmarks_us(raw):
    result = {}
    errors = []
    for market, symbol in (("NASDAQ", "^IXIC"), ("NYSE", "^GSPC")):
        try:
            rows = core.fetch_yahoo_index(symbol, years=1)
            closes = [c for _, c in rows]
            result[market] = {
                "ret7": round((core.pct_change(closes, 7) or 0) * 100, 2),
                "ret30": round((core.pct_change(closes, 30) or 0) * 100, 2),
                "source": "Yahoo Finance " + symbol,
            }
        except Exception as exc:
            members = [x for x in raw if x.get("krx_market") == market]
            result[market] = {
                "ret7": round(statistics.median([x.get("ret7") or 0 for x in members]), 2) if members else 0,
                "ret30": round(statistics.median([x.get("ret30") or 0 for x in members]), 2) if members else 0,
                "source": "정밀계산 종목 중앙값 대체",
            }
            errors.append({"market": market, "error": str(exc)})
    return result, {
        "status": "LIVE" if not errors else "PARTIAL",
        "source": {k: v["source"] for k, v in result.items()},
        "benchmarks": result,
        "errors": errors,
        "message": "NASDAQ 종목은 Nasdaq Composite, NYSE·NYSE American 종목은 S&P 500과 7·30일 수익률을 비교합니다.",
    }


def build_us_market_energy(raw):
    """현재 미국 정밀계산 유니버스의 볼린저 상단 돌파 확산을 350종목으로 환산."""
    by_date = {}
    for stock in raw:
        hist = stock.get("history") or []
        closes = []
        for row in hist:
            close = float(row.get("close") or 0)
            closes.append(close)
            if len(closes) < 20 or close <= 0:
                continue
            window = closes[-20:]
            upper = statistics.mean(window) + 2 * statistics.pstdev(window)
            slot = by_date.setdefault(row["date"], {"valid": 0, "breakouts": 0, "names": []})
            slot["valid"] += 1
            if close > upper:
                slot["breakouts"] += 1
                slot["names"].append({
                    "name": stock.get("name"), "ticker": stock.get("ticker"),
                    "index": stock.get("exchange"), "close": round(close, 2),
                    "upper": round(upper, 2), "distancePct": round((close / upper - 1) * 100, 2),
                })
    dates = sorted(by_date)[-ENERGY_HISTORY_DAYS:]
    series = []
    for d in dates:
        slot = by_date[d]
        normalized = slot["breakouts"] * 350 / slot["valid"] if slot["valid"] else 0
        series.append({
            "date": d, "count": slot["breakouts"], "valid": slot["valid"],
            "normalized350": round(normalized, 2), "normalizedCount": round(normalized, 2),
        })
    for i, row in enumerate(series):
        vals = [z["normalized350"] for z in series[max(0, i - 4):i + 1]]
        row["ma5"] = round(statistics.mean(vals), 2) if len(vals) == 5 else None
    latest = series[-1] if series else {}
    latest_ma5 = latest.get("ma5")
    prior_ma5 = series[-6].get("ma5") if len(series) >= 6 else None
    slope = latest_ma5 - prior_ma5 if latest_ma5 is not None and prior_ma5 is not None else None
    below = 0
    for row in reversed(series):
        if row.get("ma5") is not None and row["ma5"] < 10:
            below += 1
        else:
            break
    coverage = latest.get("valid", 0) / len(raw) * 100 if raw else 0
    if coverage < 90 or latest_ma5 is None:
        regime, note = "DATA_CHECK", "가격 커버리지 또는 5일 이력이 부족해 구간 판정을 보류합니다."
    elif latest_ma5 < 10:
        regime, note = "DANGER", "상단 돌파 확산이 약합니다. 40거래일가량 지속되는지 확인합니다."
    elif latest_ma5 < 20:
        regime, note = "CAUTION", "중립 구간입니다. 10선 위 유지와 5일 평균 기울기 개선을 함께 봅니다."
    elif slope is not None and slope >= 0:
        regime, note = "FAVORABLE", "상단 돌파가 넓게 확산되는 상대적 우호 구간입니다."
    else:
        regime, note = "COOLING", "절대 수준은 높지만 확산 속도 둔화 여부를 확인합니다."
    latest_names = by_date.get(dates[-1], {}).get("names", []) if dates else []
    return {
        "status": "LIVE" if series else "FAILED",
        "membershipMode": "SCREENED_US_UNIVERSE",
        "source": "미국 정밀계산 유니버스 · Yahoo Finance 가격으로 직접 계산",
        "asOf": dates[-1] if dates else None,
        "constituentCount": len(raw),
        "constituents": [{"ticker": x.get("ticker"), "name": x.get("name")} for x in raw],
        "breakoutCount": int(latest.get("count") or 0),
        "normalizedCount": latest.get("normalized350"),
        "normalizedBreakoutCount": latest.get("normalized350"),
        "validCount": int(latest.get("valid") or 0),
        "ma5": latest_ma5,
        "slope5": round(slope, 2) if slope is not None else None,
        "ma5Change5d": round(slope, 2) if slope is not None else None,
        "below10Streak": below,
        "coveragePct": round(coverage, 1),
        "historyErrorCount": 0,
        "regime": regime,
        "regimeNote": note,
        "series": series,
        "breakouts": latest_names,
        "approximationUsed": False,
        "normalizationNote": "실제 검출 종목수와 별도로 비교 편의를 위해 350종목 기준 환산값을 표시합니다.",
        "survivorshipBias": "현재 정밀계산 종목을 과거에도 적용하므로 당시 실제 상장·시총·유동성 구성과 다를 수 있습니다.",
    }


def _load_cache():
    try:
        data = json.loads(SEC_CACHE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(data):
    SEC_CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def sec_company_map():
    data = _get_json(
        "https://www.sec.gov/files/company_tickers.json",
        headers={"User-Agent": SEC_UA, "Referer": "https://www.sec.gov/"},
    )
    out = {}
    for row in (data or {}).values():
        ticker = str(row.get("ticker") or "").upper()
        if ticker:
            out[ticker] = {"cik": str(row.get("cik_str") or "").zfill(10), "title": row.get("title")}
    if len(out) < 5000:
        raise RuntimeError(f"SEC ticker 목록이 비정상적으로 적습니다: {len(out)}")
    return out


def _sec_get(url):
    global _SEC_LAST_REQUEST
    # SEC asks automated clients to stay below 10 requests/second. A single lock
    # keeps the three workers at a conservative global pace.
    with _SEC_LOCK:
        wait = 0.13 - (time.monotonic() - _SEC_LAST_REQUEST)
        if wait > 0:
            time.sleep(wait)
        result = _get_json(url, headers={"User-Agent": SEC_UA, "Referer": "https://www.sec.gov/"}, retries=3)
        _SEC_LAST_REQUEST = time.monotonic()
        return result


def _fact_units(facts, concepts):
    usgaap = ((facts or {}).get("facts") or {}).get("us-gaap") or {}
    for concept in concepts:
        units = (usgaap.get(concept) or {}).get("units") or {}
        for unit in ("USD/shares", "USD", "shares"):
            if units.get(unit):
                return units[unit], concept, unit
    return [], None, None


def _quarter_frames(facts, concepts):
    rows, concept, _ = _fact_units(facts, concepts)
    values = {}
    for row in rows:
        frame = str(row.get("frame") or "")
        if not re.fullmatch(r"CY\d{4}Q[1-4]", frame) or row.get("form") not in ("10-Q", "10-K"):
            continue
        value = _num(row.get("val"))
        if value is None:
            continue
        previous = values.get(frame)
        if previous is None or str(row.get("filed") or "") >= previous[0]:
            values[frame] = (str(row.get("filed") or ""), value)
    return {frame: item[1] for frame, item in values.items()}, concept


def _quarter_growth_history(facts, concepts, count=3):
    values, concept = _quarter_frames(facts, concepts)
    result = []
    for frame in sorted(values):
        match = re.fullmatch(r"CY(\d{4})Q([1-4])", frame)
        if not match:
            continue
        previous = f"CY{int(match.group(1)) - 1}Q{match.group(2)}"
        cur, old = values[frame], values.get(previous)
        if old is None or old <= 0:
            continue
        result.append({"frame": frame, "growthPct": round((cur / old - 1) * 100, 1)})
    return result[-count:], concept


def _margin_change(facts, numerator_concepts, revenue_concepts):
    numerators, numerator_name = _quarter_frames(facts, numerator_concepts)
    revenues, revenue_name = _quarter_frames(facts, revenue_concepts)
    common = sorted(set(numerators) & set(revenues))
    if not common:
        return None
    latest = common[-1]
    match = re.fullmatch(r"CY(\d{4})Q([1-4])", latest)
    if not match:
        return None
    previous = f"CY{int(match.group(1)) - 1}Q{match.group(2)}"
    if previous not in numerators or previous not in revenues or not revenues[latest] or not revenues[previous]:
        return None
    latest_margin = numerators[latest] / revenues[latest] * 100
    previous_margin = numerators[previous] / revenues[previous] * 100
    return {
        "frame": latest, "latestPct": round(latest_margin, 1),
        "priorYearPct": round(previous_margin, 1),
        "changePp": round(latest_margin - previous_margin, 1),
        "numeratorConcept": numerator_name, "revenueConcept": revenue_name,
    }


def _latest_quarter_yoy(facts, concepts):
    rows, concept, _ = _fact_units(facts, concepts)
    usable = [r for r in rows if r.get("form") in ("10-Q", "10-K", "20-F", "40-F") and r.get("fy") and r.get("fp")]
    by_key = {}
    for r in usable:
        if r.get("val") is None or r.get("fp") not in ("Q1", "Q2", "Q3", "FY"):
            continue
        key = (int(r["fy"]), str(r["fp"]))
        previous = by_key.get(key)
        # 정정공시·중복 태그가 있으면 기간종료일, 제출일이 가장 최신인 값을 쓴다.
        rank = (str(r.get("end") or ""), str(r.get("filed") or ""), str(r.get("accn") or ""))
        old_rank = ((str(previous.get("end") or ""), str(previous.get("filed") or ""), str(previous.get("accn") or ""))
                    if previous else ("", "", ""))
        if previous is None or rank >= old_rank:
            by_key[key] = r
    ordered = sorted(by_key.items(), key=lambda kv: (str(kv[1].get("end") or ""), kv[0]), reverse=True)
    for (fy, fp), cur in ordered:
        prev = by_key.get((fy - 1, fp))
        if not prev:
            continue
        a, b = _num(cur.get("val")), _num(prev.get("val"))
        if a is None or b is None:
            continue
        if b > 0:
            return round((a / b - 1) * 100, 1), "YOY", concept
        if b <= 0 < a:
            return None, "TURNAROUND", concept
    return None, None, concept


def _annual_metrics(facts, concepts):
    rows, concept, _ = _fact_units(facts, concepts)
    annual_rows = {}
    for r in rows:
        if r.get("form") not in ("10-K", "20-F", "40-F") or r.get("fp") != "FY" or r.get("val") is None:
            continue
        fy = r.get("fy")
        if fy:
            year = int(fy)
            previous = annual_rows.get(year)
            rank = (str(r.get("end") or ""), str(r.get("filed") or ""), str(r.get("accn") or ""))
            old_rank = ((str(previous.get("end") or ""), str(previous.get("filed") or ""), str(previous.get("accn") or ""))
                        if previous else ("", "", ""))
            if previous is None or rank >= old_rank:
                annual_rows[year] = r
    annual = {year: float(row["val"]) for year, row in annual_rows.items()}
    years = sorted(annual)
    if len(years) < 2:
        return None, None, [], concept
    latest, previous = annual[years[-1]], annual[years[-2]]
    yoy = (latest / previous - 1) * 100 if previous > 0 else None
    cagr = None
    if len(years) >= 4:
        old = annual[years[-4]]
        if old > 0 and latest > 0:
            cagr = ((latest / old) ** (1 / 3) - 1) * 100
    series = [{"year": y, "value": annual[y]} for y in years[-4:]]
    return (round(yoy, 1) if yoy is not None else None,
            round(cagr, 1) if cagr is not None else None, series, concept)


def sec_enrich_one(stock, mapping):
    cik = mapping["cik"]
    submissions = _sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    time.sleep(0.12)
    facts = _sec_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
    recent = ((submissions.get("filings") or {}).get("recent") or {})
    forms = recent.get("form") or []
    accessions = recent.get("accessionNumber") or []
    primary_docs = recent.get("primaryDocument") or []
    filing_dates = recent.get("filingDate") or []
    filing_url = ""
    filing_date = ""
    for i, form in enumerate(forms):
        if form in ("10-K", "20-F", "40-F") and i < len(accessions) and i < len(primary_docs):
            accession_flat = str(accessions[i]).replace("-", "")
            filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_flat}/{primary_docs[i]}"
            filing_date = filing_dates[i] if i < len(filing_dates) else ""
            break
    cutoff = date.today() - timedelta(days=45)
    recent_filings = []
    for i, form in enumerate(forms):
        if form not in ("8-K", "10-Q", "10-K", "20-F", "40-F", "6-K") or i >= len(filing_dates):
            continue
        try:
            filed = date.fromisoformat(filing_dates[i])
        except Exception:
            continue
        if filed < cutoff:
            continue
        recent_filings.append({
            "date": filed.isoformat(), "category": "SEC 공시",
            "report": f"{form} 제출 · 촉매 여부는 원문 확인", "polarity": "NEUTRAL",
        })
        if len(recent_filings) >= 8:
            break

    eps_concepts = ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "EarningsPerShareBasic")
    sales_concepts = ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet")
    eps_yoy, eps_mode, eps_concept = _latest_quarter_yoy(facts, eps_concepts)
    sales_yoy, sales_mode, sales_concept = _latest_quarter_yoy(facts, sales_concepts)
    annual_yoy, annual_cagr, annual_series, annual_concept = _annual_metrics(facts, eps_concepts)
    eps_history, _ = _quarter_growth_history(facts, eps_concepts)
    sales_history, _ = _quarter_growth_history(facts, sales_concepts)
    eps_acceleration = bool(
        len(eps_history) >= 3
        and all(eps_history[i]["growthPct"] > eps_history[i - 1]["growthPct"] for i in range(1, len(eps_history)))
        and eps_history[-1]["growthPct"] >= 25
    )
    sales_acceleration = bool(
        len(sales_history) >= 3
        and all(sales_history[i]["growthPct"] > sales_history[i - 1]["growthPct"] for i in range(1, len(sales_history)))
        and sales_history[-1]["growthPct"] >= 20
    )
    gross_margin = _margin_change(facts, ("GrossProfit",), sales_concepts)
    operating_margin = _margin_change(facts, ("OperatingIncomeLoss",), sales_concepts)
    sic_desc = str(submissions.get("sicDescription") or "SEC 업종 미분류")
    industry = stock.get("industry") or stock.get("sector") or "산업 미분류"
    summary = f"{stock.get('name')}은 Nasdaq 분류상 {industry}에 속한 미국 상장기업입니다. SEC SIC 공식명은 {sic_desc}입니다."
    profile = {
        "summary": summary,
        "products": "SEC 10-K Item 1 원문에서 주요 제품·서비스를 확인해야 합니다.",
        "customers": "SEC 10-K Item 1 및 위험요인 원문에서 고객 집중도를 확인해야 합니다.",
        "revenue": (f"SEC XBRL 매출 계정({sales_concept})으로 최근 분기 성장률을 계산합니다."
                    if sales_concept else "SEC XBRL에서 비교 가능한 매출 계정을 확보하지 못했습니다."),
        "drivers": "실적 성장률, 가격·거래량, 산업 동조와 신규 SEC 공시를 함께 확인합니다.",
        "segments": list(dict.fromkeys([z for z in (stock.get("krxSector"), industry, sic_desc) if z]))[:4],
    }
    return {
        "secStatus": "LIVE",
        "secSource": "SEC EDGAR submissions + XBRL companyfacts",
        "cik": cik,
        "secCompanyUrl": f"https://www.sec.gov/edgar/browse/?CIK={int(cik)}&owner=exclude",
        "secFilingUrl": filing_url,
        "businessModelUrl": filing_url,
        "businessModelReportDate": filing_date,
        "businessModelSource": "SEC EDGAR 10-K·XBRL",
        "businessProfile": profile,
        "businessModelEasy": summary,
        "catalysts": recent_filings,
        "eps_yoy": eps_yoy,
        "eps_growth_mode": eps_mode,
        "epsConcept": eps_concept,
        "sales_yoy": sales_yoy,
        "sales_growth_mode": sales_mode,
        "salesConcept": sales_concept,
        "latest_annual_eps_yoy": annual_yoy,
        "annual_eps_cagr_3y": annual_cagr,
        "annualEpsSeries": annual_series,
        "annualConcept": annual_concept,
        "quarterlyEpsGrowthHistory": eps_history,
        "quarterlySalesGrowthHistory": sales_history,
        "epsAcceleration": eps_acceleration,
        "salesAcceleration": sales_acceleration,
        "grossMargin": gross_margin,
        "operatingMargin": operating_margin,
        "sepaFundamental": {
            "status": "STRONG" if eps_yoy is not None and eps_yoy >= 25 and sales_yoy is not None and sales_yoy >= 20 and (eps_acceleration or sales_acceleration) else "CHECK",
            "epsPass": bool(eps_yoy is not None and eps_yoy >= 25),
            "salesPass": bool(sales_yoy is not None and sales_yoy >= 20),
            "epsAcceleration": eps_acceleration,
            "salesAcceleration": sales_acceleration,
            "marginImproving": bool((gross_margin and gross_margin["changePp"] > 0) or (operating_margin and operating_margin["changePp"] > 0)),
            "earningsSurpriseStatus": "UNKNOWN",
            "earningsSurpriseNote": "무료 컨센서스 미연결로 어닝 서프라이즈·추정치 상향은 자동 판정하지 않습니다.",
        },
        "sic": submissions.get("sic"),
        "sicDescription": sic_desc,
        "sectorValidationSource": "Nasdaq Sector/Industry + SEC SIC",
        "secFetchedAt": date.today().isoformat(),
    }


def _apply_sec_pending_profile(stock, status, note):
    """SEC 상세가 없을 때 검증된 Nasdaq 분류만 표시하고 빈 화면을 피합니다."""
    sector = stock.get("sector") or "산업 미분류"
    broad = stock.get("krxSector") or sector
    stock.update({
        "secStatus": status,
        "secSource": "SEC EDGAR",
        "businessModelSource": "Nasdaq 산업분류 · SEC 상세 연결 대기",
        "businessModelEasy": f"{stock.get('name')}은 Nasdaq 분류상 {sector}에 속합니다. SEC 사업 원문은 아직 연결되지 않았습니다.",
        "businessProfile": {
            "summary": f"{stock.get('name')}은 Nasdaq 분류상 {sector}에 속합니다. {note}",
            "products": "SEC 10-K Item 1 연결 후 표시합니다.",
            "customers": "SEC 10-K 고객·위험요인 연결 후 표시합니다.",
            "revenue": "SEC XBRL 비교 가능 계정 연결 후 표시합니다.",
            "drivers": "현재는 가격·거래량·섹터 흐름만 확인할 수 있습니다.",
            "segments": list(dict.fromkeys([broad, sector])),
        },
    })


def enrich_sec(raw):
    cache = _load_cache()
    errors = []
    try:
        mapping = sec_company_map()
        connected = True
    except Exception as exc:
        mapping, connected = {}, False
        errors.append({"scope": "company_tickers.json", "error": str(exc)})

    priority = sorted(raw, key=lambda x: (
        1 if x.get("conditionCount", 0) >= 4 else 0,
        1 if x.get("trendTemplate") else 0,
        1 if max(x.get("high52Ratio", 0), x.get("historicalHighRatio", 0)) >= 93 else 0,
        x.get("rsPercentile", 0), x.get("market_cap_usd", 0),
    ), reverse=True)
    selected = []
    cached_count = 0
    for stock in priority:
        symbol = str(stock.get("displayTicker") or stock.get("ticker") or "").upper()
        old = cache.get(symbol) or {}
        fresh = False
        try:
            fresh = (date.today() - date.fromisoformat(str(old.get("secFetchedAt"))[:10])).days < SEC_CACHE_DAYS
        except Exception:
            pass
        if fresh and old.get("secStatus") == "LIVE":
            stock.update(old)
            stock["secStatus"] = "CACHED"
            cached_count += 1
        elif symbol in mapping and len(selected) < SEC_TARGET_MAX:
            selected.append((stock, symbol, mapping[symbol]))
        elif old.get("secStatus") in ("LIVE", "CACHED"):
            stock.update(old)
            stock["secStatus"] = "CACHED"
            cached_count += 1
        else:
            _apply_sec_pending_profile(
                stock,
                "NOT_TARGET" if symbol in mapping else "NO_MATCH",
                "SEC 회사별 상세는 이번 제한조회 대상이 아니거나 티커 연결 대기 중입니다.",
            )

    with ThreadPoolExecutor(max_workers=SEC_WORKERS) as pool:
        futures = {pool.submit(sec_enrich_one, stock, item): (stock, symbol) for stock, symbol, item in selected}
        for i, future in enumerate(as_completed(futures), 1):
            stock, symbol = futures[future]
            try:
                enriched = future.result()
                stock.update(enriched)
                cache[symbol] = enriched
            except Exception as exc:
                prior = cache.get(symbol) or {}
                if prior.get("secStatus") in ("LIVE", "CACHED"):
                    stock.update(prior)
                    stock["secStatus"] = "CACHED"
                    cached_count += 1
                else:
                    _apply_sec_pending_profile(
                        stock, "ERROR",
                        "이번 갱신에서 SEC 원문을 확보하지 못해 다음 자동갱신에서 다시 시도합니다.",
                    )
                errors.append({"ticker": symbol, "error": core.compact_provider_error(str(exc))})
            if i % 10 == 0 or i == len(futures):
                print("  SEC", i, "/", len(futures))
    _save_cache(cache)
    usable = sum(x.get("secStatus") in ("LIVE", "CACHED") for x in raw)
    return {
        "status": "LIVE" if connected and not errors else "PARTIAL" if connected or usable else "FAILED",
        "connected": connected,
        "targetCount": len(selected),
        "successCount": usable,
        "cachedCount": cached_count,
        "fetchedCount": sum(x.get("secStatus") == "LIVE" for x in raw),
        "errorCount": len(errors),
        "source": "SEC EDGAR official submissions/XBRL",
        "message": f"SEC 공식자료 사용가능 {usable}/{len(raw)}개 · 이번 조회 {len(selected)}개 · 캐시 {cached_count}개",
        "errors": errors[:20],
    }


def cs_item(status, value, criterion, why):
    return {"status": status, "value": value, "criterion": criterion, "why": why}


def add_can_slim_us(stock, sector, market):
    items = {}
    eps = stock.get("eps_yoy")
    eps_mode = stock.get("eps_growth_mode")
    sales = stock.get("sales_yoy")
    if eps_mode == "TURNAROUND":
        items["C"] = cs_item("PASS", "최근 분기 EPS 흑자전환", "최근 분기 EPS YoY ≥ +25% 또는 흑자전환", "SEC XBRL 동기 비교입니다.")
    elif eps is not None:
        items["C"] = cs_item("PASS" if eps >= 25 else "FAIL", f"EPS YoY {eps:+.1f}%" + (f" · 매출 {sales:+.1f}%" if sales is not None else ""), "최근 분기 EPS YoY ≥ +25%", "SEC XBRL 동기 비교입니다.")
    else:
        items["C"] = cs_item("UNKNOWN", "비교 가능한 분기 EPS 없음", "최근 분기 EPS YoY ≥ +25%", "미확인 값을 임의 판정하지 않습니다.")
    ay, ac = stock.get("latest_annual_eps_yoy"), stock.get("annual_eps_cagr_3y")
    if ay is not None and ac is not None:
        items["A"] = cs_item("PASS" if ay >= 25 and ac >= 25 else "FAIL", f"연간 EPS YoY {ay:+.1f}% · 3년 CAGR {ac:+.1f}%", "연간 EPS YoY와 3년 CAGR 모두 ≥ +25%", "SEC XBRL 연간값입니다.")
    else:
        items["A"] = cs_item("UNKNOWN", "비교 가능한 연간 EPS 4개년 부족", "연간 EPS YoY와 3년 CAGR 모두 ≥ +25%", "미확인 값을 임의 판정하지 않습니다.")
    high = max(stock.get("high52Ratio") or 0, stock.get("historicalHighRatio") or 0)
    items["N"] = cs_item("PASS" if high >= 95 else "FAIL", f"52주/수집이력 고점比 {high:.1f}%", "현재가 ≥ 52주 또는 전체 수집이력 고점의 95%", "가격의 새로운 고점을 대체지표로 사용합니다.")
    demand = stock.get("demandRatio")
    items["S"] = cs_item("PASS" if demand is not None and demand >= 1.15 else "FAIL", f"상승/하락일 거래량比 {demand:.2f}x" if demand is not None else "거래량 비교 없음", "거래량 수요우위 ≥ 1.15x", "무료 발행주식수 시계열이 없으므로 거래량 수요만 판정합니다.")
    lead = (stock.get("oneilRsPercentile") or 0) >= 80 and (stock.get("sectorAction") or {}).get("status") == "CONFIRMED"
    items["L"] = cs_item("PASS" if lead else "FAIL", f"오닐식 RS {stock.get('oneilRsPercentile',0):.0f} · 섹터 {sector.get('score',0):.0f}", "오닐식 RS ≥80 + 확정 섹터액션", "시장 내 주도주 여부를 봅니다.")
    items["I"] = cs_item("UNKNOWN", "무료 기관보유 시계열 자동연결 안 함", "기관 보유·후원 확인", "미확인 값을 임의 판정하지 않습니다.")
    items["M"] = cs_item("PASS" if market.get("pass") else "FAIL", "S&P 500 추세 우호" if market.get("pass") else "S&P 500 추세 비우호", "S&P 500 >50일선 >200일선 + 200일선 상승", market.get("note"))
    measured = [v for k, v in items.items() if k != "I" and v["status"] != "UNKNOWN"]
    passes = sum(v["status"] == "PASS" for v in measured)
    stock["canSlim"] = {
        "items": items, "passCount": passes, "measuredCount": len(measured),
        "unknownCount": sum(v["status"] == "UNKNOWN" for v in items.values()),
        "fullMatch": len(measured) == 6 and passes == 6,
        "strongCandidate": len(measured) >= 5 and passes >= 5,
        "preliminary": len(measured) < 5,
    }


def validate_us_payload(payload):
    stocks, sectors = payload.get("stocks") or [], payload.get("sectors") or []
    issues = []
    checks = 0
    tickers = [x.get("ticker") for x in stocks]
    checks += 1
    if len(tickers) != len(set(tickers)):
        issues.append("중복 티커 존재")
    sector_map = {s.get("name"): s for s in sectors}
    for stock in stocks:
        checks += 4
        if stock.get("instrumentType") not in ("COMPANY", "REIT"):
            issues.append(f"{stock.get('ticker')}: 제외 증권 혼입")
        if stock.get("conditionCount") != sum(bool(v) for v in (stock.get("conditions") or {}).values()):
            issues.append(f"{stock.get('ticker')}: 6조건 합계 불일치")
        if len(stock.get("stage2Checks") or {}) != 7:
            issues.append(f"{stock.get('ticker')}: Stage 2 차트조건 7개 아님")
        expected_stage = bool((stock.get("stage2Checks") or {}) and all((stock.get("stage2Checks") or {}).values()) and (stock.get("rsPercentile") or 0) >= 70)
        if bool(stock.get("trendTemplate")) != expected_stage:
            issues.append(f"{stock.get('ticker')}: Stage 2 불일치")
        vcp = stock.get("vcp") or {}
        checks += 1
        if vcp.get("status") not in ("BREAKOUT", "READY", "WATCH", "NONE", "INSUFFICIENT"):
            issues.append(f"{stock.get('ticker')}: VCP 상태값 오류")
        if vcp.get("candidate") and not (vcp.get("shrinking") and vcp.get("volumeDryUp") and vcp.get("tightRange")):
            issues.append(f"{stock.get('ticker')}: VCP 후보 조건 불일치")
        sec = sector_map.get(stock.get("sector"))
        if not sec or (stock.get("sectorAction") or {}).get("status") != core._stock_sector_action_overlay(stock, sec).get("status"):
            issues.append(f"{stock.get('ticker')}: 섹터액션 불일치")
    funnel = (payload.get("meta") or {}).get("universeFunnel") or {}
    expected = {
        "deepScanned": len(stocks),
        "growth4plus": sum(x.get("conditionCount", 0) >= 4 for x in stocks),
        "stage2": sum(bool(x.get("trendTemplate")) for x in stocks),
        "highZone": sum(max(x.get("high52Ratio", 0), x.get("historicalHighRatio", 0)) >= 93 for x in stocks),
        "tripleAxis": sum(x.get("conditionCount", 0) >= 4 and x.get("trendTemplate") and max(x.get("high52Ratio", 0), x.get("historicalHighRatio", 0)) >= 93 for x in stocks),
        "sectorAction": sum((x.get("sectorAction") or {}).get("status") == "CONFIRMED" for x in stocks),
    }
    for key, value in expected.items():
        checks += 1
        if funnel.get(key) != value:
            issues.append(f"퍼널 {key} 불일치")
    health = (payload.get("meta") or {}).get("dataHealth") or {}
    attempted = int(health.get("priceAttemptedCount") or 0)
    fetched = int(health.get("priceFetchedCount") or 0)
    failed = int(health.get("failedCount") or 0)
    short_excluded = int(health.get("shortHistoryExcludedCount") or 0)
    checks += 3
    if attempted and attempted != fetched + failed + short_excluded:
        issues.append("미국 가격수집 시도·성공·제외 합계 불일치")
    if failed != len(payload.get("errors") or []):
        issues.append("미국 가격 제공처 오류 건수 불일치")
    if short_excluded != len(payload.get("shortHistoryExclusions") or []):
        issues.append("미국 신규상장 이력부족 제외 건수 불일치")
    energy = (payload.get("meta") or {}).get("marketEnergy") or {}
    checks += 3
    if energy.get("status") == "LIVE" and (not energy.get("series") or energy.get("membershipMode") != "SCREENED_US_UNIVERSE"):
        issues.append("미국 시장에너지 기준 불일치")
    if len(energy.get("constituents") or []) != len(stocks):
        issues.append("시장에너지 구성종목 수 불일치")
    if len(stocks) < 50:
        issues.append("정밀계산 종목 50개 미만")
    if issues:
        raise RuntimeError("미국판 정합성 검사 실패: " + " / ".join(issues[:12]))
    return {
        "status": "PASS", "checks": checks,
        "checkedAt": datetime.now(KST).isoformat(timespec="minutes"),
        "note": "중복·제외증권·6조건·Stage 2 8조건·VCP 정량 후보·신고가권·3축·섹터액션·미국 볼린저 시장에너지의 숫자를 자동 대조했습니다.",
    }


def main():
    if not US_HTML.exists():
        raise SystemExit("us.html을 찾지 못했습니다.")
    old_html = US_HTML.read_text(encoding="utf-8")
    old_payload = core.extract_old_payload(old_html)
    old_live = (old_payload.get("meta") or {}).get("market") == "US"
    old_by_ticker = {x.get("ticker"): x for x in old_payload.get("stocks", []) if x.get("ticker")}
    # 첫 실행에서 뒤 단계가 실패해도 워크플로의 git add가 캐시 파일 부재로
    # 추가 실패하지 않도록 빈 캐시를 먼저 만든다.
    if not SEC_CACHE.exists():
        _save_cache({})

    print("1/9 미국 상장기업 목록 수집")
    listed, excluded_by_name = fetch_us_universe()
    # 정적 GitHub Pages의 용량·모바일 속도를 지키기 위해 시총순 최대 900개만
    # 가격 정밀계산한다. 이 제한은 화면과 메타데이터에도 명시한다.
    candidates = listed[:MAX_PRICE_UNIVERSE]
    print("  시총 10억달러 이상", len(listed), "개 · 가격수집 상위", len(candidates), "개")

    print("2/9 미국 가격·거래량 수집")
    raw, errors, short_history_exclusions = [], [], []
    fetched = liquidity_rejected = 0

    def task(meta):
        try:
            rows, host = core.fetch_yahoo_history(meta["ticker"])
            stock = core.calc_raw(meta, rows)
            stock["dataSource"] = "Yahoo Finance"
            stock["priceProvider"] = host
            return stock
        except Exception as first:
            old = old_by_ticker.get(meta["ticker"], {}) if old_live else {}
            if len(old.get("history") or []) >= 60:
                stock = core.calc_raw(meta, old["history"])
                for key in ("historicalHighRatio", "historicalHighDate", "historyStartDate"):
                    if old.get(key) is not None:
                        stock[key] = old[key]
                stock["dataSource"] = "이전 미국 정상값"
                stock["dataStatus"] = "CACHED"
                stock["fallbackReason"] = str(first)
                return stock
            raise

    with ThreadPoolExecutor(max_workers=PRICE_WORKERS) as pool:
        futures = {pool.submit(task, meta): meta for meta in candidates}
        for i, future in enumerate(as_completed(futures), 1):
            meta = futures[future]
            try:
                stock = future.result()
                fetched += 1
                if stock.get("avgTradingValue50d", 0) >= MIN_AVG_VALUE_50D_USD:
                    raw.append(stock)
                else:
                    liquidity_rejected += 1
            except Exception as exc:
                message = core.compact_provider_error(str(exc))
                history_days = core.history_days_from_error(message)
                if history_days is not None and history_days < 60:
                    short_history_exclusions.append({
                        "ticker": meta.get("ticker"), "name": meta.get("name"),
                        "historyTradingDays": history_days,
                        "reason": "신규상장·거래이력 60일 미만",
                    })
                else:
                    errors.append({"ticker": meta.get("ticker"), "name": meta.get("name"), "error": message})
            if i % 50 == 0 or i == len(futures):
                print("  price", i, "/", len(futures))
    if len(raw) < 50:
        raise RuntimeError(f"미국 정상 계산 종목이 너무 적어 us.html을 덮어쓰지 않습니다: {len(raw)}")

    print("3/9 RS·Stage 2·볼린저 시장에너지")
    rs_values = [x["rsBlend"] for x in raw]
    oneil_values = [x["oneilRsRaw"] for x in raw]
    for stock in raw:
        stock["rsPercentile"] = round(core.percentile(rs_values, stock["rsBlend"]), 1)
        stock["oneilRsPercentile"] = round(core.percentile(oneil_values, stock["oneilRsRaw"]), 1)
        stock["trendTemplate"] = bool(stock["stage2Core"] and stock["rsPercentile"] >= 70)
    energy = build_us_market_energy(raw)
    market = market_direction_us()

    print("4/9 SEC EDGAR 공식 재무·공시")
    sec_meta = enrich_sec(raw)
    print(" ", sec_meta.get("message"))

    print("5/9 미국 거래소지수 대비 섹터 흐름")
    benchmarks, sector_flow_meta = sector_flow_benchmarks_us(raw)
    sectors = core._build_sector_stats(raw, benchmarks)
    for sector in sectors:
        verified = sum(
            1 for stock in raw
            if stock.get("sector") == sector.get("name")
            and stock.get("sectorConfidence") in ("HIGH", "MEDIUM")
        )
        sector["classificationLabel"] = f"Nasdaq Industry 분류 {verified}/{sector.get('memberCount', 0)}"
    sector_by_name = {s["name"]: s for s in sectors}
    eligible_sectors = [s for s in sectors if s.get("name") != "ETF" and (s.get("flowEligibleCount") or 0) >= 3]
    sector_flow_meta.update({
        "eligibleSectorCount": len(eligible_sectors),
        "strongSectorCount": sum(s.get("flowStatus") in ("BOTH", "NEW_7D", "PERSISTENT_30D") for s in eligible_sectors),
        "bothStrongCount": sum(s.get("flowStatus") == "BOTH" for s in eligible_sectors),
        "new7dCount": sum(s.get("flowStatus") == "NEW_7D" for s in eligible_sectors),
        "persistent30dCount": sum(s.get("flowStatus") == "PERSISTENT_30D" for s in eligible_sectors),
        "expandingCount": sum(bool(s.get("expanding")) for s in eligible_sectors),
        "concentrationWarningCount": sum(bool(s.get("concentrationWarning")) for s in eligible_sectors),
        "minimumMemberCount": 3,
        "strongRule": "각 기간 5조건 중 4개 이상 + 중앙값 수익률·미국 지수 대비 모두 플러스",
    })

    print("6/9 점수·신호·CAN SLIM")
    today = datetime.now(KST).date().isoformat()
    for stock in raw:
        sector = sector_by_name[stock["sector"]]
        stock["sectorAction"] = core._stock_sector_action_overlay(stock, sector)
        add_can_slim_us(stock, sector, market)
        high_component = max(0, min(100, (stock["high52Ratio"] - 70) / 30 * 100))
        volume_component = min(100, stock["volumeRatio"] / 2 * 100)
        stock["score"] = round(0.30 * stock["rsPercentile"] + 25 * stock["conditionCount"] / 6 + 0.15 * high_component + 0.10 * volume_component + 20 * bool(stock["trendTemplate"]), 1)
        if stock.get("ma60") and stock["close"] < stock["ma60"]:
            signal, reason = "EXIT", "60일선 아래 — 추세 훼손"
        elif stock["trendTemplate"] and stock["conditionCount"] >= 5 and stock["rsPercentile"] >= 70:
            signal, reason = "BUY", "미너비니 Stage 2 + 이세무사 성장주 5/6↑ + RS 70↑"
        elif stock["conditionCount"] >= 4 and stock["rsPercentile"] >= 60:
            signal, reason = "WATCH", "이세무사 성장주 4/6↑ — 추가 확인"
        else:
            signal, reason = "NEUTRAL", "주도 조건 부족"
        old = old_by_ticker.get(stock["ticker"], {}) if old_live else {}
        if signal == "BUY" and old.get("signal") in ("BUY", "HOLD"):
            stock["signal"], stock["entered"] = "HOLD", old.get("entered") or today
        else:
            stock["signal"] = signal
            stock["entered"] = today if signal == "BUY" else old.get("entered")
        stock["signalReason"] = reason
        stock["actionAge"] = None
        if stock.get("entered"):
            try:
                stock["actionAge"] = (date.fromisoformat(today) - date.fromisoformat(stock["entered"])).days + 1
            except Exception:
                pass
        for key in ("close", "ma20", "ma50", "ma60", "ma120", "ma150", "ma200", "ma20m", "avgTradingValue50d", "market_cap_usd", "market_cap_krw"):
            if stock.get(key) is not None:
                stock[key] = round(float(stock[key]), 2)
        for key in ("chg1d", "ret7", "ret30", "ret252", "rs5", "rs20", "rs60", "rsBlend", "oneilRsRaw", "demandRatio", "volumeRatio", "volumeTrend7", "high52Ratio", "historicalHighRatio", "drawdown"):
            if stock.get(key) is not None:
                stock[key] = round(float(stock[key]), 2)
        for row in stock["history"]:
            for key in ("close", "high", "low"):
                row[key] = round(float(row[key]), 2)
            row["volume"] = int(row["volume"])
    raw.sort(key=lambda x: x["score"], reverse=True)

    print("7/9 섹터 대장주 연결")
    for sector in sectors:
        members = [x for x in raw if x.get("sector") == sector.get("name")]
        leaders = sorted(members, key=lambda x: (bool(x.get("trendTemplate")), x.get("conditionCount", 0), x.get("score", 0), x.get("rsPercentile", 0)), reverse=True)
        majors = sorted(members, key=lambda x: x.get("market_cap_usd", 0), reverse=True)
        sector["leaderStock"] = ({"name": leaders[0].get("name"), "ticker": leaders[0].get("ticker"), "score": leaders[0].get("score"), "rs": leaders[0].get("rsPercentile"), "stage2": bool(leaders[0].get("trendTemplate")), "conditions": leaders[0].get("conditionCount", 0)} if leaders else None)
        sector["leaderCandidates"] = [{"name": x.get("name"), "ticker": x.get("ticker"), "score": x.get("score"), "rs": x.get("rsPercentile"), "stage2": bool(x.get("trendTemplate")), "conditions": x.get("conditionCount", 0)} for x in leaders[:4]]
        sector["leaders"] = [x.get("name") for x in leaders[:4]]
        sector["majorCompanies"] = list(dict.fromkeys([x.get("name") for x in (leaders[:1] + majors) if x.get("name")]))[:6]

    asof = max(x["date"] for x in raw)
    asof_date = date.fromisoformat(asof)
    for stock in raw:
        stock["staleDays"] = max(0, (asof_date - date.fromisoformat(stock["date"])).days)
        stock["isStale"] = stock["staleDays"] > 4
        if stock["isStale"]:
            stock["dataStatus"] = "STALE"
    live_count = sum(x.get("dataStatus") == "LIVE" and not x.get("isStale") for x in raw)
    cached_count = sum(x.get("dataStatus") == "CACHED" and not x.get("isStale") for x in raw)
    stale_count = sum(bool(x.get("isStale")) for x in raw)
    coverage = round(fetched / len(candidates) * 100, 1) if candidates else 0
    funnel = {
        "listed": len(listed), "companyUniverse": len(listed), "excludedInstruments": excluded_by_name,
        "marketCapPass": len(listed), "liquidityPass": len(raw), "deepScanned": len(raw),
        "growth4plus": sum(x["conditionCount"] >= 4 for x in raw),
        "stage2": sum(bool(x["trendTemplate"]) for x in raw),
        "highZone": sum(max(x.get("high52Ratio", 0), x.get("historicalHighRatio", 0)) >= 93 for x in raw),
        "tripleAxis": sum(x["conditionCount"] >= 4 and x["trendTemplate"] and max(x.get("high52Ratio", 0), x.get("historicalHighRatio", 0)) >= 93 for x in raw),
        "sectorAction": sum((x.get("sectorAction") or {}).get("status") == "CONFIRMED" for x in raw),
        "buy": sum(x["signal"] in ("BUY", "HOLD") for x in raw),
        "liquidityThresholdUSD": MIN_AVG_VALUE_50D_USD,
        "marketCapThresholdUSD": MIN_MARKET_CAP_USD,
        "priceScanLimit": MAX_PRICE_UNIVERSE,
        "marketCapEnforced": True,
    }
    payload = {
        "meta": {
            "market": "US", "title": "WAMO MARKET RADAR · US", "mode": "LIVE",
            "asOf": asof, "updatedAt": datetime.now(KST).isoformat(timespec="minutes"),
            "source": "Nasdaq stock screener universe + Yahoo Finance price + SEC EDGAR official submissions/XBRL",
            "universeCount": len(listed), "eligibleUniverseCount": len(listed), "successCount": len(raw), "errorCount": len(errors),
            "marketDirection": {"US": market}, "marketEnergy": energy,
            "dataHealth": {
                "liveCount": live_count, "cachedCount": cached_count, "staleCount": stale_count,
                "failedCount": len(errors), "priceAttemptedCount": len(candidates), "priceFetchedCount": fetched,
                "priceCoveragePct": coverage, "liquidityRejectedCount": liquidity_rejected,
                "excludedInstrumentCount": excluded_by_name, "shortHistoryCount": sum((x.get("historyTradingDays") or 0) < 260 for x in raw),
                "shortHistoryExcludedCount": len(short_history_exclusions),
                "sourceCounts": {"Yahoo Finance": sum(x.get("dataSource") == "Yahoo Finance" for x in raw), "이전 미국 정상값": sum(x.get("dataSource") == "이전 미국 정상값" for x in raw)},
                "dartConnected": bool(sec_meta.get("connected")), "secConnected": bool(sec_meta.get("connected")),
                "flowConnected": False, "consensusConnected": False,
                "message": f"시총 10억달러 이상 일반기업 {len(listed):,}개 중 상위 {len(candidates):,}개 가격수집 {fetched:,}개({coverage:.1f}%) → 50일 평균 거래대금 1천만달러 통과 {len(raw):,}개 · 신규상장 60일 미만 {len(short_history_exclusions):,}개 · 제공처 오류 {len(errors):,}개",
            },
            "universeFunnel": funnel,
            "dartMeta": sec_meta, "secMeta": sec_meta,
            "profileMeta": {"status": sec_meta.get("status"), "targetCount": len(raw), "coveredCount": sec_meta.get("successCount", 0), "fetchedCount": sec_meta.get("fetchedCount", 0), "source": "SEC EDGAR 10-K·XBRL", "message": sec_meta.get("message"), "errors": sec_meta.get("errors", [])},
            "marketContextMeta": {"status": "NOT_USED", "source": "사용 안 함", "message": "무료 기관보유 시계열 자동연결 안 함"},
            "flowMeta": {"connected": False, "coverage": 0, "source": "사용 안 함", "message": "기관 수급 미사용"},
            "sectorFlowMeta": sector_flow_meta,
            "consensusMeta": {"status": "NOT_USED", "source": "사용 안 함", "message": "무료 컨센서스 자동연결 안 함"},
            "catalystMeta": {"status": sec_meta.get("status"), "source": "SEC EDGAR official"},
            "note": "미국 후보 스크리닝 대시보드입니다. SEC 10-K·10-Q/XBRL 공식자료와 가격으로 확인 가능한 항목만 계산합니다. 미국 시장에너지는 현재 정밀계산 유니버스의 20일 볼린저 상단 돌파 종목수를 350종목 기준으로 환산한 참고값이며 공식 S&P 500·Nasdaq 전체 구성 시계열이 아닙니다. 무료로 신뢰성 있게 연결하지 못한 컨센서스와 기관 보유 시계열은 확인 불가로 표시합니다.",
        },
        "sectors": sectors, "stocks": raw, "errors": errors,
        "shortHistoryExclusions": short_history_exclusions,
    }
    payload["meta"]["qa"] = validate_us_payload(payload)
    _assert_json_payload(payload)

    print("8/9 us.html 데이터 교체")
    new_html = core.replace_payload(old_html, payload)
    new_html = core.patch_index_health_ui(new_html)
    temp = US_HTML.with_suffix(".html.tmp")
    temp.write_text(new_html, encoding="utf-8")
    temp.replace(US_HTML)
    print("9/9 완료:", asof, "미국 종목", len(raw), "가격오류", len(errors), "SEC오류", sec_meta.get("errorCount"))


if __name__ == "__main__":
    main()
