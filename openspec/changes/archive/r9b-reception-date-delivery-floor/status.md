# Status — r9b-reception-date-delivery-floor

**Change**: `r9b-reception-date-delivery-floor`
**Branch**: `feat/rev2-identity-domain`
**Date**: 2026-06-03

## Status

Implemented & merged to main via **PR #5**.

**Gate**: Judgment-Day APPROVED after 2 rounds.
- Round 1 surfaced the JD-identified null-day/month gap (ADR-F3) — fixed before merge.
- Round 2 confirmed all four floor branches, side-channel propagation, graceful degrade, and domain purity.

**Test counts**: 892 backend unit tests passing (targeted paths).

## Key artifacts

- `backend/src/reconciliation/domain/date_floor.py` — pure domain floor function (FDR-012)
- `backend/src/reconciliation/domain/models.py` — `delivery_floor_applied` fields on GuiaDeRemision, GuiaContribution, ReconciliationRow
- `backend/src/reconciliation/application/pipeline.py` — `_stage_normalize_dates` floor wiring + null-day/month gap fix
- `backend/src/reconciliation/infrastructure/api/schemas.py` + `routes.py` — DTO surface
- `backend/tests/unit/domain/test_date_floor.py` — four-branch pure-function tests (no mocks)

## New requirement IDs

- **FDR-012** — Reception date delivery floor (physical lower bound, four-branch floor function)
- **FDR-013** — Null reception date with known delivery date: floor to fecha_entrega (null-day/month gap)

Both requirements are promoted into `openspec/specs/fecha-divergence/spec.md`.
