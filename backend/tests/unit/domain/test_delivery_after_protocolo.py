"""Tests for the crossed-bounds anomaly: SUNAT fecha_entrega > Protocolo ceiling.

Physical impossibility — goods cannot be DELIVERED (fecha_entrega) AFTER they are
declared RECEIVED (Protocolo authoritative date). This is almost always a HUMAN
ERROR building the Protocolo.

Policy (USER decision):
  - Do NOT apply the ceiling clamp (NEVER push the date below the SUNAT delivery floor).
  - Keep the guía's resolved (floored) read date unchanged (it is >= fecha_entrega).
  - Raise a distinct anomaly signal ``delivery_after_protocolo=True`` + requires_review.
  - The R9 fecha_divergence (computed on the ORIGINAL date) MUST remain set —
    do NOT mask it.

Mirrors test_reception_ceiling_reconciliation.py style.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from reconciliation.domain.models import GuiaDeRemision, MaterialLine, Registro
from reconciliation.domain.reconciliation import ReconciliationService


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


_MAT = 'BARRA A615 G60 1/2" 9M'
_PROTOCOLO_DATE = date(2026, 5, 28)
# Guía read date (already floored, >= fecha_entrega), LATER than Protocolo.
_GUIA_READ = date(2026, 6, 10)
# SUNAT delivery date GREATER than Protocolo → crossed bounds (impossible).
_ENTREGA_AFTER = date(2026, 6, 5)
# SUNAT delivery date BEFORE Protocolo → normal ceiling case.
_ENTREGA_BEFORE = date(2026, 5, 20)


class TestDeliveryAfterProtocoloCrossedBounds:
    def test_fecha_unchanged_not_clamped_below_floor(
        self, svc: ReconciliationService
    ) -> None:
        """Crossed bounds → fecha UNCHANGED (NOT clamped below the delivery floor)."""
        guia = _guia("T009-0001", "232", _GUIA_READ, [_line(_MAT, "TN", "2.000")])
        reg = _registro(
            "232",
            fecha_declarada=_PROTOCOLO_DATE,
            lines=[_line(_MAT, "TN", "2.000")],
        )
        rows = svc.reconcile(
            [reg], [guia], delivery_dates={"T009-0001": _ENTREGA_AFTER}
        )
        contrib = rows[0].guias[0]
        assert contrib.fecha == _GUIA_READ, (
            "Crossed bounds must NOT clamp the fecha below the SUNAT delivery floor"
        )

    def test_reception_ceiling_not_applied(self, svc: ReconciliationService) -> None:
        """Crossed bounds → reception_ceiling_applied is False (we did NOT clamp)."""
        guia = _guia("T009-0001", "232", _GUIA_READ, [_line(_MAT, "TN", "2.000")])
        reg = _registro(
            "232",
            fecha_declarada=_PROTOCOLO_DATE,
            lines=[_line(_MAT, "TN", "2.000")],
        )
        rows = svc.reconcile(
            [reg], [guia], delivery_dates={"T009-0001": _ENTREGA_AFTER}
        )
        assert rows[0].guias[0].reception_ceiling_applied is False

    def test_delivery_after_protocolo_flag_set(
        self, svc: ReconciliationService
    ) -> None:
        """Crossed bounds → delivery_after_protocolo anomaly flag is True."""
        guia = _guia("T009-0001", "232", _GUIA_READ, [_line(_MAT, "TN", "2.000")])
        reg = _registro(
            "232",
            fecha_declarada=_PROTOCOLO_DATE,
            lines=[_line(_MAT, "TN", "2.000")],
        )
        rows = svc.reconcile(
            [reg], [guia], delivery_dates={"T009-0001": _ENTREGA_AFTER}
        )
        assert rows[0].guias[0].delivery_after_protocolo is True

    def test_fecha_divergence_still_set_r9_not_masked(
        self, svc: ReconciliationService
    ) -> None:
        """Crossed bounds → R9 fecha_divergence (on ORIGINAL date) NOT masked."""
        guia = _guia("T009-0001", "232", _GUIA_READ, [_line(_MAT, "TN", "2.000")])
        reg = _registro(
            "232",
            fecha_declarada=_PROTOCOLO_DATE,
            lines=[_line(_MAT, "TN", "2.000")],
        )
        rows = svc.reconcile(
            [reg], [guia], delivery_dates={"T009-0001": _ENTREGA_AFTER}
        )
        assert rows[0].guias[0].fecha_divergence is True

    def test_requires_review_set(self, svc: ReconciliationService) -> None:
        """Crossed bounds → row.requires_review True (anomaly OR-sets)."""
        guia = _guia("T009-0001", "232", _GUIA_READ, [_line(_MAT, "TN", "2.000")])
        reg = _registro(
            "232",
            fecha_declarada=_PROTOCOLO_DATE,
            lines=[_line(_MAT, "TN", "2.000")],
        )
        rows = svc.reconcile(
            [reg], [guia], delivery_dates={"T009-0001": _ENTREGA_AFTER}
        )
        assert rows[0].requires_review is True

    def test_row_has_delivery_after_protocolo(
        self, svc: ReconciliationService
    ) -> None:
        """Crossed bounds → row.has_delivery_after_protocolo True (roll-up)."""
        guia = _guia("T009-0001", "232", _GUIA_READ, [_line(_MAT, "TN", "2.000")])
        reg = _registro(
            "232",
            fecha_declarada=_PROTOCOLO_DATE,
            lines=[_line(_MAT, "TN", "2.000")],
        )
        rows = svc.reconcile(
            [reg], [guia], delivery_dates={"T009-0001": _ENTREGA_AFTER}
        )
        assert rows[0].has_delivery_after_protocolo is True

    def test_status_delta_summed_qty_unchanged(
        self, svc: ReconciliationService
    ) -> None:
        """Crossed bounds is additive — status/delta/summed_qty unchanged vs baseline."""
        guia = _guia("T009-0001", "232", _GUIA_READ, [_line(_MAT, "TN", "2.000")])
        reg = _registro(
            "232",
            fecha_declarada=_PROTOCOLO_DATE,
            lines=[_line(_MAT, "TN", "2.000")],
        )
        # Baseline: no delivery_dates passed.
        baseline = svc.reconcile([reg], [guia])[0]
        rows = svc.reconcile(
            [reg], [guia], delivery_dates={"T009-0001": _ENTREGA_AFTER}
        )
        row = rows[0]
        assert row.status == baseline.status == "MATCH"
        assert row.delta == baseline.delta == Decimal("0")
        assert row.summed_qty == baseline.summed_qty == Decimal("2.000")


class TestNormalCeilingWithDeliveryDates:
    def test_normal_ceiling_clamps_when_entrega_before_protocolo(
        self, svc: ReconciliationService
    ) -> None:
        """Delivery before Protocolo (no crossing) → ceiling clamps as before."""
        guia = _guia("T009-0001", "232", _GUIA_READ, [_line(_MAT, "TN", "2.000")])
        reg = _registro(
            "232",
            fecha_declarada=_PROTOCOLO_DATE,
            lines=[_line(_MAT, "TN", "2.000")],
        )
        rows = svc.reconcile(
            [reg], [guia], delivery_dates={"T009-0001": _ENTREGA_BEFORE}
        )
        contrib = rows[0].guias[0]
        assert contrib.fecha == _PROTOCOLO_DATE
        assert contrib.reception_ceiling_applied is True
        assert contrib.delivery_after_protocolo is False


class TestDeliveryDatesNoneBackwardCompat:
    def test_delivery_dates_none_behaves_like_current_branch(
        self, svc: ReconciliationService
    ) -> None:
        """delivery_dates=None → existing ceiling behavior unchanged (clamps)."""
        guia = _guia("T009-0001", "232", _GUIA_READ, [_line(_MAT, "TN", "2.000")])
        reg = _registro(
            "232",
            fecha_declarada=_PROTOCOLO_DATE,
            lines=[_line(_MAT, "TN", "2.000")],
        )
        rows = svc.reconcile([reg], [guia], delivery_dates=None)
        contrib = rows[0].guias[0]
        # No delivery floor known → ceiling clamps as in the current branch.
        assert contrib.fecha == _PROTOCOLO_DATE
        assert contrib.reception_ceiling_applied is True
        assert contrib.delivery_after_protocolo is False
