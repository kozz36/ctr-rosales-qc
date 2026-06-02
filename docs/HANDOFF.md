# HANDOFF — material-reconciliation (read this first on a new workstation)

> **Why this file exists:** the engram memory used during development is **local to the
> original machine** and does NOT travel with the repo. This document (plus the other
> files in `docs/`) is the versioned source of truth for continuing the work anywhere.

Last session: **2026-06-02**. Rev-3 fully implemented (R1–R7, all 98 tasks complete). Branch
`feat/rev2-identity-domain` — **NOT yet pushed**. **Resume at visual e2e validation**.

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

## 3. RESUME HERE — exact next steps (in order)

All apply work is complete on branch `feat/rev2-identity-domain`. Do NOT push until the
gates below pass.

1. **Visual e2e validation** (Opus vision + Playwright MCP driving the running app).
   Start both servers (`make dev` from repo root or `make run` in `backend/` + `npm run dev`
   in `frontend/`). Verify: upload subset PDF → pipeline runs to status=review → reconciliation
   table renders → drill-down, reassign, export work. This is the pre-verify gate.

2. **Full-PDF run** (493 pages, registros 230–N). Required to confirm:
   - MATCH rows appear (subset PDF declared side returns `material=None` — see §known-open).
   - Stamp-crop upper-right works across all guía layouts (currently ~13/35 guías still
     null fecha on the subset run).
   - Quantities: either SUNAT enabled (`sunat.enabled=true` in config.yaml) or a working
     paddle runtime (see §rev-3 R6–R7 in `docs/DECISIONS.md`).

3. **sdd-verify** — formal spec/design/tasks validation. Run after visual e2e passes.

4. **judgment-day** — MANDATORY blind dual adversarial review + fix + re-judge before any push.

5. **sdd-archive**.

6. **Push + PRs** (user-gated). The stacked-to-main chain is PR-12 → PR-13 → PR-14 →
   PR-15 → PR-16 + R6/R7 commits. Do not push until judgment-day passes.

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
