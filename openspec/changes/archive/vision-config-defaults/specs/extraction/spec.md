# Spec — Extraction Domain (delta: vision-config-defaults)
**Change**: vision-config-defaults
**Domain**: extraction (delta over all prior extraction requirements)
**Phase**: spec (archived)
**Date**: 2026-06-03

---

## Purpose

Record the behavioral change introduced by `vision-config-defaults` (PR #3) that affects the
`extraction` capability: `VisionConfig.disable_thinking` now defaults to `True`.

This is a **delta spec** over `openspec/specs/extraction/spec.md` (EXT-001 through EXT-023).
All prior requirements remain in force. The requirement below ADDS new behaviour.
It is marked `[ADDED]`.

---

## Requirements

### EXT-024 — [ADDED] Vision model thinking phase MUST be disabled by default

`VisionConfig.disable_thinking` MUST default to `True`. When `True`, the system MUST prepend
a `/no_think` instruction (or provider-equivalent) to vision requests so that the model
skips the chain-of-thought `<think>` phase before generating the date-extraction response.

The setting MUST be overridable per-environment via the environment variable
`RECONCILIATION__VISION__DISABLE_THINKING` (Pydantic-settings env_prefix
`RECONCILIATION__`, nested delimiter `__`). Setting it to `false` restores the prior
thinking-enabled behaviour.

**Rationale**: for structured OCR/date-extraction tasks (reading DD/MM from a stamp), the
`<think>` phase adds ~12 s per call on qwen3.5:397b-class models with no measurable accuracy
benefit. The default fast path reduces median vision latency without a quality regression.

The `disable_thinking` flag MUST be applied to both guía date-extraction calls
(`VisionLLMPort.read_handwritten_date` for guía pages) and Protocolo date-extraction calls
(`_stage_extract_declared_date` — R9, FDR-001). It MUST NOT be applied to non-vision
pipeline stages.

#### Acceptance Scenarios

**Scenario EXT-S33 — disable_thinking=True by default: /no_think prefix applied**

Given a default `VisionConfig` (no env override)
When `VisionConfig.disable_thinking` is read
Then `disable_thinking = True`
And the vision adapter prepends `/no_think` (or provider-equivalent) to the system/user
  prompt for every vision call
And the adapter does NOT send the chain-of-thought `<think>` phase

**Scenario EXT-S34 — disable_thinking overridable via env var**

Given `RECONCILIATION__VISION__DISABLE_THINKING=false` is set in the environment
When `VisionConfig` is instantiated (Pydantic-settings reads the env var)
Then `disable_thinking = False`
And the vision adapter omits the `/no_think` prefix (thinking enabled)
And the behavior is identical to the pre-change default

**Scenario EXT-S35 — disable_thinking applies to both guía and Protocolo vision calls**

Given `disable_thinking = True` (default)
And a run that processes guía pages (handwritten date) and Protocolo pages (declared date)
When the pipeline calls `VisionLLMPort.read_handwritten_date` for guía pages
And calls `VisionLLMPort.read_handwritten_date` for Protocolo pages (R9 declared-date stage)
Then both calls include the `/no_think` prefix
And OCR/text-extraction stages (PaddleOCR, digital text) are unaffected

---

## Out of scope for this delta

- `.env.example` documentation (docs-only, no behavioral requirement — see proposal.md §2).
- Any other vision configuration parameter.
- Domain logic, reconciliation, grouping, or fecha-divergence behavior.
