# Spec — Run Progress
**Change**: determinate-progress-bar
**Domain**: run-progress (NEW capability)
**Phase**: spec (archived)
**Date**: 2026-06-03

---

## Purpose

Define the contract for live pipeline progress reporting during a reconciliation run.
The capability allows the frontend to render a determinate progress bar with stage labels,
per-item counts, elapsed time, and an ETA estimate for multi-minute runs.

This is a **new capability spec**. Requirements below are the initial definition;
each entry is marked `[ADDED]`.

---

## Requirements

### RPG-001 — [ADDED] Backend MUST emit live progress during pipeline run

During an active reconciliation run, the backend MUST report progress for the 5 slow
pipeline stages:

| Stage index | Stage name | `item_total` basis |
|-------------|------------|--------------------|
| 1 | decode-identities | `page_count` (total PDF pages) |
| 2 | classify | `page_count` |
| 3 | ocr | `len(guia_pages)` — actual guía-classified page count |
| 4 | vision | `len(guia_blocks)` — actual guía block count |
| 5 | declared-date | `len(registros)` — actual registro count |

For each stage, the pipeline MUST call `report_progress(stage_label, stage_index,
stage_total=5, item_done, item_total)` after processing each item (or batch of items).

`item_total` MUST be derived from the REAL count of items determined at stage start
(page_count, guia page list length, block list length, registro list length). It MUST
NOT be a hardcoded constant.

#### Acceptance Scenarios

**Scenario RPG-S01 — Progress events emitted for all 5 stages**

Given a pipeline run on a PDF with 100 pages, 30 guía pages, 20 guía blocks, 5 registros
When the pipeline executes
Then `report_progress` is called with `stage_total=5` for each of the 5 slow stages
And `item_total` in each call matches the actual count for that stage
(100 for decode-identities, 100 for classify, 30 for OCR, 20 for vision, 5 for declared-date)
And `stage_index` increments from 1 to 5 across the stages

**Scenario RPG-S02 — item_total uses real counts, not constants**

Given a multi-section PDF with 27 guía blocks in section A and 0 in section B
When the vision stage progress is emitted
Then `item_total = 27` (not 50 or any hardcoded value)
And `item_done` counts from 0 to 27 as each block is processed

---

### RPG-002 — [ADDED] Progress reporting MUST be observational-only

Progress reporting MUST NOT alter reconciliation results. A run executed with
`progress_cb = None` MUST produce **byte-identical** reconciliation output compared to
the same run with `progress_cb` set to a non-None callable.

Progress reporting MUST NOT:
- Change any domain value, quantity, or MATCH/MISMATCH status.
- Add or remove items from any group, collection, or list.
- Cause the pipeline to skip, reorder, or re-execute any stage.

The `report_progress` function MUST swallow exceptions thrown by `progress_cb`. A broken
or raising consumer MUST NOT abort the run or change its outcome.

#### Acceptance Scenarios

**Scenario RPG-S03 — Byte-identical result with and without progress_cb**

Given two identical pipeline runs on the same PDF and config
Where run A has `progress_cb = None` and run B has `progress_cb = <recording callable>`
When both runs complete
Then the reconciliation rows, MATCH/MISMATCH statuses, quantities, and all domain fields
are equal between run A and run B

**Scenario RPG-S04 — Raising progress_cb does not abort the run**

Given `progress_cb` raises `RuntimeError("test")` on every call
When the pipeline runs
Then the run completes normally (no exception propagated)
And the reconciliation result is correct (byte-identical to `progress_cb=None` run)
And the error is logged (not silently discarded)

---

### RPG-003 — [ADDED] Dependency Inversion: progress_cb injected via RunContext

`RunContext` MUST expose a `progress_cb: Callable[[...], None] | None` field (default None).
The `application/` layer (pipeline) MUST receive `progress_cb` only via `RunContext`
injection — it MUST NOT import any `infrastructure/` module, transport layer, or concrete
progress implementation.

The concrete `progress_cb` (e.g., a function that updates in-memory run state readable
by the API route) MUST be provided by the infrastructure layer when creating the run.

No new domain Port is introduced for progress reporting.

#### Acceptance Scenario

**Scenario RPG-S05 — application/ has no import of infrastructure/**

Given the full source of `application/pipeline.py` and all modules in `application/`
When statically analysed for imports
Then no import from `infrastructure/` (including `api/`, `ws/`, any transport layer)
appears in any `application/` module
And `progress_cb` is the only coupling between the pipeline and the progress consumer

---

### RPG-004 — [ADDED] GET /runs/{id} MUST expose progress fields

The `GET /runs/{id}` response MUST include a `progress` object with:

- `stage_label: str` — human-readable label of the current stage (e.g. "Clasificando páginas")
- `stage_index: int` — 1-indexed current stage number (1–5 when running, 5 when complete)
- `stage_total: int = 5` — constant; total number of tracked stages
- `item_done: int` — number of items completed in the current stage
- `item_total: int` — total items in the current stage
- `percent: float` — computed value (see formula below), clamped to [0, 100]
- `started_at: datetime | None` — ISO-8601 UTC timestamp of when the run started

**`percent` formula**:

```
percent = ((stage_index - 1) + (item_done / item_total if item_total > 0 else 1)) / stage_total * 100
```

Clamped: `max(0, min(100, percent))`. A completed stage (`item_done == item_total`) MUST
contribute its full weight to `percent`. A run that has not yet started MUST return
`percent = 0`.

`percent` MUST be a computed field — it MUST NOT be stored; it MUST be derived from
`stage_index`, `item_done`, and `item_total` at serialisation time.

#### Acceptance Scenarios

**Scenario RPG-S06 — percent formula verification**

Given stage_index=2, item_done=15, item_total=30, stage_total=5
When `percent` is computed
Then `percent = ((2-1) + (15/30)) / 5 * 100 = (1 + 0.5) / 5 * 100 = 30.0`

**Scenario RPG-S07 — item_total=0: treated as stage complete**

Given stage_index=3, item_done=0, item_total=0, stage_total=5
When `percent` is computed
Then `percent = ((3-1) + 1) / 5 * 100 = 60.0` (empty stage treated as instantly complete)

**Scenario RPG-S08 — started_at present on an active run**

Given a run that started at 2026-06-03T10:00:00Z
When `GET /runs/{run_id}` is called during execution
Then the response includes `progress.started_at = "2026-06-03T10:00:00Z"`

---

### RPG-005 — [ADDED] Frontend MUST render determinate bar with a11y, elapsed time, and ETA

The `RunProgress.vue` component MUST render a determinate progress bar with the following
behaviour:

**Determinate bar**:
- Uses an HTML `<progress>` element or a `<div role="progressbar">` with `aria-valuenow`,
  `aria-valuemin=0`, `aria-valuemax=100` attributes populated from `percent`.
- Bar width MUST equal `percent%` (CSS `width: {percent}%` or equivalent).
- When no progress event has arrived yet (`percent = 0`), the bar MUST render as
  indeterminate (fallback state).

**Labels and counts**:
- Stage label MUST be visible (e.g. "Procesando visión OCR").
- Item counts MUST be visible (e.g. "15 / 27 guías procesadas").

**Elapsed time**:
- A timer MUST display elapsed time since `started_at`, formatted as `"Xm Ys transcurrido"`.
- The timer MUST update every second while the run is active.

**ETA estimate**:
- An ETA MUST be computed as `elapsed / percent * (100 - percent)` and displayed as
  `"~Xm Ys estimado"`.
- The ETA MUST be gated: it MUST NOT be shown when `percent < 5` (to avoid erratic estimates
  in the very first stage).

#### Acceptance Scenarios

**Scenario RPG-S09 — Determinate bar renders with correct aria attributes**

Given `percent = 42`
When `RunProgress.vue` renders
Then the bar element has `aria-valuenow="42"`, `aria-valuemin="0"`, `aria-valuemax="100"`
And the bar width is 42% of its container

**Scenario RPG-S10 — ETA hidden below 5 percent**

Given `percent = 3`
When `RunProgress.vue` renders
Then no ETA estimate is shown

**Scenario RPG-S11 — ETA shown at and above 5 percent**

Given `percent = 30` and `elapsed = 90s`
When `RunProgress.vue` renders
Then an ETA string is shown (approximately `~3m 30s estimado`)
And the elapsed string is shown (`1m 30s transcurrido`)

**Scenario RPG-S12 — Fallback to indeterminate before first event**

Given the run has just started and no progress event has been received yet
When `RunProgress.vue` renders
Then the bar renders as indeterminate (e.g. no `aria-valuenow` value, or value = 0)
And no ETA or elapsed time is shown

---

## Out of scope for this capability

- New pipeline stages, reconciliation logic, or domain model changes (beyond `RunContext.progress_cb`).
- WebSocket or SSE transport (progress is surfaced via `GET /runs/{id}` polling in this change).
- Per-guía or per-line-item progress granularity beyond the 5 stage/item-count model.
- Changing MATCH/MISMATCH logic, material grouping, or fecha-divergence behaviour.
