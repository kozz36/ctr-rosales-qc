# Tasks — r8-material-matching

**Change**: `r8-material-matching` · **Phase**: tasks · **Store**: hybrid · **Date**: 2026-06-02
**Branch**: `feat/rev2-identity-domain` (continuing; no new branch)
**Strict TDD**: active — `cd backend && uv run pytest`

Delta over `material-reconciliation` (Phases 0–7, Slice 1, Slice 2: all complete).
Adds canonical material key normalisation (deterministic-primary + LLM fallback) to
resolve the declared↔guía MATCH gap. Touches domain, application, adapters/inference,
report adapter, infrastructure wiring, and API schema. Hexagonal invariants preserved.

All tasks are ordered **test-first** per strict TDD mode. Each task produces a
green-test commit. Tests and code ship in the SAME commit (work-unit-commits skill).

---

## Review Workload Forecast

| Metric | Estimate |
|--------|----------|
| New files | 6 (domain: 3, adapters: 2, tests: 4+) |
| Modified files | 8 (models, ports, reconciliation, pipeline, container, xlsx_report, schemas, 1 test file each) |
| Estimated changed lines | ~520–620 |
| 400-line budget risk | **Medium-High** — exceeds budget but commits are on an unpushed feature branch; PRs are user-gated. Forecast is informational. |
| Chained PRs recommended | Not blocking — user will gate PR submission. Each task slice is independently committable per work-unit-commits skill. |
| Decision needed before apply | No — PRs deferred; proceed with work-unit commits on `feat/rev2-identity-domain`. |

---

## Task Dependency Graph

```
R8.1 (CanonicalKey VO + MatchMethod)
  └─▶ R8.2 (MaterialKeyNormalizer + tests — pure domain)
        └─▶ R8.3 (MaterialKeyResolver Strategy + cache + tests)
              ├─▶ R8.4 (domain models: MaterialLine.match_method + ReconciliationRow.match_method)
              │     ├─▶ R8.5 (ReconciliationService aggregation — worst-wins match_method)
              │     └─▶ R8.6 (MaterialInferencePort + MaterialKeyInference in ports/models)
              │               └─▶ R8.7 (OllamaMaterialInferenceAdapter + factory — adapters/inference/)
              │                         └─▶ R8.8 (InferenceConfig + AppConfig.inference — config.py)
              └─▶ R8.9 (pipeline _stage_normalize upgrade + ctor key_resolver defensive default)
                    (depends on R8.3, R8.4, R8.6, R8.8)
                    └─▶ R8.10 (container.py wiring: build_inference_adapter + key_resolver injection)
                          └─▶ R8.11 (xlsx_report + CSV: +Método column — export round-trip)
                                └─▶ R8.12 (API schema: ReconciliationRowResponse.match_method — read-only)
                                      └─▶ R8.13 (real-data e2e: #4252 MATCH assertion + regression guard)

Parallel opportunities:
  R8.4 parallel with R8.6 (both depend on R8.3 only)
  R8.8 parallel with R8.9 start once R8.6 done (R8.9 full start requires R8.8)
  R8.11 parallel with R8.12 (both depend on R8.10)
```

---

## Slice R8-A — Pure Domain: CanonicalKey + Normalizer

> Sequential within slice. No existing adapter needed. Deployable in deterministic-only mode immediately.

### [x] R8.1 — `CanonicalKey` Value Object + `MatchMethod` literal

**Spec refs**: MAT-002, MAT-005, MAT-008, MAT-010, ADR-1.
**Depends on**: existing `domain/models.py` stable (it is).
**Parallel with**: nothing — foundation for everything else.

**Deliverables** (new file `backend/src/reconciliation/domain/material_key.py`):
- `MatchMethod = Literal["deterministic", "llm_inferred", "codigo_sunat", "unresolved"]`
  (`codigo_sunat` reserved — no production code path produces it yet).
- `CanonicalKey(BaseModel)` — frozen (`model_config = ConfigDict(frozen=True)`):
  - `familia: str`
  - `grado: str | None`
  - `diametro: str | None`
  - `presentacion: str | None`
  - `unidad: Literal["KG", "TN", "RD", "Rollo"]`
  - `method: MatchMethod = "deterministic"`
  - `raw: str = ""`
  - `@computed_field requires_review: bool` — True when method in `("llm_inferred", "unresolved")`.
  - `@computed_field group_token: str` — `"UNRESOLVED::{raw.strip().lower()}"` when unresolved;
    `" ".join([familia, grado or "?", diametro or "?", presentacion or "?"])` otherwise.
    `unidad` MUST be excluded from `group_token` (it is already a separate `_GroupKey` axis).
  - `classmethod unresolved(raw: str, unidad: str) -> CanonicalKey` — factory for the sentinel.
- Module imports: only stdlib + Pydantic. No I/O, no adapter, no SDK.

**Tests** (new file `backend/tests/unit/domain/test_material_key.py`):
- Frozen: mutating any field raises `ValidationError` or `TypeError`.
- `requires_review=False` when `method="deterministic"`.
- `requires_review=True` when `method="llm_inferred"` or `method="unresolved"`.
- `group_token` for fully resolved key: correct format, no unidad.
- `group_token` for unresolved sentinel: starts with `"UNRESOLVED::"`.
- Two `CanonicalKey` instances with identical fields compare equal (value-equality).
- Two instances differing in `presentacion` are NOT equal (9M ≠ DOB — MAT-005).
- `unresolved()` factory produces `method="unresolved"` and `requires_review=True`.

**Commit message**: `feat(domain): add CanonicalKey VO and MatchMethod literal (MAT-002)`
**Completable in**: one session (small — ~80 lines production, ~40 lines tests).

---

### [x] R8.2 — `MaterialKeyNormalizer` — deterministic regex parser

**Spec refs**: MAT-003, MAT-004, MAT-005, MAT-009, MAT-S01, MAT-S02, MAT-S03, MAT-S04, ADR-1, ADR-3.
**Depends on**: R8.1 (`CanonicalKey`).
**Parallel with**: nothing at this step; R8.3 depends on it.

**Deliverables** (new file `backend/src/reconciliation/domain/material_key_normalizer.py`):
- Module-level constants (all regex + tables — never fetched at runtime):
  - `_GRADE_PATTERNS: list[tuple[re.Pattern, str]]` — ordered list of compiled patterns
    mapping all known dual-grade variants to canonical `"A615 G60"` (MAT-003).
    Patterns MUST be case-insensitive after NFC normalization. Exact enumerated set:
    `a615/a706\s*g60`, `ag615/a706\s*g60`, `a\s+a615[-\s]g60`, `a615\s+g60`, `a615`
    (last-resort; all produce `"A615 G60"`).
  - `_DIAMETER_TABLE: list[tuple[re.Pattern, str]]` — ordered match from largest to smallest
    compound fraction first (MAT-004). Exactly 7 entries:
    `1 3/8"`, `1"`, `3/4"`, `5/8"`, `1/2"`, `3/8"`, `8mm`.
    Each pattern accepts common suffix variants (`pulg`, `pulgada`, `"`, implicit from context).
  - `_9M_SIGNALS`, `_DOB_SIGNALS` — sets of token patterns for MAT-005.
    9M signals: `x\s*9m`, `x9m`, `\b9m\b`.
    DOB signals: `\bdob\b`, `\bdimensionado\b`, `\bapl\b`, `acero\s+dimensionado`.
  - `_FAMILIA_PATTERNS` — `\bbarra\b` and `acero\s+dimensionado` → `"BARRA"`.
- `MaterialKeyNormalizer`:
  - Composes `MaterialNormalizer` from `normalizer.py` as the pre-clean step (NFC + lowercase + whitespace collapse). Does NOT replace it.
  - `parse(raw: str, unidad: str) -> CanonicalKey | None`:
    - Pre-clean via `MaterialNormalizer().canonicalize(raw)`.
    - Extract `familia` (None if no match).
    - Extract `grado` using `_GRADE_PATTERNS` (None if no known pattern matches).
    - Extract `diametro` via `_DIAMETER_TABLE` ordered match (None if no match).
    - Extract `presentacion`: scan for 9M signals, DOB signals. Both present → `None`.
      Neither present → `None`. Exactly one → `"9M"` or `"DOB"`.
    - If ALL FOUR fields are non-None → return `CanonicalKey(method="deterministic", ...)`.
    - Any field None → return `None` (ambiguous; caller falls to LLM or unresolved).

**Tests** (new file `backend/tests/unit/domain/test_material_key_normalizer.py`):
- MAT-S01: all four grade-variant inputs → `grado = "A615 G60"`, `method="deterministic"`.
- MAT-S02: `"BARRA A615 G60 1 3/8\" x 9M"` → `diametro = '1 3/8"'`, NOT `'1"'` or `'3/8"'`.
- MAT-S03: `1/2" x 9M"` → `presentacion = "9M"`; `1/2" (DOB)"` → `presentacion = "DOB"`.
- MAT-S04: `"ACERO DIMENSIONADO - BARRA A615 G60 1\" DOB APL"` → `presentacion="DOB"`, `familia="BARRA"`.
- Both 9M and DOB signals in same description → `parse()` returns `None`.
- Neither signal → `parse()` returns `None`.
- Unknown grade text → `parse()` returns `None` (grado is None).
- Unknown diameter → `parse()` returns `None`.
- Real-pair declared: `"BARRA AG615/A706 G60 1/2\" x 9M"` →
  `CanonicalKey(familia="BARRA", grado="A615 G60", diametro='1/2"', presentacion="9M")`.
- Real-pair guía: `"BARRA A A615-G60 1/2\" X 9M"` → same `CanonicalKey` as declared.
  (These two MUST compare equal — the core acceptance case for MAT-013.)
- Additional guía variants: `"BARRA A615/A706 G60 1/2\" X 9M"`, `"barra a615 g60 1/2\" x 9m"` →
  same `CanonicalKey`.
- No LLM call invoked by any of the above (pure regex path — confirmed by absence of port injection).

**Commit message**: `feat(domain): add MaterialKeyNormalizer deterministic regex parser (MAT-003/004/005)`
**Completable in**: one session (medium — ~120 lines production, ~80 lines tests).

---

### [x] R8.3 — `MaterialKeyResolver` Strategy + per-run LLM cache

**Spec refs**: MAT-006, MAT-012, ADR-3, ADR-4.
**Depends on**: R8.1 (`CanonicalKey`), R8.2 (`MaterialKeyNormalizer`).
**Note**: At this step `MaterialInferencePort` does not yet exist — resolver accepts
`inference: Any | None = None` typed as `Protocol` stub OR import is deferred to R8.6.
Safest: define a minimal `_InferenceProtocol(Protocol)` inline in the resolver, replaced by
the real port once R8.6 is done. Document this as a temporary shim.

**Deliverables** (new file `backend/src/reconciliation/domain/material_key_resolver.py`):
- `MaterialKeyResolver`:
  - `__init__(self, normalizer: MaterialKeyNormalizer, inference: Any | None = None)`.
  - `_cache: dict[tuple[str, str], CanonicalKey]` — keyed `(raw, unidad)`, populated lazily.
  - `resolve(self, description_raw: str, unidad: str) -> CanonicalKey`:
    1. Call `self._normalizer.parse(description_raw, unidad)`.
    2. If result non-None → return it directly (`method="deterministic"`).
    3. If `self._inference` is not None:
       a. Check cache; if hit, return cached key.
       b. Call `self._inference.infer(description_raw)` → `MaterialKeyInference | None`.
       c. If non-None, validate: `inf.diametro` must be in the canonical diameter set;
          `inf.presentacion` must be in `{"9M", "DOB"}`. Fail either check → fall through.
       d. Build `CanonicalKey(method="llm_inferred", requires_review=True, ...)`, cache it, return.
    4. Return `CanonicalKey.unresolved(description_raw, unidad)`.
  - Hallucination guard constants mirrored from `MaterialKeyNormalizer`: same diameter set,
    same presentacion vocabulary — defined as module-level constants here too (no cross-import
    from normalizer to keep the two pure domain modules independent).

**Tests** (new file `backend/tests/unit/domain/test_material_key_resolver.py`):
- Deterministic path: resolver with no inference → deterministic key returned; cache not consulted.
- Deterministic path: resolver with inference injected → deterministic key returned; `infer()` NOT called.
- Ambiguous + inference available → `infer()` called; result memoized; second call with same raw uses cache (assert `infer()` called once, not twice).
- Ambiguous + inference returns None → `unresolved` key returned, `requires_review=True`.
- Ambiguous + inference down (returns None) → `unresolved` key; run continues.
- LLM hallucination guard: `infer()` returns invalid diameter → falls to `unresolved`.
- LLM hallucination guard: `infer()` returns `presentacion` not in `{"9M","DOB"}` → falls to `unresolved`.
- `inference=None` (default) → `unresolved` when normalizer returns None (no crash — MAT-012 deterministic-only mode).

**Commit message**: `feat(domain): add MaterialKeyResolver strategy with det-first/LLM-fallback/cache (ADR-3/4)`
**Completable in**: one session (medium — ~100 lines production, ~70 lines tests).

---

## Slice R8-B — Domain Model Extensions

> R8.4 and R8.6 are parallel once R8.3 is done. R8.5 depends on R8.4.

### [x] R8.4 — Domain model additions: `MaterialLine.match_method`, `ReconciliationRow.match_method`

**Spec refs**: MAT-008, ADR-5.
**Depends on**: R8.1 (`MatchMethod` literal), R8.3 complete (branch is stable).
**Parallel with**: R8.6 (independent field additions).

**Deliverables** (modify `backend/src/reconciliation/domain/models.py`):
- `MaterialLine`: add `match_method: MatchMethod = "deterministic"`.
  Import `MatchMethod` from `material_key` (domain-internal, pure import).
  Backward-compatible default ensures existing serialised `review.json` / extraction cache
  deserialise cleanly without migration.
- `ReconciliationRow`: add `match_method: MatchMethod = "deterministic"`.
  Same backward-compatible default. `requires_review` already exists; the new field is additive.
- Add `MatchMethod` to the public exports of `models.py` (or re-export from `material_key.py`).

**Tests** (update `backend/tests/unit/domain/test_models.py`):
- `MaterialLine` instantiated without `match_method` → defaults to `"deterministic"`.
- `MaterialLine` with `match_method="llm_inferred"` → field stored correctly.
- `ReconciliationRow` instantiated without `match_method` → defaults to `"deterministic"`.
- Old serialised dict (no `match_method` key) → `model_validate` succeeds with default (backward-compat test).

**Commit message**: `feat(domain): add match_method field to MaterialLine and ReconciliationRow (MAT-008)`
**Completable in**: one session (small — ~15 lines production, ~20 lines tests).

---

### [x] R8.5 — `ReconciliationService` — worst-wins `match_method` aggregation

**Spec refs**: MAT-008, MAT-011, ADR-5.
**Depends on**: R8.4 (`match_method` fields on models).

**Deliverables** (modify `backend/src/reconciliation/domain/reconciliation.py`):
- In `reconcile()`, extend the existing `row_requires_review` aggregation block
  (`reconciliation.py:146`) to also compute `row_match_method`:
  ```
  if any(line.match_method == "unresolved" for ...) → "unresolved"
  elif any(line.match_method == "llm_inferred" for ...) → "llm_inferred"
  else → "deterministic"
  ```
  Lines scoped are: all declared lines contributing to the group + all guía lines contributing
  to the group (same scope as the existing `requires_review` OR-aggregation).
- Set `ReconciliationRow.match_method = row_match_method` on each constructed row.
- Ensure `requires_review` is still OR-aggregated AND is also True whenever
  `row_match_method != "deterministic"` (these are additive conditions, not replacing).

**Tests** (update `backend/tests/unit/domain/test_reconciliation.py`):
- All contributing lines `method="deterministic"` → row `match_method="deterministic"`.
- Any line `method="llm_inferred"` → row `match_method="llm_inferred"`.
- Any line `method="unresolved"` → row `match_method="unresolved"` (worst-wins over `llm_inferred`).
- `requires_review=True` when `match_method="llm_inferred"` or `"unresolved"`.
- `requires_review=False` when `match_method="deterministic"` and no other review flag set.
- MATCH scenario with all-deterministic contributing lines → `match_method="deterministic"`, `requires_review=False`.

**Commit message**: `feat(domain): aggregate worst-wins match_method in ReconciliationService (MAT-008/011)`
**Completable in**: one session (small — ~25 lines production, ~30 lines tests).

---

### [x] R8.6 — `MaterialInferencePort` + `MaterialKeyInference` — domain port + return model

**Spec refs**: MAT-006, MAT-007, ADR-2.
**Depends on**: R8.3 done (domain stable), R8.4 done (models updated).
**Parallel with**: R8.4 (was; R8.6 can start once R8.3 and R8.4 are merged).

**Deliverables**:

`backend/src/reconciliation/domain/models.py` — add:
```python
class MaterialKeyInference(BaseModel):
    familia: str
    grado: str | None = None
    diametro: str | None = None
    presentacion: str | None = None
    confidence: float = 0.0
```
(Small pure domain model — adapters return this; resolver wraps it into `CanonicalKey`.)

`backend/src/reconciliation/domain/ports.py` — add:
```python
@runtime_checkable
class MaterialInferencePort(Protocol):
    def infer(self, description: str) -> MaterialKeyInference | None: ...
```
Update R8.3's temporary shim: replace inline `_InferenceProtocol` with `MaterialInferencePort`
import from `ports.py` (or confirm the shim was never referenced externally and remove).

**Tests** (update `backend/tests/unit/domain/test_ports.py`):
- `MaterialInferencePort` structural compliance: a concrete stub class satisfies the protocol.
- `MaterialKeyInference` model instantiation with all fields; defaults work.
- Optional fields `grado`, `diametro`, `presentacion` default to `None`.

**Commit message**: `feat(domain): add MaterialInferencePort protocol and MaterialKeyInference model (MAT-006)`
**Completable in**: one session (small — ~30 lines production, ~20 lines tests).

---

## Slice R8-C — Infrastructure: Config + Adapter + Factory

> R8.7 and R8.8 are parallel once R8.6 is done. R8.9 requires both R8.7 and R8.8.

### [x] R8.7 — `OllamaMaterialInferenceAdapter` + `build_inference_adapter` factory

**Spec refs**: MAT-007, MAT-011 (S11), MAT-012, ADR-2.
**Depends on**: R8.6 (`MaterialInferencePort`, `MaterialKeyInference`).
**Parallel with**: R8.8 (independent file).

**Deliverables**:

`backend/src/reconciliation/adapters/inference/__init__.py` (new directory):
- Empty init file.

`backend/src/reconciliation/adapters/inference/ollama_material.py`:
- **Prompt constant** `_SYSTEM_PROMPT: str` — mirror the full LLM system prompt text here
  (copied verbatim from `.claude/skills/material-canonical-matching/assets/llm-inference-prompt.md`).
  This is the MANDATORY repo-tracking step from the design open-items note (skills dir is gitignored).
  The constant makes the prompt travel with the code.
- `OllamaMaterialInferenceAdapter(MaterialInferencePort)`:
  - `__init__(self, model: str, base_url: str, temperature: float, timeout_s: float)`.
  - `infer(self, description: str) -> MaterialKeyInference | None`:
    - Lazy-import `openai` inside the method body (MUST NOT import at module level).
    - Build OpenAI-compatible chat request with `_SYSTEM_PROMPT` as system message,
      `description` as user message, `temperature=0`.
    - Strip `<think>...</think>` blocks (including multiline) from raw response before parsing
      (MAT-S11 compliance).
    - `json.loads` into `MaterialKeyInference`. Any exception → `return None`.
    - Schema mismatch → `return None`.
    - Connection error / timeout → `return None` (MAT-012 graceful degradation).

`backend/src/reconciliation/adapters/inference/factory.py`:
- `build_inference_adapter(cfg: AppConfig) -> MaterialInferencePort | None`:
  - Returns `None` when `cfg.inference.enabled is False` (deterministic-only default).
  - Returns `OllamaMaterialInferenceAdapter(...)` otherwise.

**Tests** (new file `backend/tests/unit/adapters/test_ollama_material.py`):
- MAT-S11: response with `<think>...</think>` block → stripped; JSON parsed correctly;
  `MaterialKeyInference` populated with correct fields.
- Happy path: well-formed JSON response → `MaterialKeyInference` returned.
- Malformed JSON → `None` returned (no crash).
- Missing required fields in JSON → `None` returned.
- Lazy-import guard: importing the adapter module with `openai` absent does NOT raise at load time
  (mock `sys.modules` to simulate absence or structure test to only import the class).
- `build_inference_adapter` with `cfg.inference.enabled=False` → returns `None`.
- `build_inference_adapter` with `cfg.inference.enabled=True` → returns an adapter instance.

**Commit message**: `feat(adapters): add OllamaMaterialInferenceAdapter with think-block strip + factory (MAT-007)`
**Completable in**: one session (medium — ~120 lines production, ~60 lines tests).

---

### [x] R8.8 — `InferenceConfig` + `AppConfig.inference` in `config.py`

**Spec refs**: MAT-007, ADR-2 (`InferenceConfig` shape).
**Depends on**: R8.6 (ports/models stable).
**Parallel with**: R8.7.

**Deliverables** (modify `backend/src/reconciliation/application/config.py`):
- Add `InferenceConfig(BaseSettings)`:
  - `enabled: bool = False`
  - `provider: Literal["ollama", "openai"] = "ollama"`
  - `model: str = "qwen3.5:9b"`
  - `base_url: str | None = "http://localhost:11434/v1"`
  - `api_key: str | None = Field(default=None, exclude=True)`
  - `temperature: float = 0.0`
  - `timeout_s: float = 30.0`
  Mirror the same off-by-default shape as `SunatConfig` (already in codebase).
- Add `inference: InferenceConfig = Field(default_factory=InferenceConfig)` to `AppConfig`.
- Ensure `config.yaml` default does NOT enable inference (deterministic-only is the safe default).

**Tests** (update `backend/tests/unit/application/test_config.py`):
- Default `AppConfig` has `inference.enabled=False`.
- `AppConfig` with `inference: {enabled: true, model: "custom-model"}` → parsed correctly.
- `inference.api_key` excluded from `model_dump()` (secret exclusion test, mirrors SUNAT pattern).

**Commit message**: `feat(config): add InferenceConfig off-by-default for Ollama LLM fallback (ADR-2)`
**Completable in**: one session (small — ~30 lines production, ~20 lines tests).

---

## Slice R8-D — Pipeline Integration

> R8.9 requires R8.3, R8.4, R8.6, R8.7, R8.8 all complete. R8.10 depends on R8.9.

### [x] R8.9 — Pipeline `_stage_normalize` upgrade + `key_resolver` defensive default

**Spec refs**: MAT-001, MAT-009, ADR-6.
**Depends on**: R8.3, R8.4, R8.6, R8.7, R8.8 all complete.

**Deliverables** (modify `backend/src/reconciliation/application/pipeline.py`):

1. Import `MaterialKeyNormalizer` from `domain/material_key_normalizer.py` and
   `MaterialKeyResolver` from `domain/material_key_resolver.py`.

2. `ReconciliationPipeline.__init__`: add parameter
   `key_resolver: MaterialKeyResolver | None = None`.
   If `None`, construct the defensive default:
   `self._key_resolver = key_resolver or MaterialKeyResolver(MaterialKeyNormalizer())`
   — deterministic-only, no inference port. This preserves all existing direct-construction
   tests (they pass `None` implicitly).
   Keep `self._normalizer = MaterialNormalizer()` as-is (still used by
   `MaterialKeyNormalizer` internally; the old field is now unused in `_stage_normalize`
   but MUST NOT be deleted until verified all references are cleared).

3. Replace the body of `_stage_normalize` (currently `pipeline.py:1149`):
   ```python
   def _stage_normalize(self, declared, guias):
       def _norm_line(line: MaterialLine) -> MaterialLine:
           key = self._key_resolver.resolve(line.description_raw, line.unidad)
           return line.model_copy(update={
               "description_canonical": key.group_token,
               "match_method": key.method,
               "requires_review": line.requires_review or key.requires_review,
           })
       normalised_declared = [
           dataclasses.replace(r, declared_lines=[_norm_line(l) for l in r.declared_lines])
           if hasattr(r, "__dataclass_fields__") else
           r.model_copy(update={"declared_lines": [_norm_line(l) for l in r.declared_lines]})
           for r in declared
       ]
       normalised_guias = [
           g.model_copy(update={"lines": [_norm_line(l) for l in g.lines]})
           for g in guias
       ]
       return normalised_declared, normalised_guias
   ```
   (Adapt to match the actual existing fan-out pattern in `_stage_normalize` — do not
   change the fan-out logic, only the `_norm_line` inner function body.)

**Critical guard — regression check**:
All existing tests that instantiate `ReconciliationPipeline` directly (without passing
`key_resolver`) MUST still pass after this change. The defensive default (deterministic-only
resolver) is the safety net for this invariant. Run `cd backend && uv run pytest` after this
task and assert zero regressions before committing.

**Tests** (update `backend/tests/unit/application/test_pipeline.py`):
- Pipeline instantiated without `key_resolver` → `_stage_normalize` runs in deterministic-only mode;
  no crash; `description_canonical` is populated.
- Pipeline instantiated with a mock resolver that returns an `"llm_inferred"` key →
  `MaterialLine.match_method == "llm_inferred"` and `requires_review=True` after normalize stage.
- Pipeline with mock resolver returning `"unresolved"` → `description_canonical` starts with
  `"UNRESOLVED::"` and `requires_review=True`.
- Existing pipeline direct-construction tests (without `key_resolver`) still pass — assert count
  in the existing test file is unchanged or only grows.

**Commit message**: `feat(pipeline): upgrade _stage_normalize to MaterialKeyResolver; add key_resolver ctor param (ADR-6)`
**Completable in**: one session (medium — ~40 lines production change, ~30 lines tests).

---

### [x] R8.10 — `container.py` wiring: build inference adapter + inject `key_resolver`

**Spec refs**: ADR-2, ADR-6.
**Depends on**: R8.7 (factory), R8.8 (InferenceConfig), R8.9 (pipeline accepts `key_resolver`).

**Deliverables** (modify `backend/src/reconciliation/infrastructure/container.py`):
- In `build_pipeline`:
  1. Import `build_inference_adapter` from `adapters/inference/factory.py` (inside the function
     body, lazy pattern — mirrors the SUNAT and QR adapter import style already in container.py).
  2. `inference = build_inference_adapter(config)` — `None` when `inference.enabled=False`.
  3. `from reconciliation.domain.material_key_normalizer import MaterialKeyNormalizer`
     `from reconciliation.domain.material_key_resolver import MaterialKeyResolver`
  4. `key_resolver = MaterialKeyResolver(MaterialKeyNormalizer(), inference)`
  5. Pass `key_resolver=key_resolver` to `ReconciliationPipeline(...)`.
  6. Log: `"build_pipeline: inference %s (model=%s)"`,
     `"ENABLED"` or `"DISABLED (deterministic-only)"`, `config.inference.model`.

**Tests** (update `backend/tests/unit/infrastructure/test_container.py`):
- `build_pipeline` with `config.inference.enabled=False` → `ReconciliationPipeline`
  receives a `key_resolver` with no inference port (resolver `._inference is None`).
- `build_pipeline` with `config.inference.enabled=True` → `key_resolver._inference` is
  an `OllamaMaterialInferenceAdapter` instance (or satisfies `MaterialInferencePort`).
  (Use a test config pointing at a mock base_url; do not require live Ollama.)

**Commit message**: `feat(infra): wire MaterialKeyResolver + inference adapter in build_pipeline (ADR-6)`
**Completable in**: one session (small — ~25 lines production, ~20 lines tests).

---

## Slice R8-E — Export + API Surface

> R8.11 and R8.12 are parallel once R8.10 is done.

### [x] R8.11 — `xlsx_report.py` + CSV: add `"Método"` column

**Spec refs**: MAT-008, MAT-S10, ADR-5.
**Depends on**: R8.10 (pipeline + wiring stable; `ReconciliationRow.match_method` reliably populated).
**Parallel with**: R8.12.

**Deliverables** (modify `backend/src/reconciliation/adapters/report/xlsx_report.py`):
- Add `"Método"` to `_COLUMNS` list (after existing `"Año inferido"` column → becomes column 12).
- Update `_row_to_values` to append `row.match_method` as the 12th value.
  Values: `"deterministic"` / `"llm_inferred"` / `"unresolved"` (raw literal — no translation,
  consistent with how `"Año inferido"` keeps the raw bool rendered as `"Sí"`/`""`).
- CSV path uses the same `_row_to_values` serializer — no separate change needed.
- Update the module docstring column count reference from 11 to 12.

**Tests** (update `backend/tests/unit/adapters/test_xlsx_report.py`):
- MAT-S10: produce an xlsx from rows with `match_method="deterministic"`, `"llm_inferred"`,
  `"unresolved"` → assert `"Método"` column present in header; values match per row.
- `"Año inferido"` column still present at its prior position (non-regression).
- CSV output includes `"Método"` column at the correct position.
- Total column count in header is 12.

**Commit message**: `feat(report): add Método column to xlsx/csv export (MAT-008/S10)`
**Completable in**: one session (small — ~15 lines production, ~20 lines tests).

---

### [x] R8.12 — API schema: `ReconciliationRowResponse.match_method` (read-only surface)

**Spec refs**: MAT-008, ADR-5.
**Depends on**: R8.10 (domain + pipeline complete).
**Parallel with**: R8.11.

**Deliverables** (modify `backend/src/reconciliation/infrastructure/api/schemas.py`):
- Add to `ReconciliationRowResponse`:
  ```python
  match_method: Literal["deterministic", "llm_inferred", "unresolved"] = "deterministic"
  ```
  Backward-compatible default. Read-only display field — no POST/PATCH route accepts it.

- Update the row-to-response mapping in `routes.py` (wherever `ReconciliationRowResponse` is
  constructed from `ReconciliationRow`) to pass `match_method=row.match_method`.

**Tests** (update `backend/tests/unit/infrastructure/test_api_routes.py`):
- `GET /runs/{id}/rows` response: each row DTO has `match_method` field.
- Row with `match_method="deterministic"` → DTO `match_method="deterministic"`.
- Row with `match_method="llm_inferred"` → DTO `match_method="llm_inferred"`.
- Old response without `match_method` key validates with default (backward-compat model_validate test).

**Commit message**: `feat(api): surface match_method on ReconciliationRowResponse (read-only, MAT-008)`
**Completable in**: one session (small — ~10 lines production, ~15 lines tests).

---

## Slice R8-F — Real-Data Validation Gate

> Sequential. Start only after R8.9–R8.12 all complete. This is the trusted gate.

### [x] R8.13 — Real-data e2e: #4252 MATCH assertion + full regression guard

**Spec refs**: MAT-013, MAT-S08, MAT-S01 (integration level), ADR-3.
**Depends on**: R8.9–R8.12 all complete (full pipeline + export + API wired).

**Deliverables** (new file `backend/tests/integration/test_pipeline_r8_gate.py`):

1. **Unit-level real-pair assertion** (fast, no PDF needed):
   - Instantiate `MaterialKeyNormalizer`.
   - Assert `parse("BARRA AG615/A706 G60 1/2\" x 9M", "TN")` returns
     `CanonicalKey(familia="BARRA", grado="A615 G60", diametro='1/2"', presentacion="9M")`.
   - Assert `parse("BARRA A A615-G60 1/2\" X 9M", "TN")` returns the SAME key (compare equal).
   - Assert `parse("BARRA A615/A706 G60 1/2\" X 9M", "TN")` → same key.
   - Assert `parse("barra a615 g60 1/2\" x 9m", "TN")` → same key.
   - All four `match_method == "deterministic"`, `requires_review == False`.

2. **ReconciliationService unit-level #4252 simulation** (no PDF needed):
   - Build a `Registro(numero="232", ...)` with declared line
     `("BARRA AG615/A706 G60 1/2\" x 9M", "TN", Decimal("4.124"))`.
   - Build three `GuiaDeRemision` objects (simulating pages 5, 6, 8) with guía lines
     using the three variant texts, quantities summing to `4.124 TN`.
   - Pass through `MaterialKeyResolver(MaterialKeyNormalizer())` to normalize all lines.
   - Call `ReconciliationService().reconcile(...)`.
   - Assert: exactly one `ReconciliationRow` for this group.
   - Assert: `status == "MATCH"`.
   - Assert: `summed_qty == Decimal("4.124")`.
   - Assert: `match_method == "deterministic"`.
   - Assert: `requires_review == False`.

3. **Pipeline regression guard** (prevents "tests pass while pipeline broken" hazard):
   - Instantiate `ReconciliationPipeline` directly WITHOUT passing `key_resolver`
     (defensive default path).
   - Assert `pipeline._key_resolver` is not None.
   - Assert `pipeline._key_resolver._inference is None` (deterministic-only).
   - Run `_stage_normalize` on a minimal set of lines; assert `description_canonical` is
     populated and `match_method` field is set on each line.

4. **Full real-PDF e2e** (runs against the actual PDF — marks slow with `pytest.mark.slow`
   or `pytest.mark.e2e`; must be run before declaring the change complete):
   - Run the full pipeline on the real PDF (`CTR-PLC01-FR001...`).
   - Assert at least one `ReconciliationRow` has `status="MATCH"` (was zero before this change).
   - Assert the #4252-family row: `match_method="deterministic"`, `summed_qty=Decimal("4.124")`,
     `status="MATCH"`, `requires_review=False`.
   - Assert zero regressions vs the rev-3 gate: all counts from `test_pipeline_rev3_gate.py`
     still hold (existing MISMATCH/GUIA_MISSING/DECLARED_MISSING rows must not vanish).
   - Assert xlsx export contains `"Método"` column; the #4252-family row has `"deterministic"`.

> **Note**: The real-PDF e2e (item 4) is the trusted gate. Items 1–3 are fast regression guards.
> The change MUST NOT be declared complete until item 4 passes on actual data
> (per `docs/HANDOFF.md` §4: "unit tests passed while the real pipeline was broken").

**Commit message**: `test(e2e): r8 real-data gate — #4252 MATCH assertion + pipeline regression guard (MAT-013)`
**Completable in**: one session (integration — ~120 lines tests, no production changes).

---

## Task Dependency Summary

```
Slice R8-A (Pure Domain):
  R8.1 (CanonicalKey VO)
    └─▶ R8.2 (MaterialKeyNormalizer)
          └─▶ R8.3 (MaterialKeyResolver + cache)

Slice R8-B (Model Extensions):
  R8.3 ──▶ R8.4 (MaterialLine/Row.match_method) ──▶ R8.5 (reconciler aggregation)
  R8.3 ──▶ R8.6 (MaterialInferencePort + MaterialKeyInference)  [parallel with R8.4]

Slice R8-C (Infrastructure):
  R8.6 ──▶ R8.7 (OllamaMaterialInferenceAdapter + factory)  [parallel with R8.8]
  R8.6 ──▶ R8.8 (InferenceConfig in config.py)              [parallel with R8.7]

Slice R8-D (Pipeline):
  R8.3 + R8.4 + R8.6 + R8.7 + R8.8 ──▶ R8.9 (_stage_normalize upgrade)
  R8.9 ──▶ R8.10 (container.py wiring)

Slice R8-E (Export + API):
  R8.10 ──▶ R8.11 (xlsx/csv +Método)   [parallel with R8.12]
  R8.10 ──▶ R8.12 (API schema)         [parallel with R8.11]

Slice R8-F (Real-Data Gate):
  R8.11 + R8.12 ──▶ R8.13 (e2e gate — MUST pass before change is declared done)
```

**Total tasks**: 13 · **Sequential bottlenecks**: R8.1→2→3, R8.9→10→13 · **Parallel opportunities**: R8.4∥R8.6, R8.7∥R8.8, R8.11∥R8.12

---

## Invariants that each task MUST not break

| Invariant | Enforced by |
|-----------|------------|
| Domain purity — no SDK/IO in `domain/` | R8.1, R8.2, R8.3, R8.6: only stdlib + Pydantic |
| Units never converted | `unidad` excluded from `group_token`; `_GroupKey` unchanged |
| Presentación never merged | `parse()` returns `None` when both signals or neither present |
| MATCH EXACT(0) | `reconciliation.py:166` untouched |
| OCR-validation gate | `llm_inferred` and `unresolved` always `requires_review=True` |
| fecha out of material key | `_GroupKey` and `group_token` both exclude fecha |
| Local-first / air-gap | `inference.enabled=False` default; Ollama hits `localhost` only |
| Reversibility | Setting `inference.enabled=false` → deterministic-only; no migration |
| Backward compatibility | All new fields have `= "deterministic"` / `= False` defaults |
| Direct-construction tests pass | R8.9 defensive default; guard asserted in R8.13 |
