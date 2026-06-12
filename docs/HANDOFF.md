# HANDOFF — material-reconciliation (read this first on a new workstation)

> **Why this file exists:** the engram memory used during development is **local to the
> original machine** and does NOT travel with the repo. This document (plus the other
> files in `docs/`) is the versioned source of truth for continuing the work anywhere.

Last session: **2026-06-12**. Current branch: `main`. All PRs merged.
**Delivery blockers #56 + #60 — CLOSED & MERGED (PR #70). App is delivery-ready. Next: remaining backlog (#57–#62, all non-blocking).**

> **Delivery closeout (2026-06-12, PR #70)**: closed the two real delivery blockers (audit-ranked).
> **#56** air-gap OCR: the build warm-up/assertion ran inference on *random noise* → Det found no
> text → `cls_mobile`+rec models never lazy-loaded → never bundled → ~165 MB runtime download. Fixed
> with synthetic-**text** warm-up inference + dual disk-existence guards (build fails loudly if any of
> the 3 ONNX models is missing). Proven under `docker run --network none` (9 boxes, 0 downloads).
> **#60** the documented `make verify` acceptance gate referenced a non-existent test → dead. Built
> `backend/tests/e2e/test_container_verification.py` (API-faithful upload→poll→table, R8 232=4.124
> MATCH + R9 divergence invariants). Runtime closeout surfaced 4 issues a unit-green run would hide:
> (1) **vacuous-green** skip → `CTR_VERIFY_STRICT=1` strict mode (fail, never skip; caught a live 404);
> (2) **host-port :8000 collision** with a sibling project → `CTR_BACKEND_PORT` (default 8010) in
> both compose files + install.sh, container-internal stays 8000 for the nginx proxy; (3) **W2
> confirmed**: registro 232 `requires_review=True` is the *legitimate* R9b SUNAT delivery-floor, not a
> bug → assertion is now profile-aware (rejects only a spurious flag); (4) broken `/health` healthcheck
> → socket-connect probe. Bonus: `make verify-fast` runs the SAME gate on a section-safe ~50-page
> 3-Protocolo subset → **8/8 green in 15 min** (vs ~90 min full). ctr-review (Fable) ×2 APPROVE.

> **SDD#1 outcome**: deterministic OCR (RapidOCR PP-OCRv5-server, paddle-free) re-enabled as the
> primary quantity extractor. Dual-blind judgment-day PASS×2 (Opus 4.8 + Fable 5) on all 4 PRs.
> Real-data gate 13/13 GREEN (pages 148/156/160 + F1 regression-locks 0141/0164). **#40 root cause
> fixed.** PR#4 added geometric column anchoring: topmost-structural cluster anchor (NOT largest-cluster
> — the popularity contest silently dropped real rows on 1-line guías, caught by JD F1 finding) +
> `_has_paired_qty_unit` stamp/footer exclusion by POSITION (M-6 intact). Clean in-table rows now
> emit `requires_review=False` (trusted reads restored). Residual: 3 rows on p156 still
> `requires_review=True` from the EXT-004 0.85 conf gate on genuinely garbled descriptors + 1
> unit-ownership edge (0.041 stray fragment) — accepted, documented, deferred. SDD#1 archived.

> **SDD#2 outcome**: zero-silent-drop proven on the real 493-page PDF: 469 = 126 (assembled guías)
> + 343 (discarded). Option B DiscardedPage side-channel (routes.py bulk-sweep + registro-inheritance)
> beat Option A enrollment path. JD×2 rounds on PR-2 (double-count CRITICAL: recover hook lacked
> the D2 idempotency guard; 6th consecutive PR where dual-blind JD caught silent data corruption
> behind a green TDD suite). Fable-as-judge-B highest ROI: reproduced the CRITICAL with worktree
> RED-proofs; Fable apply on frontend slices produced chain's only zero-defect reviews.
> Vision demoted to date-reads only (126 calls, 0 quantity reads). Full-PDF e2e: 343 discarded /
> 11 contiguous runs. SDD#2 archived to `openspec/changes/archive/discarded-pages-recovery/`.

> **SDD#3 outcome**: cross-restart run history live. Per-run manifest written at pipeline completion
> (success + failure, non-fatal, atomic). Startup scan indexes all existing run dirs (manifest-full
> or degraded legacy). `GET /runs` sorted newest-first + lazy 48h failed-sweep. Lazy hydration dep
> (`_get_hydrated_entry`) replaces `_require_run` — restores full editing on past runs without
> eager hydration at startup. `DELETE /runs/{id}` (UUID-validate, 409 guard, rmtree own-dir).
> `POST /runs/{id}/retry` (dir reset keep PDF+sunat/, same run_id, re-fire pipeline). Frontend:
> hamburger menu [Nuevo]/[Batch actual]/[Historial] + `/historial` route (`RunHistoryPage`) +
> `ReviewPage` cold-load fix (`runStore.runId` set from route param on mount) + `localStorage`
> persistence for runId. SA-5 caught two runtime bugs before merge: manifest registro field
> mismatch (fix PR #68) + refetchInterval-ignores-error-state infinite polling (fix PR #69).
> JD pattern (8th & 9th): PR-1 caught suite-RED + tests-can-rmtree-real-runs; PR-2 caught
> cold-load 409 CRITICAL + sweep-deletes-mid-retry CRITICAL, both live-reproduced by both blind
> judges. SDD#3 archived to `openspec/changes/archive/run-history-persistence/`.
> Run-history spec promoted: `openspec/specs/run-history/spec.md`.

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

## 2. Current state (all merged to main as of 2026-06-11)

```
PR #46  feat/guia-reprocess-reprocesar-ia         MERGED  Reprocesar con IA + canonical-matching fix
PR #47  Backend: bulk endpoint + #42 fix          MERGED  guia-reprocess-bulk-viewer (PR-A)
PR #48  Frontend: tabs + bulk UX                  MERGED  guia-reprocess-bulk-viewer (PR-B)
PR #49  Frontend: viewer + Acciones + SA-5        MERGED  guia-reprocess-bulk-viewer (PR-C)
PR #51  feat/deterministic-ocr-parser             MERGED  SDD#1 PR#1 — pure box-row parser
PR #52  feat/deterministic-ocr-adapter            MERGED  SDD#1 PR#2 — RapidOCRAdapter + factory + wiring
PR #53  feat/deterministic-ocr-deps-gate          MERGED  SDD#1 PR#3 — deps, Docker air-gap, real-data gate
PR #54  feat/deterministic-ocr-column-anchoring   MERGED  SDD#1 PR#4 — geometric column anchoring + trusted reads
PR #61  feat/discarded-pages-surface (PR-1)       MERGED  SDD#2 PR#1 — DiscardedPage model, drop-site emit, cache, API
PR #63  feat/discarded-pages-recovery (PR-2)      MERGED  SDD#2 PR#2 — OCR-first recovery + endpoints (JD×2)
PR #64  feat/discarded-pages-tab (PR-3a)          MERGED  SDD#2 PR#3a — Descartadas tab, groups, selection, single recover
PR #65  feat/discarded-pages-bulk (PR-3b)         MERGED  SDD#2 PR#3b — bulk recovery, ETA dialog, poll, re-attach
PR #66  feat/run-history-persistence (PR-1)       MERGED  SDD#3 PR#1 — persistence core: port, adapter, manifest, scan, GET /runs (JD×2)
PR #67  feat/run-history-lifecycle (PR-2)         MERGED  SDD#3 PR#2 — lifecycle: lazy hydration, DELETE, retry, 48h sweep (JD×2)
PR #68  fix/run-history-manifest-fields           MERGED  SDD#3 fix — SA-5: manifest registro field mismatch
PR #69  feat/run-history-ui (PR-3)                MERGED  SDD#3 PR#3 — frontend: hamburger, /historial, cold-load, localStorage; fix infinite poll
PR #70  fix/delivery-blockers-56-60               MERGED  Delivery: #56 air-gap OCR bundling + #60 in-container acceptance gate (make verify/verify-fast)
```

- **Acceptance gate**: `make verify` (full 493-page, ~90 min) or `make verify-fast` (3-section
  ~50-page subset, ~15 min) → R8 MATCH + R9 divergence over the real API. Strict mode fails on any
  missing precondition. Backend host port is `CTR_BACKEND_PORT` (default 8010, off :8000).

- **Test counts**: ~1568+ backend targeted (+~120 run-history tests; real-data gate intact) + 376 frontend
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

### SDD#2 — discarded-pages-recovery — COMPLETE & MERGED (PR #61/#63/#64/#65)

**Delivered**: zero-silent-drop at the QR-evidence gate (issue #50 closed); `DiscardedPage`
domain model (pure, no IO/SDK); `PipelineResult.discarded_pages` + backward-compat cache hydration;
`GET /table` surfaces `discarded_pages: DiscardedPageResponse[]`; OCR-first 3-tier recovery
(`apply_page_recovery`): cached-lines → OCR re-run → vision fallback; `recover_discarded_page`
hook + sidecar replay (mirrors `add_recovered_guia`); batch endpoints
(`POST /recover-batch`, `GET /recover-status`); [Descartadas para revisión] tab (A1 grouped
contiguous runs, A2 collapsed + lazy thumbnails, A3 tri-state selection, A4 mount re-attach);
ETA confirm dialog; bulk fire + poll-until-done; completion summary.

**Full-PDF evidence**: 469 = 126 (assembled) + 343 (discarded); 11 contiguous page-runs.
**Archived**: `openspec/changes/archive/discarded-pages-recovery/`.
**Deferred from SDD#2**: history/persistence hamburger menu (now SDD#3 candidate).

### SDD#3 — run-history-persistence — COMPLETE & MERGED (PR #66/#67/#68/#69)

**Delivered**: cross-restart run history live. Per-run manifest (`run_manifest.json`) written at
`_run_pipeline_background` composition boundary (non-fatal, atomic, `pipeline.py` zero-diff).
Startup scan indexes all run dirs (manifest-full or degraded legacy). `GET /runs` sorted
newest-first + lazy 48h failed-sweep. `DELETE /runs/{id}` (UUID-validate, 409 guard, rmtree
own-dir). `POST /runs/{id}/retry` (dir reset keep PDF+sunat/, same run_id, re-fire pipeline).
Lazy hydration dep (`_get_hydrated_entry`) replaces `_require_run` — full editing on past runs
without eager startup hydration. Frontend: hamburger menu [Nuevo]/[Batch actual]/[Historial] +
`/historial` route + `ReviewPage` cold-load (runStore.runId from route param) + localStorage
persistence. SA-5 caught manifest registro field mismatch + refetchInterval infinite polling.
Archived to `openspec/changes/archive/run-history-persistence/`. Spec promoted:
`openspec/specs/run-history/spec.md`.

### Remaining backlog (SDD#4 candidates)

| Candidate | Notes |
|-----------|-------|
| **#56** Air-gap: RapidOCR model re-download | Deploy concern — bake PP-OCRv5-server weights at build time |
| **#57** Deadline-guard `DEADLINE_S` env var | Expose as runtime env var (not baked-in constant) |
| **#58** Magnitude guard | Guard against implausible quantity magnitudes (OCR digit noise) |
| **#59** Canonicalization Tier-1 hardening | Dual-spec normalization edge cases |
| **#60** `make verify` / containerized-verify gate | Automate Makefile gate in CI |
| **#62** Recovery hardening | Edge cases: page rendering errors, corrupt cached lines |
| **#44** Cross-model consensus | kimi+qwen: agree→accept, disagree→`requires_review`. Lower priority now OCR is primary |
| **#45** Stale status endpoint | `/table` is the fresh source; status endpoint is cosmetic |
| **#41** Deadline-guard cancel | Cancel in-flight httpx on server context vs abandon |
| **#43** Unit-map consolidation | Single domain source for `UNIT_LABEL_MAP` / `_SUNAT_UNIT_MAP` |

**Housekeeping**:
- `openspec/changes/guia-reprocess-staged-flow/` — stale change folder to archive (never shipped)
- `docs/playwright/` — stray `.json`/`.mjs` SA-5 artifacts (gitignored; leave as-is or clean)

## 4. Open issues

| Issue | Severity | Description |
|-------|----------|-------------|
| ~~**#50**~~ | ~~High~~ | **CLOSED** (SDD#2). Zero-silent-drop: 469 = 126 + 343 proven on real PDF. |
| ~~**#56**~~ | ~~Medium~~ | **CLOSED** (PR #70). Air-gap: 3 ONNX models bundled at build (synthetic-text warm-up + disk guards); `--network none` proven. |
| ~~**#60**~~ | ~~Med~~ | **CLOSED** (PR #70). `make verify` acceptance gate rebuilt (was dead); strict mode + verify-fast subset. |
| **#57** | Low | `DEADLINE_S` baked-in constant; expose as runtime env var. |
| **#58** | Low | Magnitude guard against implausible OCR qty values (digit noise). |
| **#59** | Low | Canonicalization Tier-1 edge cases (dual-spec normalization). |
| **#60** | Low | `make verify` / containerized-verify gate automation. |
| **#62** | Low | Recovery hardening: page rendering errors, corrupt cached lines. |
| **#44** | Medium | Cross-model consensus reprocess (qwen+kimi). Lower priority now OCR is primary. |
| **#45** | Low | Run status endpoint stale after reprocess. Use `/table` as fresh source. |
| **#41** | Low | Deadline-guard abandons in-flight httpx request (still billed). Cancel instead. |
| **#43** | Low | 3-map unit normalizer consolidation into single domain source. |
| **OCR-F-1** | Low | Above-table spurious-anchor residual (PR#4 deferred follow-up). Zero corpus evidence. See `docs/DECISIONS.md` §2026-06-10. |

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
