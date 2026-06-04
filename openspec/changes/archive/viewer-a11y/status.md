# Status — viewer-a11y

**Change**: `viewer-a11y`
**Branch**: `fix/viewer-a11y-31`
**Date**: 2026-06-04

## Status

Implemented on branch `fix/viewer-a11y-31`. **PR #32 open** (base: main@84a087b). Pending
merge at time of documentation; ctr-reviewer verdict APPROVE (clean).

**Gate**: ctr-reviewer APPROVE (clean, no findings) + SA-5 Playwright validation
(VITE_MOCK=1): W1 focus restored to chip on ESC; W2 Tab last→first and Shift+Tab first→last
wrap correctly; S1 `=` key → `scale(1.5)`, `-` key → `scale(1.0)` (layout-independent).

**TDD cycle**: RED commit `6d853fc` (4 a11y tests failing) → GREEN commit `06345c5`.

**Test counts (on branch)**: Frontend 241/241 vitest · PageSheetViewer 20/20.
Typecheck: files clean (pre-existing `GuiaDrillDown.test.ts` errors are in main, not this PR).

## Key artifacts

- `frontend/src/features/review/PageSheetViewer.vue` — W1 focus-restore, W2 focus-trap
  (`onTab`), S1 unified `@keydown="onKeydown"` handler.
- `frontend/src/features/review/__tests__/PageSheetViewer.test.ts` — 4 new RED a11y tests
  (W1, W2-tab, W2-shift-tab, S1 key normalization).

## Requirement IDs

No new capability spec promoted (a11y correctness fix; resolves deferred findings from
PR #30). Aligns with REV-* accessibility requirements in `openspec/specs/review/spec.md`.

**Gotcha — page-change guard**: the trigger reference capture uses `if (!wasOpen)` in the
`watch(isOpen)` handler. Without this guard, a page-navigation event while the viewer is open
would overwrite `triggerEl` with whatever element is momentarily focused during navigation,
breaking focus restore on the eventual close.
