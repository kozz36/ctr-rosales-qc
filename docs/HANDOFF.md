# HANDOFF — material-reconciliation (read this first on a new workstation)

> **Why this file exists:** the engram memory used during development is **local to the
> original machine** and does NOT travel with the repo. This document (plus the other
> files in `docs/`) is the versioned source of truth for continuing the work anywhere.

Last session: **2026-06-06**. Current branch: `main`. All recent PRs merged.
**Next action: start SDD#1 (deterministic OCR backend).** See §SDD-plan.

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

## 2. Current state (all merged to main as of 2026-06-06)

```
PR #46  feat/guia-reprocess-reprocesar-ia   MERGED  Reprocesar con IA + canonical-matching fix
PR #47  Backend: bulk endpoint + #42 fix    MERGED  guia-reprocess-bulk-viewer (PR-A)
PR #48  Frontend: tabs + bulk UX            MERGED  guia-reprocess-bulk-viewer (PR-B)
PR #49  Frontend: viewer + Acciones + SA-5  MERGED  guia-reprocess-bulk-viewer (PR-C)
```

- **Test counts**: ~1300+ backend targeted + 322 frontend vitest passing. Monolithic
  `pytest -q` still hangs on paddle import — use targeted paths only.
- **Backend**: `uvicorn reconciliation.infrastructure.api.main:app --reload` from `backend/`.
- **Frontend**: `npm install && npm run dev` from `frontend/`.
- **Vision model in use**: `kimi-k2.5:cloud` via `OpenAICompatibleVisionAdapter` (Ollama
  cloud). Config: `provider=ollama, OLLAMA__MODEL=kimi-k2.5:cloud, DEADLINE_S=60`.
- **OCR status**: `RECONCILIATION__OCR__ENABLED=false` (paddle excluded from runtime image).
  This is the structural issue SDD#1 addresses.

## 3. RESUME HERE — SDD plan (approved 2026-06-06)

**Two sequential SDDs.** OCR backend FIRST (no UI changes), then the UI for dropped-guía
recovery. SDD execution: **interactive · hybrid artifact store · ask-on-risk delivery ·
stacked-to-main chains. Frontend-visual apply → opus model.**

### SDD#1 — Deterministic OCR backend (NO UI)

**Goal**: re-enable deterministic OCR in the deployed (paddle-free) image. Make OCR the
primary quantity extractor; reduce vision to date reads + illegible-page fallback.

**Key decisions** (from OCR engine eval — see `docs/EVAL-RESULTS.md` §2):
1. **Engine**: RapidOCR (PP-OCRv5 server, ONNXRuntime). `pip install rapidocr onnxruntime`
   (enums `OCRVersion.PPOCRV5`, `ModelType.SERVER`). ONNX — no paddlepaddle → fits the
   runtime image.
2. **Auto page-orientation/deskew BEFORE OCR** (critical — guías are scanned sideways).
   Do NOT hardcode -90°; need page-level doc-orientation detection. Investigate: RapidOCR
   doc-orientation model, or a lightweight 4-way scorer. RapidOCR `cls` is textline-level
   only and does not solve this.
3. **Layout-aware parser**: associate DETALLE + UNIDAD + CANTIDAD cells by bounding-box
   y-center row; accept `TNE→TN`; pair `codigo` column. Replaces `_LINE_RE` (which expects
   one-liner `<desc> <qty> <unit>` and produces 0 lines on columnar GRE tables). The PoC
   box-associator in `docs/eval/ocr_compare.py` already works.
4. **Re-enable OCR path**: set `RECONCILIATION__OCR__ENABLED=true` in the runtime config;
   add `rapidocr + onnxruntime` to the runtime image.
5. **Architecture**: new `RapidOCRAdapter` behind `ExtractionPort` — mirror the
   vision provider-agnostic pattern (config-selectable engine). Domain stays pure.
6. **Validate**: against real reg227 guías (GT in `docs/eval/ground_truth.md`: pages
   0148/0156/0160 + others). Strict-TDD.

**Impact**: the #40 vision quantity problem largely disappears — deterministic OCR reads
printed GRE tables exactly. Vision becomes date reads + rare fallback only.

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
| **#50** | High | GUIA-classified page with no identity silently dropped. Root cause: `assemble_blocks` QR-evidence gate at `pipeline.py:964-982`. Addressed in SDD#1 (backend root fix) or SDD#2. |
| **#44** | Medium | Cross-model consensus reprocess (qwen+kimi): agree→accept, disagree→`requires_review`. Upgrade path for vision accuracy. Lower priority once OCR is re-enabled. |
| **#45** | Low | Run status endpoint stale after reprocess (errored count + `vision_calls_made` lag). Use `/table` as the fresh source; endpoint is cosmetic. |
| **#41** | Low | Deadline-guard abandons in-flight httpx request (still billed). Cancel instead of abandon in the server context. |
| **#43** | Low | 3-map unit normalizer — consolidate `UNIT_LABEL_MAP` / `_SUNAT_UNIT_MAP` / `pipeline._normalize_sunat_unit` into single domain source. |

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
