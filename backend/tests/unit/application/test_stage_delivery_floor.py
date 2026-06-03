"""Tests for the delivery-floor wiring in ``_stage_normalize_dates`` (R9b).

Two behaviours under test:
  1. When SUNAT fetch map carries an OfficialGre whose ``fecha_entrega`` is AFTER
     the vision-read day-month, the guía's ``fecha`` is floored to ``fecha_entrega``
     and ``delivery_floor_applied`` is set to ``True``.
  2. When the SUNAT fetch map is absent (None/empty), no floor is applied and
     ``delivery_floor_applied`` remains ``False`` (backward-compatible).

Uses the same fake infrastructure as test_stage_normalize_dates_guia.py.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from reconciliation.application.config import AppConfig
from reconciliation.application.pipeline import ReconciliationPipeline
from reconciliation.domain.models import GuiaDeRemision, MaterialLine, OfficialGre, VisionResult


class _FakeDoc:
    def page_count(self) -> int:
        return 1

    def render_page(self, idx: int, dpi: int = 200) -> bytes:
        return b"\x89PNG\r\n"

    def page_text(self, idx: int) -> str | None:
        return None


class _FakeExtractor:
    def extract_declared(self, text: str) -> list:
        return []

    def extract_printed_table(self, image: bytes) -> list:
        return []


class _FakeVision:
    supports_batch: bool = False

    def read_handwritten_date(self, image: bytes, hint: str | None = None) -> VisionResult:
        return VisionResult(date=None, confidence=0.0, raw="")

    def read_handwritten_date_batch(self, images: list[bytes]) -> list[VisionResult]:
        raise NotImplementedError


def _pipeline() -> ReconciliationPipeline:
    return ReconciliationPipeline(
        doc_source=_FakeDoc(),
        extractor=_FakeExtractor(),
        vision=_FakeVision(),
        config=AppConfig(),
        page_to_registro={},
    )


def _line() -> MaterialLine:
    return MaterialLine(
        description_raw="x",
        description_canonical="x",
        unidad="TN",
        cantidad=Decimal("1"),
    )


def _official_gre(guia_id: str, fecha_entrega: date) -> OfficialGre:
    """Build a minimal OfficialGre with a given delivery date."""
    parts = guia_id.split("-", 1)
    return OfficialGre(
        guia_id=guia_id,
        serie=parts[0],
        numero=parts[1] if len(parts) > 1 else "",
        ruc_emisor="12345678901",
        ruc_receptor="98765432100",
        fecha_entrega=fecha_entrega,
    )


class TestDeliveryFloorApplied:
    def test_floor_applied_when_reception_before_entrega(self) -> None:
        """R9b: vision date before fecha_entrega → floored to fecha_entrega + flag.

        Scenario:
          - guía vision read: 2026-05-10 (wrong year corrected → 2026-05-10)
          - SUNAT fecha_entrega: 2026-05-20
          - 2026-05-10 < 2026-05-20 → floor to 2026-05-20, delivery_floor_applied=True
        """
        guia = GuiaDeRemision(
            guia_id="T009-0001",
            registro="232",
            fecha=date(2026, 5, 10),  # reception before delivery
            fecha_confidence=1.0,
            lines=[_line()],
            source_pages=[0],
        )
        entrega = date(2026, 5, 20)
        sunat_map = {"T009-0001": _official_gre("T009-0001", entrega)}

        out = _pipeline()._stage_normalize_dates([guia], sunat_fetch_map=sunat_map)
        assert len(out) == 1
        result = out[0]
        assert result.fecha == entrega, "fecha must be floored to fecha_entrega"
        assert result.delivery_floor_applied is True

    def test_no_floor_when_reception_after_entrega(self) -> None:
        """R9b valid case: reception after delivery → unchanged, no flag."""
        guia = GuiaDeRemision(
            guia_id="T009-0002",
            registro="232",
            fecha=date(2026, 5, 28),  # reception after delivery
            fecha_confidence=1.0,
            lines=[_line()],
            source_pages=[0],
        )
        entrega = date(2026, 5, 20)
        sunat_map = {"T009-0002": _official_gre("T009-0002", entrega)}

        out = _pipeline()._stage_normalize_dates([guia], sunat_fetch_map=sunat_map)
        assert len(out) == 1
        result = out[0]
        assert result.fecha == date(2026, 5, 28)
        assert result.delivery_floor_applied is False

    def test_no_sunat_no_floor(self) -> None:
        """R9b graceful degrade: no SUNAT fetch map → delivery_floor_applied stays False."""
        guia = GuiaDeRemision(
            guia_id="T009-0003",
            registro="232",
            fecha=date(2016, 5, 10),  # wrong year but no SUNAT to floor
            fecha_confidence=1.0,
            lines=[_line()],
            source_pages=[0],
        )
        out = _pipeline()._stage_normalize_dates([guia], sunat_fetch_map=None)
        assert len(out) == 1
        result = out[0]
        # Year is inferred but no delivery floor
        assert result.delivery_floor_applied is False

    def test_empty_sunat_map_no_floor(self) -> None:
        """R9b graceful degrade: empty sunat_fetch_map → no floor."""
        guia = GuiaDeRemision(
            guia_id="T009-0004",
            registro="232",
            fecha=date(2016, 5, 10),
            fecha_confidence=1.0,
            lines=[_line()],
            source_pages=[0],
        )
        out = _pipeline()._stage_normalize_dates([guia], sunat_fetch_map={})
        assert len(out) == 1
        assert out[0].delivery_floor_applied is False
