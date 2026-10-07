# WAMO Resilience Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Recover failed/missed refreshes without duplicate successful collection or false freshness.
**Architecture:** Preserve existing collectors and Pages. Improve the orchestration boundary, publication deduplication, and status UI.
**Tech Stack:** Python, unittest, vanilla JS, node:test, GitHub Actions.
**Spec:** docs/superpowers/specs/2026-10-07-resilience-design.md

## Global Constraints
- Keep verification thresholds and public data allowlists.
- Retain last good data; do not change credentials or sharing.
- Use the existing repository and bounded provider requests.

## Review Focus
- Interrupted runner must be retryable, and auxiliary failure must not undo price success.
- Holidays, DST and next-session intraday data must remain guarded.
- Remote writes must not overwrite concurrent work.
- Repeated upstream waiting must not churn catalog/Pages commits.
- UI network failure and cached page must never certify fresh prices.

### Task 1: Orchestration recovery
- [x] Add regression cases for failed retry, cooldown, successful-session dedup, interrupted run and auxiliary isolation; observe failures.
- [x] Implement retry policy/checkpoint/finalizer in wamo_run.py, watchdog cron and consistent timeouts in main.yml.
- [x] Run unittest suite and record results.
### Task 2: Efficient publication and collection
- [x] Add tests for unchanged catalog/bridge and deterministic provider errors; observe failures.
- [x] Deduplicate catalog output, diagnose radar readiness without leaking private errors, avoid repeated deterministic retries.
- [x] Verify full suite and inspect real payload compatibility.
### Task 3: Status and delivery
- [x] Add UI cases for running/interrupted/newer page states.
- [x] Add timeout and periodic visible-tab status checks; preserve good payload and offer reload on mismatch.
- [ ] Run Python/JS/browser checks, independent code review, publish and inspect actual Actions/Pages state.

## Execution record
- Existing clean clone at ce1c8bd, work isolated on codex/wamo-resilience-20261007.
- Initial test attempt blocked by missing local bs4; installing declared dependencies.
- User granted discretion; implementation proceeds without repetitive design approval.

- Baseline: 66 Python tests passed after dependencies installed.
- Added regressions were observed failing before changes: cooldown/recovery/checkpoint, unchanged catalog, short history retries, missing radar credential status, auxiliary-only recovery, long-holiday selection, running/polling UI and upstream waiting reason.
- Independent review: concurrent-push dirty-movers and >7-day holiday cases found; fixed with per-market finalized publication and calendar session lookup. Separate auxiliary recovery added without price recollection.
- Local browser download returned an invalid archive. Browser smoke will run in GitHub CI; no local browser pass claimed.
- Final publication validation remains tracked in the task result.

- Local final: Python 74/74, JS 35/35. Real temporary Git remote test passed: concurrent unrelated commit preserved, both markets published, no dirty tracked outputs.
- First GitHub CI: dashboard browser 6/6 passed; discovery test also fails on unchanged baseline because fresh_radar retains the live FAILED status. Fixed at test-only routing boundary; production snapshots and validation remain unchanged. Awaiting rerun.
