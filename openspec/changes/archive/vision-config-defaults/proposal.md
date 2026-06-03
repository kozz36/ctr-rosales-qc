# Proposal — vision-config-defaults

**Change**: `vision-config-defaults`
**Phase**: archived (implemented & merged)
**Artifact store**: hybrid (engram + openspec)
**Date**: 2026-06-03
**Gate**: strict-TDD (118 config + vision tests for the `disable_thinking` part, PR #3).
  `.env.example` is docs-only (PR #4); no behavioral tests required.
**Status**: Implemented & merged to main via PR #3 (`disable_thinking`) and PR #4 (`.env.example`).

---

## 1. Intent

Two independent improvements packaged together since both relate to operator configuration
experience:

### Part A — `disable_thinking` default flipped to True (PR #3)

`VisionConfig.disable_thinking` defaulted to `False`, meaning every `qwen3.5:397b-cloud`
vision call sent the full chain-of-thought `<think>` phase. In practice:
- The `<think>` phase added ~12 s per vision call for the qwen3.5 model.
- Disabling it (`/no_think` system prefix) improved OCR/vision capture quality in tests.
- The docker-compose environment hardcoded no override.

Flipping the default to `True` and adding `RECONCILIATION__VISION__DISABLE_THINKING=true`
in docker-compose makes the fast path the default. It remains overridable per-machine via
the environment variable `RECONCILIATION__VISION__DISABLE_THINKING=false`.

### Part B — `.env.example` files corrected (PR #4)

Two `.env.example` files existed with usability problems:

1. **Root `.env.example`** was absent (no documented guide for the docker-compose
   `${}` interpolation variables: `OLLAMA_MODEL`, `OLLAMA_BASE_URL`, and the full
   `RECONCILIATION__*` app config namespace).

2. **`backend/.env.example`** used bare env var names like `VISION_PROVIDER`,
   `VISION_MODEL`, etc. These names **never bound** under the Pydantic-settings
   `env_prefix = "RECONCILIATION__"` with `env_nested_delimiter = "__"`. Only the API
   keys (injected directly by `VisionConfig` from `os.environ`) worked. The file gave
   a false impression that bare names were valid, causing operator confusion and failed
   local overrides.

Part B is **docs-only** — no behavioral requirements authored; effects are limited to
operator documentation.

---

## 2. Scope

### In scope

**Part A (behavioral)**:
- `VisionConfig.disable_thinking` default changed `False → True` in `config.py`.
- `docker-compose.yml`: add `RECONCILIATION__VISION__DISABLE_THINKING=true` to the backend
  `environment` block.
- All 118 config + vision tests verified green against the new default.

**Part B (docs-only)**:
- New root `.env.example`: documents `OLLAMA_MODEL`, `OLLAMA_BASE_URL` (compose interpolation
  variables) and the complete `RECONCILIATION__*` namespace (all settings under `env_prefix
  = "RECONCILIATION__"` with `env_nested_delimiter = "__"`).
- Corrected `backend/.env.example`: replaces bare names with the correct prefixed form
  (e.g. `RECONCILIATION__VISION__PROVIDER=ollama`) plus a comment explaining the prefix
  requirement. The API keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) are documented as-is
  because they are read from bare names by `VisionConfig` directly.

### Out of scope

- Any domain change, pipeline logic change, or reconciliation behavior modification.
- New configuration settings (all settings documented in `.env.example` already existed).
- Changes to ports, adapters, or any non-config layer.

---

## 3. Rationale

`disable_thinking=True` is the default that matches the real-world usage pattern (operators
run the cloud provider with `qwen3.5:397b-cloud` where `<think>` is expensive and provides
no measurable accuracy improvement for structured OCR/date-extraction tasks). The env-var
override preserves flexibility for operators who want to experiment with thinking enabled.

The `.env.example` fix is a usability requirement surfaced post-R10: the old `backend/.env.example`
misled operators into setting bare `VISION_PROVIDER=ollama` which silently did nothing under
Pydantic-settings. Correcting the documented form prevents silent misconfiguration.

---

## 4. Rollback / Abort plan

Part A: revert `disable_thinking` default to `False` and remove the docker-compose env var.
One-commit revert, no data impact.
Part B: delete the new root `.env.example` and restore the old `backend/.env.example`.
Docs-only; no behavioral revert needed.
