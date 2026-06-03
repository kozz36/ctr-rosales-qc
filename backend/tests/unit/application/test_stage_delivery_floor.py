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
    def test_floor_applied_via_rule2_inference_returns_none(self) -> None:
        """R9b: floor activates through the Rule-2 path (inference→None → floor).

        FIX F1 (honest test): this exercises Rule 2, NOT Rule 3. ``infer_reception_year``
        is called with the SAME ``lower=fecha_entrega`` and pre-filters candidates to
        ``>= lower``. So the inferred date is NEVER ``< fecha_entrega`` — Rule 3 of
        ``apply_delivery_floor`` is UNREACHABLE through ``_stage_normalize_dates``.

        Here the vision day-month (05-10) cannot be placed at/after the 2026-05-20
        floor within the inference window, so ``infer_reception_year`` returns None,
        and Rule 2 floors the resolved date to ``fecha_entrega`` and flags it.
        """
        guia = GuiaDeRemision(
            guia_id="T009-0001",
            registro="232",
            fecha=date(2026, 5, 10),  # day-month cannot land >= entrega in window
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


class TestDeliveryFloorNoVisionDate:
    """FIX F2: vision read NO date (day/month None) but fecha_entrega exists.

    Rule 2 of apply_delivery_floor MUST still fire from the day/month-None guard
    in ``_stage_normalize_dates``: floor to fecha_entrega and flag. This is the
    most common None-reception case and was previously left unfloored because the
    guard returned BEFORE apply_delivery_floor.
    """

    def test_no_vision_date_with_sunat_floors_to_entrega(self) -> None:
        """day/month None (fecha_raw="") + fecha_entrega present → floored + flagged."""
        guia = GuiaDeRemision(
            guia_id="T009-0005",
            registro="232",
            fecha=None,  # vision produced no date
            fecha_raw="",  # no raw string to parse day/month from
            fecha_confidence=0.0,
            lines=[_line()],
            source_pages=[0],
        )
        entrega = date(2026, 5, 20)
        sunat_map = {"T009-0005": _official_gre("T009-0005", entrega)}

        out = _pipeline()._stage_normalize_dates([guia], sunat_fetch_map=sunat_map)
        assert len(out) == 1
        result = out[0]
        assert result.fecha == entrega, "fecha must be floored to fecha_entrega"
        assert result.delivery_floor_applied is True

    def test_no_vision_date_no_sunat_graceful_degrade(self) -> None:
        """day/month None + NO sunat (lower None) → unchanged, no floor."""
        guia = GuiaDeRemision(
            guia_id="T009-0006",
            registro="232",
            fecha=None,
            fecha_raw="",
            fecha_confidence=0.0,
            lines=[_line()],
            source_pages=[0],
        )
        out = _pipeline()._stage_normalize_dates([guia], sunat_fetch_map=None)
        assert len(out) == 1
        result = out[0]
        assert result.fecha is None
        assert result.delivery_floor_applied is False
