# CLAUDE.md — ctr-rosales-qc

> Auto-loaded each session. Read `docs/HANDOFF.md` first when resuming — it has the exact
> next steps. This file is the durable project contract (LLM-first).

## What this is

Local-first QC tool for a civil-engineering quality engineer. Ingests a 493-page Autodesk
Forma PDF (`CTR-PLC01-FR001 Recepción de Materiales en Obra`) and reconciles, per **Registro
N° + handwritten reception date**, the **declared** materials (digital text: detail Notes +
Protocolo de Recepción) against the **sum of materials** from scanned **guías de remisión**.
Flags mismatches, lets the engineer reassign misfiled guías, exports xlsx/csv.

## Architecture (do not violate)

- **Hexagonal / Ports & Adapters.** Domain core is PURE — never import an SDK, framework, or
  IO library in `backend/src/reconciliation/domain/`.
- **`application/pipeline.py` depends only on domain ports** (Protocols) + config/run_context.
  No concrete adapter imports (Dependency Inversion).
- **Adapters lazy-import heavy deps** (`paddleocr`, `anthropic`, `openai`, `pyzbar`,
  `zxing-cpp`) inside methods so the test suite runs without them installed.
- **Vision is provider-agnostic** behind `VisionLLMPort`: Anthropic, or OpenAI-compatible
  (OpenAI cloud + Ollama via `base_url`). Selected by config `provider:`. Never bind the
  domain to a vendor.
- Deterministic single pipeline (no agent/orchestration framework).

## Domain rules (invariants — encode as MUST, never silently break)

- Group by `(registro, material_canonical, unidad)` — **`fecha` is NOT a grouping axis** (rev-3
  R8/MAT-001). Including it split declared↔guía groups whenever the vision-read date differed
  (year unreliable), killing MATCH. Material reconciliation is date-independent.
- Units **KG/TN/RD/Rollo summed independently — NEVER converted**.
- Classify pages by **TITLE**, not supplier name (Aceros Arequipa is on non-guía sheets too).
- **MATCH tolerance EXACT (0)**; **confidence auto-flag at 0.85**; MISMATCH always flags.
- Reconciliation vs the trusted digital declared side **is the OCR validation gate** —
  mismatches are flagged for human review, never auto-corrected.
- **Reception-date authority** (rev-3 R9): the declared reception date is the **HANDWRITTEN
  `Fecha:` on the Protocolo de Recepción** (vision-read), linked to the Registro N° — NOT the
  electronic `fecha_declarada` nor the GRE date. Guías should carry that same handwritten date.
  A guía whose handwritten date **diverges** (compared by **day-month**; year is vision-unreliable
  and reconstructed by bounded inference) is a **misfiled signal** → non-blocking no-match
  **WARNING** that flags the guía `requires_review` with its **page number** and a **red highlight**
  (individual or per-registro group) for human review + manual reassign. Never auto-corrected.
- **Three identifiers, don't confuse them**: Contents-ID `#4252` (section) ≠ Registro N° `232`
  (business key, group by this) ≠ QR `serie-numero` (deterministic guía id from rev-2).
- Input PDF is **read-only**; each run writes its own isolated output dir. **Local-first**:
  QR decode is local; SUNAT fetch (deferred) would break the air-gap and is opt-in/off.

## Working agreements

- SDD methodology, hybrid artifact store (engram + openspec). **Engram is local to one
  machine** — persist durable knowledge to `docs/` so it travels with the repo.
- Apply in **reviewable slices**; run a **real-data e2e check** before trusting green unit
  tests (they once passed while the pipeline was broken — see `docs/DECISIONS.md` §audit).
- Conventional commits; **no AI attribution / Co-Authored-By** in commits.
- Tooling: use `bat`/`rg`/`fd`/`eza`, not `cat`/`grep`/`find`/`ls`.
- Before publishing/pushing: **judgment-day** (adversarial review) is a required gate.

## Status & next steps

Backend complete + e2e-validated (455 tests). Frontend complete (85 tests). A design rev-2
delta (QR identity tier + guía-granularity review) is specced and ready. **Resume at the
spec-delta slice** — see `docs/HANDOFF.md` §3 for the exact ordered steps.

## Map

- `docs/HANDOFF.md` — resume-here guide (read first).
- `docs/DECISIONS.md` — every decision + audit finding (engram mirror).
- `docs/ARCHITECTURE.md` — folder layout, pipeline, how to run.
- `openspec/changes/material-reconciliation/` — proposal, specs, design (rev-2 §A–F), tasks, state.
