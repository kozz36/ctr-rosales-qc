# Status — r9c-reception-date-ceiling

**Change**: `r9c-reception-date-ceiling`
**Branch**: `feat/rev2-identity-domain`
**Date**: 2026-06-03

## Status

Implemented & merged to main via **PR #8**.

**Gate**: Judgment-Day APPROVED after 3 rounds + 2 fix iterations.
- Round 1 surfaced the ordering invariant risk (divergence must run before ceiling — ADR-C3);
  test `test_ceiling_does_not_mask_divergence` added.
- Round 2 surfaced the crossed-bounds no-clamp policy (ADR-C2) and the persistence gap
  (ADR-C4 — `fecha_entrega` lost on ReviewService re-reconcile); both fixed before round 3.
- Round 3 confirmed all ceiling branches, crossed-bounds detection, persistence fix, and
  domain purity. APPROVED.

**Test counts**: 972 backend unit tests passing (targeted paths).

## Key artifacts

- `backend/src/reconciliation/domain/date_ceiling.py` — pure domain ceiling function (FDR-014, FDR-015)
- `backend/src/reconciliation/domain/models.py` — `reception_ceiling_applied`, `delivery_after_protocolo`, `fecha_entrega` (persisted) fields on GuiaDeRemision, GuiaContribution; `has_reception_ceiling` on ReconciliationRow
- `backend/src/reconciliation/domain/reconciliation_service.py` — ceiling wiring + divergence-before-clamp ordering (ADR-C3)
- `backend/src/reconciliation/application/pipeline.py` — `_stage_sunat_fetch` writes `guia.fecha_entrega`; `_stage_reconcile` rebuilds `delivery_dates` from guías (ADR-C4)
- `backend/src/reconciliation/services/review_service.py` — reassign, line-edit, field-edit paths rebuild `delivery_dates` from guías (FDR-016)
- `backend/src/reconciliation/infrastructure/api/schemas.py` + `routes.py` — DTO surface (FDR-014, FDR-015)
- `backend/tests/unit/domain/test_date_ceiling.py` — four-branch ceiling tests + crossed-bounds + divergence ordering invariant (no mocks)

## New requirement IDs

- **FDR-014** — Reception date ceiling: Protocolo authoritative date as physical upper bound (four-branch ceiling function, critical ordering)
- **FDR-015** — Crossed-bounds anomaly: delivery after Protocolo — warn, do NOT clamp (distinct `delivery_after_protocolo` WARNING)
- **FDR-016** — Persistence of `fecha_entrega` and `delivery_dates` across review re-reconcile (single source of truth)

All three requirements are promoted into `openspec/specs/fecha-divergence/spec.md`.
