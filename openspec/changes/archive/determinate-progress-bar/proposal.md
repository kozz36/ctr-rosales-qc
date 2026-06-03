# Proposal — determinate-progress-bar

**Change**: `determinate-progress-bar`
**Phase**: archived (implemented & merged)
**Artifact store**: hybrid (engram + openspec)
**Date**: 2026-06-03
**Gate**: ctr-reviewer APPROVED + SA-5 Playwright runtime validation.
**Status**: Implemented & merged to main via PR #6. 895 backend unit + 199 frontend vitest.

---

## 1. Intent

### Problem

The pipeline progress bar was indeterminate (a cycling bar with no percentage). The operator
running a multi-minute background pipeline on a 493-page PDF had no visibility into:
- Which stage the pipeline was currently in.
- How far through the current stage it had progressed (e.g., "processing guía 15 of 27").
- How long the run had been going and when it might finish.

This made it impossible to distinguish a stalled run from a slow-but-working one, and
prevented the operator from making time estimates for manual review scheduling.

### Why now

The change was listed as a deferred follow-up after the rev-3 close-out
(`docs/HANDOFF.md §follow-ups: determinate progress bar UX`). Once the pipeline stabilised
(R8/R9/R10 merged), implementing a clean progress layer without touching reconciliation logic
became low-risk and high operational value.

### Success looks like

- During a live run, the frontend progress bar shows a determinate percentage.
- The stage label displays the current phase (e.g. "Clasificando páginas", "Leyendo fechas guías").
- Per-item counts show progress within the stage (e.g. "15 / 27 guías procesadas").
- Elapsed time ticks in real time. An ETA estimate appears once the bar has progressed enough
  to be meaningful (>= 5% complete).
- If progress_cb is None (e.g. tests, CLI mode), the pipeline produces byte-identical results
  with no behavioral difference — progress reporting is strictly observational.
- No console errors in the browser during the run.

---

## 2. Scope

### In scope

**Backend — `RunContext.progress_cb` (Dependency Inversion)**:
- `RunContext` receives an injected `progress_cb: Callable[[ProgressEvent], None] | None`.
- `application/` MUST NOT import `infrastructure/`; progress reporting is via the injected
  Callable — the pipeline never knows the transport (WebSocket, SSE, etc.).

**Backend — 5 slow stage reporters**:
- `decode-identities`, `classify`, `ocr`, `vision`, `declared-date` — these MUST report
  `(stage_label, stage_index: int, stage_total=5, item_done: int, item_total: int)`.
- `item_total` derives from REAL counts (page_count, len(guia_pages), len(blocks),
  len(registros)) computed once per stage start — not a hardcoded constant.

**Backend — `GET /runs/{id}` progress field**:
- The run response MUST expose `progress.percent` (computed:
  `((stage_index-1) + (item_done/item_total if item_total else 1)) / stage_total`, clamped 0–100)
  and `progress.stage_label`, `progress.item_done`, `progress.item_total`, `progress.started_at`.

**Backend — `report_progress` safety**:
- `report_progress` MUST swallow callback exceptions so a broken progress consumer never aborts
  a run.

**Frontend — `RunProgress.vue`**:
- Renders a determinate bar (HTML `<progress>` or equivalent with `aria-valuenow`, `aria-valuemax`
  attributes; `width = percent%`).
- Shows stage label + item counts ("X / Y").
- Shows elapsed time ("Xm Ys transcurrido") updated every second.
- Shows ETA ("~Xm Ys estimado") gated at `percent >= 5` to avoid noise in early stages.
- Falls back to indeterminate (`percent = 0`) until the first progress event arrives.

### Out of scope

- New pipeline stages or behavioral changes to reconciliation, extraction, or vision.
- Changing reconciliation grouping logic, domain models (beyond `RunContext`), or ports.
- WebSocket protocol changes (progress rides the existing polling `GET /runs/{id}` endpoint).
- Export changes.
- Changing any material matching or fecha-divergence logic.

---

## 3. Approach

`RunContext` is the pipeline's existing configuration/run context object. Adding `progress_cb`
as an injected Callable follows the Dependency Inversion principle already used for
`VisionLLMPort`, `ExtractionPort`, etc. The infrastructure layer (API route handler) injects
a concrete Callable that updates the run's in-memory progress state, which `GET /runs/{id}`
polls. The pipeline never imports the route handler or WebSocket logic.

`percent` is a computed property on the progress state object, not stored — it is derived
from `stage_index`, `item_done`, and `item_total` at read time.

---

## 4. Risks & Mitigations

| Risk | Trigger | Impact | Mitigation |
|------|---------|--------|------------|
| Progress callback throws → run aborts | Consumer raises exception | Run aborts mid-pipeline | `report_progress` wraps `progress_cb` call in try/except; exception logged, run continues |
| `item_total = 0` → division-by-zero in percent formula | Stage with zero items | UI shows incorrect percent | Formula uses `item_done / item_total if item_total else 1` (treat zero-item stage as complete) |
| Progress state shared across concurrent runs | Two runs update the same progress dict | Cross-run pollution | Progress state is keyed by run_id; isolated per run |
| SA-5: green unit tests do not prove UI behavior | Mocked frontend tests pass, bar broken | Operator sees no progress | Playwright runtime validation REQUIRED before marking done (SA-5) |

---

## 5. Rollback / Abort plan

`progress_cb=None` is the default; the pipeline is byte-identical when None. Removing the
UI progress component leaves the old indeterminate bar. Reversible with zero data impact.
