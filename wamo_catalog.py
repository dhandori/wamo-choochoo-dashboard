"""Build the public discovery catalog from the WAMO HTML snapshots."""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote

from wamo_runtime import write_json
from wamo_update import extract_old_payload


ROOT = Path(__file__).resolve().parent
PAGES = {"KR": ROOT / "index.html", "US": ROOT / "us.html"}
STAGE2_CHECKS = (
    "주가>150·200일선",
    "150일선>200일선",
    "200일선 상승",
    "50일선>150·200일선",
    "주가>50일선",
    "52주 저점 대비 +30%",
    "52주 고점 25% 이내",
)
TECHNICAL_FIELDS = ("rsPercentile", "ma50", "ma150", "ma200")


def _date(value, label):
    if not isinstance(value, str):
        raise ValueError(f"invalid {label} date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid {label} date: {value!r}") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"invalid {label} date: {value!r}")
    return value


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"invalid {label}")
    return value.strip()


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"invalid {label}")
    return value


def _identity(country, stock):
    ticker = _text(stock.get("ticker"), "ticker")
    if country == "KR":
        raw_symbol = stock.get("stock_code")
        if raw_symbol is None:
            raw_symbol = ticker.split(".", 1)[0]
        symbol = _text(raw_symbol, "symbol")
        if len(symbol) != 6 or not symbol.isascii() or not symbol.isalnum() or symbol != symbol.upper():
            raise ValueError(f"invalid KR symbol: {symbol!r}")
        exchange = _text(stock.get("krx_market"), "KR market").upper()
        if exchange not in ("KOSPI", "KOSDAQ"):
            raise ValueError(f"invalid KR market: {exchange!r}")
        return symbol, ticker, f"KR-{exchange}"
    if country == "US":
        symbol = ticker.upper()
        return symbol, ticker, "US"
    raise ValueError(f"unsupported country: {country!r}")


def _unknown_technical(as_of):
    return {
        "ready": False,
        "asOf": as_of,
        "stage2": None,
        "aligned": None,
        "rsPercentile": None,
        "ma50": None,
        "ma150": None,
        "ma200": None,
        "checks": {},
    }


def _technical(stock, as_of):
    coverage_ready = (
        stock.get("technicalCoverage") == "FULL"
        and stock.get("historyReady200") is True
        and stock.get("isStale") is False
    )
    if not coverage_ready:
        return _unknown_technical(as_of)

    stage2 = stock.get("stage2Core")
    alignment = stock.get("alignment")
    aligned = alignment.get("isAligned") if isinstance(alignment, dict) else None
    values = [stock.get(field) for field in TECHNICAL_FIELDS]
    indicators_valid = (
        type(stage2) is bool
        and type(aligned) is bool
        and all(
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(value)
            for value in values
        )
    )
    if not indicators_valid:
        return _unknown_technical(as_of)

    original_checks = stock.get("stage2Checks")
    checks = {}
    if isinstance(original_checks, dict):
        checks = {
            key: original_checks[key]
            for key in STAGE2_CHECKS
            if type(original_checks.get(key)) is bool
        }
    return {
        "ready": True,
        "asOf": as_of,
        "stage2": stage2,
        "aligned": aligned,
        "rsPercentile": stock["rsPercentile"],
        "ma50": stock["ma50"],
        "ma150": stock["ma150"],
        "ma200": stock["ma200"],
        "checks": checks,
    }


def _project_stock(country, stock, market_meta):
    if not isinstance(stock, dict):
        raise ValueError(f"invalid {country} stock row")
    symbol, ticker, market = _identity(country, stock)
    as_of = _date(stock.get("date"), "stock")
    close = _number(stock.get("close"), "close")
    source = stock.get("dataSource")
    if not isinstance(source, str) or not source.strip():
        source = market_meta.get("source")
    source = _text(source, "source")
    page = "index.html" if country == "KR" else "us.html"
    return {
        "id": f"{country}:{symbol}",
        "country": country,
        "market": market,
        "symbol": symbol,
        "ticker": ticker,
        "name": _text(stock.get("name"), "name"),
        "sector": _text(stock.get("sector"), "sector"),
        "asOf": as_of,
        "currency": "KRW" if country == "KR" else "USD",
        "close": close,
        "detailUrl": f"{page}?stock={quote(ticker, safe='')}",
        "source": source,
        "technical": _technical(stock, as_of),
    }


def build_catalog(payloads, generated_at):
    """Return a strict, non-mutating projection of public KR/US WAMO payloads."""
    if not isinstance(payloads, dict):
        raise ValueError("payloads must be a mapping")
    unknown = set(payloads) - set(PAGES)
    if unknown:
        raise ValueError(f"unsupported markets: {sorted(unknown)!r}")

    markets = {country: {"asOf": None, "count": 0} for country in PAGES}
    stocks = []
    seen = set()
    for country in PAGES:
        if country not in payloads:
            continue
        payload = payloads[country]
        if not isinstance(payload, dict) or not isinstance(payload.get("meta"), dict):
            raise ValueError(f"invalid {country} payload")
        market_meta = payload["meta"]
        market_as_of = _date(market_meta.get("asOf"), "market")
        source_stocks = payload.get("stocks")
        if not isinstance(source_stocks, list):
            raise ValueError(f"invalid {country} stocks")
        projected = []
        for source_stock in source_stocks:
            row = _project_stock(country, source_stock, market_meta)
            if row["id"] in seen:
                raise ValueError(f"duplicate stock identity: {row['id']}")
            seen.add(row["id"])
            projected.append(row)
        stocks.extend(projected)
        markets[country] = {"asOf": market_as_of, "count": len(projected)}

    return {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "markets": markets,
        "stocks": stocks,
    }


def main():
    payloads = {}
    for country, path in PAGES.items():
        payload = extract_old_payload(path.read_text(encoding="utf-8"))
        if not payload:
            raise RuntimeError(f"WAMO_DATA extraction failed: {path.name}")
        payloads[country] = payload
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    catalog = build_catalog(payloads, generated_at)
    destination = ROOT / "wamo_catalog.json"
    write_json(destination, catalog)
    print(
        "catalog generated:",
        ", ".join(f"{country}={catalog['markets'][country]['count']}" for country in PAGES),
    )


if __name__ == "__main__":
    main()
