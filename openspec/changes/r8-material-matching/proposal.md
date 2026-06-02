# Proposal — r8-material-matching

**Change**: `r8-material-matching`
**Phase**: proposal (done) → spec / design (next, parallel)
**Artifact store**: hybrid (engram + openspec)
**Date**: 2026-06-02
**Parent**: rev-3 R8 MATCH-resolution gap (see `docs/MATERIAL-MATCHING.md`, `docs/DECISIONS.md` §known-open)

---

## 1. Intent

### Problem
The declared side (Autodesk Forma digital text) and the guía side (SUNAT GRE PDF) name the **same physical rebar with different text**. The current reconciliation groups by exact material string, so on real data it produces **zero MATCH** — every declared row falls to `DECLARED_MISSING` and every guía to `GUIA_MISSING`. The extraction, classification, deskew, vision-date and per-unit summation stages all work end-to-end, but the table is useless because nothing ties the two sides together.

Real proof (section #4252):
- declared `BARRA AG615/A706 G60 1/2" x 9M = 4.124 TN`
- guías pages 5+6+8 summing `4.124 TN`
- same physical item, two different descriptions, no match.

### Why now
This is the **last gap blocking the tool's core value**. Everything around reconciliation is built and e2e-validated (455 backend tests, 85 frontend tests); the reconciliation engine itself runs but cannot match. Until the canonical key lands, the product cannot do the one thing it exists to do. The matching **strategy is already decided** by the user (deterministic-primary + LLM-fallback behind a port), so this is a small, tractable feature — not a redesign. The user explicitly chose to carve it as its own mini-SDD change for traceability.

### Success looks like
- Declared and guía contributions for the same physical item land in the **same group** via a canonical key, not raw text.
- The #4252 real case reconciles: `BARRA A615 G60 1/2" 9M / TN` declared `4.124` == summed guía `4.124` → **MATCH**.
- Every group reports a `match_method` (`deterministic` | `llm_inferred`); LLM-inferred rows are always `requires_review`.
- The deterministic path resolves the common descriptions with no model call; only the ambiguous tail invokes the local LLM.
- The OCR-validation-gate is preserved: matching changes *grouping*, never the trusted declared quantities, and never auto-corrects a mismatch.

---

## 2. Scope

### In scope
- **`CanonicalKey` value object** (pure domain) — the tuple `(familia, grado, diámetro, presentación, unidad)`, e.g. `BARRA · A615 G60 · 1/2" · 9M · TN`.
- **Deterministic `MaterialKeyNormalizer`** (pure domain) — regex-driven extraction of each tuple component from a raw description:
  - **grado collapse**: `a615`, `a615/a706 g60`, `ag615/a706 g60`, `a a615-g60` → `A615 G60`.
  - **diámetro normalization**: `8mm, 3/8", 1/2", 5/8", 3/4", 1", 1 3/8"`.
  - **presentación**: `9M` (straight bar, from `x 9m`) vs `DOB` (cut/bent, from `dob` / `dimensionado` / `apl`) — **never merged**.
  - **unidad** carried verbatim (KG/TN/RD/Rollo) — **never converted**, part of the key.
- **`MaterialInferencePort`** (pure domain Protocol) — provider-agnostic LLM fallback contract returning a structured tuple `{familia, grado, diámetro, presentación, confidence}` for descriptions the regex cannot resolve.
- **LLM adapter** (infrastructure) — Ollama via the existing OpenAI-compatible path (`qwen3.5:9b`, `temperature=0`, strip `<think>` blocks, JSON tuple out); lazy-imports the SDK; selected by config (reuses the existing provider-agnostic pattern).
- **Grouping change** in `ReconciliationService` — group by `(registro, canonical_key, unidad)` instead of raw material string; sum guía `cantidad` per group; compare to declared **EXACT (0)** → MATCH / MISMATCH / DECLARED_MISSING / GUIA_MISSING.
- **`match_method` field** on each reconciliation group (`deterministic` | `llm_inferred`); `llm_inferred` rows flagged `requires_review`.
- **Surface the flag** in the existing review grid (read-only display of `match_method` + `requires_review`) — minimal wiring, no new UI workflow.
- Tests: deterministic-normalizer unit tests over the real declared↔SUNAT pairs; a #4252 e2e match assertion; a stubbed `MaterialInferencePort` for the fallback path.

### Out of scope (this change)
- **SUNAT `código producto` join** (e.g. 407797) as an authoritative key — the Forma declared side currently has no código, so this is a future change once a declared↔código map exists.
- **PaddleOCR runtime / extraction accuracy** — matching consumes already-extracted descriptions; OCR quality is orthogonal and owned by the extraction stage.
- **Frontend changes beyond surfacing the flag** — no new editing, no reassignment-by-key UI, no match-method override control in this change.
- **Unit conversion** between KG/TN/RD/Rollo (forbidden domain invariant).
- **`fecha` folding** — the handwritten reception date stays out of the material key; a fecha divergence remains the misfiled-guía signal, handled by the existing reassignment path.
- New vision providers, batching changes, or persistence/DB changes.

---

## 3. Approach (deterministic-primary + LLM-fallback behind a port)

The matching logic is **pure domain**. The deterministic normalizer needs no IO and lives entirely in `backend/src/reconciliation/domain/`. The LLM fallback sits behind a domain `Protocol` (`MaterialInferencePort`) and is implemented by a lazy-importing infrastructure adapter — same Dependency-Inversion + Strategy pattern already used for `VisionLLMPort`, so no new architectural concept is introduced.

```
extracted description ─▶ MaterialKeyNormalizer (deterministic regex)
                              │
                  resolved ───┴─── ambiguous
                     │                │
                     │         MaterialInferencePort.infer()  (Ollama qwen3.5, temp 0)
                     │                │  → tuple + confidence, ALWAYS requires_review
                     ▼                ▼
                 CanonicalKey  ◀───────┘
                     │
        group by (registro, canonical_key, unidad) → sum guía → compare EXACT(0)
                     │
        MATCH / MISMATCH / DECLARED_MISSING / GUIA_MISSING  (+ match_method, requires_review)
```

| Stage | Layer | Responsibility | Adapter / Port |
|-------|-------|----------------|----------------|
| **normalize-key** | domain | Raw description → `CanonicalKey` tuple via regex | `MaterialKeyNormalizer` (pure) |
| **infer (fallback)** | domain port + adapter | Ambiguous description → tuple via local LLM, flagged | `MaterialInferencePort` → Ollama adapter (lazy-import, OpenAI-compatible) |
| **reconcile** | domain | Group by `(registro, canonical_key, unidad)`, sum, EXACT compare | `ReconciliationService` (modified grouping key) |

### Key rationale
- **Deterministic-first, not LLM-first.** Regex normalization is fast, auditable, zero-cost, and deterministic — it must resolve the common descriptions. The LLM is the **long-tail fallback only**, never the primary path. This keeps runs cheap and the match logic explainable.
- **LLM behind a port (Dependency Inversion + Strategy).** The domain depends on `MaterialInferencePort`, never on Ollama/OpenAI. The adapter lazy-imports the SDK so the test suite runs without it, consistent with the existing adapter contract. Reuses the OpenAI-compatible provider path already proven for vision.
- **LLM output is always flagged.** `match_method=llm_inferred` ⇒ `requires_review`. This **respects the OCR-validation-gate**: the declared side is trusted, inferred groupings are surfaced for human confirmation, mismatches are never auto-corrected. The model never silently changes a quantity or finalizes a match.
- **Presentación and unidad are key components, never normalized away.** `9M` ≠ `DOB`; KG/TN/RD/Rollo summed independently. The normalizer is forbidden from collapsing across either — enforced as a domain invariant in the value object.
- **`fecha` stays out of the key.** Grouping is `(registro, canonical_key, unidad)`. A fecha divergence is a separate signal (misfiled guía), already handled by reassignment — folding it in would mask that signal.

---

## 4. Risks & Mitigations

> **Assumption:** extracted descriptions are noisy and the supplier uses inconsistent text for the same item. The deterministic rules cover the known real pairs; the LLM covers the tail; human review is the final gate. We do not assume any target match-rate number for MVP.

| Risk | Runtime trigger | What breaks if ignored | Mitigation |
|------|-----------------|------------------------|------------|
| **Over-merge across presentación** | Normalizer maps `DOB` and `9M` to the same key | A bent-bar guía sums into a straight-bar declared row → false MATCH | `presentación` is a mandatory key component; explicit `9M`/`DOB` extraction with a deny rule; unit tests over both real pairs. |
| **Grade collapse too aggressive** | A non-A615 grade gets folded into `A615 G60` | Wrong item matched | Collapse only the enumerated dual-grade variants; unknown grades stay distinct and fall to the LLM/`requires_review` path, never silently merged. |
| **LLM hallucinated tuple** | qwen3.5 invents a diameter/grade not in the text | A spurious match passes | `llm_inferred` rows are ALWAYS `requires_review`; never auto-confirmed; declared quantity is untouched; human gate decides. |
| **LLM `<think>` leakage / non-JSON** | Reasoning model emits `<think>` or prose around the JSON | Parse failure → row dropped | Strip `<think>` blocks, `temperature=0`, strict JSON parse with a fallback to `requires_review` (unresolved key), never a crash. |
| **Diameter regex ambiguity** | `1 3/8"` parsed as `1` + `3/8"` | Wrong diameter key | Ordered regex (compound fraction before simple), normalized against the canonical diameter table; unit tests per diameter. |
| **Silent zero-match regression** | A future description variant matches nothing | Tool looks "green" but matches drop | A real-data #4252 e2e assertion guards the canonical match; unmatched rows surface as `DECLARED_MISSING`/`GUIA_MISSING`, never hidden. |
| **LLM unavailable (Ollama down)** | Local model not running | Fallback path errors | Adapter degrades to an unresolved-key result flagged `requires_review`; deterministic matches are unaffected; the run still completes. |

---

## 5. Rollback / Abort plan

- **Additive and isolated.** The change adds a value object + normalizer + port + adapter and modifies the `ReconciliationService` grouping key. No data migration, no external service to decommission.
- **Behind the existing pipeline contract.** The pipeline stages and ports are unchanged in shape; reverting the change branch restores raw-string grouping with no downstream cleanup.
- **LLM is opt-in/degradable.** Disabling the inference adapter (config) reverts to deterministic-only matching; the run still completes with the ambiguous tail flagged `requires_review`.
- **Per-run isolation preserved.** Matching is in-memory within a run; aborting discards only that run's output dir. Input PDF stays read-only.

---

## 6. Open questions (for spec/design)
- Exact `MaterialInferencePort` contract: return shape (`{familia, grado, diámetro, presentación, confidence}`), the `confidence`→`requires_review` policy (LLM rows always flagged regardless, but does a low deterministic-disambiguation confidence also flag?).
- Where the "ambiguous → fallback" boundary is drawn: which regex-failure conditions trigger the LLM vs. yield an unresolved key directly.
- Whether `match_method` and `requires_review` need to round-trip through the xlsx/csv export, or grid-display only for this change.
- Canonical diameter table edge cases beyond the seven known sizes (any `mm`-only or unusual fractions in the real data tail).
- Whether the LLM adapter reuses the existing `OpenAICompatibleVisionAdapter` config block or gets its own `inference:` provider config section.
