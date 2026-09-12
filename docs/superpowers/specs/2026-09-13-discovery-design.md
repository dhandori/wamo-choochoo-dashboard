# WAMO discovery rebuild — approved design

Approved in conversation on 2026-09-13: rebuild discovery, preserve the original WAMO,
test and deploy to the existing GitHub Pages site. No new external data disclosure.

## Product contract

Three modes: verified high candidates, industry exploration, and known-security search.
Search covers the public WAMO KR/US snapshots plus allowlisted radar candidates, not an
entire-exchange directory. Show the scope and counts explicitly. No result is not proof
that a security has no signal: distinguish outside coverage, no matching current candidate,
stale release and failed connection. All market/query/industry filters compose.

Candidate cards show country, ticker, name, as-of, currency/close, true high flags and
classification caveats. Industry cards show numerator/denominator, ratio and source phase;
clicking an industry filters candidates by country and exact classification path. Display
the classified-cohort scope, not whole-market breadth. Never mix WAMO sector labels with
radar industry membership. No opaque investment score.

Every security opens a discovery detail with evidence. Existing WAMO KR/US securities
offer an explicit link to their original detail. Company quality and fair-price evaluation
remain separate and unevaluated in discovery. WAMO Stage 2 core, MA alignment and RS
can be attached only with identifiable source/date; require FULL technical coverage,
history readiness and matching dates before using them as current candidate filters.
RS is WAMO's market-cohort percentile, not a global rank. Uncovered global indicators
stay unknown. Do not infer them from high flags or change their formulas here.

## Architecture

- `wamo_catalog.py`: offline allowlisted catalog derived only from existing public HTML
  payloads. Stable country+ticker identity, finite values, source dates, technical readiness,
  precise original-detail links. Atomic output `wamo_catalog.json`. Regenerate alongside
  radar sync and after market update (including failure status); never delay price publication.
- `discovery/model.js`: pure normalization, join, signal/market/industry filters and ordering.
- `discovery/store.js`: fetch lifecycle, validation, freshness, retry. Three-hour connection
  TTL remains; stale/failed releases excluded from current candidates but optionally visible
  as explicitly historical records. Five-minute refresh, focus/online refresh and manual retry;
  timeout requests and never permanently disable search. A failed fetch must not relabel
  cached candidates current. Preserve user filters when replacing data.
- `discovery/view.js` and `discovery/detail.js`: scoped responsive DOM rendering with
  textContent for data. Keyboard-operable controls, accessible dialogs, explicit empty states.
- `wamo_discovery.js`: small bootstrap loader retaining the existing status-script entry.

Both catalog and radar may fail independently; existing WAMO must continue operating.
No credentials or new private fields in browser, generated JSON or logs. Keep schema v1
radar backward compatible. Catalog is a separate schema. No external runtime CDN required.

## Verification and delivery

Unit tests first for duplicate identities, leading-zero KR codes, unknown technical fields,
cross-country joins, combined filters, future/expired timestamps, partial editions, retry
and recovery. Browser tests desktop/mobile with actual public snapshots and deterministic
network/time fixtures for failure paths. Test real typing, clearing, industry clickthrough,
signal filtering, all detail types, existing WAMO detail links and long-open refresh.
CI must pass before merge; verify production page and data after Pages deployment.

## Exclusions

Global fundamental valuation, sizing and performance journals are separate work. Additional
global Stage 2/RS price-history sourcing is not silently simulated; report actual coverage.
