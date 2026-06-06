# CLAUDE.md — ctr-rosales-qc

> Auto-loaded each session. Read `docs/HANDOFF.md` first when resuming — it has the exact
> next steps. This file is the durable project contract (LLM-first).

## What this is

Local-first QC tool for a civil-engineering quality engineer. Ingests a 493-page Autodesk
Forma PDF (`CTR-PLC01-FR001 Recepción de Materiales en Obra`) and reconciles, per **Registro
N°**, the **declared** materials (digital text: detail Notes +
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
- **Reception-date authority** (rev-3 R9; corrected 2026-06-03): the declared reception date is the
  **DIGITAL printed `Fecha:` on the Protocolo de Recepción** — deterministically parsed from the PDF
  text layer (`digital_text_extractor._parse_date_ddmmyy`, real year included, **NO vision**), linked
  to the Registro N° — NOT the GRE date. **Correction**: the Protocolo `Fecha:` is printed (Forma),
  **not handwritten**; the prior "handwritten Protocolo, vision-read" premise (#2709) was a
  misinterpretation — **handwritten dates exist ONLY on the guías** (stamp + signature). The Protocolo
  date is the **upper authority (límite máximo)**: every guía in that Registro **should carry that same
  date**. A guía whose **handwritten** (vision-read) date **diverges** — **earlier OR later**, compared
  by **day-month**; year is vision-unreliable and reconstructed by bounded inference — is an **assembly
  error** (whoever built the Protocolo misfiled the guía) → non-blocking **WARNING** that flags the guía
  `requires_review` with its **page number** and a **red highlight** (individual or per-registro group)
  so the operator can **report it or reassign the guía to the correct Registro**. Never auto-corrected.
  The divergence review logic is unchanged — only the **declared-side source** is digital, not vision.
- **Reception-date floor = guía SUNAT delivery date** (rev-3 R9b, MUST): a resolved reception date
  can **NEVER be earlier than that guía's `fecha_entrega`** (SUNAT GRE delivery) — goods cannot be
  received before they are delivered. If the resolved date falls before `fecha_entrega` (or cannot
  be placed at/after it within the inference window), **fall back to `fecha_entrega`** AND raise a
  non-blocking **verify WARNING** flagging the guía `requires_review`. This is the **lower bound**
  paired with the Protocolo **upper authority** above; `fecha_entrega` was previously only the
  year-inference lower bound (`infer_reception_year`), now a **full-date floor** on the resolved
  value. Only active when SUNAT is enabled (off by default → no `fecha_entrega` → no floor; graceful).
  Never auto-corrected beyond the physical-invariant floor; always flagged for human review.
- **Reception-date ceiling = Protocolo date** (rev-3 R9c): the guía reception date should not EXCEED
  the Registro's Protocolo authoritative date (the upper bound). When it does, clamp DOWN to the
  Protocolo date (`domain/date_ceiling.py::apply_reception_ceiling`, applied in `reconcile` AFTER the
  R9 divergence check so the divergence WARNING is **never masked**). **Crossed-bounds anomaly**: if
  `fecha_entrega` (delivery floor) **>** Protocolo (ceiling) — physically impossible (goods delivered
  after declared reception; likely a Protocolo-assembly human error) — **do NOT clamp** (never push
  below the SUNAT delivery floor); keep the read date and flag the distinct `delivery_after_protocolo`
  WARNING + `requires_review`. SUNAT `fecha_entrega` is **persisted on `GuiaDeRemision`** so the
  `[floor, ceiling]` bracket survives the ReviewService re-reconcile (reassign/edit), not just the
  pipeline. Floor + ceiling are an additive side-channel: NEVER touch the group key/status/delta/qty.
- **Three identifiers, don't confuse them**: Contents-ID `#4252` (section) ≠ Registro N° `232`
  (business key, group by this) ≠ QR `serie-numero` (deterministic guía id from rev-2).
- Input PDF is **read-only**; each run writes its own isolated output dir. **Local-first**:
  QR decode is local; SUNAT fetch (deferred) would break the air-gap and is opt-in/off.
- **Dual-spec normalization + grade-tolerant recovery (feat/guia-reprocess-reprocesar-ia, JD-APPROVED)**:
  `A615/A706` ≡ `A615A706` (no-slash, physical guía) ≡ `A6151A706` (OCR digit noise) ≡ `A615-A706` ≡
  `A615 A706` — all normalize to the same canonical grade `A615 G{n}`. Grade detection is
  **context-anchored** (g-prefix tokens or post-family numeric `{2,3}` digits); incidental numbers
  (`lote 119`) and diameter leads (`1"`, `1 3/8"`) are NEVER misread as grades. Valid grades are
  DISTINCT: G60 / G42 / G75 — **NEVER collapse G42/G75 into G60**. An illegible grade token (OCR
  misread like `580`/`680`) → `parse()` returns **None**, triggering **Tier-2 grade-tolerant recovery**:
  `_apply_grade_tolerant_recovery` (pre-pass before grouping) adopts the UNIQUE same-registro declared
  item's grade; zero or >1 declared match → stays UNRESOLVED. Sets `match_method="grade_tolerant"` +
  `requires_review=True` — never a silent auto-accept. See `.claude/skills/material-canonical-matching`
  for full algorithm.

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

**Branch `feat/guia-reprocess-reprocesar-ia` — ALL gates passed, push DEFERRED pending vision quantity-accuracy eval.**

PR#3 (Reprocesar con IA) + canonical-matching fix VALIDATED:
- sdd-verify PR#3 (PASS-WITH-WARNINGS, 0 CRITICAL, 272 backend + 272 frontend vitest green, vue-tsc clean).
- **Canonical-matching fix**: dual-spec normalization (Tier 1) + grade-tolerant recovery (Tier 2) — JD-APPROVED after 3 rounds. Fixes 11 real UNRESOLVED → 3 deterministic + 8 grade_tolerant+requires_review (OCR misreads 580/680/660). JD CRITICAL fixes: illegible-grade guard context-anchored (not whole-string scan); `{2,3}` digit quantifier excludes diameter leads (`1"`, `1 3/8"`) — data-corrupting regression caught by JD that the green suite masked.
- SA-5 Playwright runtime validation COMPLETE: grade_tolerant rows render in UI with requires_review badges; reprocess button gated on retry_attempted; table invalidation on reprocess-success.

**Push deferred**: `kimi-k2.5:cloud` is fastest for table reads but **quantity accuracy unverified** — observed read 0.091 vs guía 191. `requires_review=True` is the safety net; a qwen-vs-kimi quantity-accuracy eval is the **TOP backlog item** before prod use. **Vision model findings**: kimi-k2.5:cloud fastest+reliable (avg 6-10s); qwen3.5:397b-cloud reliable but slower (9-14s, needs `DEADLINE_S≥45`); qwen3.5:9b TOO WEAK for table extraction (returns generic `ACERO DIMENSIONADO`, no quantities). Config for reprocess: `provider=ollama, OLLAMA__MODEL=kimi-k2.5:cloud, DEADLINE_S=60`. **Deadline-guard follow-up**: under throttle the abandoned in-flight request is still billed (thread not cancelled); in the long-running server context the httpx request should be cancelled instead of abandoned (not blocking PR#3).

**Test counts**: 886+ backend targeted + 188+ frontend vitest passing (monolithic `pytest -q` still hangs on paddle import — targeted paths only). Real-PDF gates pass on the subset.

`ocr.enabled=false` is a config escape hatch (NullOcrExtractor → ZERO paddle) for machines
where the paddle runtime is broken; SUNAT then supplies quantities. `vision.enabled=false`
(`RECONCILIATION__VISION__ENABLED=false`) is the symmetric vision escape hatch (NullVisionAdapter
→ ZERO LLM calls): a deterministic **SUNAT-authoritative date mode** where guía reception dates
resolve to SUNAT `fecha_entrega` via the existing R9b Rule-2 floor (declared date stays the
digital Protocolo parse). Deterministic (real ETA) + air-gap-friendly. **Fail-fast invariant**:
`AppConfig` rejects `vision.enabled=false` + `sunat.enabled=false` (no date source). Caveat:
`fecha_entrega` is delivery = a lower bound used AS reception — safe because divergence is still
flagged `requires_review`. Vision can run local (Ollama) or cloud (Ollama-cloud
`qwen3.5:397b-cloud`, openai-compatible base_url) — config only, never a vendor binding. Compose
Ollama base_url/model are env-configurable (port 11435).

## Map

- `docs/HANDOFF.md` — resume-here guide (read first; §known-open-rev3b + §follow-ups + §infra).
- `docs/DECISIONS.md` — every decision + audit finding (engram mirror; §dates, §rev-3 R8/R9).
- `docs/MATERIAL-MATCHING.md` — R8 canonical-key domain reference.
- `docs/ARCHITECTURE.md` — folder layout, pipeline, how to run.
- `backend/Dockerfile` + `docker-compose.yml` + `Makefile` — r10 containerized verification.
- `openspec/specs/` — 8 promoted capability specs (reconciliation, extraction, ingestion, review, export, material-matching, fecha-divergence, containerized-verification).
- `openspec/changes/archive/` — 4 closed change folders (material-reconciliation, r8, r9, r10).
