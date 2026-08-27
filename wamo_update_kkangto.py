#!/usr/bin/env python3
"""WAMO 깡토 추세추종 모듈.

기존 한국/미국 대시보드가 생성한 ``window.WAMO_DATA``를 읽어 별도
``kkangto.html``을 만든다. 기존 성장주·미너비니·신고가·섹터액션의
판정 로직은 수정하지 않는다.

중요: 이 파일의 점수와 임계값은 백테스트 전 Wamo 실험값 v0.1이다.
깡토가 직접 제시한 확정 공식으로 표시하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


KST = timezone(timedelta(hours=9))
ROOT = Path(os.environ.get("WAMO_ROOT", Path(__file__).resolve().parent))
KR_HTML = ROOT / "index.html"
US_HTML = ROOT / "us.html"
MOVERS_HTML = ROOT / "movers.html"
OUT_HTML = ROOT / "kkangto.html"
STATE_JSON = ROOT / "wamo_kkangto_state.json"

# Wamo 실험 기준 v0.1. 정식 백테스트 전이며 화면에도 같은 사실을 표시한다.
CFG = {
    "version": "Wamo 실험 기준 v0.1",
    "rs_watch": 70.0,
    "rs_strong": 80.0,
    "rs_leader": 90.0,
    "max_risk_pct": 8.0,
    "breakout_volume_ratio": 1.5,
    "near_pivot_pct": 3.0,
    "extended_from_pivot_pct": 5.0,
    "atr_stop_multiple": 1.5,
    "base_lookback": 60,
    "candidate_limit": 40,
}


def finite(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def sma(values: list[float], period: int) -> float | None:
    return mean(values[-period:]) if len(values) >= period else None


def extract_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"meta": {"mode": "MISSING", "asOf": None}, "stocks": [], "sectors": []}
    html = path.read_text(encoding="utf-8")
    marker = "window.WAMO_DATA = "
    if marker not in html:
        return {"meta": {"mode": "INVALID", "asOf": None}, "stocks": [], "sectors": []}
    start = html.index(marker) + len(marker)
    payload, _ = json.JSONDecoder().raw_decode(html[start:])
    return payload if isinstance(payload, dict) else {"meta": {}, "stocks": [], "sectors": []}


def normalized_history(stock: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in stock.get("history") or []:
        close = finite(row.get("close"))
        high = finite(row.get("high"), close)
        low = finite(row.get("low"), close)
        volume = finite(row.get("volume"), 0.0)
        date = str(row.get("date") or "")
        if not date or close is None or high is None or low is None:
            continue
        rows.append({"date": date, "close": close, "high": high, "low": low, "volume": volume or 0.0})
    dedup = {row["date"]: row for row in rows}
    return [dedup[key] for key in sorted(dedup)]


def is_operating_company(stock: dict[str, Any]) -> bool:
    """구버전 payload에도 대응해 ETF·ETN·스팩을 기업 후보에서 제외한다."""
    instrument = str(stock.get("instrumentType") or "").upper()
    if instrument in {"ETF_ETN", "SPAC", "ETF", "ETN", "FUND"}:
        return False
    name = str(stock.get("name") or "").upper().strip()
    fund_prefixes = (
        "KODEX ", "TIGER ", "RISE ", "ACE ", "SOL ", "HANARO ", "KOSEF ",
        "KBSTAR ", "ARIRANG ", "TIMEFOLIO ", "PLUS ", "WOORI ", "1Q ",
    )
    if name.startswith(fund_prefixes) or " ETN" in name or name.endswith("스팩") or "SPAC" in name:
        return False
    return True


def true_range_at(rows: list[dict[str, Any]], index: int) -> float:
    high, low = rows[index]["high"], rows[index]["low"]
    if index == 0:
        return high - low
    prev = rows[index - 1]["close"]
    return max(high - low, abs(high - prev), abs(low - prev))


def atr(rows: list[dict[str, Any]], period: int = 14) -> float | None:
    if len(rows) < period + 1:
        return None
    return mean([true_range_at(rows, i) for i in range(len(rows) - period, len(rows))])


def local_contractions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recent = rows[-min(150, len(rows)):]
    if len(recent) < 80:
        return []
    radius, events = 3, []
    for i in range(radius, len(recent) - radius):
        highs = [x["high"] for x in recent[i - radius:i + radius + 1]]
        lows = [x["low"] for x in recent[i - radius:i + radius + 1]]
        kind = "P" if recent[i]["high"] >= max(highs) else "T" if recent[i]["low"] <= min(lows) else None
        if not kind:
            continue
        if events and events[-1][0] == kind:
            old_i = events[-1][1]
            better = recent[i]["high"] > recent[old_i]["high"] if kind == "P" else recent[i]["low"] < recent[old_i]["low"]
            if better:
                events[-1] = (kind, i)
        elif not events or i - events[-1][1] >= 3:
            events.append((kind, i))
    result = []
    for (kind, peak_i), (next_kind, trough_i) in zip(events, events[1:]):
        if kind != "P" or next_kind != "T" or trough_i <= peak_i:
            continue
        peak, trough = recent[peak_i]["high"], recent[trough_i]["low"]
        depth = (peak - trough) / peak * 100 if peak else 0
        if 1 <= depth <= 45:
            result.append({
                "peakDate": recent[peak_i]["date"], "troughDate": recent[trough_i]["date"],
                "peak": round(peak, 2), "trough": round(trough, 2), "depthPct": round(depth, 1),
            })
    return result[-4:]


def vcp_like(rows: list[dict[str, Any]], stock_vcp: dict[str, Any] | None = None) -> dict[str, Any]:
    if stock_vcp and stock_vcp.get("status") in {"BREAKOUT", "READY", "WATCH", "NONE", "INSUFFICIENT"}:
        result = dict(stock_vcp)
        result["source"] = "기존 Wamo VCP 정량검사"
        return result
    if len(rows) < 80:
        return {"status": "INSUFFICIENT", "label": "이력 부족", "candidate": False, "contractions": [], "source": "깡토 모듈"}
    contractions = local_contractions(rows)
    depths = [finite(x.get("depthPct"), 0.0) or 0.0 for x in contractions]
    shrinking = len(depths) >= 2 and all(depths[i] <= depths[i - 1] * 0.88 for i in range(1, len(depths))) and depths[-1] <= 15
    volumes = [x["volume"] for x in rows]
    avg50 = mean(volumes[-50:]) or 0
    avg10 = mean(volumes[-10:]) or 0
    dry_ratio = avg10 / avg50 if avg50 else None
    volume_dry = dry_ratio is not None and dry_ratio <= 0.80
    range10 = (max(x["high"] for x in rows[-10:]) / min(x["low"] for x in rows[-10:]) - 1) * 100
    tight = range10 <= 12
    pivot = contractions[-1]["peak"] if contractions else max(x["high"] for x in rows[-20:-1])
    close = rows[-1]["close"]
    vol_ratio = volumes[-1] / avg50 if avg50 else None
    breakout = close > pivot and vol_ratio is not None and vol_ratio >= CFG["breakout_volume_ratio"]
    candidate = shrinking and volume_dry and tight
    distance = (pivot / close - 1) * 100 if close else None
    if candidate and breakout:
        status, label = "BREAKOUT", "VCP 유사 돌파"
    elif candidate and distance is not None and -1.5 <= distance <= 5:
        status, label = "READY", "VCP 유사 피봇 근접"
    elif shrinking and (volume_dry or tight):
        status, label = "WATCH", "수축 관찰"
    else:
        status, label = "NONE", "유사도 낮음"
    return {
        "status": status, "label": label, "candidate": candidate, "contractions": contractions,
        "shrinking": shrinking, "volumeDryUp": volume_dry,
        "volumeDryRatio": round(dry_ratio, 2) if dry_ratio is not None else None,
        "tightRange": tight, "range10Pct": round(range10, 1), "pivot": round(pivot, 2),
        "distanceToPivotPct": round(distance, 1) if distance is not None else None,
        "breakout": breakout, "breakoutVolumeRatio": round(vol_ratio, 2) if vol_ratio is not None else None,
        "source": "깡토 모듈 VCP 유사도",
    }


def recent_breakout(rows: list[dict[str, Any]], lookback: int = 20) -> dict[str, Any] | None:
    if len(rows) < 80:
        return None
    start = max(60, len(rows) - lookback)
    found = None
    for i in range(start, len(rows)):
        prior = rows[max(0, i - 60):i]
        if len(prior) < 40:
            continue
        pivot = max(x["high"] for x in prior)
        avg_vol = mean([x["volume"] for x in prior[-20:]]) or 0
        ratio = rows[i]["volume"] / avg_vol if avg_vol else 0
        if rows[i]["close"] > pivot and ratio >= CFG["breakout_volume_ratio"]:
            found = {"date": rows[i]["date"], "pivot": pivot, "volumeRatio": ratio, "daysAgo": len(rows) - 1 - i}
    return found


def market_regime(payload: dict[str, Any], market: str) -> dict[str, Any]:
    stocks = payload.get("stocks") or []
    eligible = []
    for stock in stocks:
        close, ma50, ma200 = finite(stock.get("close")), finite(stock.get("ma50")), finite(stock.get("ma200"))
        if close is not None and ma50 is not None and ma200 is not None:
            eligible.append(close > ma50 > ma200)
    breadth = 100 * sum(eligible) / len(eligible) if eligible else None
    meta = payload.get("meta") or {}
    if market == "KR":
        direction = (meta.get("marketDirection") or {}).get("KOREA") or {}
    else:
        direction = meta.get("marketDirection") or meta.get("market") or {}
        if isinstance(direction, dict) and "US" in direction:
            direction = direction["US"]
    if not isinstance(direction, dict):
        direction = {}
    index_pass = direction.get("pass")
    if index_pass is True and breadth is not None and breadth >= 50:
        status = "GREEN"
    elif index_pass is True or (breadth is not None and breadth >= 35):
        status = "YELLOW"
    else:
        status = "RED"
    return {
        "status": status, "indexPass": index_pass, "breadthPct": round(breadth, 1) if breadth is not None else None,
        "eligibleCount": len(eligible),
        "criterion": "GREEN=대표지수 추세 통과+정밀계산 종목 중 종가>50일>200일 비율 50% 이상; YELLOW=둘 중 하나; 그 외 RED",
        "label": "Wamo 실험 시장상태 v0.1",
    }


def sector_context(stock: dict[str, Any], sector: dict[str, Any] | None) -> dict[str, Any]:
    sector = sector or {}
    score = finite(sector.get("score"), 0.0) or 0.0
    flow = sector.get("flowStatus")
    action = str(sector.get("action") or "미확인")
    confirmed = (stock.get("sectorAction") or {}).get("status") == "CONFIRMED"
    if confirmed or flow in {"BOTH", "NEW_7D", "PERSISTENT_30D"} or score >= 60:
        strength = "강세"
    elif score >= 45 or action in {"상승", "중립"}:
        strength = "중립"
    else:
        strength = "약세"
    return {
        "name": stock.get("sector") or "미분류", "strength": strength, "score": round(score, 1),
        "action": action, "flowStatus": flow or "NONE", "confirmed": bool(confirmed),
        "leader": ((sector.get("leaderStock") or {}).get("name") or ((sector.get("leaders") or [None])[0])),
        "memberCount": sector.get("memberCount"),
    }


def score_components(*, market: str, sector: str, rs: float, trend: bool, high_ratio: float,
                     base: bool, vcp: dict[str, Any], risk_pct: float | None, room: str) -> dict[str, float]:
    return {
        "시장": 10 if market == "GREEN" else 5 if market == "YELLOW" else 0,
        "섹터": 15 if sector == "강세" else 8 if sector == "중립" else 0,
        "RS": 20 if rs >= 90 else 16 if rs >= 80 else 12 if rs >= 70 else 6 if rs >= 60 else 0,
        "추세": 15 if trend else 0,
        "신고가": 10 if high_ratio >= 100 else 8 if high_ratio >= 97 else 6 if high_ratio >= 93 else 2 if high_ratio >= 85 else 0,
        "베이스": 10 if base else 0,
        "VCP유사": 10 if vcp.get("candidate") else 6 if vcp.get("status") == "WATCH" else 0,
        "Risk": 5 if risk_pct is not None and risk_pct <= 6 else 3 if risk_pct is not None and risk_pct <= 8 else 0,
        "3R공간": 5 if room == "높음" else 3 if room == "보통" else 0,
    }


def analyze_stock(stock: dict[str, Any], sector: dict[str, Any] | None,
                  regime: dict[str, Any], market: str) -> dict[str, Any] | None:
    rows = normalized_history(stock)
    if len(rows) < 60:
        return None
    closes = [x["close"] for x in rows]
    close = closes[-1]
    ma20 = finite(stock.get("ma20"), sma(closes, 20))
    ma50 = finite(stock.get("ma50"), sma(closes, 50))
    ma150 = finite(stock.get("ma150"), sma(closes, 150))
    ma200 = finite(stock.get("ma200"), sma(closes, 200))
    trend = bool(stock.get("trendTemplate")) or bool(
        ma50 and ma150 and close > ma50 > ma150 and (ma200 is None or ma150 > ma200)
    )
    weekly = "상승" if trend and ma50 and close > ma50 else "부분확인" if ma50 and close > ma50 else "약화"
    rs = finite(stock.get("oneilRsPercentile"), finite(stock.get("rsPercentile"), 0.0)) or 0.0
    high52 = max(x["high"] for x in rows[-min(252, len(rows)):])
    high_ratio = close / high52 * 100 if high52 else 0.0
    vcp = vcp_like(rows, stock.get("vcp"))
    pivot = finite(vcp.get("pivot")) or max(x["high"] for x in rows[-60:-1])
    recent = rows[-CFG["base_lookback"]:]
    base_depth = (max(x["high"] for x in recent) / min(x["low"] for x in recent) - 1) * 100
    range10 = (max(x["high"] for x in rows[-10:]) / min(x["low"] for x in rows[-10:]) - 1) * 100
    avg50v = mean([x["volume"] for x in rows[-50:]]) or 0
    avg10v = mean([x["volume"] for x in rows[-10:]]) or 0
    dry_ratio = avg10v / avg50v if avg50v else None
    base = bool(close > (ma50 or 0) and base_depth <= 30 and range10 <= 12 and (dry_ratio is None or dry_ratio <= 1.0))
    breakout = recent_breakout(rows)
    volume_ratio = rows[-1]["volume"] / avg50v if avg50v else None
    atr14 = atr(rows)
    contraction_trough = finite((vcp.get("contractions") or [{}])[-1].get("trough")) if vcp.get("contractions") else None
    supports = [x for x in (contraction_trough, ma20, min(x["low"] for x in rows[-10:])) if x and x < max(close, pivot)]
    structural_support = max(supports) if supports else min(x["low"] for x in rows[-20:])
    entry = close if breakout and close >= pivot else pivot
    noise_stop = entry - CFG["atr_stop_multiple"] * atr14 if atr14 else structural_support * 0.995
    technical_stop = min(structural_support * 0.995, noise_stop)
    risk_amount = entry - technical_stop
    risk_pct = risk_amount / entry * 100 if entry > 0 and risk_amount > 0 else None
    r3_price = entry + 3 * risk_amount if risk_amount > 0 else None

    # 확보된 이력에서만 위쪽 국지 저항을 계산한다. 전체 상장 이력이라고 부르지 않는다.
    resistance_peaks = []
    if r3_price:
        for i in range(3, len(rows) - 3):
            h = rows[i]["high"]
            if h <= entry * 1.01 or h >= r3_price:
                continue
            if h >= max(x["high"] for x in rows[i - 3:i + 4]):
                if not resistance_peaks or abs(h / resistance_peaks[-1] - 1) >= 0.03:
                    resistance_peaks.append(h)
    resistance_count = len(resistance_peaks)
    resistance = "낮음" if resistance_count == 0 else "보통" if resistance_count == 1 else "높음"
    sector_info = sector_context(stock, sector)
    if risk_pct is not None and risk_pct <= CFG["max_risk_pct"] and resistance == "낮음" and regime["status"] != "RED" and sector_info["strength"] == "강세" and rs >= CFG["rs_watch"]:
        room = "높음"
    elif risk_pct is not None and risk_pct <= CFG["max_risk_pct"] and resistance_count <= 1:
        room = "보통"
    else:
        room = "낮음"

    pivot_distance = (pivot / close - 1) * 100 if close else None
    current_breakout = bool(close > pivot and volume_ratio is not None and volume_ratio >= CFG["breakout_volume_ratio"])
    if ma50 and close < ma50 and ma200 and close < ma200:
        state = "매도신호"
    elif ma50 and close < ma50:
        state = "추세훼손"
    elif breakout and breakout["daysAgo"] > 0 and close >= breakout["pivot"] and (ma20 is None or close >= ma20):
        state = "보유"
    elif current_breakout and risk_pct is not None and risk_pct <= CFG["max_risk_pct"] and room != "낮음":
        state = "매수신호"
    elif trend and base and pivot_distance is not None and -1.0 <= pivot_distance <= CFG["near_pivot_pct"]:
        state = "돌파대기"
    elif trend and base:
        state = "베이스 형성"
    else:
        state = "관찰"

    if risk_pct is None:
        action = "기술적 손절 계산 불가 — 진입 보류"
    elif risk_pct > CFG["max_risk_pct"]:
        action = f"필요 Risk {risk_pct:.1f}%로 최대 허용 후보 8% 초과 — 더 나은 진입가격 대기"
    elif state == "돌파대기":
        action = f"Pivot 돌파와 거래량 {CFG['breakout_volume_ratio']:.1f}배 확인 전 추격매수 금지"
    elif state == "매수신호":
        action = "돌파·거래량 확인. 실제 주문 전 시장·저항·손절가격 재확인"
    elif state == "보유":
        action = "승자 보유 구간. 20일·50일선과 기술적 손절을 따라 추세 관리"
    elif state in {"추세훼손", "매도신호"}:
        action = "신규진입 금지. 기존 보유자는 사전에 정한 추세이탈 규칙 점검"
    else:
        action = "시장·섹터·RS·베이스가 더 정렬될 때까지 관찰"

    components = score_components(
        market=regime["status"], sector=sector_info["strength"], rs=rs, trend=trend,
        high_ratio=high_ratio, base=base, vcp=vcp, risk_pct=risk_pct, room=room,
    )
    score = round(sum(components.values()), 1)
    grade = "A" if score >= 85 else "B" if score >= 75 else "C" if score >= 65 else "D"
    profile = stock.get("businessProfile") or {}
    actuals = {
        "salesYoY": finite(stock.get("sales_yoy")), "epsYoY": finite(stock.get("eps_yoy")),
        "roe": finite(stock.get("roe")), "dartStatus": stock.get("dartStatus") or stock.get("secStatus") or "미확인",
    }
    catalysts = []
    for event in (stock.get("catalysts") or [])[:6]:
        url = str(event.get("url") or "")
        catalysts.append({
            "date": event.get("date"), "report": event.get("report"),
            "category": event.get("category"), "polarity": event.get("polarity"),
            "url": url if url.startswith(("https://", "http://")) else None,
        })
    return {
        "market": market, "ticker": stock.get("ticker"), "name": stock.get("name"), "date": stock.get("date") or rows[-1]["date"],
        "close": round(close, 2), "currency": "원" if market == "KR" else "달러", "sector": sector_info,
        "marketRegime": regime, "rs": round(rs, 1), "rsDefinition": "현재 정밀계산 모집단 내 오닐식 가중수익률 상대순위",
        "high52Ratio": round(high_ratio, 1), "distance52HighPct": round(high_ratio - 100, 1),
        "trend": trend, "stage": 2 if trend else None, "weeklyTrend": weekly,
        "base": {"detected": base, "depth60Pct": round(base_depth, 1), "range10Pct": round(range10, 1), "volumeDryRatio": round(dry_ratio, 2) if dry_ratio is not None else None},
        "vcp": vcp, "pivot": round(pivot, 2), "pivotDistancePct": round(pivot_distance, 1) if pivot_distance is not None else None,
        "technicalStop": round(technical_stop, 2), "riskPct": round(risk_pct, 1) if risk_pct is not None else None,
        "oneR": round(risk_amount, 2), "threeRPrice": round(r3_price, 2) if r3_price else None,
        "threeRRoom": room, "resistance": resistance, "resistanceCount": resistance_count,
        "nearestResistance": round(min(resistance_peaks), 2) if resistance_peaks else None,
        "atr14": round(atr14, 2) if atr14 is not None else None, "volumeRatio": round(volume_ratio, 2) if volume_ratio is not None else None,
        "recentBreakout": breakout, "state": state, "action": action,
        "score": score, "grade": grade, "scoreComponents": components, "scoreVersion": CFG["version"],
        "profile": {"summary": profile.get("summary") or stock.get("businessModelEasy") or "기업 설명 미연결", "products": profile.get("products"), "drivers": profile.get("drivers")},
        "actuals": actuals, "catalysts": catalysts, "history": rows[-180:],
        "coverage": {"historyDays": len(rows), "resistanceScope": f"확보된 {len(rows)}거래일 이력", "forwardConsensusUsed": False},
    }


def load_state() -> dict[str, Any]:
    try:
        state = json.loads(STATE_JSON.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def update_state(all_rows: dict[str, list[dict[str, Any]]], checked_at: str) -> dict[str, Any]:
    state = load_state()
    state.setdefault("version", 1)
    markets = state.setdefault("markets", {})
    for market, candidates in all_rows.items():
        market_state = markets.setdefault(market, {})
        for item in candidates:
            ticker = str(item.get("ticker") or item.get("name"))
            old = market_state.get(ticker) or {}
            changes = list(old.get("changes") or [])
            if not changes or old.get("state") != item["state"]:
                changes.append({"at": checked_at, "date": item.get("date"), "from": old.get("state"), "to": item["state"]})
            market_state[ticker] = {"name": item.get("name"), "state": item["state"], "since": changes[-1]["at"], "changes": changes[-20:]}
            item["stateHistory"] = market_state[ticker]["changes"]
            item["stateSince"] = market_state[ticker]["since"]
    state["updatedAt"] = checked_at
    STATE_JSON.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


def add_navigation(path: Path) -> None:
    if not path.exists():
        return
    html = path.read_text(encoding="utf-8")
    if 'href="kkangto.html"' in html:
        return
    link = '<a href="kkangto.html" title="시장→섹터→주도주→Risk/Reward를 추적하는 별도 모듈">📈 깡토 추세추종</a>'
    nav_close = html.find("</nav>")
    if nav_close >= 0:
        html = html[:nav_close] + link + html[nav_close:]
    else:
        floating = ('<a href="kkangto.html" style="position:fixed;right:14px;bottom:14px;z-index:9999;'
                    'padding:10px 13px;border-radius:999px;background:#17345c;color:#fff;border:1px solid #4f7cb8;'
                    'text-decoration:none;font:700 12px system-ui">📈 깡토 추세추종</a>')
        html = html.replace("<body>", "<body>" + floating, 1)
    path.write_text(html, encoding="utf-8")


def build_payload() -> dict[str, Any]:
    source_payloads = {"KR": extract_payload(KR_HTML), "US": extract_payload(US_HTML)}
    checked_at = datetime.now(KST).isoformat(timespec="minutes")
    analyzed: dict[str, list[dict[str, Any]]] = {}
    today: dict[str, list[dict[str, Any]]] = {}
    market_meta = {}
    for market, payload in source_payloads.items():
        regime = market_regime(payload, market)
        sector_map = {x.get("name"): x for x in payload.get("sectors") or []}
        rows = []
        for stock in payload.get("stocks") or []:
            if not is_operating_company(stock):
                continue
            item = analyze_stock(stock, sector_map.get(stock.get("sector")), regime, market)
            if item:
                rows.append(item)
        state_priority = {"매수신호": 6, "돌파대기": 5, "보유": 4, "베이스 형성": 3, "관찰": 2, "추세훼손": 1, "매도신호": 0}
        rows.sort(key=lambda x: (x["score"], state_priority.get(x["state"], 0), x["rs"]), reverse=True)
        qualified = [
            x for x in rows
            if x["score"] >= 60
            or (x["score"] >= 55 and x["state"] in {"매수신호", "돌파대기"})
        ]
        analyzed[market] = rows[:CFG["candidate_limit"]]
        today[market] = qualified[:10]
        meta = payload.get("meta") or {}
        market_meta[market] = {
            "asOf": meta.get("asOf"), "updatedAt": meta.get("updatedAt"), "mode": meta.get("mode"),
            "source": meta.get("source"), "stockCount": len(payload.get("stocks") or []),
            "candidateCount": len(analyzed[market]), "qualifiedCount": len(today[market]), "regime": regime,
        }
    update_state(analyzed, checked_at)
    return {
        "meta": {
            "title": "Wamo 투자 대시보드 — 깡토 추세추종", "checkedAt": checked_at,
            "scoreVersion": CFG["version"], "backtestStatus": "검증 대기",
            "backtestReason": "점시점 구성종목·상장폐지 종목·과거 섹터분류 데이터가 없어 생존편향 없는 정식 백테스트를 아직 통과하지 않았습니다.",
            "markets": market_meta,
        },
        "config": CFG, "candidates": analyzed, "today": today,
    }


def html_document(data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return r'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wamo 깡토 추세추종</title><style>
:root{--bg:#07101e;--panel:#0d192c;--line:#29405f;--text:#eef4ff;--muted:#93a7c4;--blue:#72b7ff;--good:#75e0b0;--warn:#f0cc72;--bad:#ff8f98;--violet:#ad91ff}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0,#173568 0,transparent 35%),linear-gradient(#081220,#050b14);color:var(--text);font-family:Inter,system-ui,-apple-system,"Noto Sans KR",sans-serif}.app{max-width:1480px;margin:auto;padding:20px}nav{display:flex;gap:8px;overflow:auto;margin-bottom:18px}nav a{white-space:nowrap;color:#b8c7db;text-decoration:none;border:1px solid var(--line);background:#0a1526;border-radius:10px;padding:9px 12px;font-size:12px}nav a.active{background:#24466f;color:white;border-color:#5a8bc7}.top{display:flex;justify-content:space-between;gap:18px;align-items:flex-start}.eyebrow{color:var(--blue);font-weight:900;font-size:12px;letter-spacing:.14em}.top h1{font-size:34px;margin:8px 0 5px}.sub,.meta{color:var(--muted);font-size:12px;line-height:1.65}.stamp{text-align:right}.badge{display:inline-flex;padding:6px 9px;border-radius:999px;border:1px solid var(--line);font-size:11px;font-weight:900}.GREEN,.매수신호{color:var(--good);border-color:#2f6b56;background:#102b22}.YELLOW,.돌파대기,[class*="베이스"]{color:var(--warn);border-color:#6d5d2c;background:#292313}.RED,.추세훼손,.매도신호{color:var(--bad);border-color:#714047;background:#301a20}.보유{color:var(--blue);border-color:#315c8a;background:#102640}.관찰{color:#b6c2d5}.notice{margin:16px 0;padding:13px 15px;border:1px solid #665824;background:#292313;border-radius:14px;color:#e8d48f;font-size:11px;line-height:1.7}.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}.toolbar button,.toolbar select,.toolbar input{border:1px solid var(--line);background:#0d192b;color:var(--text);border-radius:10px;padding:9px 11px}.toolbar button.active{background:#2b5485;border-color:#6a9bd4}.toolbar input{min-width:220px;flex:1}.summary{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:12px 0}.metric{border:1px solid var(--line);background:var(--panel);border-radius:14px;padding:13px}.metric span{color:var(--muted);font-size:10px}.metric b{display:block;font-size:24px;margin-top:5px}.metric small{color:#7f94b1;font-size:9px}.section{border:1px solid var(--line);background:rgba(10,20,36,.94);border-radius:17px;padding:15px;margin:14px 0}.section h2{font-size:18px;margin:0 0 5px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}#mobile{display:none}.card{border:1px solid var(--line);background:#0d192b;border-radius:14px;padding:13px;cursor:pointer;text-align:left;color:var(--text)}.card:hover{border-color:#5f8fc7;transform:translateY(-1px)}.cardhead{display:flex;justify-content:space-between;gap:8px}.card h3{margin:0;font-size:15px}.ticker{color:var(--muted);font-size:10px;margin-top:3px}.score{font-size:27px;font-weight:950;color:var(--blue)}.state{display:inline-flex;border:1px solid currentColor;border-radius:999px;padding:4px 7px;font-size:9px;font-weight:900}.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin-top:11px}.mini{background:#091425;border-radius:9px;padding:8px}.mini span{display:block;color:var(--muted);font-size:9px}.mini b{display:block;margin-top:3px;font-size:12px}.reason{color:#b8c5d7;font-size:10px;line-height:1.55;margin-top:9px}.flow{display:grid;grid-template-columns:repeat(9,1fr);gap:5px;margin-top:12px}.flow span{padding:7px 4px;border:1px solid #223a58;background:#091526;border-radius:8px;text-align:center;color:#9fb3cf;font-size:9px}.tablewrap{overflow:auto;border:1px solid var(--line);border-radius:12px}.table{width:100%;border-collapse:collapse;min-width:1100px}.table th,.table td{padding:10px;border-bottom:1px solid #213550;text-align:left;font-size:11px}.table th{color:#8fa4c2;background:#12213a;position:sticky;top:0}.table tbody tr{cursor:pointer}.table tbody tr:hover{background:#10223d}.method{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}.method details{border:1px solid var(--line);background:#0c182a;border-radius:12px;padding:11px}.method summary{cursor:pointer;font-weight:850;font-size:12px}.method p,.method li{color:#a4b5cb;font-size:10px;line-height:1.7}.drawer{position:fixed;inset:0 0 0 auto;width:min(790px,100vw);height:100vh;background:#081321;border-left:1px solid var(--line);z-index:20;transform:translateX(102%);transition:.2s;overflow:auto;box-shadow:-25px 0 55px #0008}.drawer.open{transform:translateX(0)}.dhead{position:sticky;top:0;z-index:2;background:#081321ef;backdrop-filter:blur(8px);display:flex;justify-content:space-between;padding:16px;border-bottom:1px solid var(--line)}.dhead h2{margin:0}.close{width:38px;height:38px;border:1px solid var(--line);background:#12213a;color:white;border-radius:10px}.dbody{padding:16px}.hero{border:1px solid #31547c;background:#10213a;border-radius:14px;padding:13px}.hero b{font-size:17px}.hero p{font-size:11px;color:#b5c5da;line-height:1.65}.detailgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:12px 0}.dcard{border:1px solid var(--line);background:#0c192b;border-radius:11px;padding:10px}.dcard span{display:block;color:var(--muted);font-size:9px}.dcard b{display:block;margin-top:4px;font-size:14px}.chart{height:340px;border:1px solid var(--line);border-radius:12px;background:#06101d;margin:12px 0;position:relative;overflow:hidden}.chart svg{width:100%;height:100%}.tip{display:none;position:absolute;pointer-events:none;background:#101d30;border:1px solid #45668f;border-radius:8px;padding:7px;font-size:9px;line-height:1.5}.components{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}.comp{border:1px solid var(--line);border-radius:9px;padding:8px;background:#0b1728;display:flex;justify-content:space-between;font-size:10px}.timeline{display:grid;gap:6px}.event{display:grid;grid-template-columns:125px 1fr;gap:8px;border-left:3px solid #416b9d;background:#0b1728;padding:8px;font-size:10px}.caveat{color:#91a6c2;font-size:10px;line-height:1.7}.empty{padding:30px;text-align:center;color:var(--muted)}
@media(max-width:1000px){.cards{grid-template-columns:1fr 1fr}.summary{grid-template-columns:repeat(3,1fr)}.method{grid-template-columns:1fr 1fr}.flow{grid-template-columns:repeat(3,1fr)}}@media(max-width:680px){.app{padding:12px}.top{display:block}.stamp{text-align:left;margin-top:10px}.top h1{font-size:26px}.cards{grid-template-columns:1fr}.summary{grid-template-columns:1fr 1fr}.method{grid-template-columns:1fr}.detailgrid{grid-template-columns:1fr 1fr}.components{grid-template-columns:1fr 1fr}.tablewrap{display:none}#mobile{display:grid}}
</style></head><body><div class="app"><nav><a href="./">🇰🇷 한국</a><a href="us.html">🇺🇸 미국</a><a href="movers.html">🚀 상승률 TOP 30</a><a class="active" href="kkangto.html">📈 깡토 추세추종</a></nav><div class="top"><div><div class="eyebrow">WAMO TREND FOLLOWING · KANGTO MODULE</div><h1>깡토 추세추종</h1><div class="sub">시장 → 섹터 → 주도주 → 상대강도 → 추세 → 베이스 → 돌파 → Risk/Reward → 보유 → 추세훼손</div></div><div class="stamp"><span class="badge" id="mode">검사 중</span><div class="meta" id="stamp"></div></div></div><div class="notice"><b>백테스트 전 실험 모듈입니다.</b> 점수·VCP 유사도·시장상태는 깡토의 확정 공식이 아니라 공개된 원칙을 Wamo가 정량화한 초기 운용값입니다. 3R 가격은 목표주가가 아니며, Forward Consensus는 사용하지 않습니다.</div><div class="toolbar"><button id="kr" onclick="setMarket('KR')">한국</button><button id="us" onclick="setMarket('US')">미국</button><select id="state" onchange="render()"><option value="ALL">전체 상태</option><option>매수신호</option><option>돌파대기</option><option>베이스 형성</option><option>보유</option><option>관찰</option><option>추세훼손</option><option>매도신호</option></select><input id="search" oninput="render()" placeholder="종목·티커·섹터 검색"></div><div class="summary" id="summary"></div><section class="section"><h2>오늘의 깡토 후보</h2><div class="sub">매수 추천이 아니라 현재 추세 단계와 진입 조건을 점검할 우선순위입니다.</div><div class="cards" id="top"></div></section><section class="section"><h2>후보 전체</h2><div class="sub">종목을 누르면 피봇·기술적 손절·1R·3R 공간·상태 변경 이력과 차트가 열립니다.</div><div class="tablewrap"><table class="table"><thead><tr><th>종목</th><th>상태</th><th>실험점수</th><th>시장</th><th>섹터</th><th>RS</th><th>신고가 거리</th><th>Pivot</th><th>Risk</th><th>3R 공간</th><th>거래량</th></tr></thead><tbody id="tbody"></tbody></table></div><div class="cards" id="mobile"></div></section><section class="section"><h2>판정 흐름과 공개 기준</h2><div class="flow"><span>시장</span><span>섹터</span><span>RS</span><span>추세</span><span>신고가</span><span>베이스·VCP</span><span>Pivot·거래량</span><span>Risk·3R</span><span>보유·훼손</span></div><div class="method"><details open><summary>깡토 원칙과 Wamo 실험값 구분</summary><p>RS·주도섹터·추세·돌파·작은 손실·큰 승자 보유는 전략 원칙입니다. RS 70, 거래량 1.5배, 최대 Risk 8%, 시장 Breadth 50% 등은 백테스트 전 초기값이며 확정 공식이 아닙니다.</p></details><details><summary>RS 산식과 한계</summary><p>RSI가 아닙니다. 현재 대시보드의 오닐식 가중수익률을 정밀계산 모집단 안에서 백분위화합니다. IBD의 독점 RS Rating과 동일하지 않으며, 모집단이 달라지면 순위도 달라질 수 있습니다.</p></details><details><summary>기술적 손절과 3R</summary><p>손절은 최근 수축 저점·20일선·최근 스윙 저점 중 구조적 지지와 1.5 ATR 여유를 함께 봅니다. 필요한 Risk가 8%를 넘으면 손절을 8%로 끌어올리지 않고 진입 부적합으로 판정합니다. 3R은 목표가가 아닌 시나리오 가격입니다.</p></details><details><summary>VCP 유사도</summary><p>국지 고점·저점의 조정폭 축소, 최근 10일 가격폭, 10일/50일 거래량을 정량 검사합니다. 미너비니의 정식 VCP를 자동 확정하지 않으며 사람이 차트를 최종 확인해야 합니다.</p></details><details><summary>실적과 컨센서스</summary><p>DART·SEC/Nasdaq 실제 매출·EPS·ROE와 기업 설명은 보조정보로 보여주되 초기 점수의 필수조건으로 쓰지 않습니다. Forward Consensus와 리비전은 사용하지 않습니다.</p></details><details><summary>백테스트 상태</summary><p id="bt"></p></details></div></section></div><aside class="drawer" id="drawer"><div class="dhead"><div><h2 id="dname"></h2><div class="sub" id="dsub"></div></div><button class="close" onclick="closeDrawer()">✕</button></div><div class="dbody" id="dbody"></div></aside><script>
const DATA=__DATA__;let MARKET='KR';const $=s=>document.querySelector(s);const fmt=(v,d=1)=>v==null?'—':Number(v).toLocaleString('ko-KR',{maximumFractionDigits:d});const pct=v=>v==null?'—':`${Number(v)>0?'+':''}${fmt(v,1)}%`;const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
function setMarket(m){MARKET=m;$('#kr').classList.toggle('active',m==='KR');$('#us').classList.toggle('active',m==='US');render()}
function filtered(){const q=$('#search').value.trim().toLowerCase(),s=$('#state').value;return(DATA.candidates[MARKET]||[]).filter(x=>(s==='ALL'||x.state===s)&&(!q||[x.name,x.ticker,x.sector.name].join(' ').toLowerCase().includes(q)))}
function card(x){return `<button class="card" onclick="openStock('${esc(x.ticker)}')"><div class="cardhead"><div><h3>${esc(x.name)}</h3><div class="ticker">${esc(x.ticker)} · ${esc(x.sector.name)}</div></div><div class="score">${fmt(x.score,0)}</div></div><div><span class="state ${esc(x.state)}">${esc(x.state)}</span> <span class="badge">${esc(x.grade)}등급 · 실험점수</span></div><div class="grid4"><div class="mini"><span>RS</span><b>${fmt(x.rs,0)}</b></div><div class="mini"><span>52주 고점거리</span><b>${pct(x.distance52HighPct)}</b></div><div class="mini"><span>Risk</span><b>${x.riskPct==null?'—':'-'+fmt(x.riskPct,1)+'%'}</b></div><div class="mini"><span>3R 공간</span><b>${esc(x.threeRRoom)}</b></div></div><div class="reason">${esc(x.action)}</div></button>`}
function render(){const rows=filtered(),all=DATA.candidates[MARKET]||[],today=DATA.today[MARKET]||[],m=DATA.meta.markets[MARKET]||{},reg=m.regime||{};$('#mode').className='badge '+(reg.status||'');$('#mode').textContent=`${MARKET==='KR'?'한국':'미국'} ${reg.status||'미확인'}`;$('#stamp').innerHTML=`기준일 ${esc(m.asOf||'—')}<br>자동검사 ${esc(DATA.meta.checkedAt.replace('T',' ').slice(0,16))} KST`;$('#bt').textContent=DATA.meta.backtestReason;const cnt=n=>all.filter(x=>x.state===n).length;$('#summary').innerHTML=`<div class="metric"><span>시장상태</span><b class="${reg.status}">${reg.status||'—'}</b><small>Breadth ${fmt(reg.breadthPct,1)}% · ${fmt(reg.eligibleCount,0)}종목</small></div><div class="metric"><span>매수신호</span><b>${cnt('매수신호')}</b><small>Pivot+거래량+Risk 확인</small></div><div class="metric"><span>돌파대기</span><b>${cnt('돌파대기')}</b><small>Pivot 3% 이내</small></div><div class="metric"><span>베이스·보유</span><b>${cnt('베이스 형성')+cnt('보유')}</b><small>형성 ${cnt('베이스 형성')} · 보유 ${cnt('보유')}</small></div><div class="metric"><span>데이터 범위</span><b>${fmt(m.stockCount,0)}</b><small>추적 ${fmt(m.candidateCount,0)} · 우선후보 ${fmt(m.qualifiedCount,0)}</small></div>`;$('#top').innerHTML=(today.slice(0,3).map(card).join('')||'<div class="empty">오늘은 최소 점수 60점(또는 55점 이상 돌파대기·매수신호)을 충족한 우선후보가 없습니다.</div>');$('#tbody').innerHTML=rows.map(x=>`<tr onclick="openStock('${esc(x.ticker)}')"><td><b>${esc(x.name)}</b><div class="ticker">${esc(x.ticker)}</div></td><td><span class="state ${esc(x.state)}">${esc(x.state)}</span></td><td>${fmt(x.score,0)} · ${esc(x.grade)}</td><td>${esc(x.marketRegime.status)}</td><td>${esc(x.sector.strength)} · ${esc(x.sector.name)}</td><td>${fmt(x.rs,0)}</td><td>${pct(x.distance52HighPct)}</td><td>${fmt(x.pivot,2)}</td><td>${x.riskPct==null?'—':'-'+fmt(x.riskPct,1)+'%'}</td><td>${esc(x.threeRRoom)}</td><td>${fmt(x.volumeRatio,2)}x</td></tr>`).join('');$('#mobile').innerHTML=rows.map(card).join('')||'<div class="empty">검색 결과가 없습니다.</div>'}
function maSeries(h,n){return h.map((_,i)=>i+1<n?null:h.slice(i+1-n,i+1).reduce((a,b)=>a+b.close,0)/n)}
function chart(x){const h=x.history||[],w=740,hg=320,p=34,vals=h.flatMap(r=>[r.close,r.high,r.low]).concat([x.pivot,x.technicalStop].filter(Boolean)),lo=Math.min(...vals),hi=Math.max(...vals),xx=i=>p+i*(w-2*p)/Math.max(1,h.length-1),yy=v=>hg-p-(v-lo)*(hg-2*p)/Math.max(1,hi-lo),line=(arr,color,width=1.5)=>`<polyline fill="none" stroke="${color}" stroke-width="${width}" points="${arr.map((v,i)=>v==null?'':`${xx(i)},${yy(v)}`).filter(Boolean).join(' ')}"/>`;const close=h.map(r=>r.close),m20=maSeries(h,20),m50=maSeries(h,50),m200=maSeries(h,200);return `<div class="chart" id="chart"><svg viewBox="0 0 ${w} ${hg}" preserveAspectRatio="none"><line x1="${p}" x2="${w-p}" y1="${yy(x.pivot)}" y2="${yy(x.pivot)}" stroke="#f0cc72" stroke-dasharray="6 5"/>${line(m200,'#ad91ff',1)}${line(m50,'#72b7ff',1.2)}${line(m20,'#75e0b0',1.2)}${line(close,'#eef4ff',2)}<line id="cursor" y1="${p}" y2="${hg-p}" stroke="#8da7c7" display="none"/><rect x="${p}" y="${p}" width="${w-2*p}" height="${hg-2*p}" fill="transparent" onmousemove="hoverChart(event,${w},${hg},${p})" onmouseleave="leaveChart()"/></svg><div class="tip" id="tip"></div></div><div class="caveat">흰색 가격 · 초록 20일선 · 파랑 50일선 · 보라 200일선 · 노랑 Pivot. 마우스/손가락을 올리면 날짜별 값을 확인할 수 있습니다.</div>`}
let ACTIVE=null;function hoverChart(e,w,hg,p){if(!ACTIVE)return;const box=$('#chart').getBoundingClientRect(),x=(e.clientX-box.left)/box.width*w,rows=ACTIVE.history||[],i=Math.max(0,Math.min(rows.length-1,Math.round((x-p)/(w-2*p)*(rows.length-1)))),px=p+i*(w-2*p)/Math.max(1,rows.length-1),m20=maSeries(rows,20)[i],m50=maSeries(rows,50)[i],m200=maSeries(rows,200)[i];const c=$('#cursor');c.setAttribute('x1',px);c.setAttribute('x2',px);c.style.display='block';const t=$('#tip');t.style.display='block';t.style.left=Math.min(box.width-150,e.clientX-box.left+10)+'px';t.style.top=Math.max(6,e.clientY-box.top-58)+'px';t.innerHTML=`${rows[i].date}<br>종가 ${fmt(rows[i].close,2)} · 거래량 ${fmt(rows[i].volume,0)}<br>MA20 ${fmt(m20,2)} · MA50 ${fmt(m50,2)} · MA200 ${fmt(m200,2)}`}
function leaveChart(){const c=$('#cursor'),t=$('#tip');if(c)c.style.display='none';if(t)t.style.display='none'}
function openStock(t){ACTIVE=(DATA.candidates[MARKET]||[]).find(x=>x.ticker===t);if(!ACTIVE)return;const x=ACTIVE;$('#dname').textContent=x.name;$('#dsub').textContent=`${x.ticker} · ${x.sector.name} · 기준일 ${x.date}`;const cards=[['종합판정',`${x.grade} · ${x.state}`],['시장',x.marketRegime.status],['섹터',`${x.sector.strength} · ${fmt(x.sector.score,1)}`],['RS',fmt(x.rs,0)],['52주 고점거리',pct(x.distance52HighPct)],['Stage',x.stage?`Stage ${x.stage}`:'미충족'],['주봉 추세',x.weeklyTrend],['VCP 유사도',x.vcp.label||x.vcp.status],['Pivot',fmt(x.pivot,2)],['기술적 손절',fmt(x.technicalStop,2)],['Risk',x.riskPct==null?'—':`-${fmt(x.riskPct,1)}%`],['1R',fmt(x.oneR,2)],['3R 시나리오',fmt(x.threeRPrice,2)],['3R 저항',`${x.resistance} · ${x.resistanceCount}구간`],['돌파 거래량',`${fmt(x.volumeRatio,2)}x`],['ATR(14)',fmt(x.atr14,2)]];$('#dbody').innerHTML=`<div class="hero"><b>${esc(x.action)}</b><p>${esc(x.profile.summary)}<br><span class="caveat">Forward Consensus 미사용 · 실제실적은 보조정보 · 저항범위 ${esc(x.coverage.resistanceScope)}</span></p></div><div class="detailgrid">${cards.map(([a,b])=>`<div class="dcard"><span>${a}</span><b>${esc(b)}</b></div>`).join('')}</div>${chart(x)}<h3>실험점수 구성 — ${esc(x.scoreVersion)}</h3><div class="components">${Object.entries(x.scoreComponents).map(([k,v])=>`<div class="comp"><span>${esc(k)}</span><b>${fmt(v,0)}점</b></div>`).join('')}</div><h3>실적 보조정보</h3><div class="detailgrid"><div class="dcard"><span>매출 YoY</span><b>${pct(x.actuals.salesYoY)}</b></div><div class="dcard"><span>EPS YoY</span><b>${pct(x.actuals.epsYoY)}</b></div><div class="dcard"><span>ROE</span><b>${pct(x.actuals.roe)}</b></div><div class="dcard"><span>공식자료 상태</span><b>${esc(x.actuals.dartStatus)}</b></div></div><h3>상태 변경 이력</h3><div class="timeline">${(x.stateHistory||[]).slice().reverse().map(e=>`<div class="event"><b>${esc(e.at.replace('T',' ').slice(0,16))}</b><span>${esc(e.from||'최초 관찰')} → ${esc(e.to)}</span></div>`).join('')}</div><p class="caveat">이 화면은 매매권유가 아닙니다. 손절선·Pivot·3R은 확보된 가격 이력에 기반한 정량 시나리오이며 장중 갭, 유동성, 기업 이벤트를 보장하지 않습니다.</p>`;$('#drawer').classList.add('open')}
const openStockBase=openStock;
openStock=function(t){openStockBase(t);if(!ACTIVE)return;const x=ACTIVE;const filings=(x.catalysts||[]).map(c=>`<div class="event"><b>${esc(String(c.date||'').replace(/(\d{4})(\d{2})(\d{2})/,'$1-$2-$3'))}</b><span>${c.url?`<a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.report||c.category||'공시 원문')}</a>`:esc(c.report||c.category||'공시')}</span></div>`).join('')||'<div class="caveat">이번 데이터에는 연결된 최근 공식 공시가 없습니다.</div>';$('#dbody').insertAdjacentHTML('beforeend',`<h3>기업·실적 동인</h3><div class="hero"><p>주요 제품·서비스: ${esc(x.profile.products||'미연결')}<br>실적이 좋아지는 조건: ${esc(x.profile.drivers||'미연결')}</p></div><h3>최근 공식 공시·이벤트</h3><div class="timeline">${filings}</div>`)}
function closeDrawer(){$('#drawer').classList.remove('open');ACTIVE=null}document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDrawer()});setMarket('KR');
</script></body></html>'''.replace("__DATA__", payload).replace(
        "현재 대시보드의 오닐식 가중수익률을 정밀계산 모집단 안에서 백분위화합니다.",
        "12개월을 4개 구간으로 나누어 최근 3개월 수익률 40%, 그 이전 각 3개월 수익률을 20%씩 반영한 뒤 현재 정밀계산 모집단 안에서 백분위화합니다.",
    )


def validate_output(data: dict[str, Any], html: str) -> None:
    if "__DATA__" in html or "window.WAMO_DATA" in html:
        raise RuntimeError("깡토 HTML 데이터 삽입 실패")
    if "깡토 추세추종" not in html or "Forward Consensus 미사용" not in html:
        raise RuntimeError("필수 설명 누락")
    for market in ("KR", "US"):
        rows = data.get("candidates", {}).get(market, [])
        tickers = [x.get("ticker") for x in rows]
        if len(tickers) != len(set(tickers)):
            raise RuntimeError(f"{market} 후보 티커 중복")
        for item in rows:
            if abs(sum(item["scoreComponents"].values()) - item["score"]) > 0.01:
                raise RuntimeError(f"{market} {item.get('ticker')} 점수 합계 불일치")
            if item["riskPct"] is not None and item["riskPct"] > CFG["max_risk_pct"] and item["state"] == "매수신호":
                raise RuntimeError(f"{market} {item.get('ticker')} 과도 Risk 매수신호")
            if item["threeRPrice"] is not None and item["threeRPrice"] <= item["pivot"]:
                raise RuntimeError(f"{market} {item.get('ticker')} 3R 계산 오류")


def self_test() -> None:
    rows = []
    for i in range(140):
        close = 100 + i * 0.22 + math.sin(i / 5) * (4 if i < 70 else 2 if i < 110 else 0.8)
        rows.append({"date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "close": close, "high": close * 1.012, "low": close * 0.988, "volume": 1_000_000 if i < 120 else 650_000})
    stock = {"ticker": "TEST", "name": "테스트", "sector": "테스트업", "history": rows, "oneilRsPercentile": 88, "trendTemplate": True}
    regime = {"status": "GREEN", "breadthPct": 60, "eligibleCount": 1}
    item = analyze_stock(stock, {"name": "테스트업", "score": 70, "action": "상승", "memberCount": 4}, regime, "KR")
    assert item and item["technicalStop"] < item["pivot"] < item["threeRPrice"]
    assert abs(sum(item["scoreComponents"].values()) - item["score"]) < 0.01
    breakout_rows = [dict(row) for row in rows]
    old_pivot = max(row["high"] for row in breakout_rows[-60:])
    breakout_rows[-1].update({"close": old_pivot * 1.01, "high": old_pivot * 1.015, "low": old_pivot * 0.995, "volume": 2_000_000})
    breakout_stock = dict(stock, ticker="BREAKOUT", history=breakout_rows, oneilRsPercentile=92)
    breakout = analyze_stock(breakout_stock, {"name": "테스트업", "score": 75, "action": "상승", "memberCount": 5}, regime, "KR")
    assert breakout and breakout["state"] == "매수신호"
    assert breakout["riskPct"] is not None and breakout["riskPct"] <= CFG["max_risk_pct"]
    assert breakout["volumeRatio"] >= CFG["breakout_volume_ratio"]
    assert not is_operating_company({"name": "KODEX 테스트", "instrumentType": "ETF_ETN"})
    print("깡토 계산 self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    data = build_payload()
    html = html_document(data)
    validate_output(data, html)
    OUT_HTML.write_text(html, encoding="utf-8")
    for path in (KR_HTML, US_HTML, MOVERS_HTML):
        add_navigation(path)
    print(
        "깡토 추세추종 완료:",
        "한국", len(data["candidates"]["KR"]),
        "/ 미국", len(data["candidates"]["US"]),
        "/ 자동검사", data["meta"]["checkedAt"],
    )


if __name__ == "__main__":
    main()
