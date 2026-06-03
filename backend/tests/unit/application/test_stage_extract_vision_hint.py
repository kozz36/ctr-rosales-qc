"""Tests for SUNAT delivery-date hint wiring in ``_stage_extract_vision`` (Part 2).

Verifies that on the non-batch (sequential) path:
  - When a ``sunat_fetch_map`` carries an OfficialGre with a ``fecha_entrega``
    for the guía, ``read_handwritten_date`` is called with a non-None hint that
    contains the formatted delivery date.
  - When SUNAT is not available (empty map / None), ``read_handwritten_date`` is
    called with ``hint=None`` (current behaviour — graceful degrade).

The batch path is NOT tested here because the batch variant
``read_handwritten_date_batch`` has no per-image hint parameter.
This is documented as a follow-up: see the inline comment in pipeline.py.

Uses the same fake infrastructure as test_stage_delivery_floor.py.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from reconciliation.application.config import AppConfig
from reconciliation.application.pipeline import ReconciliationPipeline
from reconciliation.domain.models import MaterialLine, OfficialGre, VisionResult


# ---------------------------------------------------------------------------
# Fake infrastructure — lightweight, no external SDK
# ---------------------------------------------------------------------------


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


class _HintCapturingVision:
    """Non-batch fake VisionLLMPort that records the hint passed to each call."""

    supports_batch: bool = False

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def read_handwritten_date(self, image: bytes, hint: str | None = None) -> VisionResult:
        self.calls.append({"image": image, "hint": hint})
        return VisionResult(date=date(2026, 5, 28), confidence=0.9, raw="28/05/2026")

    def read_handwritten_date_batch(self, images: list[bytes]) -> list[VisionResult]:
        raise NotImplementedError("batch path — not under test here")


def _pipeline(vision: _HintCapturingVision) -> ReconciliationPipeline:
    return ReconciliationPipeline(
        doc_source=_FakeDoc(),
        extractor=_FakeExtractor(),
        vision=vision,
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


def _make_block(guia_id: str) -> Any:
    """Build a minimal _GuiaBlock for testing _stage_extract_vision directly.

    Imports _GuiaBlock from pipeline so the test is coupled to the private
    dataclass — acceptable for a unit test of a private stage method.
    """
    from reconciliation.application.pipeline import _GuiaBlock  # noqa: PLC0415

    return _GuiaBlock(
        guia_id=guia_id,
        first_page=0,
        source_pages=[0],
        first_page_image=b"\x89PNG\r\n",
        lines=[_line()],
        registro="232",
        identity_source="qr",
        identity_confidence=1.0,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestVisionHintWithSunatMap:
    def test_hint_contains_formatted_date_when_sunat_available(self) -> None:
        """Non-batch path + sunat_fetch_map with fecha_entrega → hint is non-None.

        The hint must contain the delivery date formatted as DD/MM/YYYY so the
        vision model can use it as a lower-bound reference when reading the stamp.
        """
        guia_id = "T009-0001"
        entrega = date(2026, 5, 20)
        sunat_map = {guia_id: _official_gre(guia_id, entrega)}

        vision = _HintCapturingVision()
        pipeline = _pipeline(vision)
        block = _make_block(guia_id)

        pipeline._stage_extract_vision([block], sunat_fetch_map=sunat_map)

        assert len(vision.calls) == 1
        hint = vision.calls[0]["hint"]
        assert hint is not None, "hint must be non-None when fecha_entrega is available"
        # Hint must contain the delivery date in DD/MM/YYYY format
        assert "20/05/2026" in hint, (
            f"hint must contain '20/05/2026' (the formatted fecha_entrega); got: {hint!r}"
        )

    def test_hint_is_none_without_sunat_map(self) -> None:
        """Non-batch path + no sunat_fetch_map → hint is None (graceful degrade)."""
        guia_id = "T009-0002"

        vision = _HintCapturingVision()
        pipeline = _pipeline(vision)
        block = _make_block(guia_id)

        pipeline._stage_extract_vision([block], sunat_fetch_map=None)

        assert len(vision.calls) == 1
        assert vision.calls[0]["hint"] is None, (
            "hint must be None when no SUNAT data is available"
        )

    def test_hint_is_none_with_empty_sunat_map(self) -> None:
        """Non-batch path + empty sunat_fetch_map (guia_id not present) → hint is None."""
        guia_id = "T009-0003"

        vision = _HintCapturingVision()
        pipeline = _pipeline(vision)
        block = _make_block(guia_id)

        # Map exists but doesn't contain this guia_id
        pipeline._stage_extract_vision([block], sunat_fetch_map={})

        assert len(vision.calls) == 1
        assert vision.calls[0]["hint"] is None, (
            "hint must be None when guia_id is absent from sunat_fetch_map"
        )

    def test_hint_is_none_when_no_fecha_entrega(self) -> None:
        """Non-batch path + OfficialGre with fecha_entrega=None → hint is None."""
        guia_id = "T009-0004"
        # OfficialGre present but no fecha_entrega
        parts = guia_id.split("-", 1)
        gre_no_entrega = OfficialGre(
            guia_id=guia_id,
            serie=parts[0],
            numero=parts[1],
            ruc_emisor="12345678901",
            ruc_receptor="98765432100",
            fecha_entrega=None,
        )
        sunat_map = {guia_id: gre_no_entrega}

        vision = _HintCapturingVision()
        pipeline = _pipeline(vision)
        block = _make_block(guia_id)

        pipeline._stage_extract_vision([block], sunat_fetch_map=sunat_map)

        assert len(vision.calls) == 1
        assert vision.calls[0]["hint"] is None, (
            "hint must be None when OfficialGre.fecha_entrega is None"
        )
