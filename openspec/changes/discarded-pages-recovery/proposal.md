# Proposal — discarded-pages-recovery (SDD#2)

> Status: **PROPOSED** (product decisions settled by user 2026-06-11 — fixed constraints,
> not re-openable here). Artifact store: hybrid (engram + openspec).
> Depends on: SDD#1 deterministic-ocr-backend (merged, PR #51–#54).

## 1. Intent / Problem

**Issue #50 is a data-integrity hole**: a page classified `GUIA` (e.g. page 0152, confidence
0.99) whose QR did not decode and that carries no other QR evidence is **silently dropped** by
the `assemble_blocks` rev-6 QR-evidence gate (`application/pipeline.py:977-982`). The page is
not in recovered, errored, or unresolved output. The operator gets **zero signal that a guía
was lost** — declared totals simply look short with no explanation, which directly undermines
the product's reason to exist: per-Registro reconciliation the engineer can trust.

**Business value**: close the silent-loss channel. Every GUIA-classified page must be
accounted for somewhere visible — reconciled, errored, or **discarded-for-review** — and the
operator must be able to recover discarded pages with the cheapest reliable tool first
(deterministic OCR, SDD#1 path) and vision IA only as fallback.

**Success looks like**: after a run, the operator opens a new **[Descartadas para revisión]**
tab listing every dropped GUIA-candidate page with its page number and thumbnail, views each
sheet, selects which ones are real guías, runs a bulk recovery, and sees the recovered lines
land in reconciliation flagged `requires_review`. Zero silent drops.

## 2. Scope

The five product decisions below were settled by the user on 2026-06-11 and are **fixed
constraints** for spec/design — do not re-open them.

### In scope

1. **Backend root fix at the drop site**: `assemble_blocks` no longer discards
   no-QR-evidence GUIA-classified pages into the void; each becomes a *discarded entry*
   (page number, section registro, cached OCR lines if any) surfaced in `PipelineResult`
   and the review API.
2. **Discarded = "possible guías" post-classification** (decision 1). Classification
   catches all guías; the dropped bucket holds *candidates* only. The UI is built ONLY for
   that list. The recovery endpoint is keyed by page (`POST .../pages/{page}/recover`
   style), so it is general at the API level for free — but **no UI for arbitrary-page
   processing** (YAGNI; zero observed misclassified-guía instances; trivial later slice).
3. **Registro comes from the section** (decision 2). Every guía lives in a section with an
   assigned Registro N°, so every discarded page already carries its registro
   (`page_to_registro`). **No mandatory assignment dialog on recovery** — the recovered
   guía lands under its section registro. Registro reassignment stays the EXCEPTIONAL flow
   via the existing [Acciones] menu after a fecha-divergence warning (R9,
   `reception-date-authority` skill). This supersedes the exploration's
   "operator must assign a registro" finding.
4. **[Descartadas para revisión] tab** with **bulk recovery + preview + selection**
   (decision 3): thumbnails per page (reuse `GET /runs/{run_id}/pages/{page}/thumbnail`),
   per-page sheet viewer, checkboxes to select which pages join the batch (so non-guía
   sheets are excluded by the operator), then a bulk recover mirroring the existing
   "Procesar todos con IA" pattern with selection added.
5. **OCR-first recovery reusing cached lines** (decision 4): pages dropped with
   `raw.lines` already populated (QR failed but OCR read the table) **reuse those lines
   directly** — RapidOCR is deterministic; same image → same lines; re-running computes
   the same result twice. Re-run OCR ONLY when cached lines are empty; vision IA is the
   last fallback. Implication: the discarded entry must **persist its OCR lines** in the
   extraction cache (`ErroredGuia` has no lines field today — see Open Questions).
6. **Recovered rows are flagged, never auto-accepted**: every recovered line carries
   `requires_review=True`; reconciliation against the trusted digital declared side
   remains the validation gate (architecture invariant).

### Out of scope

- **History/persistence hamburger menu → SDD#3** (decision 5). Requires cross-restart
  persistence (today's `run_registry` is in-memory) — different architectural scope.
- **UI for arbitrary-page recovery** (process page N at will). The API shape allows it;
  no UI slice now (decision 1).
- **Issue #56** (air-gap regression — RapidOCR runtime model download). Found during the
  in-flight e2e run; deploy-hygiene concern, separate fix.
- **Classification changes**. The classifier already catches all guías; this change only
  handles the post-classification identity gate.
- Issues #44 (cross-model consensus), #45 (stale status endpoint), #41 (deadline-guard
  cancel), #43 (unit-map consolidation) — unrelated.

## 3. Approach

### 3.1 Root fix at the drop site (application layer)

At `pipeline.py:977-982`, replace the bare `continue` with: append a *discarded entry*
carrying `source_page`, the section registro (already resolved via `page_to_registro` /
`raw.registro`), and the cached `raw.lines` (possibly empty). The QR-evidence gate's
*blocking* semantics are unchanged — the page still never opens or extends a block (the
rev-6 phantom-block invariant stands); it just stops being invisible. No image bytes are
persisted: the existing thumbnail endpoint renders on demand from the read-only PDF.

### 3.2 Domain model — recommended direction (final call: sdd-design)

The exploration mapped two viable models:

- **Option A** — extend `ErroredGuia` with `reason: Literal["zero_lines", "no_identity"]`
  (+ an optional cached-lines field). Pro: reuses every existing rail end-to-end
  (`PipelineResult.errored_guias`, `ReviewService._errored_guias`,
  `add_recovered_guia`, `ErroredGuiaResponse`, panel components). Con: `ErroredGuia`
  today means "valid identity, zero lines"; a no-identity candidate with cached lines is
  semantically the inverse, and the new lines field weakens A's "minimal change" appeal.
- **Option B** — new `UnidentifiedGuia` domain model (page, registro, cached lines).
  Pro: clean SRP, lines field is native, no semantic overload of `ErroredGuia`. Con: a
  parallel list through `PipelineResult` → `ReviewService` → API → frontend types.

**Recommended direction: Option A with the `reason` discriminator**, per the exploration's
recommendation — the recovery lifecycle (surface → operator-triggered reprocess →
`add_recovered_guia` → re-reconcile) is identical to the errored-guía lifecycle built in
PRs #46–#49, and a discriminator keeps one rail. The cached-lines requirement (decision 4)
is the honest counter-pressure toward B; sdd-design makes the final model call with the
cache backward-compat constraint in view.

### 3.3 Recovery service (application layer)

Extend `ReprocessService` (vision-only today) with an OCR-first recovery path keyed by
page: (1) if the discarded entry has cached OCR lines → use them directly (no re-render,
no OCR call); (2) else render the page at recovery DPI and call
`ExtractionPort.extract_printed_table` (SDD#1 `RapidOCRAdapter` via the OCR factory);
(3) else fall back to vision via `VisionLLMPort`. All recovered lines:
`requires_review=True`. Recovered guía gets a **synthetic identity** (no QR `serie-numero`
exists) — sentinel id pattern and the additive `identity_source` Literal value are design
decisions (see Open Questions). Recovered guía lands under its section registro and flows
through the existing canonical-matching reconciliation (`material-canonical-matching`
skill): grouping by `(registro, material_canonical, unidad)`, units never converted,
`fecha` never a grouping axis.

### 3.4 API (infrastructure layer)

- Surface discarded entries on the table/review response (reusing `errored_guias` with
  `reason`, or a parallel field — follows the model choice).
- `POST /runs/{run_id}/pages/{page}/recover` (exact shape → design): per-page recovery;
  bulk is operator-selected pages over this endpoint (client-orchestrated loop vs batch
  endpoint → design). Note the existing bulk endpoint
  `/registros/{registro}/reprocess` is registro-keyed and does not fit page-keyed
  recovery.

### 3.5 Frontend

- Third tab `[Descartadas para revisión]` on `ReviewPage.vue` (extend the hardcoded
  2-element `TAB_ORDER`).
- Tab content: thumbnail grid/list with page numbers, per-page sheet viewer (existing
  viewer from PR #48), checkbox selection, bulk "recover selected" mirroring the
  `PendientesPorProcesarTab` bulk-progress pattern (including the PR #49 SA-5 lesson:
  never settle bulk progress prematurely).
- Frontend-visual apply → opus model (per session execution preference).

## 4. Impact (per hexagonal layer)

| Layer | Files (expected) | Change |
|---|---|---|
| Domain (pure) | `domain/models.py` | `ErroredGuia.reason` discriminator + cached-lines field, OR new `UnidentifiedGuia`; synthetic-identity rules. No SDK/IO imports — stays pure. |
| Application | `application/pipeline.py` (drop site ~977-982, `PipelineResult`), `application/review_service.py`, `application/reprocess_service.py` | Emit discarded entries; OCR-first recovery path; ports only — zero concrete adapter imports. |
| Infrastructure | `infrastructure/api/schemas.py`, `infrastructure/api/routes.py`, extraction cache serializer | `reason`/lines on DTOs; recover endpoint; cache schema for persisted lines. |
| Adapters | none new expected | Reuses SDD#1 `RapidOCRAdapter` + existing vision adapters via ports/factories. |
| Frontend | `ReviewPage.vue`, new `DescartadasTab.vue` (or similar), `api/types.ts`, api client | Third tab, selection + bulk recover, DTO types. |

## 5. Risks

1. **Extraction-cache backward compatibility**: persisting OCR lines on discarded entries
   changes the cache schema; old cached runs must load without error (versioning or
   tolerant deserialization).
2. **Synthetic `guia_id`**: must never collide with real QR `serie-numero` ids and must
   not confuse the three-identifier rule (#4252 ≠ Registro N° ≠ QR serie-numero). Sentinel
   pattern (`unid_{page}`-style) vs UUID is a design call.
3. **`identity_source` Literal**: currently `"qr" | "ocr_fallback" | "vision"`; needs an
   additive value for recovered pages. Precedent risk: a missing Literal value on the API
   DTO caused a 500 on the table endpoint (`match_method` lesson) — DTO must be updated in
   lockstep.
4. **REINTENTAR UX leakage**: if Option A merges discarded entries into the errored list,
   the existing REINTENTAR / "Reprocesar con IA" per-guía actions could surface for
   `no_identity` entries where they are wrong or misleading — the UI must discriminate by
   `reason`.
5. **Non-guía sheets in the batch**: mitigated by design (decision 3 — operator preview +
   checkbox selection before bulk), but recovered non-guía content would still be caught by
   the reconciliation gate (`requires_review`), never silently accepted.
6. **Real discarded-page inventory unknown until e2e completes** (see §7) — sizing of
   spec/tasks may shift if the count is much larger than the single confirmed instance
   (page 0152).

## 6. Open Questions (for sdd-design — SA-2: not invented here)

1. **Model choice**: Option A (`ErroredGuia` + `reason` + cached-lines field) vs Option B
   (new `UnidentifiedGuia`). Recommended A; decision 4's lines-persistence requirement is
   the strongest argument for B.
2. **Exact recovery endpoint shape**: `POST /runs/{run_id}/pages/{page}/recover` request/
   response contract; bulk as client-orchestrated per-page calls vs a batch endpoint
   (progress reporting implications — see PR #49 bulk-progress lesson).
3. **Synthetic identity format** and the exact additive `identity_source` value.
4. **Cache versioning strategy** for the persisted-lines schema change.

## 7. Evidence / validation in flight (do not block on this)

A full-PDF e2e run (493 pages, OCR-on post-PR#54) is running now; it will yield the REAL
discarded-pages inventory (count + page numbers), which refines spec/tasks sizing when
ready. Issue #56 (air-gap regression — RapidOCR runtime model download) was found during
this run; related to deploy hygiene, OUT of SDD#2 scope.

## 8. Acceptance sketch (operator-visible)

- Every GUIA-classified page is accounted for after a run: reconciled, errored, or listed
  in [Descartadas para revisión] — **zero silent drops** (the #50 page 0152 case appears
  in the tab).
- The tab shows each discarded page with page number + thumbnail; the operator can open
  the full sheet viewer per page.
- The operator selects a subset via checkboxes and runs bulk recovery; progress mirrors
  the existing bulk-IA pattern and settles only when work truly finishes.
- A page dropped with cached OCR lines recovers WITHOUT re-running OCR; an empty-lines
  page re-runs OCR; vision fires only when OCR yields nothing.
- Recovered guías land under their section registro, flow through canonical matching, and
  every recovered row is flagged `requires_review` — never silently auto-accepted.
- Existing behavior unchanged: QR-evidence gate blocking semantics, grouping key, EXACT
  tolerance, units never converted, input PDF read-only, isolated output dirs, local-first.
