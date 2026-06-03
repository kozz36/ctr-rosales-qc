"""Regression: crossed-bounds protection survives the ReviewService re-reconcile.

JD round-2 confirmed bug: the reception-ceiling crossed-bounds guard
(``delivery_after_protocolo`` — do NOT clamp below the SUNAT delivery floor when
``fecha_entrega > Protocolo``) lives inside ``ReconciliationService.reconcile`` and
depends on the ``delivery_dates`` map.  ``ReviewService`` re-reconciles on every
edit/reassign but historically called ``reconcile(...)`` WITHOUT ``delivery_dates``
— so after any review edit (especially a REASSIGN, the primary R9 misfiled-guía
workflow) the guard was lost: the crossed-bounds guía got re-clamped BELOW the
delivery floor and ``delivery_after_protocolo`` flipped back to False.

The fix persists ``fecha_entrega`` ON the guía and rebuilds ``delivery_dates`` from
the guías inside ReviewService, so the bracket survives.

These tests build a crossed-bounds guía (``fecha_entrega > Protocolo``, read date >
Protocolo), perform a REASSIGN and a LINE EDIT through ReviewService, and assert
the crossed-bounds protection is STILL intact afterwards.  They FAIL against the
pre-fix branch HEAD (no delivery_dates on the review path → re-clamp).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from reconciliation.application.review_service import ReviewService
from reconciliation.application.run_context import RunContext
from reconciliation.domain.models import (
    GuiaDeRemision,
    MaterialLine,
    Registro,
)
from reconciliation.domain.reconciliation import ReconciliationService

_MAT = 'BARRA A615 G60 1/2" 9M'
_PROTOCOLO_DATE = date(2026, 5, 28)
# Guía read date already >= fecha_entrega, LATER than Protocolo.
_GUIA_READ = date(2026, 6, 10)
# SUNAT delivery date GREATER than Protocolo → crossed bounds (impossible).
_ENTREGA_AFTER = date(2026, 6, 5)


def _line(qty: str = "2.000") -> MaterialLine:
    return MaterialLine(
        description_raw=_MAT.upper(),
        description_canonical=_MAT,
        unidad="TN",  # type: ignore[arg-type]
        cantidad=Decimal(qty),
        confidence=0.95,
        source_page=5,
    )


def _crossed_guia(registro: str | None = "232") -> GuiaDeRemision:
    """A crossed-bounds guía: fecha_entrega > Protocolo, read date > Protocolo."""
    return GuiaDeRemision(
        guia_id="T009-0001",
        registro=registro,
        fecha=_GUIA_READ,
        fecha_confidence=0.95,
        lines=[_line()],
        source_pages=[5],
        fecha_entrega=_ENTREGA_AFTER,
    )


def _registro(numero: str = "232") -> Registro:
    return Registro(
        numero=numero,
        fecha_declarada=_PROTOCOLO_DATE,
        fecha_declarada_handwritten=_PROTOCOLO_DATE,
        declared_lines=[_line()],
    )


def _build_service(tmp_path: Path, guias: list[GuiaDeRemision], declared: list[Registro]):
    ctx = RunContext(
        pdf_path=tmp_path / "input.pdf",
        output_base=tmp_path / "runs",
        run_id="crossed-bounds-test",
    )
    ctx.write_review_sidecar({"edits": [], "audit_trail": []})
    reconciler = ReconciliationService()
    # Initial rows must already carry the crossed-bounds protection.
    delivery_dates = {g.guia_id: g.fecha_entrega for g in guias if g.fecha_entrega is not None}
    rows = reconciler.reconcile(declared, guias, delivery_dates=delivery_dates)
    service = ReviewService(declared=declared, guias=guias, rows=rows, ctx=ctx)
    return service


def _contrib(rows):
    for row in rows:
        if row.guias:
            return row.guias[0]
    raise AssertionError("no contributing guía found in rows")


class TestCrossedBoundsSurvivesReassign:
    def test_reassign_keeps_fecha_not_clamped_below_floor(self, tmp_path: Path) -> None:
        """After REASSIGN, the crossed-bounds fecha is NOT clamped below the floor."""
        service = _build_service(tmp_path, [_crossed_guia("231")], [_registro("232")])
        # Reassign the misfiled guía to the correct registro 232 (the R9 workflow).
        rows = service.apply_reassignment("T009-0001", "232", _GUIA_READ)
        contrib = _contrib(rows)
        assert contrib.fecha == _GUIA_READ, (
            "REASSIGN re-reconcile must NOT re-clamp below the SUNAT delivery floor"
        )

    def test_reassign_keeps_delivery_after_protocolo_true(self, tmp_path: Path) -> None:
        """After REASSIGN, the crossed-bounds anomaly flag stays True."""
        service = _build_service(tmp_path, [_crossed_guia("231")], [_registro("232")])
        rows = service.apply_reassignment("T009-0001", "232", _GUIA_READ)
        contrib = _contrib(rows)
        assert contrib.delivery_after_protocolo is True

    def test_reassign_keeps_reception_ceiling_not_applied(self, tmp_path: Path) -> None:
        """After REASSIGN, reception_ceiling_applied stays False (no clamp)."""
        service = _build_service(tmp_path, [_crossed_guia("231")], [_registro("232")])
        rows = service.apply_reassignment("T009-0001", "232", _GUIA_READ)
        contrib = _contrib(rows)
        assert contrib.reception_ceiling_applied is False


class TestCrossedBoundsSurvivesLineEdit:
    def test_line_edit_keeps_fecha_not_clamped_below_floor(self, tmp_path: Path) -> None:
        """After a LINE EDIT, the crossed-bounds fecha is NOT clamped below the floor."""
        service = _build_service(tmp_path, [_crossed_guia("232")], [_registro("232")])
        rows = service.apply_guia_line_edit("T009-0001", 0, None, Decimal("2.000"))
        contrib = _contrib(rows)
        assert contrib.fecha == _GUIA_READ
        assert contrib.delivery_after_protocolo is True
        assert contrib.reception_ceiling_applied is False

    def test_field_edit_keeps_crossed_bounds(self, tmp_path: Path) -> None:
        """After a field edit (fecha), the crossed-bounds protection survives."""
        service = _build_service(tmp_path, [_crossed_guia("232")], [_registro("232")])
        rows = service.apply_edit("T009-0001", "fecha", _GUIA_READ)
        contrib = _contrib(rows)
        assert contrib.fecha == _GUIA_READ
        assert contrib.delivery_after_protocolo is True


class TestNoSunatDeliveryDatesUnchanged:
    def test_no_fecha_entrega_review_path_unchanged(self, tmp_path: Path) -> None:
        """No fecha_entrega on guías → empty delivery_dates → existing ceiling behavior."""
        guia = GuiaDeRemision(
            guia_id="T009-0002",
            registro="232",
            fecha=_GUIA_READ,
            fecha_confidence=0.95,
            lines=[_line()],
            source_pages=[5],
        )  # fecha_entrega defaults None
        service = _build_service(tmp_path, [guia], [_registro("232")])
        rows = service.apply_guia_line_edit("T009-0002", 0, None, Decimal("2.000"))
        contrib = _contrib(rows)
        # No delivery floor known → ceiling clamps to the Protocolo date.
        assert contrib.fecha == _PROTOCOLO_DATE
        assert contrib.reception_ceiling_applied is True
        assert contrib.delivery_after_protocolo is False
