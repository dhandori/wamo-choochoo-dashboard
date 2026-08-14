#!/usr/bin/env python3
# WAMO Market Radar - GitHub cloud updater
# Price/volume screening works without any API key.
# Fundamentals / official disclosures will be connected in the next step.

from __future__ import annotations

import io
import json
import math
import re
import statistics
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.html"
KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (WAMO-Market-Radar/9.0)"
MIN_MARKET_CAP = 1_000_000_000_000       # 1조원
MIN_AVG_VALUE_50D = 10_000_000_000       # 100억원
MAX_WORKERS = 14

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

def cs_item(status, value, criterion, why):
    return {"status": status, "value": value, "criterion": criterion, "why": why}

def add_can_slim(x, sector, mkt):
    items = {}
    items["C"] = cs_item("UNKNOWN", "DART 실적 연결 대기", "최근 분기 EPS YoY ≥ +25%", "다음 단계에서 OpenDART 공식 실적을 연결합니다.")
    items["A"] = cs_item("UNKNOWN", "DART 연간실적 연결 대기", "연간 EPS 성장 + 3년 성장성", "공식 실적 연결 전에는 판정을 만들지 않습니다.")
    near = x["high52Ratio"] >= 95
    items["N"] = cs_item("PASS" if near else "FAIL", f"52주 고점比 {x['high52Ratio']:.1f}%", "새로운 고점 또는 확인된 신규 촉매", "현재 자동버전에서는 새로운 가격 고점을 우선 사용합니다.")
    dr = x["demandRatio"]
    items["S"] = cs_item("PASS" if dr >= 1.15 else "FAIL", f"상승/하락일 거래량比 {dr:.2f}x", "거래량 수요 우위 ≥1.15x", "발행주식수 희석은 DART 연결 후 추가됩니다.")
    ors = x["oneilRsPercentile"]
    ss = sector["score"]
    items["L"] = cs_item("PASS" if ors >= 80 and ss >= 58 else "FAIL", f"오닐식 RS {ors:.0f} · 섹터 {ss:.0f}", "RS ≥80 + 강한 섹터", "시장 내 주도주인지 봅니다.")
    items["I"] = cs_item("UNKNOWN", "KRX 수급 연결 대기", "기관/외국인 누적 수급 개선", "공식 수급 연결 전에는 UNKNOWN으로 둡니다.")
    if mkt.get("pass") is None:
        items["M"] = cs_item("UNKNOWN", "시장 데이터 확인 불가", "시장 상승추세", mkt.get("note", ""))
    else:
        items["M"] = cs_item("PASS" if mkt["pass"] else "FAIL", "시장 상승" if mkt["pass"] else "시장 비우호", "KOSPI >50일선 >200일선 + 200일선 상승", mkt.get("note", ""))

    sts = [items[k]["status"] for k in "CANSLIM"]
    x["canSlim"] = {
        "items": items,
        "passCount": sts.count("PASS"),
        "measuredCount": sum(s != "UNKNOWN" for s in sts),
        "unknownCount": sts.count("UNKNOWN"),
        "fullMatch": sts.count("PASS") == 7,
        "strongCandidate": False,
        "preliminary": items["L"]["status"] == "PASS" and sts.count("UNKNOWN") >= 1,
    }

def main():
    if not INDEX.exists():
        raise SystemExit("index.html을 찾지 못했습니다.")

    old_html = INDEX.read_text(encoding="utf-8")
    old_payload = extract_old_payload(old_html)
    old_mode = (old_payload.get("meta") or {}).get("mode")
    old_by_ticker = {x.get("ticker"): x for x in old_payload.get("stocks", []) if x.get("ticker")}

    print("1/5 코스피·코스닥 시가총액 목록 수집")
    listed = fetch_market_summary(0, ".KS", "KOSPI") + fetch_market_summary(1, ".KQ", "KOSDAQ")
    sector_map = kind_sector_map()
    for r in listed:
        r["sector"] = sector_map.get(r["stock_code"], "KRX 업종 미분류")

    cap_pass = [r for r in listed if r["market_cap_krw"] >= MIN_MARKET_CAP]
    if len(cap_pass) < 80:
        raise RuntimeError(f"시가총액 1조원 통과 종목이 비정상적으로 적습니다: {len(cap_pass)}")

    print("2/5 시총 1조 이상 종목 가격 이력 수집:", len(cap_pass))
    raw = []
    errors = []
    def task(meta):
        rows = fetch_naver_history(meta["stock_code"], 900)
        x = calc_raw(meta, rows)
        return x

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
        raise RuntimeError(f"정상 계산 종목이 너무 적어 기존 사이트를 덮어쓰지 않습니다: {len(raw)}")

    print("3/5 RS / Stage 2 / 세부섹터 계산")
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

    print("4/5 점수 / 신호 / CAN SLIM 가격항목 계산")
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

    payload = {
        "meta": {
            "title": "WAMO MARKET RADAR · AUTO",
            "mode": "LIVE",
            "asOf": asof,
            "updatedAt": datetime.now(KST).isoformat(timespec="minutes"),
            "source": "NAVER Finance price/market-cap + KRX KIND industry map (best effort)",
            "universeCount": len(listed),
            "successCount": len(raw),
            "errorCount": len(errors),
            "marketDirection": {"KOREA": mkt},
            "dataHealth": {
                "liveCount": len(raw),
                "cachedCount": 0,
                "staleCount": 0,
                "failedCount": len(errors),
                "dartConnected": False,
                "message": f"전체 {len(listed):,}개 → 시총 1조 이상 {len(cap_pass):,}개 → 거래대금 100억원 이상 정밀계산 {len(raw):,}개",
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
            "consensusMeta": {"status": "NOT_CONNECTED"},
            "catalystMeta": {"status": "NOT_CONNECTED"},
            "note": "가격·거래량·시가총액 자동갱신 버전입니다. DART 실적·공시와 공식 투자자 수급은 다음 연결 단계에서 추가합니다.",
        },
        "sectors": sectors,
        "stocks": raw,
        "errors": errors[:100],
    }

    print("5/5 index.html 갱신")
    new_html = replace_payload(old_html, payload)
    tmp = INDEX.with_suffix(".html.tmp")
    tmp.write_text(new_html, encoding="utf-8")
    tmp.replace(INDEX)
    print("완료:", asof, "종목", len(raw), "오류", len(errors))

if __name__ == "__main__":
    main()
