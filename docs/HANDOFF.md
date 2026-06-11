# HANDOFF — material-reconciliation (read this first on a new workstation)

> **Why this file exists:** the engram memory used during development is **local to the
> original machine** and does NOT travel with the repo. This document (plus the other
> files in `docs/`) is the versioned source of truth for continuing the work anywhere.

Last session: **2026-06-10**. Current branch: `main`. All PRs merged.
**SDD#1 (deterministic OCR backend) — COMPLETE. PR#1–4 all merged (#51/#52/#53/#54). Next action: SDD#2.**

> **SDD#1 outcome**: deterministic OCR (RapidOCR PP-OCRv5-server, paddle-free) re-enabled as the
> primary quantity extractor. Dual-blind judgment-day PASS×2 (Opus 4.8 + Fable 5) on all 4 PRs.
> Real-data gate 13/13 GREEN (pages 148/156/160 + F1 regression-locks 0141/0164). **#40 root cause
> fixed.** PR#4 added geometric column anchoring: topmost-structural cluster anchor (NOT largest-cluster
> — the popularity contest silently dropped real rows on 1-line guías, caught by JD F1 finding) +
> `_has_paired_qty_unit` stamp/footer exclusion by POSITION (M-6 intact). Clean in-table rows now
> emit `requires_review=False` (trusted reads restored). Residual: 3 rows on p156 still
> `requires_review=True` from the EXT-004 0.85 conf gate on genuinely garbled descriptors + 1
> unit-ownership edge (0.041 stray fragment) — accepted, documented, deferred. SDD#1 archived.

---

## 1. What this project is

A local-first QC tool for a civil-engineering quality engineer. It ingests a 493-page
Autodesk Forma PDF export (`CTR-PLC01-FR001 Recepción de Materiales en Obra`) and
reconciles, per **Registro N°**, the **declared** materials (digital text from the detail
page Notes + Protocolo de Recepción) against the **summed** materials from the scanned
**guías de remisión**. It flags mismatches, lets the engineer reassign misfiled guías, and
exports the reconciled table to xlsx/csv.

Full domain context: `docs/DECISIONS.md`. Architecture: `docs/ARCHITECTURE.md`.
Eval results: `docs/EVAL-RESULTS.md`.

## 2. Current state (all merged to main as of 2026-06-10)

```
PR #46  feat/guia-reprocess-reprocesar-ia         MERGED  Reprocesar con IA + canonical-matching fix
PR #47  Backend: bulk endpoint + #42 fix          MERGED  guia-reprocess-bulk-viewer (PR-A)
PR #48  Frontend: tabs + bulk UX                  MERGED  guia-reprocess-bulk-viewer (PR-B)
PR #49  Frontend: viewer + Acciones + SA-5        MERGED  guia-reprocess-bulk-viewer (PR-C)
PR #51  feat/deterministic-ocr-parser             MERGED  SDD#1 PR#1 — pure box-row parser
PR #52  feat/deterministic-ocr-adapter            MERGED  SDD#1 PR#2 — RapidOCRAdapter + factory + wiring
PR #53  feat/deterministic-ocr-deps-gate          MERGED  SDD#1 PR#3 — deps, Docker air-gap, real-data gate
PR #54  feat/deterministic-ocr-column-anchoring   MERGED  SDD#1 PR#4 — geometric column anchoring + trusted reads
```

- **Test counts**: ~1300+ backend targeted (13/13 real-data gate on CTR_PDF_PATH) + 322 frontend
  vitest passing. Monolithic `pytest -q` still hangs on paddle import — use targeted paths only.
- **Backend**: `uvicorn reconciliation.infrastructure.api.main:app --reload` from `backend/`.
- **Frontend**: `npm install && npm run dev` from `frontend/`.
- **Vision model in use**: `kimi-k2.5:cloud` via `OpenAICompatibleVisionAdapter` (Ollama
  cloud). Config: `provider=ollama, OLLAMA__MODEL=kimi-k2.5:cloud, DEADLINE_S=60`.
- **OCR status**: `RECONCILIATION__OCR__ENABLED=true`, `RECONCILIATION__OCR__ENGINE=rapidocr`
  (RapidOCR ONNX PP-OCRv5-server, paddle-free). #40 root cause fixed.

## 3. RESUME HERE — SDD plan

**SDD execution parameters**: interactive · hybrid artifact store · ask-on-risk delivery ·
stacked-to-main chains. Frontend-visual apply → opus model.

### SDD#1 — Deterministic OCR backend — COMPLETE & MERGED (PR#1–4: #51/#52/#53/#54)

**Delivered**: RapidOCR ONNX PP-OCRv5-server as the primary quantity extractor (paddle-free);
pure box-row parser for layout-aware GRE table parsing; provider-agnostic engine factory; Docker
air-gap bundling; geometric column anchoring (topmost structural cluster, trusted reads restored);
real-data gate 13/13 GREEN. Deploy defaults: `RECONCILIATION__OCR__ENABLED=true`,
`RECONCILIATION__OCR__ENGINE=rapidocr`. #40 root cause fixed. Archived to
`openspec/changes/archive/deterministic-ocr-backend/`.

**Known deferred follow-ups from PR#4 (low priority):**
- **Above-table spurious-anchor residual**: a paired qty+unit line above the table could hijack
  the topmost-structural cluster anchor (probability-gated; zero corpus evidence so far). Fix-later:
  DESC-anchored pair detection or keep-all logging.
- **F2 intra-table split-table**: pre-existing case of a table split across two page regions with
  ≤4 rows between anchors. Physically implausible in the corpus; deferred.
- **Gate is quantity-only**: material identity is validated downstream by canonical matching, not
  inside the OCR gate. Accepted scope boundary.

### SDD#2 — [Descartadas para revisión] tab + recover-specific-page + history UI

**Goal**: surface GUIA-classified pages dropped due to no identity (issue #50), and give the
operator a way to recover them. Also: later add a processing-history hamburger menu.

**Key decisions**:
1. **Backend root fix** (may land in SDD#1 or SDD#2): `assemble_blocks` must NOT silently
   drop a GUIA-classified page with no resolvable identity. Emit an errored/unidentified
   entry (page number + thumbnail) instead. The root cause: `pipeline.py:964-982`
   `assemble_blocks` rev-6 QR-evidence gate silently drops pages with no QR and no OCR
   identity.
2. **[Descartadas para revisión] tab**: new tab on ReviewPage surfacing unidentified GUIA
   pages with page number + thumbnail. Operator can trigger deterministic OCR (SDD#1 path)
   or IA fallback if OCR fails — mirrors [Pendientes por procesar] tab flow.
3. **Recover specific page/sheet** function: operator points at page N → process as guía
   (OCR then IA). Handles classification/QR misses generally.
4. **History/persistence** (later, SDD#2 or SDD#3): hamburger menu showing sections
   ([Nuevo] / [batch actual] / [historial]). Persist what each batch/run processed for an
   auditable UI history.

**Frontend-visual apply → opus** (per session execution preference).

## 4. Open issues

| Issue | Severity | Description |
|-------|----------|-------------|
| **#50** | High | GUIA-classified page with no identity silently dropped. Root cause: `assemble_blocks` QR-evidence gate at `pipeline.py:964-982`. Fix in SDD#2. |
| **#44** | Medium | Cross-model consensus reprocess (qwen+kimi): agree→accept, disagree→`requires_review`. Upgrade path for vision accuracy. Lower priority now OCR is re-enabled. |
| **#45** | Low | Run status endpoint stale after reprocess (errored count + `vision_calls_made` lag). Use `/table` as the fresh source; endpoint is cosmetic. |
| **#41** | Low | Deadline-guard abandons in-flight httpx request (still billed). Cancel instead of abandon in the server context. |
| **#43** | Low | 3-map unit normalizer — consolidate `UNIT_LABEL_MAP` / `_SUNAT_UNIT_MAP` / `pipeline._normalize_sunat_unit` into single domain source. |
| **OCR-F-1** | Low | Above-table spurious-anchor residual (PR#4 deferred follow-up): a paired qty+unit line above the table could hijack `_infer_table_region`'s topmost-structural cluster anchor. Zero corpus evidence. Fix-later: DESC-anchored pair detection or keep-all logging. See `docs/DECISIONS.md` §2026-06-10. |

## 5. Hard-won lessons (do not relearn these)

- **Unit tests passed while the real pipeline was broken.** This happened multiple times.
  Always run a real-data e2e check — see `docs/DECISIONS.md` §recurring-mock-gap.
- **OCR is correct for printed GRE tables; vision is a poor substitute.** The #40 quantity
  errors are an artifact of running OCR-off. See `docs/EVAL-RESULTS.md` §2.
- **kimi empty-returns are stochastic and recoverable by retry; qwen systematic misreads
  are not.** Use `requires_review=True` as the mandatory gate on all vision-recovered lines.
- **Poll-based progress needs a real backend completion signal, not a timing heuristic.**
  SA-5 caught this bug in the bulk-reprocess flow (PR-C #49 fix).
- **Scanned guía pages have NO "GUÍA DE REMISIÓN" in their digital text layer** — only the
  Autodesk Forma header. The classifier must use QR presence (Condition A) or heuristic
  (Condition B).
- **Three identifiers, never confuse**: Contents-ID `#4252` (section) ≠ Registro N° `232`
  (business key) ≠ QR `serie-numero` (deterministic guía id). Group by the Registro N°.
- **GUIA-classified pages with no QR + OCR off are silently dropped.** Issue #50. The
  operator has no signal a guía was lost. Data-integrity hole — fix in SDD#1/2.
- **The stamp crop region is UPPER-RIGHT on CTR guías** (`x0=0.55, y0=0.05, x1=1.0, y1=0.45`).
- **Units (KG/TN/RD/Rollo) are summed independently — never converted.**

## 6. §known-open-rev3b — deferred runtime issues

- **KI-2 — Cloud vision throttling.** kimi-k2.5:cloud throttles under rapid sequential
  reprocess calls. Bounded by `Semaphore(3)`; per-guía (not batch) is the mitigation.
  `DEADLINE_S=60` required.
- **KI-3 — SUNAT subset re-fetch.** Intermittent timeout on some guías under load.
- **OCR is OFF in the DEPLOY, not broken on the host.** The 2026-06-06 OCR eval ran
  PaddleOCR 3.x successfully on the host uv env (`paddle imports OK`; ~4s/page; exact
  quantity reads). OCR is disabled in the *deployed* app because the runtime image is
  built paddle-free (target `runtime`) + `RECONCILIATION__OCR__ENABLED=false`. A prior
  paddle 3.3.1 oneDNN/PIR CPU bug may recur on some envs — which is exactly why SDD#1
  standardizes on **RapidOCR (ONNX)**: deployable in the image, no paddlepaddle runtime.

## 7. §infra — how to run

- Image: `docker compose build backend` (target `test` = paddle-free + pytest + tests).
- Networking: compose uses `network_mode: host` (Linux). Container reaches host Ollama at
  `localhost:11434` — `host.docker.internal` does NOT work on Linux.
- Run gates: `docker compose run --rm -e CTR_PDF_PATH=/data/input.pdf backend python -m pytest
  tests/integration/test_pipeline_r8_gate.py::TestRealPDFGate
  tests/integration/test_pipeline_r9_gate.py::TestR9RealPDFGate -p no:cacheprovider -v -s`
- Vision deadline: `RECONCILIATION__VISION__DEADLINE_S` (default 20s; set 60 for kimi cloud).

## 8. Engram → docs mapping

| Engram topic | Versioned in |
|---|---|
| stack, domain-rules, llm-provider-abstraction | `docs/DECISIONS.md`, `docs/ARCHITECTURE.md` |
| design rev-2 (A–F), locked-defaults | `docs/DECISIONS.md`, `openspec/.../design.md` |
| e2e-integration-findings (5 bugs) | `docs/DECISIONS.md` §audit |
| frontend-review-findings (2 criticals) | `docs/DECISIONS.md` §frontend-review |
| qr-sunat-evaluation | `docs/DECISIONS.md` §QR |
| reception-date-authority | `docs/DECISIONS.md` §dates |
| rev-3 real-run findings (7 items) | `docs/DECISIONS.md` §rev-3 |
| vision-quantity-accuracy-eval (#3021 session / #2995 SA-5) | `docs/EVAL-RESULTS.md` §1 |
| ocr-engine-eval (#3023) | `docs/EVAL-RESULTS.md` §2 |
| ocr-off-vision-only-dropped-guia (#3022) | `docs/DECISIONS.md` §2026-06-06 |
| plan/ocr-deterministic-and-discarded-ui (#3024) | this file §3 + `docs/DECISIONS.md` §2026-06-06 |
| sdd/guia-reprocess-bulk-viewer/archive-report (#3019) | `docs/DECISIONS.md` §2026-06-06 |
| pr46-reprocess-canonical-merge (#3003) | `docs/DECISIONS.md` §2026-06-06 |
