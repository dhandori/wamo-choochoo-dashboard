# WAMO Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the unreliable candidate list with scoped search, composable discovery,
industry drilldown and evidence details that recover from data failures.

**Architecture:** Offline catalog joins existing public WAMO snapshots to allowlisted radar.
Pure model and async store are independent of a scoped DOM view. Existing pages remain intact.

**Tech Stack:** Python 3.12, vanilla JS modules, node:test, Python unittest, Playwright in CI.

**Spec:** ../specs/2026-09-13-discovery-design.md

## Global Constraints

- Preserve existing WAMO pages, calculations, authentication and private/public boundaries.
- Publish only existing public WAMO fields and already-allowlisted radar candidates/industries.
- Unknown is null, not false/zero. No global Stage 2/RS claim from incomplete coverage.
- Three-hour radar connection TTL; five-minute refresh, timeout and user retry.
- Existing GitHub Pages deployment only; no paid dependencies or new runtime services.

### Task 1: Public catalog and regeneration

**Files:** Create `wamo_catalog.py`, `tests/test_catalog.py`; modify `.github/workflows/radar-sync.yml`, `.github/workflows/main.yml`; generate `wamo_catalog.json`.

**Interfaces:** `build_catalog(payloads, generated_at)` takes {'KR': WAMO_DATA, 'US': WAMO_DATA}.
Returns `{schemaVersion:1, generatedAt, markets:{KR:{asOf,count},US:{asOf,count}}, stocks:[...]}`.
Rows: `id` (KR:001450 or US:NVDA), `country`, `market` (KR-KOSPI/KR-KOSDAQ/US),
`symbol`, `ticker`, `name`, `sector`, `asOf` YYYY-MM-DD, `currency`, `close`,
`detailUrl` index.html?stock=encoded ticker / us.html?stock=encoded ticker,
`source`, `technical:{ready,asOf,stage2,aligned,rsPercentile,ma50,ma150,ma200,checks}`.
`checks` is original stage2Checks allowlisted booleans. Technical ready requires FULL,
historyReady200 true, not isStale, and valid close/date; otherwise all indicator values null.

- [ ] Write tests for KR leading zero identity, US identity, duplicates, invalid dates/nonfinite,
  unknown technical fields, strict projection and no mutation of source.
  ```python
  out = build_catalog({'KR': {'meta': {'asOf':'2026-09-11'}, 'stocks': [kr]}}, '2026-09-13T00:00:00Z')
  self.assertEqual(out['stocks'][0]['id'], 'KR:001450')
  self.assertNotIn('private_value', out['stocks'][0])
  ```
- [ ] Run `python -m unittest discover -s tests -p test_catalog.py -v`; observe missing implementation failure.
- [ ] Implement pure projection plus CLI reading the existing HTML extractor and atomic writer.
  Invalid or duplicate rows reject generation instead of silently undercounting. Missing technical
  metadata must not reject otherwise valid search records. No arbitrary provider objects copied.
- [ ] Regenerate on radar-sync before git add and in a separate always-run main workflow step
  after market publication. Catalog failure does not rollback existing pages; exit failure visible.
  Save catalog using existing serialized Git workflow. Do not add new triggers with secrets on PRs.
- [ ] Run focused tests and CLI; report counts, commit only owned files.

### Task 2: Discovery model, lifecycle and responsive UI

**Files:** create `discovery/{model,store,view,detail}.js`, `discovery/discovery.css`,
`discovery/package.json` (type module), `tests/discovery.test.mjs`; replace `wamo_discovery.js`.

**Interfaces:** model consumes catalog schema above and radar schema1 (`editions`, `checkedAt`).
Export pure `buildModel(catalog, radar, now, connectionFailed=false)`,
`selectStocks(model, filters)`, `selectIndustries(model, filters)`.
Model has `stocks`, `industries`, `status`; rows retain source signals separate from technical.
Filters: `{mode:'candidates'|'search', query:'', country:'', industry:'', signal:'', historical:false}`.
Store exposes `createStore({fetcher, now, onChange})` returning `refresh()` and `getState()`;
view owns refresh scheduling on interval/focus/online, error display and filter state.

- [ ] Tests first: market words/name/ticker normalized token search; stable country identity;
  country+industry+signal conjunction; no same-ticker cross-country join; unknown Stage2
  excluded from Stage2-only; dated technical mismatches ignored for candidate filtering.
  ```js
  assert.equal(selectStocks(model,{mode:'candidates',query:'미국',country:'KR'}).length,0);
  assert.equal(selectIndustries(model,{country:'KR'}).every(x=>x.country==='KR'),true);
  ```
- [ ] Run `node --test tests/discovery.test.mjs` RED, implement pure model, run GREEN.
- [ ] Add lifecycle RED tests for old/future/invalid checkedAt, FAILED edition, network failure
  and recovery. Implement concurrent-safe fetch, bounded timeout, independent source statuses,
  retain historical data only with explicit stale status. Search never gets disabled on expiry.
- [ ] Add UI through model/store: distinct modes, visible scope, labeled query/country/industry/
  signal selectors, reset, current/historical opt-in, results counter, sort and pagination.
  Industry view groups exact radar paths by country, sorts by ratio then count, shows phase
  and denominator, click narrows current candidates. Explain if filters yield no results.
- [ ] Details always show signal flags (true/false/unknown), dates, close/currency, taxonomy
  caveats and separate company/price/chart panels. Only explicit links open original analysis.
  Handle `?stock=` on existing KR/US pages using WAMO_OPEN_DETAIL independently of radar fetch.
- [ ] Dynamic module bootstrap must work on index/us with existing status loader, show an error
  on failed module load, scope CSS and IDs, no XSS innerHTML from data. Use DOM text nodes.
- [ ] Run Node tests, syntax checks and commit owned files; return test evidence and limitations.

### Task 3: Browser regression and release

**Files:** modify `tests/browser_smoke.py`, create `tests/discovery_browser.py`, modify `.github/workflows/ci.yml`; update docs.

- [ ] Adapt existing browser fixture asset copying and deliberate detail click contract.
- [ ] Add real public snapshot tests for country names, known noncandidate search, unknown symbol,
  candidate filter+reset, industry click to matching names, dialog close/Escape, original detail
  on both pages, no mobile overflow. All external network intercepted in CI tests.
- [ ] Add controlled feed fetch failure/recovery and simulated expiry tests using actual modules;
  ensure input enabled, current candidates hidden, historical opt-in marked and retry recovers.
- [ ] CI run Node tests, Python tests, original browser regression plus discovery browser suite.
- [ ] Review branch diff and fix any correctness findings with focused regression tests.
- [ ] Push via authenticated GitHub connector, PR, wait CI, merge only passing head, verify Pages
  deployment and live UI. No completion claim without actual deployed behavior.
