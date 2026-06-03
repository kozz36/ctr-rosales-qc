# Tasks — determinate-progress-bar

**Change**: `determinate-progress-bar` · **Phase**: tasks (archived) · **Store**: hybrid · **Date**: 2026-06-03
**Branch**: `feat/rev2-identity-domain`
**Strict TDD**: active
**Gate**: ctr-reviewer APPROVED + SA-5 Playwright runtime validation (RunProgress.vue, live run observed).
**Status**: Implemented & merged to main via PR #6. 895 backend unit + 199 frontend vitest.

All tasks are marked `[x]` (implemented).

---

## Slice PB-A — Backend: RunContext progress_cb + report_progress

### [x] PB.1 — `RunContext.progress_cb` injection + `report_progress` safety wrapper

**Spec refs**: RPG-002, RPG-003.
**Depends on**: existing `application/run_context.py` (or equivalent `RunContext` definition).

**Deliverables**:
- Add `progress_cb: Callable[[ProgressEvent], None] | None = None` to `RunContext`.
- Add `report_progress(ctx: RunContext, event: ProgressEvent) -> None` helper that calls
  `ctx.progress_cb(event)` inside a try/except; logs exception, never re-raises.
- `ProgressEvent` is a lightweight dataclass or TypedDict in `application/` — no
  infrastructure-layer types.

**Tests** (`backend/tests/unit/application/`):
- `RunContext` with `progress_cb=None`: `report_progress` does not raise.
- `RunContext` with `progress_cb` that raises: `report_progress` returns normally, exception logged (RPG-S04).
- `RunContext` with a recording `progress_cb`: `report_progress` calls it with the event.

**Commit message**: `feat(application): add RunContext.progress_cb injection + safe report_progress wrapper (RPG-003)`

---

## Slice PB-B — Backend: 5 stage reporters in pipeline

### [x] PB.2 — 5 slow pipeline stages emit progress events with real-count item_total

**Spec refs**: RPG-001, RPG-002.
**Depends on**: PB.1 (`RunContext.progress_cb` + `report_progress`).

**Deliverables** (`backend/src/reconciliation/application/pipeline.py`):
- `decode-identities` stage: `report_progress(ctx, stage_label="Decodificando identidades", stage_index=1, stage_total=5, item_done=n, item_total=page_count)` per page.
- `classify` stage: same pattern, `stage_index=2`, `item_total=page_count`.
- `ocr` stage: `stage_index=3`, `item_total=len(guia_pages)`.
- `vision` stage: `stage_index=4`, `item_total=len(guia_blocks)`.
- `declared-date` stage: `stage_index=5`, `item_total=len(registros)`.
- All `item_total` values computed from real runtime counts (not constants).

**Tests** (update `backend/tests/unit/application/test_pipeline.py`):
- Pipeline with recording `progress_cb`: verifies events emitted for all 5 stages with correct `stage_index` progression.
- `item_total` for OCR stage = len of actual guia_pages list (not hardcoded).
- Pipeline with `progress_cb=None`: reconciliation result byte-identical (RPG-S03).
- Pipeline where `progress_cb` raises every call: run completes normally, result correct (RPG-S04).
- No `infrastructure/` import appears in any `application/` module (RPG-S05).

**Commit message**: `feat(pipeline): emit progress events for 5 slow stages with real-count item_total (RPG-001)`

---

## Slice PB-C — Backend: GET /runs/{id} progress field

### [x] PB.3 — `GET /runs/{id}` response exposes `progress` object with `percent` + `started_at`

**Spec refs**: RPG-004.
**Depends on**: PB.2 (progress events flowing; in-memory run state updated by progress_cb).

**Deliverables**:
- In-memory run state (per-run dict keyed by `run_id`) stores latest `stage_index`, `stage_label`,
  `item_done`, `item_total`, `started_at`.
- `GET /runs/{id}` response schema includes a `progress` field with all RPG-004 fields.
- `percent` MUST be a computed property (`@property` or `@computed_field`) — NOT stored.
- `percent` formula: `((stage_index-1) + (item_done/item_total if item_total else 1)) / stage_total * 100`, clamped [0, 100].

**Tests** (`backend/tests/unit/infrastructure/test_api_routes.py`):
- `percent` formula verification: stage_index=2, item_done=15, item_total=30 → 30.0% (RPG-S06).
- `item_total=0` → stage treated as complete, no ZeroDivisionError (RPG-S07).
- `started_at` present on active run (RPG-S08).
- `percent` is NOT stored in the state dict (verified by asserting the dict keys).

**Commit message**: `feat(api): expose progress.percent + started_at on GET /runs/{id} (RPG-004)`

---

## Slice PB-D — Frontend: RunProgress.vue

### [x] PB.4 — `RunProgress.vue`: determinate bar + stage label + elapsed + ETA

**Spec refs**: RPG-005.
**Depends on**: PB.3 (API shape stable).

**Deliverables** (`frontend/src/features/review/RunProgress.vue` or equivalent):
- `<progress>` or `<div role="progressbar">` with `aria-valuenow={percent}`, `aria-valuemin=0`,
  `aria-valuemax=100`; bar width = `percent%`.
- Stage label + item counts visible.
- Elapsed time: seconds timer updated every second; formatted `"Xm Ys transcurrido"`.
- ETA: `elapsed / percent * (100 - percent)`, formatted `"~Xm Ys estimado"`, gated at `percent >= 5`.
- Fallback: when `percent = 0`, renders as indeterminate (no aria-valuenow value or value = 0).

**Tests** (`frontend` — `cd frontend && npm run test`):
- `aria-valuenow="42"`, `aria-valuemin="0"`, `aria-valuemax="100"` at `percent=42` (RPG-S09).
- ETA hidden when `percent < 5` (RPG-S10).
- ETA visible when `percent >= 5` (RPG-S11).
- Indeterminate fallback when `percent = 0` (RPG-S12).
- No console errors during component mount.

**SA-5 gate** (mandatory before "done"):
- Playwright: upload real PDF → observe RunProgress.vue in-browser during live run →
  confirm stage label updates, percent increases, elapsed ticks, ETA appears after 5%.
- Zero browser console errors during the observed run.

**Commit message**: `feat(frontend): RunProgress.vue — determinate bar + stage label + elapsed + ETA (RPG-005)`
