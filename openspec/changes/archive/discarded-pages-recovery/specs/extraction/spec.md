# Spec — Extraction Domain (Delta)
**Change**: discarded-pages-recovery (SDD#2)
**Domain**: extraction (delta against promoted spec at `openspec/specs/extraction/spec.md` + SDD#1 delta)
**Phase**: spec
**Date**: 2026-06-11

---

## Purpose

This document is an additive delta to the promoted extraction spec and the SDD#1 extraction
delta. It specifies the behavioural requirements for the backend root fix: replacing the
silent drop of GUIA-classified pages with no QR evidence with an explicit *discarded entry*
that surfaces in `PipelineResult` and the review API.

All existing extraction requirements (EXT-001 through EXT-033) remain in force unless
explicitly modified below.

**Non-goal boundary inherited from the proposal**: this delta does NOT change classification
(EXT-001/EXT-019), the QR-evidence gate's blocking semantics (EXT-NG-001 / rev-6 invariant),
or any block-grouping logic. The gate's blocking semantics are UNCHANGED — a no-evidence page
never opens or extends a block. What changes is that the drop is no longer invisible.

---

## What MUST be true after this change is applied

1. Every page classified `guia` that falls at the QR-evidence gate (`has_guia_evidence =
   False`) MUST be emitted as a **discarded entry** in `PipelineResult` instead of being
   silently dropped.
2. A discarded entry carries: `source_page` (int), `registro` (str | None — from
   `page_to_registro`/`raw.registro`), and `cached_lines` (the `raw.lines` list at the time
   of drop — possibly empty).
3. The QR-evidence gate blocking semantics remain unchanged — no-evidence pages NEVER open
   or extend a guía block (the rev-6 phantom-block invariant stands).
4. No image bytes are persisted in the discarded entry — thumbnails are served on demand via
   the existing `GET /runs/{run_id}/pages/{page}/thumbnail` endpoint (REV-C07 fallback
   chain).
5. Old extraction caches (without the discarded-entries field) MUST hydrate without error
   (Pydantic default value / backward-compatible deserialization).
6. The new model carrying discarded entries MUST remain in the domain or application layer;
   it MUST NOT import any SDK, framework, or IO library.
7. A discarded entry with non-empty `cached_lines` MUST reuse those lines on recovery
   WITHOUT re-running OCR — the deterministic engine (SDD#1 RapidOCRAdapter) produces the
   same output for the same image; re-running adds no information and wastes compute.
8. A discarded entry with empty `cached_lines` MUST trigger OCR re-run (via
   `ExtractionPort.extract_printed_table`) on recovery.

---

## Delta Requirements

> Each entry is marked [ADDED] or [MODIFIED: modifies <id>].

### EXT-034 — [ADDED] ZERO silent drops: discarded entry at the QR-evidence gate

**[ADDED: previously, a `guia`-classified page at `_stage_assemble_blocks` that fails
`has_guia_evidence` (identity is None AND the OCR-fallback material condition is False) was
silently discarded with `continue`. This caused issue #50: the operator had zero signal that
a guía was lost. This requirement closes that hole.]**

The `_stage_assemble_blocks` stage (or its equivalent in `application/pipeline.py`) MUST NOT
silently discard any page classified `guia`. Every page that fails the `has_guia_evidence`
gate MUST instead produce a **discarded entry** and append it to the `PipelineResult`
discarded collection (see EXT-035).

The discarded entry MUST carry:
- `source_page: int` — zero-based page index of the dropped page.
- `registro: str | None` — the section registro resolved from `page_to_registro` (or
  `raw.registro`) at the time of the drop. MAY be `None` when the section map yields no
  registro for this page.
- `cached_lines: list[MaterialLine]` — the `raw.lines` populated by the OCR stage before the
  QR-evidence check. MAY be empty (`[]`) if OCR produced no rows for this page.

The model shape (extend `ErroredGuia` with a `reason` discriminator vs. a new
`UnidentifiedGuia` domain model) is a **design decision** — this spec requires only the
semantic content of the entry, not its Python representation. The design MUST carry this
content regardless of the chosen model option.

#### Scenario EXT-S034a — page with no QR evidence produces a discarded entry

Given a `guia`-classified page whose `identity` is `None`
And `page_hashqr_url` is `None` (no URL-variant QR found)
And `raw.lines` contains 2 `MaterialLine` objects from the OCR stage
And `raw.registro` is `"232"`
When `_stage_assemble_blocks` processes this page
Then the page does NOT open or extend any guía block
And a discarded entry is appended to `PipelineResult` with:
  - `source_page` = the correct page index
  - `registro = "232"`
  - `cached_lines` = the 2 `MaterialLine` objects
And no `GuiaDeRemision` is created for this page

#### Scenario EXT-S034b — page with no QR evidence and empty OCR lines still produces discarded entry

Given a `guia`-classified page whose `identity` is `None` and `page_hashqr_url` is `None`
And `raw.lines` is `[]` (OCR found nothing on this page)
And `raw.registro` is `"229"`
When `_stage_assemble_blocks` processes this page
Then the page does NOT open or extend any guía block
And a discarded entry is appended with `source_page`, `registro="229"`, `cached_lines=[]`
And no `GuiaDeRemision` is created for this page

#### Scenario EXT-S034c — page with valid QR evidence is NOT discarded

Given a `guia`-classified page whose `identity` is a valid `GuiaIdentity` (QR decoded)
When `_stage_assemble_blocks` processes this page
Then the page opens or extends a guía block normally
And NO discarded entry is produced for this page

#### Scenario EXT-S034d — page with OCR-fallback evidence (hashqr_url + lines) is NOT discarded

Given a `guia`-classified page where `identity` is `None`
And `page_hashqr_url` is a non-None URL QR value
And `raw.lines` contains at least 1 `MaterialLine`
When `_stage_assemble_blocks` processes this page
Then the page opens or extends an `ocr_fallback` guía block normally (EXT-019 rev-6 rule)
And NO discarded entry is produced for this page

#### Scenario EXT-S034e — registro=None discarded entry is valid and surfaced

Given a `guia`-classified page with no QR evidence
And `page_to_registro` returns `None` for this page (section map yields no registro)
When the discarded entry is produced
Then `discarded_entry.registro` is `None`
And the entry is still appended to the `PipelineResult` discarded collection
And the entry is still surfaced in the review API response

---

### EXT-035 — [ADDED] PipelineResult carries a discarded collection

`PipelineResult` MUST expose a collection of discarded entries (the model type is a design
decision — `list[ErroredGuia]` filtered by `reason`, or `list[UnidentifiedGuia]`, or
equivalent). The field MUST default to an empty collection so that:

1. Existing callers that do not read the discarded collection are unaffected.
2. Old serialized `PipelineResult` objects that lack the field hydrate without error.

The discarded collection MUST be populated ONLY from the `_stage_assemble_blocks` EXT-034
drop path. It MUST NOT receive entries from the existing errored-guía path (zero-OCR-lines
guías with a valid identity).

The existing `PipelineResult.errored_guias` collection MUST retain its current semantics:
guías with a valid identity (QR or OCR fallback) whose OCR yielded zero material lines.
The two collections MUST be semantically distinct and MUST NOT be mixed.

#### Scenario EXT-S035a — PipelineResult has separate discarded and errored collections

Given a run that produces:
  - 1 guía with valid QR identity but 0 OCR lines (existing errored case)
  - 1 guía-classified page with no QR evidence (new discarded case)
When the pipeline completes
Then `PipelineResult.errored_guias` contains the identity-valid zero-lines guía
And `PipelineResult.discarded_guia_pages` (or equivalent) contains the no-evidence page
And neither collection contains the other's entries

#### Scenario EXT-S035b — old PipelineResult cache without discarded field hydrates cleanly

Given an existing serialized `PipelineResult` (from a run before SDD#2) that has no
  discarded collection field
When the deserialization/hydration step processes the cached result
Then no `ValidationError` or `KeyError` is raised
And the discarded collection defaults to `[]` (empty)

---

### EXT-036 — [ADDED] Cached OCR lines preserved in discarded entry; reused on recovery

When the discarded entry is produced at the drop site, the `cached_lines` field MUST be
populated from `raw.lines` at that exact moment — the OCR stage has already run and the
lines are available. Persisting them avoids a redundant re-OCR call on recovery.

On recovery, the recovery service MUST:
1. Read `cached_lines` from the discarded entry.
2. If `cached_lines` is **non-empty**: use those lines directly as the recovered material
   lines WITHOUT invoking `ExtractionPort.extract_printed_table`. The deterministic OCR
   engine (SDD#1 `RapidOCRAdapter`) is idempotent — same image → same output; re-running
   adds no value.
3. If `cached_lines` is **empty**: invoke `ExtractionPort.extract_printed_table` on the
   page image (rendered at recovery DPI) and use the resulting lines.
4. If OCR also returns empty lines in step 3: fall back to `VisionLLMPort` for material
   line extraction. (Vision fallback specifics are a design decision; the spec requires
   that vision is the LAST resort, after both cached-lines and OCR paths are exhausted.)

The recovery service MUST NOT invoke OCR when step 2 applies. The recovery service MUST
NOT invoke vision when step 3 succeeds (non-empty OCR result).

This requirement is at the application layer (recovery service behaviour) but is specified
here because it constrains how the `cached_lines` field of the discarded entry is used.

#### Scenario EXT-S036a — recovery with cached lines: OCR not re-run

Given a discarded entry with `cached_lines = [MaterialLine(cantidad=0.191, unidad="TN", ...)]`
When the recovery service processes this entry
Then the `cached_lines` are used directly as the recovered material lines
And `ExtractionPort.extract_printed_table` is NOT called for this page
And `VisionLLMPort` is NOT called for this page

#### Scenario EXT-S036b — recovery with empty cached lines: OCR is re-run

Given a discarded entry with `cached_lines = []`
And `ExtractionPort.extract_printed_table` is available (OCR enabled)
When the recovery service processes this entry
Then `ExtractionPort.extract_printed_table` is called with the rendered page image
And the returned lines are used as the recovered material lines (if non-empty)

#### Scenario EXT-S036c — recovery with empty cached lines and empty OCR result: vision fallback

Given a discarded entry with `cached_lines = []`
And `ExtractionPort.extract_printed_table` returns `[]` for this page
When the recovery service processes this entry
Then `VisionLLMPort` is called for material extraction as the last fallback
And if vision also returns nothing, the recovery fails with a structured error
  (the entry stays in the discarded collection; it is NOT silently removed)

---

### EXT-037 — [ADDED] Synthetic identity for recovered pages (design-level contract)

A recovered guía page MUST receive a **synthetic identity** because no QR `serie-numero`
exists. The exact format (sentinel pattern vs UUID) and the additive `identity_source`
Literal value are **design decisions**. However, the spec constrains the semantics:

1. The synthetic identity MUST NEVER collide with a real QR-derived `guia_id` (format
   `{serie}-{numero}`).
2. The synthetic identity MUST NOT be confused with the three domain identifiers:
   Contents-ID (e.g. `#4252`) ≠ Registro N° (e.g. `232`) ≠ QR `serie-numero`
   (e.g. `T009-0741770`).
3. `identity_source` on the recovered `GuiaDeRemision` MUST use an additive Literal value
   distinct from `"qr"` and `"ocr_fallback"` to signal operator-recovered origin.
4. The API DTO `identity_source` field MUST be updated in lockstep with the new Literal
   value — a missing Literal value causes a 500 on the table endpoint (precedent:
   `match_method` lesson from the material-matching spec).
5. The recovered guía MUST carry `requires_review=True` on ALL recovered material lines,
   regardless of OCR confidence. This is absolute — recovery is never a confirmed-accurate
   read.
6. The recovered guía MUST land under the `registro` inherited from the discarded entry's
   `registro` field (the section registro). No mandatory assignment dialog on recovery
   (decision 2 from the proposal). Registro reassignment is the exceptional [Acciones] flow.

#### Scenario EXT-S037a — synthetic identity does not collide with QR format

Given a recovered page at page index 152 (decimal)
When the synthetic identity is assigned
Then the `guia_id` does NOT match the pattern `[A-Z]\d+-\d+` (the QR `serie-numero` format)
And the `guia_id` does NOT equal `"152"` (a bare page index could be confused with
  a section/registro N°)
And `identity_source` is NOT `"qr"` and NOT `"ocr_fallback"`

#### Scenario EXT-S037b — all recovered lines carry requires_review=True

Given a recovered page where OCR returns 3 `MaterialLine` objects
And the OCR confidence for all 3 rows is >= 0.95 (high confidence)
When the recovered `GuiaDeRemision` is assembled
Then all 3 `MaterialLine` objects have `requires_review=True`
And the reconciliation gate will flag the recovered group for human review

#### Scenario EXT-S037c — recovered guía lands under section registro

Given a discarded entry with `registro="232"` and `source_page=152`
When recovery is completed and the `GuiaDeRemision` is assembled
Then `guia_de_remision.registro = "232"`
And no assignment dialog is triggered
And the guía appears in the reconciliation result under registro 232

---

## MUST-NOT Invariants for this delta

- The QR-evidence gate blocking semantics MUST NOT change. A no-evidence page NEVER opens
  or extends a guía block — only the silent drop is replaced by an explicit discarded entry.
- `fecha` MUST NOT be part of the discarded entry or the recovered guía's grouping key.
  Grouping key remains `(registro, material_canonical, unidad)`.
- Units MUST NOT be converted. `cached_lines` carry raw units; they are used as-is.
- The input PDF MUST NOT be modified by drop-site changes or recovery. Images are rendered
  on demand from the read-only PDF.
- No SDK, framework, or IO import MUST be added to `domain/` as a result of this change.
- `application/pipeline.py` MUST NOT import any concrete adapter as a result of this change.
- Backward compatibility: old extraction caches MUST load without error.

---

## Out of scope for this delta

- Classification changes (EXT-001/EXT-019 are unchanged).
- Vision date extraction (EXT-005/EXT-017/EXT-020/EXT-021 are unchanged).
- SUNAT fetch port (EXT-016/EXT-023/EXT-026 are unchanged).
- Block grouping logic (EXT-015/EXT-022 are unchanged).
- History/persistence across application restarts (SDD#3).
- Arbitrary-page UI processing (API allows it; no UI for it in this change).
- Issue #56 (RapidOCR runtime model download air-gap regression) — separate deploy concern.
