# Status — determinate-progress-bar

**Change**: `determinate-progress-bar`
**Branch**: `feat/rev2-identity-domain`
**Date**: 2026-06-03

## Status

Implemented & merged to main via **PR #6**.

**Gate**: ctr-reviewer APPROVED + SA-5 Playwright runtime validation.
- SA-5: RunProgress.vue validated against the running app (stage labels, percent, elapsed,
  ETA visible; zero browser console errors).

**Test counts**: 895 backend unit tests + 199 frontend vitest passing.

## Key artifacts

- `backend/src/reconciliation/application/run_context.py` — `RunContext.progress_cb` injection
- `backend/src/reconciliation/application/pipeline.py` — 5 stage reporters + `report_progress`
- `backend/src/reconciliation/infrastructure/api/routes.py` + `schemas.py` — `GET /runs/{id}` progress field
- `frontend/src/features/review/RunProgress.vue` — determinate bar component

## New requirement IDs (new capability)

**New capability spec: `openspec/specs/run-progress/spec.md`**

- **RPG-001** — Backend MUST emit live progress events for 5 slow stages with real-count item_total
- **RPG-002** — Progress reporting MUST be observational-only (byte-identical with progress_cb=None)
- **RPG-003** — Dependency Inversion: progress_cb injected via RunContext; application/ has no infrastructure/ import
- **RPG-004** — GET /runs/{id} MUST expose progress fields including computed percent + started_at
- **RPG-005** — Frontend MUST render determinate bar with a11y, elapsed time, and ETA
