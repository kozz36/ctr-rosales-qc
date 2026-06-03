"""Tests for delivery-floor flag propagation through ReconciliationService (R9b).

Verifies that a GuiaDeRemision with ``delivery_floor_applied=True`` causes:
  - ``GuiaContribution.delivery_floor_applied=True`` in the output row.
  - ``ReconciliationRow.requires_review=True`` (OR-set, non-blocking).
  - ``ReconciliationRow.has_delivery_floor=True`` (computed roll-up property).

Mirrors the R9.4 fecha-divergence tests in test_reconciliation.py.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from reconciliation.domain.models import GuiaDeRemision, MaterialLine, Registro
from reconciliation.domain.reconciliation import ReconciliationService


# ---------------------------------------------------------------------------
# Helpers — mirror test_reconciliation.py style
# ---------------------------------------------------------------------------


def _line(
    canonical: str,
    unidad: str,
    cantidad: str,
    confidence: float | None = None,
    page: int | None = None,
) -> MaterialLine:
    return MaterialLine(
        description_raw=canonical.upper(),
        description_canonical=canonical,
        unidad=unidad,  # type: ignore[arg-type]
        cantidad=Decimal(cantidad),
        confidence=confidence,
        source_page=page,
    )


def _guia(
    guia_id: str,
    registro: str | None,
    fecha: date | None,
    lines: list[MaterialLine],
    pages: list[int] | None = None,
    delivery_floor_applied: bool = False,
) -> GuiaDeRemision:
    return GuiaDeRemision(
        guia_id=guia_id,
        registro=registro,
        fecha=fecha,
        lines=lines,
        source_pages=pages or [],
        delivery_floor_applied=delivery_floor_applied,
    )


def _registro(
    numero: str,
    fecha: date | None,
    lines: list[MaterialLine],
) -> Registro:
    return Registro(
        numero=numero,
        fecha_declarada=fecha,
        declared_lines=lines,
    )


@pytest.fixture()
def svc() -> ReconciliationService:
    return ReconciliationService()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDeliveryFloorPropagation:
    def test_floor_flag_propagates_to_contribution(self, svc: ReconciliationService) -> None:
        """R9b: guía with delivery_floor_applied=True → contribution carries flag."""
        mat = "BARRA A615 G60 1/2\" 9M"
        guia = _guia(
            "T009-0001",
            "232",
            date(2026, 5, 20),
            [_line(mat, "TN", "2.000")],
            delivery_floor_applied=True,
        )
        reg = _registro("232", date(2026, 5, 20), [_line(mat, "TN", "2.000")])
        rows = svc.reconcile([reg], [guia])
        assert len(rows) >= 1
        row = rows[0]
        assert len(row.guias) == 1
        assert row.guias[0].delivery_floor_applied is True

    def test_floor_flag_sets_requires_review(self, svc: ReconciliationService) -> None:
        """R9b: delivery_floor_applied=True on any contributing guía → row.requires_review=True."""
        mat = "BARRA A615 G60 1/2\" 9M"
        guia = _guia(
            "T009-0001",
            "232",
            date(2026, 5, 20),
            [_line(mat, "TN", "2.000")],
            delivery_floor_applied=True,
        )
        reg = _registro("232", date(2026, 5, 20), [_line(mat, "TN", "2.000")])
        rows = svc.reconcile([reg], [guia])
        row = rows[0]
        assert row.requires_review is True

    def test_has_delivery_floor_property_true(self, svc: ReconciliationService) -> None:
        """R9b: row.has_delivery_floor computed property is True when any guía was floored."""
        mat = "BARRA A615 G60 1/2\" 9M"
        guia = _guia(
            "T009-0001",
            "232",
            date(2026, 5, 20),
            [_line(mat, "TN", "2.000")],
            delivery_floor_applied=True,
        )
        reg = _registro("232", date(2026, 5, 20), [_line(mat, "TN", "2.000")])
        rows = svc.reconcile([reg], [guia])
        row = rows[0]
        assert row.has_delivery_floor is True

    def test_no_floor_no_requires_review_inflation(self, svc: ReconciliationService) -> None:
        """R9b: guía with delivery_floor_applied=False → no spurious requires_review."""
        mat = "BARRA A615 G60 1/2\" 9M"
        guia = _guia(
            "T009-0001",
            "232",
            date(2026, 5, 20),
            [_line(mat, "TN", "2.000")],
            delivery_floor_applied=False,
        )
        reg = _registro("232", date(2026, 5, 20), [_line(mat, "TN", "2.000")])
        rows = svc.reconcile([reg], [guia])
        row = rows[0]
        assert row.guias[0].delivery_floor_applied is False
        assert row.has_delivery_floor is False
        # requires_review must be False for a clean MATCH with a non-null fecha and no floor
        assert row.requires_review is False

    def test_match_status_unaffected_by_floor_flag(self, svc: ReconciliationService) -> None:
        """R9b invariant: floor flag is additive side-channel — MATCH status unchanged."""
        mat = "BARRA A615 G60 1/2\" 9M"
        guia = _guia(
            "T009-0001",
            "232",
            date(2026, 5, 20),
            [_line(mat, "TN", "2.000")],
            delivery_floor_applied=True,
        )
        reg = _registro("232", date(2026, 5, 20), [_line(mat, "TN", "2.000")])
        rows = svc.reconcile([reg], [guia])
        row = rows[0]
        # MATCH status must NOT be affected by the floor flag
        assert row.status == "MATCH"
        assert row.delta == Decimal("0")
