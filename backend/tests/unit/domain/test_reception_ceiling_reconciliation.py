"""Tests for reception-ceiling flag propagation through ReconciliationService.

Verifies that when a guía's fecha is LATER than the registro's authoritative
Protocolo date (ceiling), the reconciler:

  (a) clamps the contribution fecha to the Protocolo date.
  (b) sets reception_ceiling_applied=True on the contribution.
  (c) STILL sets fecha_divergence=True on the contribution (R9 NOT masked —
      divergence is computed on the ORIGINAL date before the clamp).
  (d) sets row.requires_review=True (ceiling OR-sets, never clears).
  (e) sets row.has_reception_ceiling=True (computed roll-up).
  (f) leaves status, delta, summed_qty UNCHANGED (fecha is never a grouping axis).

Also verifies the negative case:
  - A guía date <= Protocolo date is NOT clamped; reception_ceiling_applied=False.

Mirrors the delivery-floor tests (test_delivery_floor_reconciliation.py) and the
R9 divergence tests (test_reconciliation.py).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from reconciliation.domain.models import GuiaDeRemision, MaterialLine, Registro
from reconciliation.domain.reconciliation import ReconciliationService


# ---------------------------------------------------------------------------
# Helpers — mirror test_delivery_floor_reconciliation.py style
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
) -> GuiaDeRemision:
    return GuiaDeRemision(
        guia_id=guia_id,
        registro=registro,
        fecha=fecha,
        lines=lines,
        source_pages=pages or [],
    )


def _registro(
    numero: str,
    *,
    fecha_declarada: date | None = None,
    lines: list[MaterialLine],
) -> Registro:
    return Registro(
        numero=numero,
        fecha_declarada=fecha_declarada,
        declared_lines=lines,
    )


@pytest.fixture()
def svc() -> ReconciliationService:
    return ReconciliationService()


# Shared material canonical key used across tests
_MAT = 'BARRA A615 G60 1/2" 9M'
# Protocolo authoritative date (the ceiling)
_PROTOCOLO_DATE = date(2026, 5, 28)
# Guía date that is LATER than the Protocolo (must be clamped)
_GUIA_DATE_LATER = date(2026, 6, 5)
# Guía date that is EARLIER than the Protocolo (must NOT be clamped)
_GUIA_DATE_EARLIER = date(2026, 5, 20)
# Guía date that is EQUAL to the Protocolo (must NOT be clamped)
_GUIA_DATE_EQUAL = date(2026, 5, 28)


# ---------------------------------------------------------------------------
# Tests: guía date LATER than Protocolo (ceiling fires)
# ---------------------------------------------------------------------------


class TestReceptionCeilingApplied:
    def test_contribution_fecha_clamped_to_protocolo(
        self, svc: ReconciliationService
    ) -> None:
        """(a) Guía fecha later than Protocolo → contribution.fecha clamped to ceiling."""
        guia = _guia(
            "T009-0001", "232", _GUIA_DATE_LATER, [_line(_MAT, "TN", "2.000")]
        )
        reg = _registro(
            "232",
            fecha_declarada=_PROTOCOLO_DATE,
            lines=[_line(_MAT, "TN", "2.000")],
        )
        rows = svc.reconcile([reg], [guia])
        assert len(rows) >= 1
        row = rows[0]
        assert len(row.guias) == 1
        contrib = row.guias[0]
        assert contrib.fecha == _PROTOCOLO_DATE, (
            f"Expected fecha clamped to {_PROTOCOLO_DATE}, got {contrib.fecha}"
        )

    def test_reception_ceiling_applied_flag_set(
        self, svc: ReconciliationService
    ) -> None:
        """(b) Guía fecha later than Protocolo → reception_ceiling_applied=True."""
        guia = _guia(
            "T009-0001", "232", _GUIA_DATE_LATER, [_line(_MAT, "TN", "2.000")]
        )
        reg = _registro(
            "232",
            fecha_declarada=_PROTOCOLO_DATE,
            lines=[_line(_MAT, "TN", "2.000")],
        )
        rows = svc.reconcile([reg], [guia])
        contrib = rows[0].guias[0]
        assert contrib.reception_ceiling_applied is True

    def test_fecha_divergence_still_set_r9_not_masked(
        self, svc: ReconciliationService
    ) -> None:
        """(c) R9 divergence check runs on ORIGINAL date — not masked by ceiling clamp.

        A guía date later than the Protocolo diverges by day-month too, so
        fecha_divergence MUST still be True even after the ceiling is applied.
        This confirms the sequencing invariant: divergence first, clamp second.
        """
        guia = _guia(
            "T009-0001", "232", _GUIA_DATE_LATER, [_line(_MAT, "TN", "2.000")]
        )
        reg = _registro(
            "232",
            fecha_declarada=_PROTOCOLO_DATE,
            lines=[_line(_MAT, "TN", "2.000")],
        )
        rows = svc.reconcile([reg], [guia])
        contrib = rows[0].guias[0]
        assert contrib.fecha_divergence is True, (
            "R9 fecha_divergence must be True — ceiling clamp MUST NOT mask divergence"
        )

    def test_requires_review_set_by_ceiling(self, svc: ReconciliationService) -> None:
        """(d) Ceiling clamp OR-sets row.requires_review=True."""
        guia = _guia(
            "T009-0001", "232", _GUIA_DATE_LATER, [_line(_MAT, "TN", "2.000")]
        )
        reg = _registro(
            "232",
            fecha_declarada=_PROTOCOLO_DATE,
            lines=[_line(_MAT, "TN", "2.000")],
        )
        rows = svc.reconcile([reg], [guia])
        assert rows[0].requires_review is True

    def test_has_reception_ceiling_property_true(
        self, svc: ReconciliationService
    ) -> None:
        """(e) row.has_reception_ceiling computed property is True when any guía was clamped."""
        guia = _guia(
            "T009-0001", "232", _GUIA_DATE_LATER, [_line(_MAT, "TN", "2.000")]
        )
        reg = _registro(
            "232",
            fecha_declarada=_PROTOCOLO_DATE,
            lines=[_line(_MAT, "TN", "2.000")],
        )
        rows = svc.reconcile([reg], [guia])
        assert rows[0].has_reception_ceiling is True

    def test_status_delta_summed_qty_unchanged(
        self, svc: ReconciliationService
    ) -> None:
        """(f) Ceiling clamp is additive side-channel — MATCH status, delta, summed_qty unchanged.

        fecha is NEVER a grouping axis (R8/MAT-001): clamping the contribution
        fecha must NOT affect the group key, status, delta, or quantity math.
        """
        guia = _guia(
            "T009-0001", "232", _GUIA_DATE_LATER, [_line(_MAT, "TN", "2.000")]
        )
        reg = _registro(
            "232",
            fecha_declarada=_PROTOCOLO_DATE,
            lines=[_line(_MAT, "TN", "2.000")],
        )
        rows = svc.reconcile([reg], [guia])
        row = rows[0]
        assert row.status == "MATCH"
        assert row.delta == Decimal("0")
        assert row.summed_qty == Decimal("2.000")


# ---------------------------------------------------------------------------
# Tests: guía date NOT later than Protocolo (ceiling does NOT fire)
# ---------------------------------------------------------------------------


class TestReceptionCeilingNotApplied:
    def test_earlier_guia_date_not_clamped(self, svc: ReconciliationService) -> None:
        """Guía date before Protocolo → NOT clamped; reception_ceiling_applied=False."""
        guia = _guia(
            "T009-0002", "232", _GUIA_DATE_EARLIER, [_line(_MAT, "TN", "2.000")]
        )
        reg = _registro(
            "232",
            fecha_declarada=_PROTOCOLO_DATE,
            lines=[_line(_MAT, "TN", "2.000")],
        )
        rows = svc.reconcile([reg], [guia])
        contrib = rows[0].guias[0]
        assert contrib.reception_ceiling_applied is False
        assert contrib.fecha == _GUIA_DATE_EARLIER

    def test_equal_guia_date_not_clamped(self, svc: ReconciliationService) -> None:
        """Guía date == Protocolo date → NOT clamped (boundary: <= is valid)."""
        guia = _guia(
            "T009-0003", "232", _GUIA_DATE_EQUAL, [_line(_MAT, "TN", "2.000")]
        )
        reg = _registro(
            "232",
            fecha_declarada=_PROTOCOLO_DATE,
            lines=[_line(_MAT, "TN", "2.000")],
        )
        rows = svc.reconcile([reg], [guia])
        contrib = rows[0].guias[0]
        assert contrib.reception_ceiling_applied is False
        assert contrib.fecha == _GUIA_DATE_EQUAL

    def test_has_reception_ceiling_false_no_clamp(
        self, svc: ReconciliationService
    ) -> None:
        """row.has_reception_ceiling is False when no guía was clamped."""
        guia = _guia(
            "T009-0002", "232", _GUIA_DATE_EARLIER, [_line(_MAT, "TN", "2.000")]
        )
        reg = _registro(
            "232",
            fecha_declarada=_PROTOCOLO_DATE,
            lines=[_line(_MAT, "TN", "2.000")],
        )
        rows = svc.reconcile([reg], [guia])
        assert rows[0].has_reception_ceiling is False

    def test_no_requires_review_inflation_when_no_ceiling(
        self, svc: ReconciliationService
    ) -> None:
        """No ceiling clamp → no spurious requires_review inflation."""
        guia = _guia(
            "T009-0002", "232", _GUIA_DATE_EQUAL, [_line(_MAT, "TN", "2.000")]
        )
        reg = _registro(
            "232",
            fecha_declarada=_PROTOCOLO_DATE,
            lines=[_line(_MAT, "TN", "2.000")],
        )
        rows = svc.reconcile([reg], [guia])
        # fecha_divergence is also False (same day-month), so overall requires_review=False
        assert rows[0].requires_review is False

    def test_no_ceiling_data_graceful_degrade(self, svc: ReconciliationService) -> None:
        """No Protocolo date (ceiling=None) → graceful degrade; no clamp, no flag."""
        guia = _guia(
            "T009-0004", "232", _GUIA_DATE_LATER, [_line(_MAT, "TN", "2.000")]
        )
        # Registro with no electronic fecha (ceiling is None)
        reg = _registro(
            "232",
            fecha_declarada=None,
            lines=[_line(_MAT, "TN", "2.000")],
        )
        rows = svc.reconcile([reg], [guia])
        contrib = rows[0].guias[0]
        # No ceiling → reception_ceiling_applied must be False
        assert contrib.reception_ceiling_applied is False
        # fecha is unchanged from the guía (not clamped)
        assert contrib.fecha == _GUIA_DATE_LATER
