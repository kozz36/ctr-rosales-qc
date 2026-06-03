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

## Sub-agent discipline (orchestrator → implementation sub-agents)

Adapted from cnsic-agent SA-rules. Apply to every delegated implementation/fix.

- **SA-1 — Repeat critical instructions ×3.** Sub-agents drift; state each non-negotiable
  (strict-TDD, the invariant at risk, "do NOT push") more than once in the prompt.
- **SA-2 — Deviation → `status: partial`, never invent.** If a sub-agent hits an unauthorized
  design decision or an ambiguity not covered by spec/skills, it STOPS and reports — it does NOT
  improvise architecture or build something to fill the gap. **No build-for-the-sake-of-building.**
- **SA-3 — Ship is orchestrator-only.** Push, PR, merge belong to the orchestrator. Implementation
  sub-agents commit work-units but never push/PR. State this in every apply prompt.
- **SA-5 — Visible-UX features require runtime validation before "done".** Green unit tests with
  happy-path mocks prove signatures, NOT behavior. Any change to `frontend/src/**` or a
  user-visible flow MUST be validated against the RUNNING app via Playwright MCP (upload → review →
  the specific feature) before it is marked complete.
- **SA-6 — Envelope vs reality.** After apply, the orchestrator runs `git diff --stat` and compares
  claimed files/LOC vs actual; >20% drift is a process signal — re-audit.
- **SA-7 — Invariants are injected, not assumed.** Every implementation sub-agent prompt MUST embed
  the §Architecture + §Domain rules as hard anti-patterns (below) AND the relevant project skill
  paths (`material-canonical-matching`, `reception-date-authority`). Pass paths, not summaries.

## Architecture invariants — inject to every implementation sub-agent (auto-reject)

Hard anti-pattern checklist the implementation sub-agent AND the reviewer enforce (mirror of
§Architecture + §Domain rules):

- **Domain purity** — no SDK/framework/IO import under `domain/` (proven: suite runs with
  `anthropic` uninstalled). A heavy import there = auto-reject.
- **Ports at the boundary** — `application/pipeline.py` imports ZERO concrete adapters; depends only
  on Protocols + config/run_context. A concrete-adapter import in `application/` = auto-reject.
- **Lazy heavy deps** — adapters import `paddleocr`/`anthropic`/`openai`/`pyzbar`/`zxing-cpp`/
  `fitz`/`openpyxl` INSIDE methods, never at module top.
- **Vision provider-agnostic** — never bind domain or pipeline to a vendor; selection is config
  (`provider:`) behind `VisionLLMPort`.
- **`fecha` is NEVER a grouping axis** — key is `(registro, material_canonical, unidad)`.
- **Units never converted** (KG/TN/RD/Rollo summed independently). **Three identifiers never
  confused** (`#4252` ≠ Registro N° ≠ QR `serie-numero`; group by Registro N°).
- **Reconciliation is the validation gate** — mismatches/divergences are flagged `requires_review`,
  NEVER auto-corrected. **Input PDF read-only**; isolated output dir per run; local-first.

## Fix / Feature Discipline (mandatory — every implementation closes with)

1. **Strict-TDD**: a failing test FIRST that would fail without the change (when touching `*.py` /
   `frontend/src/**`); then green. Not required for docs/infra-only.
2. **Real-data over mock theatre** — unit-green ≠ correct: the suite once passed while the pipeline
   was broken (`docs/DECISIONS.md §audit`), and JD later found a totally-dead feature (guía
   line-edit always HTTP 422) hiding behind a green suite. Pair happy-path mocks with a real-data
   or runtime check; UI features → Playwright per **SA-5**.
3. **Conventional commit** as reviewable work-units; **no AI attribution**.
4. **judgment-day** before push for non-trivial code (mandatory gate); a single-pass second opinion
   via the `ctr-reviewer` agent for lighter PRs where full dual-blind JD is overkill.
5. Never mark a task done without 1–4.

## Status & next steps

**Close-out COMPLETE — branch `feat/rev2-identity-domain` ready to push.** All gates passed:
sdd-verify (R8/R9/r10 + base material-reconciliation, PASS-WITH-WARNINGS) → Judgment-Day
core R8+R9+r10 (APPROVED after 3 rounds; fixed C1 stale gate test, C2-A/B cross-registro
pollution + ISO date parse, KI-1 graceful vision-cap degrade, dead-code W1, racy W2-A/B) →
Judgment-Day base rev-2 areas (APPROVED after 2 rounds; recovered dead guía line-edit HTTP
422, stopped restart data-loss, section-ID-as-Registro guard, idempotent reassign) → KI-4
faithful e2e captured (TestR9RealPDFGate 5/5 PASS, 6:05, pages 1-25 subset, real cloud
vision, #4252 1/2"×9M = 4.124 TN MATCH deterministic + R9 divergence confirmed) → sdd-archive
(8 capability specs → `openspec/specs/`, 4 changes → `openspec/changes/archive/`) → visual
validation via Playwright (review table, R8 MATCH "Conforme" 4.124 TN, R9 badges + page-refs,
filters, drill-down, XLSX+CSV export 13 cols — 0 console errors). Only step remaining: push.

**Test counts**: 886 backend unit/targeted passing (targeted paths only — monolithic `pytest
-q` hangs on paddle import) + 188 frontend vitest. Real-PDF gates pass on the subset.

**KI-1 FIXED** (ba3b0c5, graceful vision-cap degrade). **KI-4 CAPTURED** (R9 gate 5/5 on
pages 1-25 subset; recipe in `docs/HANDOFF.md` §known-open-rev3b). **KI-2** (cloud
throttling) and **KI-3** (SUNAT under load) remain open environment limitations — the subset
sidesteps them. Three post-merge follow-ups deferred (see HANDOFF §follow-ups): disable_thinking
perf lever, determinate progress bar UX, date-read variance verify.

`ocr.enabled=false` is a config escape hatch (NullOcrExtractor → ZERO paddle) for machines
where the paddle runtime is broken; SUNAT then supplies quantities. Vision can run local
(Ollama) or cloud (Ollama-cloud `qwen3.5:397b-cloud`, openai-compatible base_url) — config
only, never a vendor binding. Compose Ollama base_url/model are env-configurable (port 11435).

## Map

- `docs/HANDOFF.md` — resume-here guide (read first; §known-open-rev3b + §follow-ups + §infra).
- `docs/DECISIONS.md` — every decision + audit finding (engram mirror; §dates, §rev-3 R8/R9).
- `docs/MATERIAL-MATCHING.md` — R8 canonical-key domain reference.
- `docs/ARCHITECTURE.md` — folder layout, pipeline, how to run.
- `backend/Dockerfile` + `docker-compose.yml` + `Makefile` — r10 containerized verification.
- `openspec/specs/` — 8 promoted capability specs (reconciliation, extraction, ingestion, review, export, material-matching, fecha-divergence, containerized-verification).
- `openspec/changes/archive/` — 4 closed change folders (material-reconciliation, r8, r9, r10).
