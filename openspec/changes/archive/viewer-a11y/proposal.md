# Proposal — viewer-a11y

**Change**: `viewer-a11y`
**Phase**: archived (implemented; PR #32 open — merged or pending merge at time of writing)
**Artifact store**: hybrid (engram + openspec)
**Date**: 2026-06-04
**Gate**: ctr-reviewer APPROVE (clean verdict) + SA-5 Playwright runtime validation (focus
  restore, focus-trap, layout-safe zoom keys — validated via VITE_MOCK=1).
**Status**: Implemented on branch `fix/viewer-a11y-31`; PR #32 open (base: main@84a087b).
**Issue**: #31

---

## 1. Intent

### Problem

PR #30 (page-sheet-viewer) shipped with three non-blocking a11y findings from ctr-reviewer:

- **W1** (WCAG 2.4.3 — Focus Not Obscured): When the viewer closes, focus was not restored to
  the chip that triggered it. Screen-reader and keyboard users lost their position in the
  review table.
- **W2** (focus-trap): Tab/Shift+Tab could escape the dialog to background content while the
  viewer was open, violating the modal interaction contract.
- **S1** (layout-safe zoom keys): The `@keydown.+` Vue modifier required Shift (the `+`
  character), making the keybinding layout-dependent. On some keyboards, `+` without Shift
  produces `=`, breaking the zoom-in shortcut.

These were deferred from PR #30 as non-blocking, tracked as issue #31.

### Why now

a11y correctness is a product quality standard, not a cosmetic concern. Focus management
(WCAG 2.4.3) is required for WCAG compliance. The deferred findings have a designated issue
and are unambiguous to implement.

### Success looks like

- ESC (or close button) closes the viewer and returns focus to the chip that opened it.
- Tab and Shift+Tab cycle only within the dialog while it is open.
- `=` and `+` both zoom in; `-` and `_` both zoom out, regardless of keyboard layout.
- All 20 PageSheetViewer vitest tests pass (including 4 new a11y RED tests).

---

## 2. Scope

**Frontend-only (`PageSheetViewer.vue` and its test):**
- **W1**: capture `document.activeElement` on the open transition; restore `triggerEl?.focus()`
  on close. Guard with `if (!wasOpen)` so a page-change while open does not overwrite the
  trigger reference.
- **W2**: `onTab(e)` handler: Tab → advance to first focusable if currently at last; Shift+Tab
  → advance to last focusable if currently at first. Uses `querySelectorAll` on the dialog for
  focusable elements.
- **S1**: replace `@keydown.+` / `@keydown.-` with a unified `@keydown="onKeydown"` comparing
  `event.key` directly (`+` or `=` → zoom in; `-` or `_` → zoom out). Remove Shift dependency.

**Out of scope:** no backend, domain, or pipeline change.

---

## 3. Rollback / Abort plan

Revert `PageSheetViewer.vue` to the PR #30 state. a11y regressions are non-breaking
for sighted mouse users. No data impact.
