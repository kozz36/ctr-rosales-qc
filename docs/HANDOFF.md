# HANDOFF — material-reconciliation (read this first on a new workstation)

> **Why this file exists:** the engram memory used during development is **local to the
> original machine** and does NOT travel with the repo. This document (plus the other
> files in `docs/`) is the versioned source of truth for continuing the work anywhere.

Last session: **2026-06-02** (long session). Rev-3 + **R8 (canonical matching)** + **R9
(fecha-divergence)** + **r10 (containerized cloud-vision verification)** all implemented +
committed. Branch `feat/rev2-identity-domain` — **NOT yet pushed**. **Resume at §3 REVISED
plan: sdd-verify → judgment-day (fixes the §known-open-rev3b issues) → archive → visual
validation (now last).** The full e2e was blocked by transient external cloud/SUNAT throttling.

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
verify ⏳   judgment-day ⏳   archive ⏳
```

- **Backend**: 590 unit tests + integration gates passing (r1: 10/10, r2: 7/7, r3 airgap:
  3/3). Branch `feat/rev2-identity-domain`.
  Runs: `cd backend && uvicorn reconciliation.infrastructure.api.main:app --reload`.
- **Frontend**: 175 vitest passing, 0 TS errors. `cd frontend && npm install && npm run dev`.
- **Heavy deps**: `pyzbar`, `zxing-cpp`, `pillow`, `numpy`, `paddleocr`, `openai` installed
  in the dev venv (uv, Python 3.12) on the development machine this session.
- **Ollama models pulled**: `qwen3.5:9b` (6.6GB, vision — the selected local model).
- **PaddleOCR status**: paddle 3.3.1 + paddleocr 3.6.0 API compat fixed (R5); BUT
  `predict()` raises `NotImplementedError` (oneDNN/PIR CPU bug on this machine). Graceful
  degradation active — runs complete with OCR quantities empty + `_ocr_failed=True` warning.
  See `docs/DECISIONS.md` §rev-3 R6–R7.

## 3. RESUME HERE — REVISED plan (user, 2026-06-02 session close)

R8 (canonical matching), R9 (fecha-divergence), `ocr.enabled`, and r10 (containerized
cloud-vision verification) are all IMPLEMENTED + COMMITTED on `feat/rev2-identity-domain`
(NOT pushed). 766 backend + 188 frontend unit tests green (verified). The strategy CHANGED:
**defer the visual validation to the very end (after archive), defer the known issues below
to be fixed in the verify/JD fix phases, and drive the close-out as verify → JD → archive.**

Run in THIS order next session:

1. **sdd-verify** (full rev-3 + R8 + R9 + r10 vs spec/design/tasks). Surface the KNOWN ISSUES
   below as findings so JD fixes them.

2. **judgment-day** (canonical: blind dual review → FIX → re-judge). MANDATORY pre-push gate.
   Its fix phase MUST resolve the deferred KNOWN ISSUES (§known-open-rev3b).

3. **(within/after JD) the full-pipeline faithful e2e** — R8 MATCH (#4252 = 4.124 TN) +
   R9 divergence — run in the r10 container (`make`/`docker compose`, see §infra below).
   This is the trusted gate (HANDOFF §4). It was BLOCKED this session by transient external
   throttling — retry in a quiet window and/or after the pacing fix (KI-2).

4. **sdd-archive**.

5. **Visual validation** (Playwright MCP) — MOVED HERE, after archive. Drive the running app:
   upload → review table → drill-down/reassign/export + the R9 red-highlight/page-ref UI.

6. **Push + PRs** (user-gated). Do not push until JD passes.

### §known-open-rev3b — deferred KNOWN ISSUES (fix in verify/JD fix phases)

The code is correct where tested; these are the open items, NOT regressions. Verified-FIXED
already: vision read-timeout (6f188c3), max_retries=0 (4a135ad), **hard per-call wall-clock
deadline** (48bb268, confirmed firing live), paddle-free container + `ocr.enabled` (1a7ef2b).

- **KI-1 — VisionCapExceededError crashes the run.** When `vision.max_vision_calls` is hit,
  `pipeline._stage_extract_vision` RAISES instead of degrading gracefully (stop calling vision,
  leave remaining `fecha=None`/flagged). Fix: degrade, don't crash.
- **KI-2 — Cloud vision throttling.** `qwen3.5:397b-cloud` (Ollama cloud) throttles the
  pipeline's rapid sequential calls → every call >25s under load (isolated calls are 5-9s).
  Fix: add inter-call pacing (like SUNAT) and/or a local fallback. This is why the e2e didn't
  complete this session (deadline correctly degraded all calls; nothing was a code bug).
- **KI-3 — SUNAT subset re-fetch.** Intermittent `read operation timed out / retry` on the
  subset's guías under load; cross-run cache only works via the container named volume.
- **KI-4 — Full e2e not yet captured.** The end-to-end R8 MATCH + R9 divergence run has NOT
  completed once (blocked by KI-2/KI-3). MUST pass before final "done" per §4.

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
