"""SEC 중계: GitHub 서버 대신 사용자 컴퓨터의 브라우저가 SEC 자료를 받아 온다.

SEC blocks GitHub's shared runner addresses ("Request Rate Threshold Exceeded"),
so once a week the Claude app's browser on the person's computer runs
tools/sec_relay.js for the same priority companies the US update would fetch,
and this script turns its compact output into normal SEC cache entries
(wamo_us_sec_cache.json, valid SEC_CACHE_DAYS days). The regular US update then
uses them as "SEC 캐시" without touching sec.gov.

  python wamo_sec_relay.py targets [N]      -> JSON list of tickers to fetch
  python wamo_sec_relay.py apply relay.json -> merge into the SEC cache
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import wamo_update_us_sec as us
from wamo_update import extract_old_payload

ROOT = Path(__file__).resolve().parent
SOURCE = "SEC EDGAR submissions + XBRL companyfacts (사용자 컴퓨터 중계)"


def _stocks(root=ROOT):
    payload = extract_old_payload((root / "us.html").read_text(encoding="utf-8"))
    return payload.get("stocks") or []


def _symbol(stock):
    return str(stock.get("displayTicker") or stock.get("ticker") or "").upper()


def targets(limit=us.SEC_TARGET_MAX, root=ROOT, today=None):
    """Same priority order as enrich_sec(); skips entries already fresh from SEC."""
    today = today or date.today()
    cache = json.loads((root / "wamo_us_sec_cache.json").read_text(encoding="utf-8")) if (root / "wamo_us_sec_cache.json").exists() else {}
    stocks = sorted(_stocks(root), key=lambda x: (
        1 if x.get("conditionCount", 0) >= 4 else 0,
        1 if x.get("trendTemplate") else 0,
        1 if us.core.is_52week_high_zone(x) else 0,
        x.get("rsPercentile", 0), x.get("market_cap_usd", 0),
    ), reverse=True)
    out = []
    for stock in stocks:
        sym = _symbol(stock)
        old = cache.get(sym) or {}
        try:
            fresh = old.get("secStatus") == "LIVE" and (today - date.fromisoformat(str(old.get("secFetchedAt"))[:10])).days < us.SEC_CACHE_DAYS - 1
        except ValueError:
            fresh = False
        if sym and not fresh:
            out.append(sym)
        if len(out) >= limit:
            break
    return out


def _r(value, digits=1):
    return None if value is None else round(value, digits)


def _f(value):
    # JSON from the browser turns 2.0 into 2; Python's direct path keeps floats.
    return None if value is None else float(value)


def parts_from_summary(sm, sector=""):
    """Round the browser's raw numbers exactly where wamo_update_us_sec rounds them."""
    eps, sales, annual = sm.get("epsYoy") or {}, sm.get("salesYoy") or {}, sm.get("annual") or {}

    def yoy(x):
        if x.get("mode") == "YOY" and x.get("a") is not None and x.get("b"):
            return round((_f(x["a"]) / _f(x["b"]) - 1) * 100, 1), "YOY", x.get("concept")
        return None, x.get("mode"), x.get("concept")

    years = [(int(y), _f(v)) for y, v in (annual.get("years") or [])]
    if annual.get("count", 0) < 2 or len(years) < 2:
        annual_part = (None, None, [], annual.get("concept"))
    else:
        latest, previous = years[-1][1], years[-2][1]
        a_yoy = (latest / previous - 1) * 100 if previous > 0 else None
        cagr = None
        if annual.get("count", 0) >= 4 and len(years) >= 4:
            old = years[-4][1]
            if old > 0 and latest > 0:
                cagr = ((latest / old) ** (1 / 3) - 1) * 100
        annual_part = (_r(a_yoy), _r(cagr), [{"year": y, "value": v} for y, v in years[-4:]], annual.get("concept"))

    def hist(h):
        return [{"frame": x["frame"], "growthPct": round(_f(x["growthPct"]), 1)} for x in ((h or [None, None])[0] or [])]

    def margin(m):
        if not m:
            return None
        latest, prior = _f(m["latestPct"]), _f(m["priorYearPct"])
        return {"frame": m["frame"], "latestPct": round(latest, 1), "priorYearPct": round(prior, 1),
                "changePp": round(latest - prior, 1),
                "numeratorConcept": m.get("numeratorConcept"), "revenueConcept": m.get("revenueConcept")}

    return {
        "eps": yoy(eps), "sales": yoy(sales), "annual": annual_part,
        "epsHistory": hist(sm.get("epsHistory")), "salesHistory": hist(sm.get("salesHistory")),
        "grossMargin": margin(sm.get("grossMargin")), "operatingMargin": margin(sm.get("operatingMargin")),
        "health": us.financial_health_from_values({k: _f(v) for k, v in (sm.get("health") or {}).items()}, sector),
    }


def apply(relay, root=ROOT):
    stocks = {_symbol(s): s for s in _stocks(root)}
    cache_path = root / "wamo_us_sec_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    done, failed = [], {}
    for ticker, sm in (relay or {}).items():
        stock = stocks.get(str(ticker).upper())
        if not stock or not isinstance(sm, dict):
            failed[ticker] = "NOT_IN_LIST"
            continue
        if sm.get("error") or not sm.get("cik"):
            failed[ticker] = sm.get("error") or "NO_CIK"
            continue
        sector = stock.get("sector") or stock.get("krxSector") or ""
        filings = list(sm.get("filings") or [])
        annual = sm.get("annualFiling")
        # Companies with very many 8-K/6-K filings can push the annual report past the
        # list cut; the browser also records it separately, same rule as the direct path.
        if annual and not any(f.get("form") in ("10-K", "20-F", "40-F") for f in filings):
            filings.append({"form": annual.get("form") or "10-K", "accession": annual.get("accession"),
                            "doc": annual.get("doc"), "date": annual.get("date")})
        entry = us.build_sec_entry(stock, sm["cik"], sm.get("sic"), sm.get("sicDescription"), filings,
                                   parts_from_summary(sm, sector), source=SOURCE)
        old = cache.get(_symbol(stock)) or {}
        # Keep the Nasdaq profile fields gathered earlier; SEC fields win where both exist.
        cache[_symbol(stock)] = {**old, **entry}
        done.append(_symbol(stock))
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    return done, failed


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "targets":
        print(json.dumps(targets(int(sys.argv[2]) if len(sys.argv) > 2 else us.SEC_TARGET_MAX)))
    elif cmd == "apply":
        ok, bad = apply(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
        print(f"SEC 중계 반영 {len(ok)}개 · 실패 {len(bad)}개", json.dumps(bad, ensure_ascii=False)[:500])
    else:
        print(__doc__)
