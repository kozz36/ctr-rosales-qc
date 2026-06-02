# Design — r8-material-matching

**Change**: `r8-material-matching`
**Phase**: design (resolves the proposal §6 open forks)
**Artifact store**: hybrid (engram `sdd/r8-material-matching/design` + this file)
**Date**: 2026-06-02
**Reads**: proposal (engram #2772 + `proposal.md`), `docs/MATERIAL-MATCHING.md`, existing `material-reconciliation/design.md` (rev-2 §A–F, rev-3 D1–D6)
**Patterns**: Ports & Adapters (Hexagonal), Dependency Inversion, Strategy (deterministic-vs-LLM key resolution), Value Object (`CanonicalKey`), Adapter (Ollama inference), Factory (provider selection).

---

## 0. Architectural through-line

The reconciliation engine already groups by `MaterialLine.description_canonical` (`reconciliation.py:73`),
and the pipeline already has a dedicated `_stage_normalize` (`pipeline.py:1149`) whose ONLY job is to
fill `description_canonical` from `description_raw`. Today that stage calls
`MaterialNormalizer.canonicalize` — NFC + lowercase + whitespace-collapse only. That is precisely why
real data produces zero MATCH: `barra ag615/a706 g60 1/2" x 9m` and `barra a a615-g60 1/2" x 9m`
normalise to two different strings.

**The whole change is therefore a normalize-stage upgrade plus a provenance channel.** We replace the
weak string canonicalizer with a canonical-key builder (deterministic-primary, LLM-fallback behind a
port), serialize the key into the SAME `description_canonical` field, and thread a new
`match_method`/`requires_review` provenance signal from the line through the row to export. The
grouping engine and the entire downstream pipeline shape stay intact. This is additive and reversible
(proposal §5).

```
description_raw ─▶ MaterialKeyResolver (domain Strategy)
                     │
        deterministic │ MaterialKeyNormalizer.parse(raw) → CanonicalKey | PARTIAL
                     │
            resolved?├── yes ─▶ CanonicalKey(method=deterministic)
                     │
                     └── no (ambiguous) ─▶ MaterialInferencePort.infer(raw)
                                              → CanonicalKey(method=llm_inferred, requires_review)
                                              │ unavailable / non-JSON / hallucination-guard fail
                                              └─▶ CanonicalKey.unresolved(raw) (method=unresolved, requires_review)
                     ▼
        line.description_canonical = key.group_token   (string serialization of the key)
        line.match_method          = key.method
        line.requires_review      |= key.requires_review
                     ▼
   ReconciliationService groups by (registro, fecha, description_canonical, unidad) — UNCHANGED
                     ▼
   ReconciliationRow.match_method (aggregated) + requires_review → xlsx/csv export
```

---

## ADR-1 — Canonical normalization lives in a PURE domain service + Value Object

**Decision.** Add two new pure domain modules:

- `domain/material_key.py` — the `CanonicalKey` **Value Object** (frozen Pydantic model) plus the
  enum-like `MatchMethod` literal.
- `domain/material_key_normalizer.py` — `MaterialKeyNormalizer`, the deterministic regex parser
  (a pure domain service, sibling to `MaterialNormalizer`).

`CanonicalKey`:

```python
MatchMethod = Literal["deterministic", "llm_inferred", "codigo_sunat", "unresolved"]

class CanonicalKey(BaseModel):
    model_config = ConfigDict(frozen=True)        # value object: immutable, equality by value
    familia: str                                  # "BARRA"
    grado: str | None                             # "A615 G60"
    diametro: str | None                          # '1/2"'  (canonical form from the diameter table)
    presentacion: str | None                      # "9M" | "DOB"
    unidad: Literal["KG", "TN", "RD", "Rollo"]    # carried verbatim; part of the key
    method: MatchMethod = "deterministic"
    raw: str = ""                                 # provenance: the source description

    @computed_field
    @property
    def requires_review(self) -> bool:
        # OCR-validation gate: anything not deterministically resolved is flagged.
        return self.method in ("llm_inferred", "unresolved")

    @computed_field
    @property
    def group_token(self) -> str:
        # Stable string the ReconciliationService groups on (slots into description_canonical).
        # unidad is NOT in the token because the grouping key already carries unidad separately
        # (the engine groups by (registro, fecha, description_canonical, unidad)).
        if self.method == "unresolved":
            return f"UNRESOLVED::{self.raw.strip().lower()}"   # never collapses distinct raws
        parts = [self.familia, self.grado or "?", self.diametro or "?", self.presentacion or "?"]
        return " ".join(parts)                                 # e.g. 'BARRA A615 G60 1/2" 9M'
```

**Why a Value Object, not fields on `reconciliation.py`.** The canonical key is a domain concept with
its own invariants (presentación never merged across, unidad never converted, grade-collapse rules).
Encoding it as a frozen VO with value-equality is the idiomatic DDD expression: two lines with the same
`(familia, grado, diametro, presentacion, unidad)` ARE the same physical item. Putting normalization
logic inside `ReconciliationService` would (a) overload a class whose single responsibility is
group/sum/compare, (b) make the regex untestable in isolation, and (c) couple grouping to parsing.
Keeping `CanonicalKey` + `MaterialKeyNormalizer` separate honours SRP and mirrors the existing
`MaterialNormalizer` placement.

**Why `group_token` is a string, not a tuple.** The reconciliation engine already groups on
`description_canonical: str` (`reconciliation.py:73`, `_GroupKey.material_canonical: str`). Serializing
the key to a deterministic string lets us reuse the engine and the export untouched — the grouping
`_GroupKey` and `ReconciliationRow.material_canonical` stay `str`. `unidad` is already a separate
component of `_GroupKey`, so it is intentionally excluded from `group_token` to avoid double-counting
it in the key (it remains an independent grouping axis — units summed independently, never converted).

**Domain purity.** Both modules import only stdlib (`re`, `unicodedata`) + Pydantic, exactly like
`models.py`/`normalizer.py`. No IO, no SDK. Verified against the existing domain-purity invariant.

---

## ADR-2 — `MaterialInferencePort` (Protocol) + dedicated Ollama adapter with its OWN config block

**Decision.** Add to `domain/ports.py`:

```python
@runtime_checkable
class MaterialInferencePort(Protocol):
    """Provider-agnostic LLM fallback for material descriptions the deterministic
    normalizer cannot resolve. Lazy-importing adapter; never called on the
    deterministic happy path. Returns None on any failure (graceful)."""

    def infer(self, description: str) -> MaterialKeyInference | None:
        ...
```

Return shape — a small pure domain model in `models.py` (NOT the full `CanonicalKey`, so the adapter
stays ignorant of `group_token`/`method` policy):

```python
class MaterialKeyInference(BaseModel):
    familia: str
    grado: str | None = None
    diametro: str | None = None
    presentacion: str | None = None
    confidence: float = 0.0
```

The resolver (ADR-3) wraps this into `CanonicalKey(method="llm_inferred", requires_review=True, ...)`.
`infer` returns `None` (not raises) on Ollama-down / non-JSON / parse failure, so the resolver degrades
to `unresolved` without a crash (proposal risk table: "LLM unavailable", "<think> leakage").

**DECISION: dedicated `inference:` config block + dedicated adapter — NOT reuse of `VisionConfig`/`VisionLLMPort`.**

| Option | Tradeoff |
|--------|----------|
| **(A) Reuse `VisionLLMPort` + `vision:` config** (rejected) | DRY on the OpenAI-compatible HTTP plumbing, but couples two unrelated concerns: vision reads *images* and returns a `VisionResult{date}`; inference reads *text* and returns a material tuple. Forcing both behind one port pollutes `VisionLLMPort` with a text method, and forces material inference to share `vision.provider` (you could not run vision on Anthropic while inferring on a local Ollama text model). The cost-cap (`max_vision_calls`), stamp-crop, and batch semantics are vision-specific and irrelevant here. |
| **(B) Dedicated `MaterialInferencePort` + `inference:` config block** (chosen) | Honours Interface Segregation and Separation of Concerns: text-tuple inference is its own port with its own provider/model/temperature. Independently togglable (`inference.enabled`) so deterministic-only mode is a one-line config flip (proposal §5 rollback). The *only* duplication is a thin OpenAI-compatible HTTP call — acceptable, and the adapter MAY internally reuse an `openai` client builder helper if one is extracted later. Reuses the architectural **pattern** (DI + Strategy + lazy-import) without reusing the vision **instance**. |

New config (`application/config.py`), mirroring `SunatConfig`'s off-by-default shape so deterministic-only
is the safe default and the LLM is opt-in:

```python
class InferenceConfig(BaseSettings):
    enabled: bool = False                 # deterministic-only unless turned on
    provider: Literal["ollama", "openai"] = "ollama"
    model: str = "qwen3.5:9b"             # bake-off winner; not hard-coded in code
    base_url: str | None = "http://localhost:11434/v1"
    api_key: str | None = Field(default=None, exclude=True)
    temperature: float = 0.0              # determinism
    timeout_s: float = 30.0
# AppConfig gains:  inference: InferenceConfig = Field(default_factory=InferenceConfig)
```

**Adapter** `adapters/inference/ollama_material.py: OllamaMaterialInferenceAdapter` implements
`MaterialInferencePort`. It **lazy-imports** the `openai` SDK inside `infer()` (consistent with
`OpenAICompatibleVisionAdapter`), sends the domain system prompt from
`.claude/skills/material-canonical-matching/assets/llm-inference-prompt.md` (mirror the prompt text
into the adapter or a domain-adjacent constants module so it travels with the repo), sets
`temperature=0`, strips `<think>…</think>` blocks before JSON parse, and `json.loads` into
`MaterialKeyInference`. Any exception / schema-mismatch → `return None`.

**Factory** `adapters/inference/factory.py: build_inference_adapter(cfg) -> MaterialInferencePort | None`
returns `None` when `cfg.inference.enabled is False` (Strategy + Factory, identical pattern to
`build_vision_adapter`). The composition root wires it.

---

## ADR-3 — Trigger boundary: the `MaterialKeyResolver` Strategy and the precise "ambiguous" condition

**Decision.** Add `domain/material_key_resolver.py: MaterialKeyResolver` — a pure domain **Strategy**
that owns the deterministic-first / LLM-fallback decision. It takes an *optional* `MaterialInferencePort`
(injected; `None` ⇒ deterministic-only). The resolver is the single place the boundary is defined, so
the rule is testable in isolation.

```python
class MaterialKeyResolver:
    def __init__(self, normalizer: MaterialKeyNormalizer,
                 inference: MaterialInferencePort | None = None) -> None: ...

    def resolve(self, description_raw: str, unidad: str) -> CanonicalKey:
        parsed = self._normalizer.parse(description_raw, unidad)   # → CanonicalKey | None
        if parsed is not None and not _is_ambiguous(parsed):
            return parsed                                          # method="deterministic"
        if self._inference is not None:
            inf = self._inference.infer(description_raw)
            if inf is not None and not _is_ambiguous_inf(inf):
                return CanonicalKey(method="llm_inferred", raw=description_raw,
                                    unidad=unidad, **inf_fields)   # requires_review=True
        return CanonicalKey.unresolved(description_raw, unidad)    # method="unresolved", flagged
```

**Precise "ambiguous" condition (the boundary).** A deterministic parse is considered *ambiguous*
(→ yield to LLM) when, after running the full regex pipeline:

- `familia` is None (no `BARRA` / `acero dimensionado` family token recognised), **OR**
- `grado` is None (no grade token matched the enumerated collapse set), **OR**
- `diametro` is None (no token matched the canonical diameter table), **OR**
- `presentacion` is None (neither a `9M`-class nor a `DOB`-class marker found).

`unidad` is NOT part of the ambiguity test — it arrives already-typed on `MaterialLine.unidad`
(`Literal["KG","TN","RD","Rollo"]`), so it is always present; it is carried verbatim into the key.

Rationale: presentación and grado are the two fields most prone to silent over-merge (proposal risk
table). Requiring ALL FOUR to be non-None before accepting a deterministic match means a partial parse
never silently collapses two different physical items — it escalates to the LLM (flagged) or to
`unresolved` (flagged). This is the conservative posture the OCR-validation gate demands: **never auto-
confirm a key the regex could not fully resolve.**

**LLM hallucination guard.** Even an LLM result is re-validated: if the inferred `diametro` is not in
the canonical diameter table or `presentacion` not in `{9M, DOB}`, the resolver treats it as still-
ambiguous and falls to `unresolved` (flagged). The model can suggest, but cannot invent out-of-vocabulary
key components (proposal risk: "LLM hallucinated tuple").

---

## ADR-4 — Caching: per-run, in the resolver instance, keyed by `(raw, unidad)`

**Decision.** Cache LLM-inferred keys in a `dict[tuple[str, str], CanonicalKey]` held on the
`MaterialKeyResolver` instance, populated lazily inside `resolve()`. Key = `(description_raw, unidad)`.

**Where, and why there.**

| Option | Tradeoff |
|--------|----------|
| `RunContext` (rejected) | `RunContext` owns *filesystem* run isolation (output dirs, sidecars). Threading a model-inference cache through it pollutes its responsibility and would require passing `ctx` into a pure domain service — breaking purity. |
| Adapter (`OllamaMaterialInferenceAdapter`) (partially) | Reasonable for cross-run reuse, but the adapter has no notion of run lifecycle; a long-lived adapter could leak memory across runs and would cache even deterministic-path misses. |
| **Resolver instance, per-run** (chosen) | The resolver is constructed once per pipeline build (composition root) and lives exactly one run, matching the "per-run isolation" invariant (CLAUDE.md). The deterministic path is already pure-functional and fast (no cache needed); only the *expensive LLM calls* are memoized. Repeated identical descriptions across many guías in one run hit the cache and avoid re-invoking Ollama. The cache dies with the resolver → no cross-run leakage, deterministic reproducibility preserved. |

The deterministic parse itself is NOT cached (it is cheap and pure); only `infer()` results are. Cache
hits return the same `CanonicalKey` (frozen VO, safe to share). This keeps determinism: `temperature=0`
+ cache ⇒ a given raw description maps to exactly one key per run.

---

## ADR-5 — `match_method` + `requires_review` propagation to export

**Decision — minimal DTO additions, reuse the existing provenance pattern (`any_year_inferred`).**

The pipeline writes `description_canonical` from `CanonicalKey.group_token` AND carries the provenance
onto the line. We add ONE field to `MaterialLine` and ONE computed field to `ReconciliationRow`:

1. `MaterialLine` gains `match_method: MatchMethod = "deterministic"`. `requires_review: bool` already
   exists (`models.py:26`) — the resolver OR-sets it from `CanonicalKey.requires_review`.

2. `ReconciliationService` is **unchanged in grouping logic** but already aggregates
   `requires_review` from contributing lines (`reconciliation.py:146`). It gains a derived
   row-level method. Rather than store it (the engine builds rows from a key-union), expose a
   computed field on `ReconciliationRow`:

   ```python
   match_method: MatchMethod = "deterministic"   # set by reconciler: worst-case across contributing lines
   ```

   Aggregation rule (precedence, worst-wins): if any contributing declared or guía line is
   `unresolved` → row `unresolved`; else if any `llm_inferred` → `llm_inferred`; else `deterministic`.
   This is computed in `reconcile()` where the contributing lines are already in scope (extend the
   existing `row_requires_review` block at `reconciliation.py:146`). `requires_review` continues to be
   OR-aggregated as today, now also true whenever `match_method != "deterministic"`.

3. **Export round-trip (xlsx + csv).** `_row_to_values` (`xlsx_report.py:72`) and `_HEADERS` add one
   column `"Método"` (deterministic / llm_inferred / unresolved), mirroring how `"Año inferido"`
   (`any_year_inferred`) was added in rev-3 D5. CSV export uses the same row serializer. The existing
   `requires_review` already drives the review flag; `match_method` makes WHY visible.

**Persistence/extraction-cache.** `_stage_persist` already `model_dump`s lines/rows
(`pipeline.py:1199`). The new fields default to `"deterministic"` / `False`, so old serialized runs
(`review.json`, extraction cache) deserialize cleanly — backward compatible, no migration (same
strategy as D5/D6).

**API response model.** `ReconciliationRowResponse` (rev-2) should surface `match_method` for the
grid; this is the minimal frontend wiring (read-only display) the proposal scopes IN. No new endpoint,
no editing control.

---

## ADR-6 — Pipeline integration: upgrade the EXISTING `_stage_normalize`, do not add a stage

**Decision.** Do NOT add a new pipeline stage. Replace the body of the existing `_stage_normalize`
(`pipeline.py:1149`) so it uses the `MaterialKeyResolver` instead of `MaterialNormalizer.canonicalize`.

**Why fold into the existing stage, not add one.** `_stage_normalize` already exists for exactly this
purpose — "canonicalize material descriptions" — and already runs at the correct point (after
`normalize_dates`, before `reconcile`). Adding a parallel stage would create two normalization steps
writing the same field, an obvious code smell. The stage's contract ("fill `description_canonical`")
is preserved; only the *strength* of the canonicalizer changes. The stage graph in the rev-3 design
(`split → … → normalize → reconcile → persist`) is unchanged in shape — satisfying proposal §5
"behind the existing pipeline contract."

**New `_stage_normalize` body (data flow):**

```python
def _stage_normalize(self, declared, guias):
    def _norm_line(line: MaterialLine) -> MaterialLine:
        key = self._key_resolver.resolve(line.description_raw, line.unidad)
        return line.model_copy(update={
            "description_canonical": key.group_token,
            "match_method": key.method,
            "requires_review": line.requires_review or key.requires_review,
        })
    # same model_copy fan-out over declared.declared_lines and guias.lines as today
```

`MaterialNormalizer` (NFC/lowercase/whitespace) is **retained** and used INSIDE
`MaterialKeyNormalizer.parse` as the pre-clean step (execution step 1 of the skill: lowercase, strip
supplier prefix, normalise punctuation) — it is not deleted, it is composed.

**Composition root wiring** (`infrastructure/container.py: build_pipeline`):

```python
from reconciliation.adapters.inference.factory import build_inference_adapter
inference = build_inference_adapter(config)            # None when inference.enabled is False
key_resolver = MaterialKeyResolver(MaterialKeyNormalizer(), inference)
# pass key_resolver into ReconciliationPipeline(... key_resolver=key_resolver)
```

The pipeline constructor gains `key_resolver: MaterialKeyResolver` (constructed in `__init__` with a
default `MaterialKeyResolver(MaterialKeyNormalizer())` when not injected, so existing tests that build
the pipeline directly without the resolver keep working in deterministic-only mode — same defensive-
default approach as `deskew=None`). The pipeline depends only on the domain `MaterialKeyResolver` +
`MaterialInferencePort` Protocol; the concrete Ollama adapter is wired solely in the composition root
(Dependency Inversion preserved).

---

## Component & data-flow summary

| New / changed | Layer | Responsibility |
|---------------|-------|----------------|
| `CanonicalKey` (VO) + `MatchMethod` | domain (`material_key.py`) | Immutable canonical material key; `group_token`, `requires_review` policy |
| `MaterialKeyInference` (model) | domain (`models.py`) | LLM return shape (tuple + confidence) |
| `MaterialKeyNormalizer` | domain (`material_key_normalizer.py`) | Deterministic regex parse → `CanonicalKey` or None; composes `MaterialNormalizer` for pre-clean |
| `MaterialKeyResolver` (Strategy) | domain (`material_key_resolver.py`) | det-first → LLM-fallback → unresolved; per-run cache; ambiguity boundary |
| `MaterialInferencePort` (Protocol) | domain (`ports.py`) | Provider-agnostic text→tuple inference contract |
| `MaterialLine.match_method` | domain (`models.py`) | per-line provenance |
| `ReconciliationRow.match_method` | domain (`models.py`) | row-level worst-wins aggregate |
| `reconcile()` aggregation | domain (`reconciliation.py`) | set row `match_method` + `requires_review` from contributing lines (grouping UNCHANGED) |
| `InferenceConfig` + `AppConfig.inference` | application (`config.py`) | opt-in LLM provider/model/temp; off by default |
| `_stage_normalize` upgrade | application (`pipeline.py`) | use resolver; write `group_token`+`match_method`+`requires_review` |
| `OllamaMaterialInferenceAdapter` + factory | adapters (`adapters/inference/`) | lazy-import openai SDK; temp 0; strip `<think>`; JSON→tuple; None on failure |
| `_row_to_values` + `_HEADERS` `"Método"` col | adapters (`report/xlsx_report.py`) | export round-trip |
| `build_pipeline` wiring | infrastructure (`container.py`) | build resolver + inference adapter; inject into pipeline |
| `ReconciliationRowResponse.match_method` | infrastructure (api) | read-only grid surface |

---

## Invariants preserved (verification checklist)

- **Domain purity**: all new domain modules import only stdlib + Pydantic; LLM strictly behind
  `MaterialInferencePort`; adapter lazy-imports the SDK; pipeline depends on the Protocol only.
- **Units never converted**: `unidad` is a verbatim, separate grouping axis (excluded from
  `group_token`, present in `_GroupKey`); KG/TN/RD/Rollo summed independently — engine unchanged.
- **Presentación never merged**: `9M` and `DOB` are distinct, mandatory key components; a partial
  parse (presentación None) escalates rather than collapsing.
- **MATCH EXACT(0)**: reconciliation comparison logic untouched (`reconciliation.py:166`).
- **OCR-validation gate**: declared quantities never altered; `llm_inferred` and `unresolved` keys are
  ALWAYS `requires_review`; mismatches flagged, never auto-corrected.
- **`fecha` out of the material key**: grouping stays `(registro, fecha, group_token, unidad)`; the
  date is the handwritten reception date, untouched.
- **Local-first / air-gap**: inference is OFF by default; when enabled it hits a LOCAL Ollama
  (`localhost:11434`), introducing no external egress (distinct from the opt-in SUNAT network exception).
- **Reversibility**: setting `inference.enabled=false` ⇒ deterministic-only; reverting the branch
  restores `MaterialNormalizer.canonicalize` grouping. No migration.

## Rejected alternatives (summary)

- Put normalization in `ReconciliationService` — rejected (SRP/testability, ADR-1).
- Tuple grouping key instead of string `group_token` — rejected (would touch engine + export; ADR-1).
- Reuse `VisionLLMPort`/`vision:` config for inference — rejected (ISP/SoC, independent toggling; ADR-2).
- Cache in `RunContext` or adapter — rejected (purity / lifecycle mismatch; ADR-4).
- New parallel normalize stage — rejected (double-write smell; ADR-6).
- Accept partial deterministic parse as a match — rejected (silent over-merge risk; ADR-3).

## Open items for `sdd-tasks` / risks

- Canonical diameter table edge cases beyond the 7 known sizes (any `mm`-only or unusual fractions in
  the real-data tail) — `MaterialKeyNormalizer` must encode the table from
  `.claude/skills/material-canonical-matching/assets/canonical-key.md`; unmatched diameters → ambiguous.
- The LLM system prompt must be mirrored into the repo (skills dir is gitignored) — adapter constant or
  `docs/`-adjacent module.
- `codigo_sunat` method value is reserved in `MatchMethod` for the future SUNAT-código join (proposal
  OUT of scope); no code path produces it yet.
- Frontend: confirm `ReconciliationRowResponse` + grid column is the agreed minimal surface (read-only).
