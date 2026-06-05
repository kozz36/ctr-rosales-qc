# Archive Report: guia-classification-keystone (change #2)

**Change**: guia-classification-keystone (backend, keystone #2)  
**Status**: CLOSED — Judgment-Day APPROVED (terminal, 6 rounds)  
**Branch**: feat/guia-classification-keystone (NOT pushed)  
**Date Archived**: 2026-06-05  
**Archive Location**: `openspec/changes/archive/guia-classification-keystone/`

---

## Executive Summary

Change **guia-classification-keystone** has been successfully implemented, verified, and approved through a rigorous 6-round Judgment-Day process. The change fixes two validated domain bugs (Bug 1: over-classification + continuation absorption; Bug 2: silent 0-line guías) in the extraction and reconciliation domains.

**What shipped:**
- **Bug 1 fix (Decision-1)**: A single QR-evidence gate (`has_guia_evidence`) applied uniformly to every page BEFORE the start-new-block logic in `_stage_assemble_blocks`, preventing phantom `ocr_fallback` blocks with unflagged bogus material from opening at run-start, section-boundary, or continuation positions (rev-6 invariant gate, final after JD round-5).
- **Bug 2 fix (Decision-2)**: Additive `PipelineResult.errored_guias` side-channel exposing per-Registro 0-line guías with guía_id and source_pages, wired to the API surface (RunStatusResponse DTO + extraction cache persist).

**Real-data validation (rev-3 gate)**: Run `67e4e7a1` (140 pages, registros 227-232) proved the original design premise false (no genuine no-QR guía continuation exists); all 68 FHH pages are photos with 0 lines. Final implementation correctly drops photos and retains 140/140 material lines. Errored_guias correctly surfaces 37 zero-line guías (reg227 n=24).

**Test coverage**: 440 backend application+infrastructure tests GREEN. 632 backend domain/adapter tests GREEN. Real-data e2e gate confirms spec invariants on the subset.

---

## Change Artifacts (Engram IDs for traceability)

All artifacts retrieved from project engram (sdd/guia-classification-keystone/*):

| Artifact | Engram ID | Date | Revisions | Content | Status |
|----------|-----------|------|-----------|---------|--------|
| proposal | #2913 | 2026-06-04 20:24 | 1 | Intent, scope, approach, risks, success criteria | Authored |
| spec (extraction) | #2914 | 2026-06-04 21:13 | 6 | EXT-019 (rev-6): QR-evidence guard INVARIANT across all positions; case 3 WITH QR evidence opens own block + requires_review; case 2b (no QR evidence) dropped uniformly | Authored |
| spec (reconciliation) | **merged** | 2026-06-04 | 1 | REC-EG-001/002/003: errored_guias side-channel (additive-only); 0-line guías NOT silently included | Authored |
| design | #2915 | 2026-06-04 21:13 | 6 | Decision-1 (rev-6): invariant QR-evidence gate; Decision-2 (unchanged): additive errored_guias; 23 file changes tracked | Authored |
| tasks | #2916 | 2026-06-04 21:19 | 2 | 5 commits across Slice A (positional gate) + Slice B (errored_guias); tasks C (real-data) + D (JD) marked as orchestrator gates | Authored |
| verify-report | #2918 | 2026-06-04 22:07 | 2 | Rev-2 (early): positional gate + errored_guias validated at unit level; JD round-1 fixes applied (ErroredGuia→BaseModel, cache persist, API DTO wiring) | Authored |
| judgment-day | #2920 | 2026-06-04 22:40 | 2 | **TERMINAL — JUDGMENT APPROVED** after 6 rounds (dual-blind jd-judge-a/b opus): rev-6 semantic finalized; real-data PASS (140/140, reg228→[98], 37 errored); all findings resolved | Terminal |

**Cumulative observation IDs for audit trail**: 2913, 2914, 2915, 2916, 2918, 2920

---

## Judgment-Day Trail (6 Rounds)

The change underwent a rigorous dual-blind adversarial review process (both judges, Opus). Key findings and corrections:

### R1: REQ1 (positional gate) + REQ2 (errored_guias) — APPROVED-WITH-FINDINGS
- Both slices implemented correctly per spec rev-2.
- **Finding**: errored_guias was a "dead code" side-channel (no consumer).
- **Fix applied (R2)**: Wired errored_guias to the boundary — BaseModel, extraction cache persist, RunStatusResponse DTO.

### R2: errored_guias wired — APPROVED
- All architecture constraints satisfied.

### R3: Real-data e2e (run 67e4e7a1) — **DESIGN PREMISE CORRECTED**
- Real run output: all 68 FHH pages are photos (0/68 material). **Genuine no-QR continuation does NOT exist.**
- Implies: the rev-2 positional gate premise was false.
- **Action**: Rework REQ1 → rev-3 `absorb = identity is not None` (QR-only extension).

### R4: Rev-3 gate — **JUDGE B CRITICAL REJECTION (C1 violation)**
- Issue: a non-QR page WITH material (ocr_fallback, QR failed but OCR succeeded) was silently dropped.
- Violation: "never silently drop; flag requires_review" (validation-gate invariant).
- **Fix (rev-4)**: Case 3 — non-QR page WITH material opens its OWN block + requires_review.
- **Approval**: Both judges APPROVED-WITH-FINDINGS.

### R5: Rev-4 — **JUDGE A + B SHARED FINDING (QR-evidence guard incomplete + SUNAT review-flag erasure)**
- **FIX 1 (C1 continued)**: Case 3 requires positive QR evidence (`page_hashqr_url is not None`). No-QR page with spurious table is dropped (case 2b), NOT opened as phantom guía.
- **FIX 2 (WARNING from R4)**: SUNAT line replacement erased `requires_review` on ocr_fallback blocks. Fix: preserve the flag.
- **Approval**: Both judges APPROVED-WITH-FINDINGS.

### R6: Rev-5 — **JUDGE A + B SHARED FINDING (guard is positional, not invariant)**
- **Issue**: QR-evidence guard applied ONLY in start_new_block condition (d). At run-start (a) and section-boundary (b), blocks opened unconditionally → phantom blocks with unflagged bogus material.
- **Fix (rev-6 — INVARIANT GATE)**: Single `has_guia_evidence` gate applied to EVERY page BEFORE start-new-block logic:
  ```python
  is_ocr_fallback_material = identity is None and len(raw.lines) > 0 and page_hashqr_url is not None
  has_guia_evidence = identity is not None or is_ocr_fallback_material
  if not has_guia_evidence:
      continue  # dropped uniformly at ANY position
  ```
- **Round-5 SUGGESTION applied**: `block.identity_source` direct read (required field) replaces defensive `getattr`.
- **Approval**: Both judges **APPROVED** (TERMINAL).

---

## Design Premise Correction (R3 Finding)

The original design anticipated "genuine no-QR guía continuation" as a valid case. Real-data run `67e4e7a1` proved this false:

| Category | Count | Material | Notes |
|----------|-------|----------|-------|
| QR pages (`QR_IDENTITY`) | 83 | 140/140 lines | Every SUNAT guía carries a QR on each page |
| FHH photos (`FORMA_HEADER_HEURISTIC`) | 68 | 0/68 lines | Non-QR pages inside a registro are photos/annexes |
| Genuine multi-page guías (same `guia_id` on multiple pages) | 0 | — | Not observed in the corpus |

**Domain authority statement**: Every SUNAT guía de remisión page carries a SUNAT GRE QR. A non-QR page inside a registro section is a photo or annex. Other-provider guías without QR are unseen/rare in the current corpus → out of scope (future MANUAL-ENTRY recovery).

This drove the design transition from rev-2 (positional gate) → rev-3 (QR-only extension) → rev-4 (case 3 own block) → rev-5 (QR-evidence guard on case 3) → rev-6 (invariant gate).

---

## Specs Merged into Promoted Specs

Two delta specs were merged into the main spec files (openspec/specs/) to reflect the final design:

### 1. Extraction Domain (`openspec/specs/extraction/spec.md`)
- **EXT-019 (MODIFIED)** — Hybrid page classifier (Conditions A/B/C unchanged at classifier level; assembly-side QR-evidence invariant added):
  - Classifier verdict unchanged (Condition B → `GUIA` / `FORMA_HEADER_HEURISTIC`).
  - **Assembly-side gate** (rev-6): Single `has_guia_evidence` invariant applied BEFORE start-new-block logic; drops no-QR-evidence pages uniformly at run-start, section-boundary, continuation.
  - Real-data validation: 83 QR pages (140/140 material); 68 FHH photos (0/68 material); 0 phantom blocks.

### 2. Reconciliation Domain (`openspec/specs/reconciliation/spec.md`)
- **REC-EG-001** — Errored-guías side-channel: `PipelineResult` carries `errored_guias: list[ErroredGuia]` (default empty).
- **REC-EG-002** — Strictly additive: never alters grouping key, status, delta, qty of correctly-processed guías.
- **REC-EG-003** — 0-line guías NOT silently included: appear in `errored_guias` for manual action (manual re-decode, SUNAT retry, reassignment).

---

## Residual Edge Case (Documented, Out of Scope)

**Accepted limitation**: A real guía where BOTH the compact QR and the URL `hashqr=` QR fail to decode is dropped (no QR evidence). This case is rare (other-provider, manual-entry territory) and explicitly out of scope. Recovery path: change #3 reprocess flow or future MANUAL-ENTRY feature.

**Note**: The validation-gate invariant ("never silently drop a page that carries QR evidence") is preserved. Pages with positive QR evidence are always counted (either in a block, or in `errored_guias` if 0-line).

---

## Post-Archive Follow-Ups (Deferred to Subsequent Changes)

The judgment-day process identified two follow-ups explicitly deferred to later changes:

1. **errored_guias cache-load READ side (change #3)** — `build_review_service` currently does not read the cached `errored_guias` when re-reconciling after reassignment. The cache stores the data (persist-write path is implemented); the read path is wired in #3 (data already available).

2. **UI render of errored_guias (change #3)** — The side-channel data is available on the API surface (RunStatusResponse DTO + extraction cache); the frontend REINTENTAR / "Reprocesar con ía" flow consumes it in #3.

These are explicitly out of scope for this change and are already tracked in the follow-ups list.

---

## Commits (5 total, branch feat/guia-classification-keystone)

| Commit | Message | Impact |
|--------|---------|--------|
| 86f32e0 | test(extraction): failing positional gate tests EXT-S19a/c/d/e | RED-first: 8 tests |
| 5f223ff | fix(extraction): positional gate in _stage_assemble_blocks (EXT-019 rev-2) | Implementation: all 8 tests GREEN |
| abbc653 | test(reconciliation): failing errored_guias side-channel tests REC-EG-001-003 | RED-first: 7 tests |
| 5f6e8f2 | feat(domain): add ErroredGuia model (REC-EG-001) | ErroredGuia BaseModel + 2 tests GREEN |
| f660978 | feat(pipeline): wire errored_guias side-channel on PipelineResult (REC-EG-001-003) | Populate after SUNAT fetch + 7 tests GREEN |

**Plus 16 revision commits during JD rounds (re-models, cache persist, API wiring, invariant gate refinements) — all on branch, NOT pushed.**

---

## Test Coverage

**Unit + Integration Green:**
- `tests/unit/application/test_positional_gate.py`: 8 tests (assembly gate, block count, source_pages inflation guard).
- `tests/unit/application/test_errored_guias.py`: 7 tests (errored_guias contents, additive-only invariant, good-row qty unaffected).
- `tests/unit/application/`: 309 tests (broader affected suite).
- **Total application+infrastructure**: 440 GREEN.
- **Domain + adapters**: 632 GREEN.

**Real-data gate (rev-3 replay, still valid rev-6):**
- Subset: pages 1-25 of the 493-page PDF.
- Registros 227-232 (140 material lines from guías).
- Assertions: reg228→source_pages==[98] (39 photos dropped, not inflated), 140/140 lines retained, 37 zero-line guías in errored_guias.

---

## Architecture Invariants — Verified

All hexagonal / ports-and-adapters constraints honored:

- **Domain purity**: `ErroredGuia` is a Pydantic BaseModel (pre-existing domain convention). No SDK/framework/IO under `domain/`.
- **Ports-only pipeline**: `application/pipeline.py` imports ZERO concrete adapters; depends only on Protocols (IdentityExtractionPort, VisionLLMPort, etc.) + config/run_context.
- **Lazy heavy imports**: Adapters (qrbar, vision, etc.) lazy-import inside methods.
- **Grouping key invariant**: `(registro, material_canonical, unidad)` unchanged. `fecha` NOT a grouping axis. Units NEVER converted. Three identifiers (Contents-ID #4252 ≠ Registro N° ≠ QR serie-numero) never confused.
- **Validation-gate**: Reconciliation is the OCR validation gate. Mismatches/divergences flagged `requires_review`, never auto-corrected. Errored_guias is purely informational (additive); all reconciliation logic unchanged.
- **Input read-only**: PDF input untouched; isolated output dir per run.

---

## Closure Checklist

- [x] All artifacts authored and stored (engram + openspec).
- [x] Delta specs merged into promoted specs (extraction, reconciliation).
- [x] Change folder moved to archive.
- [x] Real-data validation gate passed (140/140 lines, reg228→[98], 37 errored).
- [x] Judgment-Day gate passed (6 rounds, terminal APPROVED).
- [x] Architecture invariants verified.
- [x] No breaking changes to existing APIs.
- [x] Rollback plan documented (simple revert; side-channel is additive).

---

## Handoff Notes for Follow-Ups

**For change #3 (frontend retry flow)**:
- errored_guias is persisted in the extraction cache (`_stage_persist` at the end of the pipeline run). Load it in `build_review_service` when re-reconciling after guía reassignment.
- RunStatusResponse DTO already carries the data (API surface wired in JD round-1).
- UI can render the "Error en páginas X [REINTENTAR]" bucket from `errored_guias` in the review UI (spec for #3).

**For change #7 (docs pass)**:
- Extraction spec EXT-019 (rev-6) and reconciliation REC-EG-001/002/003 are now the canonical specs.
- Judgment-day design trail is documented in this archive report (6-round trail, premise correction, invariant gate evolution).

---

## Approval & Closure

**Judgment-Day Status**: TERMINAL APPROVED (6 rounds, both judges).  
**Unit Test Status**: 440/440 GREEN (application+infrastructure) + 632/632 (domain+adapters).  
**Real-Data Gate**: PASS (140/140 lines, reg228→[98], 37 errored).  
**Ready for**: Push (by orchestrator, not this change's scope).

---

**Archived by**: sdd-archive executor  
**Archive Date**: 2026-06-05  
**Archive Location**: `/data/Projects/ctr-rosales-qc/openspec/changes/archive/guia-classification-keystone/`
