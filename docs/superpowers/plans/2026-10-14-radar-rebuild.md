# WAMO Radar Rebuild Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or executing-plans. User requested autonomous completion; do not add approval gates.

**Goal:** Reliable KR/US near-high, industry-action and movers daily dashboard.
**Architecture:** Deterministic projection from existing verified OHLCV snapshots, static inline fallback, optional live refresh of same-origin JSON. Existing Pages hosting and detail pages retained.
**Tech Stack:** Python, stdlib unittest, browser ES modules, node test, GitHub Actions/Pages.
**Spec:** docs/superpowers/specs/2026-10-14-radar-rebuild.md

## Global constraints
Default 10% near-high threshold. Real dates, source, coverage; no fabricated catalysts. 252 observations for high. 50 prior sessions for volume. No forced publish of stale/bad prices. Shared workflow concurrency group wamo-data-update.

## Review focus
Stale individual row among current rows; a new-listing short history; tiny industries; deployment/network failures while old snapshot exists; market holiday/weekend.

### Task 1: Deterministic snapshot builder
Files: wamo_insights.py, tests/test_insights.py. Inputs existing extract_old_payload, expected_session and next_slot; output schema in spec. Own only these files.
- [ ] Write tests for the five review cases, 10% boundary, downside movers and official catalyst allowlist.
- [ ] Run failing tests; implement build_insights(payloads, now, previous=None) and CLI which writes JSON only.
- [ ] Preserve last valid market independently; do not discard good KR because US invalid.
- [ ] Run targeted tests, report output schema and any limits.

### Task 2: Source and publishing resilience (owner)
Files: price/universe helpers, wamo_run.py, workflow files, integration tests.
- [ ] Reproduce single-source universe failure and data-valid-but-metadata-warning case.
- [ ] Add independent fallback or validated previous universe with scope warning; continue fetching fresh prices.
- [ ] Integrate snapshot builder after price and auxiliary updates and in radar sync; preserve prior artifacts on failure.
- [ ] Verify live alternative responses, existing and new regression tests.

### Task 3: Daily radar interface
Files: radar.html, radar/*.js/css, tests/insights.test.mjs, tests UI smoke. Input exact schema above.
- [ ] Render three tabs with KR/US switch, search/sort/filter, coherent dark financial dashboard and responsive tables/cards.
- [ ] Inline snapshot loads immediately. Fetch new JSON with bounded timeout and cache fallback, no clearing on failure. Label source/date and scope.
- [ ] Near-high and industry separate from gainers/losers/volume movers; show factual signal explanation and official catalysts, no guessed causation.
- [ ] Preserve old detail URLs, update root/us entry and navigation safely.
- [ ] Verify filters, failure retention, mobile/desktop and detail links.

### Task 4: Integration review and deployment
- [ ] All Python and JS tests; fresh reviewer for cross-file issues; fix substantive issues.
- [ ] Commit/push scoped changes to branch, create reviewable PR and integrate authorized change; preserve concurrent data updates.
- [ ] Validate Pages build and deployed snapshot dates/content; report actual scope and limits.
