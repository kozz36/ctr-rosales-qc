"""DTO round-trip test for the delivery-after-protocolo anomaly side-channel.

Asserts ``_row_to_response`` emits BOTH:
  - ``delivery_after_protocolo`` per contributing guía (GuiaContributionResponse), and
  - ``has_delivery_after_protocolo`` at the row level (group roll-up).

Mirrors test_row_to_response_reception_ceiling.py.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from reconciliation.domain.models import GuiaContribution, ReconciliationRow
from reconciliation.infrastructure.api.routes import _row_to_response


def _anomaly_row() -> ReconciliationRow:
    contrib = GuiaContribution(
        guia_id="T009-0001",
        source_pages=[5],
        cantidad=Decimal("4.124"),
        unidad="TN",
        confidence=0.9,
        identity_source="ocr_fallback",
        fecha=date(2026, 6, 10),  # NOT clamped — kept above the delivery floor
        delivery_after_protocolo=True,
    )
    return ReconciliationRow(
        registro="232",
        fecha=date(2026, 5, 28),
        material_canonical='BARRA A615 G60 1/2" 9M',
        unidad="TN",
        declared_qty=Decimal("4.124"),
        delta=Decimal("0"),
        status="MATCH",
        source_pages=[5],
        min_confidence=0.9,
        guias=[contrib],
    )


def test_row_to_response_emits_delivery_after_protocolo_fields() -> None:
    resp = _row_to_response(_anomaly_row())
    assert resp.has_delivery_after_protocolo is True
    assert len(resp.guias) == 1
    assert resp.guias[0].delivery_after_protocolo is True


def test_row_to_response_no_anomaly_defaults_false() -> None:
    contrib = GuiaContribution(
        guia_id="T009-0002",
        source_pages=[6],
        cantidad=Decimal("1"),
        unidad="TN",
        confidence=0.9,
        identity_source="ocr_fallback",
    )
    row = ReconciliationRow(
        registro="232",
        fecha=date(2026, 5, 20),
        material_canonical='BARRA A615 G60 1/2" 9M',
        unidad="TN",
        declared_qty=Decimal("1"),
        delta=Decimal("0"),
        status="MATCH",
        source_pages=[6],
        min_confidence=0.9,
        guias=[contrib],
    )
    resp = _row_to_response(row)
    assert resp.has_delivery_after_protocolo is False
    assert resp.guias[0].delivery_after_protocolo is False
