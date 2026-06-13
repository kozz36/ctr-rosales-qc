# Delta for Review

**Change**: optional-vision-key-ui
**Domain**: review
**Type**: Delta (additive to `openspec/specs/review/spec.md`)
**Date**: 2026-06-12

---

> All existing requirements REV-001 through REV-R33 and all prior deltas remain in force.
> This delta adds REV-R34 and REV-R35 only. No existing requirements are removed.

---

## ADDED Requirements

### REV-R34 — Reprocess surfaces gated visible-but-disabled when vision unavailable

The three AI reprocess surfaces MUST be rendered **visible but disabled** (not hidden) with an
explanatory tooltip when `capabilities.vision_enabled=false`. They MUST be rendered enabled
(interactive) when `capabilities.vision_enabled=true`.

Affected surfaces:

| Surface | Component | Action |
|---|---|---|
| Single-guía Reprocesar | `GuiaDrillDown` — [Acciones] Reprocesar item (REV-R24) | Single-guía AI reprocess |
| Errored guía Reprocesar con IA | `ErroredGuiasPanel` — per-guía reprocess button | Per-guía AI reprocess |
| Bulk Procesar todos con IA | `PendientesPorProcesarTab` / `ErroredGuiasPanel` — bulk button (REV-R20/R21) | Bulk AI reprocess |

Gating MUST be applied as a pre-click guard: the controls are non-interactive BEFORE the
engineer clicks, not as a post-click 503 error. The existing backend 503 (`vision.enabled=False`
returns 503 for reprocess endpoints — REV-R20-S04) remains as a safety backstop but MUST NOT
be the primary UX signal.

The tooltip text MUST communicate that a vision API key is required and direct the engineer to
the Settings modal. Exact copy is implementation-level; the spec requires the message conveys
the actionable path.

The disabled state MUST NOT remove the controls from the DOM — they MUST remain visible and
accessible (e.g., `disabled` attribute or equivalent non-interactive state), so the engineer
understands the feature exists and how to enable it.

#### Scenario REV-R34-S01: vision off — all three surfaces disabled with tooltip

- GIVEN `capabilities.vision_enabled=false`
- WHEN the engineer views the review UI (GuiaDrillDown, ErroredGuiasPanel, PendientesPorProcesarTab)
- THEN the [Acciones] > Reprocesar item is visible but disabled
- AND the per-guía reprocess button in ErroredGuiasPanel is visible but disabled
- AND the "Procesar todos con IA" bulk button is visible but disabled
- AND hovering each disabled control reveals a tooltip explaining that a vision key is required

#### Scenario REV-R34-S02: vision on — all three surfaces enabled

- GIVEN `capabilities.vision_enabled=true`
- WHEN the engineer views the review UI
- THEN all three reprocess surfaces are interactive (not disabled)
- AND no vision-key tooltip is shown on those controls

#### Scenario REV-R34-S03: disabled controls remain in the DOM

- GIVEN `capabilities.vision_enabled=false`
- WHEN the DOM is inspected
- THEN each reprocess control is present in the DOM with a disabled or non-interactive attribute
- AND NO reprocess control is conditionally absent (v-if=false or display:none hiding)

#### Scenario REV-R34-S04: pre-click gating — no 503 reaches engineer from disabled button

- GIVEN `capabilities.vision_enabled=false`
- AND all reprocess controls are disabled
- WHEN the engineer attempts to interact with a disabled control
- THEN no API call is made
- AND no 503 error is displayed to the engineer from this interaction

---

### REV-R35 — Capabilities state drives gating reactively

The disabled/enabled state of the reprocess surfaces MUST be derived reactively from the
`capabilitiesStore.vision_enabled` value (CAP-002). Gating MUST NOT be hardcoded.

If the capabilities store is not yet populated (loading state), the reprocess controls MUST
default to disabled until the store is resolved.

#### Scenario REV-R35-S01: loading state — controls default disabled

- GIVEN the app just started and the capabilities fetch is in-flight
- WHEN the engineer navigates to ReviewPage before the fetch resolves
- THEN all reprocess controls are in disabled state
- AND they transition to the correct enabled/disabled state once the fetch resolves

#### Scenario REV-R35-S02: gating is reactive — no hardcoded vision flag

- GIVEN the `capabilitiesStore.vision_enabled` value changes (e.g., simulated in tests)
- THEN the three reprocess controls reflect the updated state without a page reload

---

## MUST-NOT Invariants (extension)

- All prior MUST-NOT invariants from REV-001 through REV-R33 remain in force.
- Reprocess controls MUST NOT be removed from the DOM when vision is off (hidden ≠ disabled).
- The backend 503 backstop (REV-R20-S04) MUST NOT be removed or weakened by this delta.
- The gating state MUST NOT be based on a hardcoded compile-time flag; it MUST read the
  runtime capabilities store.
- SA-5 Playwright runtime validation is MANDATORY for gating and settings modal flows before
  marking this capability complete.
