# Global discovery integration

Approved scope: publish selected symbols, high signals and industry analysis,
never raw private caches, credentials or private operational records.

## Boundaries

- `wamo_radar.py`: read one pinned source commit, check release fingerprint,
  all required exchange sessions, coverage and candidates, then allowlist output.
- `wamo_radar.json`: public derived results only. Failures retain prior edition
  data but explicitly mark it FAILED; the browser hides failed/stale candidates.
- `wamo_discovery.js`: searchable cross-market discovery and industry analysis.
  Known KR/US securities open the existing WAMO detail; unsupported securities
  explicitly remain unevaluated. No browser authentication is used.
- `wamo_runtime.patch_status_ui`: installs a small detail-opening hook without
  replacing existing detail rendering or changing embedded market data.
- `radar-sync.yml`: hourly update at UTC minute 25, serialized with existing
  market updates; reads private reports with read-only RADAR_READ_TOKEN.
  The normal repository token writes only the derived public output.

## Reliability

Historical-high unknown values stay null. Radar signal definitions remain the
source engine's definitions; WAMO Stage 2/RS and valuation are not inferred from
high flags. Industry denominators are classified observed cohorts, not entire
exchanges. All nine market coverage entries must agree with latest completed
sessions (10-minute provider grace). No partial-current report is published.
The browser stops showing candidates when connection checks are over 3 hours old.

## Verification

Offline tests cover allowlisting, null preservation, mismatched releases,
stale sessions, duplicate symbols, invalid candidate dates and hook idempotence.
Browser tests use existing public stocks in synthetic discovery fixtures.
The branch connection workflow tests private read access without printing data.

## Remaining Investment OS scope

This connects existing discovery and analysis systems. It does not invent
fundamental coverage for Japan/China, a global Stage 2/RS formula, valuation,
position sizing, or a performance journal. Those require separate explicit
data contracts and tests. Existing WAMO scoring and DART/SEC processing remain
unchanged; no claim is made that a high signal proves a good company or price.

## Operations

Token expiration: renew the fine-grained private-repository Contents read token
in the RADAR_READ_TOKEN Actions secret. Never commit it or put it in browser code.
For rollback, revert the integration commit; the original WAMO pages and price
pipelines remain independent. Review Actions when a FAILED badge appears.
