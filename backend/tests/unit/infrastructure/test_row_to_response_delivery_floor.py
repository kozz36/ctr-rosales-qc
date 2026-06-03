"""DTO round-trip test for the R9b delivery-floor side-channel (FIX S1).

Asserts ``_row_to_response`` emits BOTH:
  - ``delivery_floor_applied`` per contributing guía (GuiaContributionResponse), and
  - ``has_delivery_floor`` at the row level (ReconciliationRowResponse, group roll-up)

for a row whose contributing guía has ``delivery_floor_applied=True``.

Project history justifies this cheap guard: JD previously found a totally-dead
feature hiding behind a green suite (guía line-edit always HTTP 422), so the
DTO emission of new side-channels is verified explicitly.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from reconciliation.domain.models import GuiaContribution, ReconciliationRow
from reconciliation.infrastructure.api.routes import _row_to_response


def _floored_row() -> ReconciliationRow:
    contrib = GuiaContribution(
        guia_id="T009-0001",
        source_pages=[5],
        cantidad=Decimal("4.124"),
        unidad="TN",
        confidence=0.9,
        identity_source="ocr_fallback",
        delivery_floor_applied=True,  # the floored guía
    )
    return ReconciliationRow(
        registro="232",
        fecha=date(2026, 5, 20),
        material_canonical="BARRA A615 G60 1/2\" 9M",
        unidad="TN",
        declared_qty=Decimal("4.124"),
        delta=Decimal("0"),
        status="MATCH",
        source_pages=[5],
        min_confidence=0.9,
        guias=[contrib],
    )


def test_row_to_response_emits_delivery_floor_fields() -> None:
    row = _floored_row()
    resp = _row_to_response(row)

    # Row-level roll-up.
    assert resp.has_delivery_floor is True

    # Per-guía contribution side-channel.
    assert len(resp.guias) == 1
    assert resp.guias[0].delivery_floor_applied is True


def test_row_to_response_no_floor_defaults_false() -> None:
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
        material_canonical="BARRA A615 G60 1/2\" 9M",
        unidad="TN",
        declared_qty=Decimal("1"),
        delta=Decimal("0"),
        status="MATCH",
        source_pages=[6],
        min_confidence=0.9,
        guias=[contrib],
    )
    resp = _row_to_response(row)
    assert resp.has_delivery_floor is False
    assert resp.guias[0].delivery_floor_applied is False
