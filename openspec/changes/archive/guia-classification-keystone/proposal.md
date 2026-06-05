# Proposal: Guía Classification Keystone (backend)

## Intent

On real run `67e4e7a1` the engineer found two domain bugs ("guías que no suman / registros sin guías"), both validated against the real PDF:

- **Bug 1 — over-classification + continuation absorption.** Classifier Condition B (`FORMA_HEADER_HEURISTIC`, `classifier.py:324-335`: body `<= _FORMA_HEADER_MAX_CHARS` (200) AND `image_dominant`, no QR) tags non-guía image pages (photos/annexes) as `GUIA`. Only `kind=="GUIA"` enters `raw_guias` (`pipeline.py:789`); `_stage_assemble_blocks` then appends them as **continuation pages** into the preceding QR guía block (continuation branch `pipeline.py:952-959`), inflating `source_pages` (e.g. reg228 spans pp98-137 vs real 85-99).
- **Bug 2 — silent 0-line guías.** Material comes from SUNAT via `gre_hashqr_url`. The compact identity QR decodes, but the URL-variant QR sometimes fails → no SUNAT fetch → 0 lines (OCR off). The guía is silently included with 0 lines → declared side undercounted. Reconciliation math is correct; the input is empty. Sub-classes: **TRANSIENT** (re-decode recovers, reg232 p8/p10) vs **SYSTEMATIC** (URL QR genuinely absent, reg227/reg228 p86).

This keystone is the backend fix that the staged frontend retry flow (#3) builds on.

## Scope

### In Scope
- **(a) Stop absorption**: misclassified Condition-B image pages are marked **`IGNORED`** (enum already exists in `classifier.py:45`), never `GUIA`, so they never enter `raw_guias`/`assemble_blocks`. Preserve genuine multi-page-guía continuation (e.g. T112-0065421 pp151-152).
- **(b) Errored-guías side-channel**: expose, per Registro, structured "0-line / unprocessed pages" data (guía id, page numbers, transient|systematic if cheaply derivable) on the pipeline output so #3's UI can render "Error en páginas X [REINTENTAR]". Additive only.

### Out of Scope
- **#3** staged frontend flow (REINTENTAR / "Reprocesar con IA", vision/OCR material path for systematic cases).
- **#7** openspec documentation pass of the whole feature.
- Any reconciliation/grouping/quantity logic change.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `extraction`: page classification gains an explicit non-guía-image disposition (Condition B → `IGNORED`, not `GUIA`); block assembly no longer absorbs non-guía pages.
- `reconciliation`: pipeline output carries an additive per-Registro errored/unprocessed-pages side-channel.

## Approach

1. **Classifier**: change Condition B verdict from `GUIA` to `IGNORED` (both `classify` path `:324-335` and `_classify_from_hybrid` path `:477-485`). Classifier stays PURE (plain booleans in, `PageClassification` out). Genuine multi-page guías are unaffected — they continue via QR identity (Condition A) or `GUIA DE REMISION` text (Condition C), not Condition B.
2. **Side-channel**: detect blocks/guías that resolved to 0 lines after SUNAT fetch; collect `(registro, guia_id, source_pages, kind)` into a new additive field on `PipelineResult` (mirrors how `warnings` already rides the result, `pipeline.py:227`). Optionally tag transient vs systematic via a cheap re-decode probe of `gre_hashqr_url` through the existing `IdentityExtractionPort` — only if it stays a port call (no domain coupling); otherwise expose without the sub-class and defer the probe to #3.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `domain/classifier.py` | Modified | Condition B → `IGNORED` (two code paths) |
| `application/pipeline.py` | Modified | `PipelineResult` additive errored-guías field; populate after `_stage_sunat_fetch` |
| `domain/models.py` | Modified (maybe) | optional typed struct for an errored-guía entry |
| `domain/ports.py` `ReportPort` | Possibly | only if errored data is surfaced through export |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Regression to genuine multi-page continuation | Med | Continuation relies on QR/text guías, not Condition B; add a regression test for pp151-152 |
| Breaking the 886 passing tests (classifier/grouping/pipeline is most sensitive) | Med | Strict-TDD: failing test first; additive side-channel never touches key/status/delta/qty |
| Mislabel a real header-only guía as IGNORED | Low | Condition A (QR) and C (text) still classify real guías; only QR-less image pages change |
| Transient/systematic probe adds vendor/IO coupling | Low | Keep probe behind `IdentityExtractionPort`; if not cheap, defer to #3 |

## Rollback Plan

Revert the classifier Condition-B verdict to `GUIA` and drop the additive `PipelineResult` field. No schema/migration; side-channel is additive so removing it cannot corrupt existing consumers.

## Dependencies

- None new. Blocks #3 (frontend retry) and #7 (docs).

## Constraints (hard invariants — MUST NOT violate)

- Hexagonal: domain core PURE — no SDK/framework/IO under `domain/`; classifier stays boolean-in/value-out.
- `application/pipeline.py` depends ONLY on ports + config/run_context — zero concrete-adapter imports.
- Adapters lazy-import heavy deps inside methods.
- Grouping key stays `(registro, material_canonical, unidad)` — `fecha` NEVER a grouping axis; units never converted.
- Three identifiers never confused (`#4252` ≠ Registro N° ≠ QR serie-numero).
- Reconciliation/divergence is the validation gate — flagged `requires_review`, never auto-corrected; input PDF read-only.
- The errored-guías exposure is an ADDITIVE side-channel: MUST NOT alter group key, status, delta, or quantities of correctly-processed guías.

## Success Criteria

- [ ] Condition-B non-guía image pages are `IGNORED`, never absorbed; `source_pages` of affected guías match their real ranges.
- [ ] Genuine multi-page guías (pp151-152) still assemble as one block.
- [ ] 0-line guías appear in the per-Registro errored side-channel with page numbers (and transient|systematic when cheap).
- [ ] All correctly-processed guías keep identical key/status/delta/qty (additive only).
- [ ] Strict-TDD failing-first tests + full suite green; real-data check on run `67e4e7a1` subset.

## Open Questions

- Q1: Should transient-vs-systematic classification land in #2 (cheap re-decode probe via `IdentityExtractionPort`) or be deferred entirely to #3? Brief says "ideally ... if cheaply derivable" — recommend deferring the probe to #3 and exposing only `(registro, guia_id, source_pages)` in #2 unless the orchestrator wants the probe now.
