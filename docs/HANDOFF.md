# HANDOFF — material-reconciliation (read this first on a new workstation)

> **Why this file exists:** the engram memory used during development is **local to the
> original machine** and does NOT travel with the repo. This document (plus the other
> files in `docs/`) is the versioned source of truth for continuing the work anywhere.

Last session: **2026-06-03** (close-out session). All gates passed: sdd-verify → JD core
(R8/R9/r10) → JD base (rev-2 areas) → KI-4 e2e captured → sdd-archive → visual validation.
Branch `feat/rev2-identity-domain` — **READY TO PUSH** (not yet pushed). **Only action
remaining: push + PRs (user-gated). See §3 REVISED — steps 1-5 all DONE.**

---

## 1. What this project is

A local-first QC tool for a civil-engineering quality engineer. It ingests a 493-page
Autodesk Forma PDF export (`CTR-PLC01-FR001 Recepción de Materiales en Obra`) and
reconciles, per **Registro N° + fecha de recepción**, the **declared** materials (digital
text from the detail page Notes + Protocolo de Recepción) against the **summed** materials
from the scanned **guías de remisión**. It flags mismatches, lets the engineer reassign
misfiled guías, and exports the reconciled table to xlsx/csv.

Full domain context: `docs/DECISIONS.md`. Architecture: `docs/ARCHITECTURE.md`.

## 2. Current state (DAG)

```
proposal ✅  spec ✅(rev-2+rev-3 delta)  design ✅ rev-2  tasks ✅(98/98 complete)
apply ✅ ALL COMPLETE — branch feat/rev2-identity-domain (NOT pushed)
verify ✅   judgment-day ✅   archive ✅   visual-validation ✅
```

- **Backend**: 886 unit/targeted tests passing. Run targeted (not monolithic — paddle import
  hangs `pytest -q` on this machine). Branch `feat/rev2-identity-domain`.
  Runs: `cd backend && uvicorn reconciliation.infrastructure.api.main:app --reload`.
- **Frontend**: 188 vitest passing, 0 TS errors. `cd frontend && npm install && npm run dev`.
- **Heavy deps**: `pyzbar`, `zxing-cpp`, `pillow`, `numpy`, `paddleocr`, `openai` installed
  in the dev venv (uv, Python 3.12) on the development machine.
- **Ollama models pulled**: `qwen3.5:9b` (6.6GB, vision — local model); `qwen3.5:397b-cloud`
  (Ollama cloud) used for the KI-4 e2e gate.
- **PaddleOCR status**: paddle 3.3.1 + paddleocr 3.6.0 API compat fixed (R5); BUT
  `predict()` raises `NotImplementedError` (oneDNN/PIR CPU bug on this machine). Graceful
  degradation active — runs complete with OCR quantities empty + `_ocr_failed=True` warning.
  See `docs/DECISIONS.md` §rev-3 R6–R7.

## 3. RESUME HERE — REVISED plan (user, 2026-06-03 close-out session)

**ALL STEPS COMPLETE. Only action remaining: push + PRs (user-gated).**

R8 (canonical matching), R9 (fecha-divergence), `ocr.enabled`, and r10 (containerized
cloud-vision verification) were implemented + committed. Close-out proceeded:

1. ✅ **sdd-verify** — PASS-WITH-WARNINGS (R8/R9/r10 + base material-reconciliation).
2. ✅ **judgment-day core (R8+R9+r10)** — APPROVED after 3 rounds. Fixed: C1 stale gate
   test, C2-A cross-registro match_method/requires_review pollution, C2-B ISO date y-m-d
   parse order, KI-1 graceful vision-cap degrade (ba3b0c5), W1 dead-code concurrency shrink,
   W2-A/B declared reads cap + racy SUNAT pacing. Commits 7e5f897..ba3b0c5, 596704f..182d72a, a3069ad.
3. ✅ **judgment-day base (rev-2 areas)** — APPROVED after 2 rounds. Fixed: guía line-edit
   dead feature (HTTP 422), restart data-loss (guia_line_edit not replayed; vision_audit
   destroyed on first review mutation), section-ID-as-Registro guard, idempotent reassign.
   Commits 010036c, ca65b0b, a0aeb99.
4. ✅ **KI-4 faithful e2e captured** — `TestR9RealPDFGate` 5/5 PASS in 6:05 on pages 1-25
   subset. See §known-open-rev3b for recipe.
5. ✅ **sdd-archive** — 8 capability specs → `openspec/specs/`; 4 changes →
   `openspec/changes/archive/`. Commit ef15a61.
6. ✅ **Visual validation** (Playwright) — review table, R8 MATCH "Conforme" 4.124 TN, R9
   divergence badges + requires_review + year-inferred + page-refs, filters, drill-down,
   XLSX+CSV export 13 cols including Método/Revisión/Año-inferido — 0 console errors.

7. **Push + PRs** (user-gated). Branch is READY.

### §known-open-rev3b — deferred KNOWN ISSUES (fix in verify/JD fix phases)

The code is correct where tested; these are the open items, NOT regressions. Verified-FIXED
already: vision read-timeout (6f188c3), max_retries=0 (4a135ad), **hard per-call wall-clock
deadline** (48bb268, confirmed firing live), paddle-free container + `ocr.enabled` (1a7ef2b).

- **KI-1 — VisionCapExceededError crashes the run. [FIXED — ba3b0c5]** When
  `vision.max_vision_calls` was hit, `pipeline._stage_extract_vision` RAISED instead of degrading.
  Now it degrades gracefully (stops calling vision, leaves remaining `fecha=None`/flagged); no raise.
- **KI-2 — Cloud vision throttling.** `qwen3.5:397b-cloud` (Ollama cloud) throttles the
  pipeline's rapid sequential calls → every call >25s under load (isolated calls are 5-9s).
  Fix: add inter-call pacing (like SUNAT) and/or a local fallback. This is why the e2e didn't
  complete this session (deadline correctly degraded all calls; nothing was a code bug).
- **KI-3 — SUNAT subset re-fetch.** Intermittent `read operation timed out / retry` on the
  subset's guías under load; cross-run cache only works via the container named volume.
- **KI-4 — [CAPTURED — 2026-06-03]** `TestR9RealPDFGate` 5/5 PASS in 6:05. Subset recipe:
  pages 1-25 of the full PDF (contents + Registro 232 block) → `/tmp/ctr_section1.pdf`.
  Run: `docker compose run --rm -v /tmp/ctr_section1.pdf:/data/section1.pdf:ro -e CTR_PDF_PATH=/data/section1.pdf -e OLLAMA_BASE_URL=http://localhost:11435/v1 backend python -m pytest tests/integration/test_pipeline_r9_gate.py::TestR9RealPDFGate -p no:cacheprovider -v -s`.
  Result: #4252 1/2"×9M = 4.124 TN MATCH deterministic + R9 date/divergence confirmed on
  real data. Full-PDF run is impractical under KI-2 throttling; the subset is the tractable
  fixture. KI-2 and KI-3 remain open environment limitations (not code bugs).

### §follow-ups — post-merge SDD slices (deferred)

These are NOT blockers for push; they are the next SDD changes **after PR + merge**
(user decision 2026-06-03):

- **Reception-date floor = guía delivery date (DOMAIN RULE, MUST)**: the vision-read reception
  date can **never be before the guía's SUNAT delivery date** (`fecha_entrega`). If OCR/vision
  reads a date **earlier** than the guía's `fecha_entrega`, **use `fecha_entrega` as the
  fallback** and raise a non-blocking **"verify" WARNING** (flag `requires_review`). Physical
  invariant: goods cannot be received before they are delivered. Today `fecha_entrega` is only
  the **year**-inference lower bound (`pipeline.py:1308 lower = official.fecha_entrega`,
  `infer_reception_year`); extend it to a **full day-month floor** on the resolved date for both
  the guía stamp read and (where applicable) the Protocolo reception date.
- **disable_thinking by default (DECISION — do it)**: set `VisionConfig.disable_thinking`
  default to `true` (and `RECONCILIATION__VISION__DISABLE_THINKING=true` in compose). The user
  confirms from other projects that **disabling `<think>` improves OCR/vision captures** (not
  just speed) — a decision, not an A/B experiment. `qwen3.5:397b-cloud` otherwise spends
  ~12s/call in `<think>`.
- **`.env.example` (config delivery)**: write a complete `.env.example` at the repo root with
  every overridable setting documented (compose interpolation `OLLAMA_BASE_URL` / `OLLAMA_MODEL`
  + the `RECONCILIATION__*` app config incl. `DISABLE_THINKING`); the user copies it to `.env`.
  Note: the agent's Write/Bash to `.env` is permission-blocked, so `.env.example` is the channel.
- **Determinate progress bar (UX)**: current pipeline progressbar is indeterminate. Add
  stage+count reporting in `GET /runs/{id}` + a determinate frontend bar with ETA for
  operator monitoring.
- **Date-read variance (verify)**: a visual run read Registro 232 declared fecha as 2026-05-26
  vs the smoke run's 2026-05-28 — likely cloud-vision non-determinism on the handwritten day;
  the reception-date floor rule above should largely neutralize its downstream effect.

### §infra — how to run the faithful e2e in the r10 container

- Image: `docker compose build backend` (target `test` = paddle-free + pytest + tests).
- Networking: compose uses `network_mode: host` (Linux) so the container reaches host Ollama
  at `localhost:11434` — `host.docker.internal` does NOT work on Linux (Ollama binds 127.0.0.1).
- Run gates: `docker compose run --rm -e CTR_PDF_PATH=/data/input.pdf backend python -m pytest
  tests/integration/test_pipeline_r8_gate.py::TestRealPDFGate
  tests/integration/test_pipeline_r9_gate.py::TestR9RealPDFGate -p no:cacheprovider -v -s`.
- Fast iteration: a 20-page section-1 subset is at `/tmp/ctr_section1.pdf` (mount it +
  `-e CTR_PDF_PATH=/data/section1.pdf`). Quality timer: section-1 should finish < ~3 min;
  if it drags to the 6-min cap, the cloud is throttled (KI-2).
- Vision deadline: `RECONCILIATION__VISION__DEADLINE_S` (default 20s) hard-bounds each call.

### Runtime requirements for a real run

| Mode | Requirements |
|------|-------------|
| Air-gapped (local-only) | Ollama running with `qwen3.5:9b` pulled; working paddle runtime (not this env) for OCR quantities; `sunat.enabled=false` |
| SUNAT-enabled (breaks air-gap) | `sunat.enabled=true` in `config.yaml`; network to `e-factura.sunat.gob.pe`; Ollama `qwen3.5:9b` for vision |
| Validation shortcut | SUNAT-enabled + qwen3.5:9b covers both quantities and dates; paddle not required |

Env setup done 2026-06-02 on the development machine:
```bash
uv venv --python 3.12 && uv pip install -e ".[dev,ml,llm,identity]"
# then: pyzbar, zxing-cpp, pillow, numpy, paddleocr, openai all present
# ollama pull qwen3.5:9b  (done)
```

## 4. Hard-won lessons (do not relearn these)

- **Unit tests passed while the real pipeline was broken.** This happened four times in
  rev-3 alone: classification gap, container identity-port wiring, paddle API format,
  SUNAT parser format (see `docs/DECISIONS.md` §recurring-mock-gap). Always run a real-data
  e2e check that does NOT inject fake page content via `HybridDocSource`.
- **Scanned guía pages have NO "GUÍA DE REMISIÓN" in their digital text layer** — only the
  Autodesk Forma header. The classifier must use QR presence (Condition A) or heuristic
  (Condition B). Never rely on digital-text title alone for scanned pages.
- **qwen3.5:9b reads day-month reliably from full-page 200dpi input; year is unreliable.**
  Year is reconstructed via bounded inference (`delivery_GRE_date <= reception <= doc_date`).
- **The stamp crop region is UPPER-RIGHT on CTR guías** (x0=0.55, y0=0.05, x1=1.0, y1=0.45).
  Do not assume lower-right without verifying against physical guías.
- **qwen3.5:9b is a thinking model** — it emits `<think>…</think>` blocks before content.
  `max_tokens=128` is exhausted by the thinking phase. Use ≥4096.
- **SUNAT descargaqr requires COLOR QR decode at 200dpi+400dpi** — the URL-variant QR is
  missed by grayscale-only decode. Use `pyzbar` AND `zxing-cpp`, union both results.
- **Three identifiers, never confuse**: Contents-ID `#4252` (section) ≠ Registro N° `232`
  (business key) ≠ QR `serie-numero` (deterministic guía id). Group by the Registro N°.
- **The grouping date is the HANDWRITTEN reception date** (vision-read), NOT the electronic
  GRE date. SUNAT date is a year-inference lower bound only — never the grouping key.
- **Units (KG/TN/RD/Rollo) are summed independently — never converted.**
- **Classify pages by TITLE, not supplier name** (Aceros Arequipa appears on non-guía sheets).

## 5. Engram → docs mapping (knowledge that was local-only, now versioned here)

| Engram topic | Versioned in |
|---|---|
| stack, domain-rules, llm-provider-abstraction | `docs/DECISIONS.md`, `docs/ARCHITECTURE.md` |
| design rev-2 (A–F), locked-defaults | `docs/DECISIONS.md`, `openspec/.../design.md` |
| e2e-integration-findings (5 bugs) | `docs/DECISIONS.md` §audit |
| frontend-review-findings (2 criticals) | `docs/DECISIONS.md` §frontend-review |
| qr-sunat-evaluation | `docs/DECISIONS.md` §QR |
| reception-date-authority | `docs/DECISIONS.md` §dates |
| rev-3 real-run findings (7 items, #2747–2760) | `docs/DECISIONS.md` §rev-3 |
| delivery-roadmap | this file §3 |

If you re-enable engram on the new machine, re-import by reading these docs; nothing is lost.
The engram IDs are recorded in `docs/DECISIONS.md` §engram-mirror-rev3 for cross-reference.
