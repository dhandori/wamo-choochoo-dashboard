# Global discovery integration

Approved scope: publish selected symbols, high signals and industry analysis,
never raw private caches, credentials or private operational records.

## Boundaries

- `wamo_radar.py`: read one pinned source commit, check release fingerprint,
  all required exchange sessions, coverage and candidates, then allowlist output.
- `wamo_radar.json`: public derived results only. Failures retain prior edition
  data but explicitly mark it FAILED; the browser hides failed/stale candidates.
- `discovery.html`: dedicated global discovery tab for search, signals and industry
  exploration. It loads the public catalog/radar, not the large KR/US price HTML.
- `wamo_discovery.js`: shared navigation and original-detail routing. It mounts the
  discovery modules only in the dedicated page's host. Existing market pages do not
  fetch the discovery catalog/radar; their `?stock=` and legacy `?radar=` links still
  open the original detail independently. The status loader supplies the navigation
  link again after every normal HTML regeneration, without patching market payloads.
  Unsupported securities remain unevaluated. No browser authentication is used.
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
The connection state and each market edition are reported separately. A recent connection
does not make a failed or malformed edition current, and a failed catalog revalidation does
not make cached technical evidence current. The historical opt-in exposes only the retained
published observations in this discovery scope; it is not whole-market or all-universe coverage.

## Verification

Offline tests cover allowlisting, null preservation, mismatched releases,
stale sessions, duplicate symbols, invalid candidate dates and hook idempotence.
Browser tests load the real discovery modules and checked-in public catalog/radar snapshots.
Their controlled clock and intercepted source responses verify desktop/mobile search, exact
industry drilldown, combined high/technical filters, original detail links, three-hour expiry,
independent failures, malformed responses, failed editions and retry recovery without external
network access. The original index/US/movers smoke suite runs separately to protect the existing
WAMO pages.
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

The discovery `다시 확인` button reloads the latest published catalog and radar snapshots; it
does not call a market-data API directly. A connection failure keeps search controls available,
but current candidates remain hidden until a valid, recent published radar response is loaded.

## 외국인·기관 추정 수급 (2026-10-23)

- `wamo_update_business_dart.py`의 8/12 단계가 후보 종목(최대 140개)에 대해 NAVER 모바일
  투자자별 매매동향 JSON(`m.stock.naver.com/api/stock/{code}/trend`, pageSize 최대 60)을 읽는다.
  구 PC `frgn.naver` HTML 표는 2026-10 개편으로 사라져 사용하지 않는다.
- 저장 필드: `foreign/institution_netbuy_{5,20,60}d`(순매수수량×당일 종가 추정, 원),
  `*_pct_mcap`(시총 대비 %), `flow_asof`, `flow_sessions`, `foreign_hold_pct`(제공사 표기 보유율).
  보유 일수가 기간보다 짧으면 그 기간 값은 만들지 않는다. 연기금·사모 세부 수급은 자료가 없어 만들지 않는다.
- `wamo_insights.py`가 검증된 숫자만 `stocks[].investorFlow`로 통과시키고(KR 한정),
  레이더 종목 상세의 '외국인·기관 순매수 (추정)' 카드가 이를 표시한다. 데이터가 없는 종목은 카드가 나타나지 않는다.
- 수급은 보조정보다: 조회 실패·예산 초과(7분)여도 가격·후보 게시를 막지 않는다. 메타는 `meta.flowMeta`.
