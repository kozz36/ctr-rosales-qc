# Spec — Run Progress Domain
**Change**: determinate-progress-bar
**Domain**: run-progress (new capability)
**Phase**: spec
**Date**: 2026-06-03

---

## Purpose

The run-progress capability defines the contract for live pipeline progress reporting during
a reconciliation run. It enables the frontend to render a determinate progress bar with
stage labels, per-item counts, elapsed time, and an ETA estimate for multi-minute pipeline
runs (493-page PDF, ~5 slow stages, 3–8 minutes wall-clock).

Progress reporting is strictly observational: it MUST NOT affect reconciliation correctness.
A run with `progress_cb = None` (CLI, test, or no-UI mode) MUST produce byte-identical
results to a run with a live progress consumer.

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
(100 for decode-identities, 100 for classify, 30 for OCR, 20 for vision, 5 for the
declared-count feeding reconcile completion)
And `stage_index` increments from 1 to 5 across the stages

**Scenario RPG-S02 — item_total uses real counts, not constants**

Given a multi-section PDF with 27 guía blocks in section A and 0 in section B
When the vision stage progress is emitted
Then `item_total = 27` (not a hardcoded value)
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

- `stage_label: str` — human-readable label of the current stage
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
contribute its full weight to `percent`.

`percent` MUST be a computed field — it MUST NOT be stored; it MUST be derived from
`stage_index`, `item_done`, and `item_total` at serialisation time.

#### Acceptance Scenarios

**Scenario RPG-S06 — percent formula verification**

Given stage_index=2, item_done=15, item_total=30, stage_total=5
When `percent` is computed
Then `percent = ((2-1) + (15/30)) / 5 * 100 = 30.0`

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
- Bar width MUST equal `percent%`.
- When no progress event has arrived yet (`percent = 0`), the bar MUST render as
  indeterminate (fallback state).

**Labels and counts**:
- Stage label MUST be visible.
- Item counts MUST be visible (e.g. "15 / 27 guías procesadas").

**Elapsed time**:
- A timer MUST display elapsed time since `started_at`, formatted as `"Xm Ys transcurrido"`.
- The timer MUST update every second while the run is active.

**ETA estimate**:
- An ETA MUST be computed as `elapsed / percent * (100 - percent)` and displayed as
  `"~Xm Ys estimado"`.
- The ETA MUST be gated: it MUST NOT be shown when `percent < 5`.

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
Then the bar renders as indeterminate (percent = 0)
And no ETA or elapsed time is shown

---

## Out of scope for this capability

- New pipeline stages, reconciliation logic, or domain model changes (beyond `RunContext.progress_cb`).
- WebSocket or SSE transport (progress rides the existing polling `GET /runs/{id}` endpoint
  in this initial implementation).
- Per-guía or per-line-item progress granularity beyond the 5 stage/item-count model.
- Changing MATCH/MISMATCH logic, material grouping, or fecha-divergence behaviour.
- Export or review domain changes.

---

## Delta — sunat-progress (2026-06-04): dynamic stage_total + "Consulta SUNAT" stage

> The requirements below ADD or MODIFY behaviour relative to RPG-001 through RPG-005 above.
> Source changes: #21 — SUNAT fetch progress instrumentation (merged to main).
> Gate: strict-TDD — 886 backend unit/targeted tests + frontend vitest passing.
> Each entry is marked [ADDED] or [MODIFIED: replaces <id>].

### RPG-006 — [MODIFIED: replaces RPG-001 stage table and RPG-004 stage_total constant] Dynamic stage_total based on sunat.enabled

**[MODIFIED: RPG-001 fixed `stage_total = 5` and listed exactly five slow pipeline stages.
RPG-004 documented `stage_total: int = 5` as a constant. Both are incorrect when SUNAT is
enabled — the pipeline gains a sixth tracked stage, "Consulta SUNAT", at stage_index 4.
`stage_total` is now DYNAMIC and MUST be computed from the runtime config, not hardcoded.]**

`stage_total` MUST be derived from `sunat.enabled` at pipeline-start time:

| `sunat.enabled` | `stage_total` | Tracked stages (in order) |
|-----------------|---------------|---------------------------|
| `false` (default) | 5 | decode-identities (1), classify (2), OCR (3), vision (4), declared-date (5) |
| `true` | 6 | decode-identities (1), classify (2), OCR (3), **Consulta SUNAT (4)**, vision (5), declared-date (6) |

The "Consulta SUNAT" stage (stage_index 4 when present) MUST track the SUNAT GRE batch
fetch. Its `item_total` MUST be the total number of guía blocks submitted for SUNAT fetch.

The `percent` formula defined in RPG-004 is unchanged; the divisor now uses the dynamic
`stage_total` value (5 or 6) rather than a constant 5.

The `GET /runs/{id}` response field `stage_total` MUST reflect the dynamic value chosen at
run start. The value MUST be stable for the lifetime of a run — it MUST NOT switch between
5 and 6 within a single run.

When `sunat.enabled = false` the run output MUST be byte-identical to the prior baseline
(`stage_total = 5`, "Consulta SUNAT" stage absent).

#### Acceptance Scenarios

**Scenario RPG-S13 — SUNAT disabled: stage_total=5, no "Consulta SUNAT" stage**

Given `sunat.enabled = false` (default config)
When the pipeline runs
Then `stage_total = 5` for all `report_progress` calls
And no progress event with `stage_label = "Consulta SUNAT"` is emitted
And `GET /runs/{id}` returns `progress.stage_total = 5`

**Scenario RPG-S14 — SUNAT enabled: stage_total=6, "Consulta SUNAT" at stage_index 4**

Given `sunat.enabled = true`
And the pipeline identifies N guía blocks for SUNAT fetch
When the SUNAT fetch stage begins
Then a `report_progress` call is emitted with `stage_label = "Consulta SUNAT"`,
  `stage_index = 4`, `stage_total = 6`, `item_done = 0`, `item_total = N`
And subsequent per-wave calls advance `item_done` from 0 to N
And the prior OCR stage used `stage_index = 3, stage_total = 6`
And the vision stage (post-SUNAT) uses `stage_index = 5, stage_total = 6`

**Scenario RPG-S15 — stage_total stable for run lifetime**

Given a run started with `sunat.enabled = true` (`stage_total = 6`)
When `GET /runs/{id}` is polled at multiple points during execution
Then every response returns `progress.stage_total = 6` (not 5)
And `stage_total` NEVER changes value within the same run

---

### RPG-007 — [ADDED] "Consulta SUNAT" stage advances per-wave; immediate 0/N emission at start

When `sunat.enabled = true`, the SUNAT fetch stage MUST emit progress in the following
pattern:

1. **Immediate 0/N emission**: at the start of the SUNAT stage, before any fetch completes,
   the pipeline MUST emit `item_done = 0, item_total = N` so the bar is not frozen.
2. **Per-wave advance**: the `SunatGreFetchPort.fetch_many` Protocol MUST accept an optional
   `on_progress(done: int, total: int) -> None` callback. The concrete adapter MUST invoke
   this callback once per completed concurrency wave (batch of concurrent fetches), passing
   the cumulative completed count as `done` and `total = N`. The pipeline wires the callback
   to `report_progress` so each wave advances `item_done`.
3. **Sequential fallback**: when the adapter processes blocks sequentially (single-worker
   path), `on_progress` MUST be called once per individual block (equivalent to wave size 1).

The `on_progress` callback parameter MUST be optional (default `None`) in the port Protocol
so callers without progress reporting (CLI, tests) can omit it without a code change.

This mechanism resolves the prior UX regression where the progress bar froze at "OCR de
guías" during the entire SUNAT fetch phase (typically the longest network-bound stage).

#### Acceptance Scenarios

**Scenario RPG-S16 — Immediate 0/N emission before first wave**

Given `sunat.enabled = true` and N = 10 guía blocks pending SUNAT fetch
When the "Consulta SUNAT" stage begins (before any fetch completes)
Then `report_progress("Consulta SUNAT", 4, 6, item_done=0, item_total=10)` is emitted first
And the UI bar does NOT stay frozen at the OCR stage level

**Scenario RPG-S17 — on_progress advances item_done per wave**

Given 10 guía blocks, concurrency wave size = 3
When wave 1 (3 blocks) completes
Then `on_progress(done=3, total=10)` is called
When wave 2 (3 more blocks) completes
Then `on_progress(done=6, total=10)` is called
And so on until all 10 are done

**Scenario RPG-S18 — on_progress=None (CLI / test path): no error**

Given `on_progress = None` is passed to `SunatGreFetchPort.fetch_many`
When the SUNAT fetch runs to completion
Then no AttributeError or TypeError is raised
And the fetch result is byte-identical to the on_progress-wired run
