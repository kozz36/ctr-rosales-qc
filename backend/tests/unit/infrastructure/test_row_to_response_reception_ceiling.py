"""DTO round-trip test for the reception-ceiling side-channel.

Asserts ``_row_to_response`` emits BOTH:
  - ``reception_ceiling_applied`` per contributing guía (GuiaContributionResponse), and
  - ``has_reception_ceiling`` at the row level (ReconciliationRowResponse, group roll-up)

for a row whose contributing guía has ``reception_ceiling_applied=True``.

Mirrors test_row_to_response_delivery_floor.py.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from reconciliation.domain.models import GuiaContribution, ReconciliationRow
from reconciliation.infrastructure.api.routes import _row_to_response


def _clamped_row() -> ReconciliationRow:
    contrib = GuiaContribution(
        guia_id="T009-0001",
        source_pages=[5],
        cantidad=Decimal("4.124"),
        unidad="TN",
        confidence=0.9,
        identity_source="ocr_fallback",
        fecha=date(2026, 5, 28),  # already clamped to ceiling
        reception_ceiling_applied=True,
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


def test_row_to_response_emits_reception_ceiling_fields() -> None:
    row = _clamped_row()
    resp = _row_to_response(row)

    # Row-level roll-up.
    assert resp.has_reception_ceiling is True

    # Per-guía contribution side-channel.
    assert len(resp.guias) == 1
    assert resp.guias[0].reception_ceiling_applied is True


def test_row_to_response_no_ceiling_defaults_false() -> None:
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
    assert resp.has_reception_ceiling is False
    assert resp.guias[0].reception_ceiling_applied is False
