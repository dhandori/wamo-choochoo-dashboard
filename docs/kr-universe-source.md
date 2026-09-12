# Korean universe source repair

## Incident and boundary

The 2026-09-11 Korean refresh failed with zero KOSPI universe rows.
The legacy `finance.naver.com/sise/sise_market_sum.naver` URL now redirects
to a client-rendered page without the old stock table. The existing runner
correctly kept the last verified dashboard instead of publishing empty data.

`wamo_market_data.fetch_naver_universe` now reads the Naver mobile JSON
market-value list, using the existing HTTP timeout and headers. The updater's
`fetch_market_summary` signature and returned fields remain unchanged.

## Contract

- `marketValueRaw` is an integer KRW amount. Do not use the rounded
  `marketValue` display field (hundred-million KRW) as if it were raw KRW.
- Codes remain six-character strings, including leading zeros and letters.
- Validate market, page number, page size, stable total count and unique codes.
- Require every page and a positive raw capitalization for every source row.
- Preserve existing preferred-share/SPAC exclusions. Existing downstream
  instrument classification, capitalization/liquidity thresholds and signals
  are unchanged.
- Schema drift, incomplete pagination, duplicate rows or changed totals fail
  the refresh. They do not silently shrink the universe or reuse stale rows as LIVE.
- This endpoint is provider-controlled, not a guaranteed public API contract.
  Future changes must remain visible as failed refreshes with old data retained.

## Verification and scope

Offline regression fixtures exercise the actual updater entry point without
market requests. A read-only live check on 2026-09-12 returned 2,365 KOSPI and
1,768 KOSDAQ rows after the existing preferred-share/SPAC exclusions and
before downstream fund, capitalization and liquidity filtering.

No generated HTML, caches, ranking formulas, financial data, secrets or
production schedules are changed. A complete production refresh still needs
its configured provider credentials and freshness gates. This repair is the
first isolated market-data boundary, not completion of Investment OS integration.

