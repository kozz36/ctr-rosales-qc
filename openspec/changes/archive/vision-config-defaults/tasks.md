# Tasks — vision-config-defaults

**Change**: `vision-config-defaults` · **Phase**: tasks (archived) · **Store**: hybrid · **Date**: 2026-06-03
**Branch**: `feat/rev2-identity-domain`
**Strict TDD**: active (Part A only — Part B is docs-only)
**Gate**:
  - Part A (`disable_thinking`): strict-TDD, 118 config + vision tests. Merged via PR #3.
  - Part B (`.env.example`): docs-only, no tests required. Merged via PR #4.
**Status**: Implemented & merged to main via PR #3 and PR #4.

All tasks are marked `[x]` (implemented).

---

## Part A — `disable_thinking` default (PR #3)

### [x] VC.1 — `VisionConfig.disable_thinking` default changed False → True + docker-compose env

**Spec refs**: EXT-024.

**Deliverables**:
- `backend/src/reconciliation/application/config.py`:
  - `VisionConfig.disable_thinking: bool = Field(default=True, ...)` (was `False`)
- `docker-compose.yml` (or equivalent compose file):
  - Add `RECONCILIATION__VISION__DISABLE_THINKING=true` to backend `environment` block.
  - Rationale comment: "Disables qwen3.5 <think> phase; improves OCR date-extraction
    latency ~12s/call. Override with =false to re-enable thinking."

**Tests** (118 config + vision tests verified; update `backend/tests/unit/application/test_config.py`
and `backend/tests/unit/adapters/vision/`):
- `VisionConfig()` with no env override → `disable_thinking = True` (default) (EXT-S33).
- `VisionConfig()` with `RECONCILIATION__VISION__DISABLE_THINKING=false` env var →
  `disable_thinking = False` (EXT-S34).
- Vision adapter with `disable_thinking=True` → request includes `/no_think` prefix
  (or provider-equivalent; verify via adapter unit test with mocked HTTP client).
- Vision adapter with `disable_thinking=False` → request does NOT include `/no_think` prefix.
- Both guía and Protocolo vision call paths respect `disable_thinking` flag (EXT-S35).
- All 118 config + vision tests green with new default.

**Commit message**: `feat(config): flip VisionConfig.disable_thinking default to True; add compose env var (EXT-024)`

---

## Part B — `.env.example` documentation (PR #4)

### [x] VC.2 — Root `.env.example`: document compose interpolation vars + RECONCILIATION__ namespace

**Spec refs**: proposal.md §2 Part B (docs-only — no spec requirement authored).

**Deliverables** (root `.env.example`):
- `OLLAMA_MODEL` — compose `${}` interpolation var for the Ollama model name (e.g. `qwen3.5:9b`)
- `OLLAMA_BASE_URL` — compose `${}` interpolation var for the Ollama base URL
- Full `RECONCILIATION__*` namespace: every setting under `env_prefix = "RECONCILIATION__"`
  with `env_nested_delimiter = "__"`, documented with type, default, and one-line description.
  Groups: `RECONCILIATION__VISION__*`, `RECONCILIATION__OCR__*`, `RECONCILIATION__SUNAT__*`,
  `RECONCILIATION__CONFIDENCE__*`, `RECONCILIATION__INFERENCE__*`, etc.
- Comment block explaining the prefix convention.

**No tests required** (docs-only file).

**Commit message**: `docs(config): add root .env.example documenting compose vars + RECONCILIATION__ namespace`

### [x] VC.3 — `backend/.env.example`: replace bare names with prefixed form

**Spec refs**: proposal.md §2 Part B (docs-only — no spec requirement authored).

**Deliverables** (`backend/.env.example`):
- Replace bare names (e.g. `VISION_PROVIDER`, `VISION_MODEL`) with the correct prefixed
  form (`RECONCILIATION__VISION__PROVIDER`, `RECONCILIATION__VISION__MODEL`).
- Add comment block: "All app settings MUST use the RECONCILIATION__ prefix +
  double-underscore nesting. Only ANTHROPIC_API_KEY and OPENAI_API_KEY are injected as
  bare names (read directly by VisionConfig from os.environ)."
- Keep `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` as bare names (these are correct).

**No tests required** (docs-only file).

**Commit message**: `docs(config): fix backend/.env.example — replace bare names with RECONCILIATION__ prefixed form`
