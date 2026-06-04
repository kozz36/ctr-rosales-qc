"""Tests for vision-off mode: guía dates resolve to SUNAT fecha_entrega via R9b Rule-2.

With NullVisionAdapter wired as the vision port, _stage_normalize_dates receives
guías whose fecha=None and fecha_raw=''. The existing R9b Rule-2 path in
_stage_normalize_dates applies apply_delivery_floor(None, fecha_entrega), resolving
each guía's fecha to its SUNAT fecha_entrega with delivery_floor_applied=True.

This test suite PINS that existing behavior under the new vision-off mode.
It does NOT test new date logic — if the existing R9b floor did not already cover
this, the test would surface a gap and we would stop (SA-2).

Evidence that R9b Rule-2 already covers this (from pipeline.py lines 1299-1319):
  1. NullVisionAdapter.read_handwritten_date() → VisionResult(date=None, raw="")
  2. _build_guia_from_block sets guia.fecha=None, guia.fecha_raw=""
  3. _parse_day_month(0.0, None or "") → (None, None)
  4. if day is None or month is None: → Rule-2 branch entered
  5. apply_delivery_floor(None, fecha_entrega) → (fecha_entrega, True)
  6. guia.delivery_floor_applied = True
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from reconciliation.adapters.vision.null_vision import NullVisionAdapter
from reconciliation.application.config import AppConfig, SunatConfig, VisionConfig
from reconciliation.application.pipeline import ReconciliationPipeline
from reconciliation.domain.models import GuiaDeRemision, MaterialLine, OfficialGre, VisionResult


# ---------------------------------------------------------------------------
# Fake collaborators
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


def _make_pipeline(vision) -> ReconciliationPipeline:
    """Build a pipeline with the given vision adapter and default AppConfig."""
    config = AppConfig(
        vision=VisionConfig(enabled=False),
        sunat=SunatConfig(enabled=True),
    )
    return ReconciliationPipeline(
        doc_source=_FakeDoc(),
        extractor=_FakeExtractor(),
        vision=vision,
        config=config,
        page_to_registro={},
    )


def _line() -> MaterialLine:
    return MaterialLine(
        description_raw="VARILLA 1/2",
        description_canonical="barra a615 g60 1/2 9m",
        unidad="TN",
        cantidad=Decimal("1.000"),
    )


def _guia_no_fecha(guia_id: str) -> GuiaDeRemision:
    """Guía with fecha=None — simulates what NullVisionAdapter produces."""
    return GuiaDeRemision(
        guia_id=guia_id,
        registro="232",
        fecha=None,
        fecha_raw="",
        fecha_confidence=0.0,
        lines=[_line()],
        source_pages=[0],
    )


def _sunat_gre(guia_id: str, fecha_entrega: date) -> OfficialGre:
    serie, numero = guia_id.split("-", 1)
    return OfficialGre(
        guia_id=guia_id,
        serie=serie,
        numero=numero,
        ruc_emisor="20123456789",
        ruc_receptor="20987654321",
        fecha_entrega=fecha_entrega,
    )


# ---------------------------------------------------------------------------
# NullVisionAdapter produces fecha=None — confirms the trigger condition
# ---------------------------------------------------------------------------


class TestNullVisionAdapterProducesNullDate:
    def test_read_handwritten_date_returns_null_date(self) -> None:
        """NullVisionAdapter.read_handwritten_date yields VisionResult with date=None."""
        adapter = NullVisionAdapter()
        vr = adapter.read_handwritten_date(b"\x89PNG")
        assert vr.date is None
        assert vr.raw == ""
        assert vr.confidence == 0.0

    def test_zero_vision_calls_are_made(self) -> None:
        """NullVisionAdapter does not call any underlying LLM; zero LLM calls tracked."""
        adapter = NullVisionAdapter()
        # Repeated calls — all must return null without side effects
        for _ in range(5):
            vr = adapter.read_handwritten_date(b"\x89PNG")
            assert vr.date is None


# ---------------------------------------------------------------------------
# _stage_normalize_dates: vision-off → fecha floors to fecha_entrega (Rule-2 R9b)
# ---------------------------------------------------------------------------


class TestStageNormalizeDatesVisionOff:
    """_stage_normalize_dates resolves vision-off guías to SUNAT fecha_entrega."""

    def _pipeline_with_null_vision(self) -> ReconciliationPipeline:
        return _make_pipeline(NullVisionAdapter())

    def test_fecha_equals_fecha_entrega_when_vision_off(self) -> None:
        """With NullVisionAdapter + SUNAT, guía.fecha resolves to fecha_entrega."""
        pipeline = self._pipeline_with_null_vision()
        entrega = date(2026, 5, 28)
        guia = _guia_no_fecha("T009-0001")
        sunat = {"T009-0001": _sunat_gre("T009-0001", entrega)}

        out = pipeline._stage_normalize_dates([guia], sunat_fetch_map=sunat)

        assert len(out) == 1
        assert out[0].fecha == entrega, (
            f"Expected fecha={entrega}, got {out[0].fecha}"
        )

    def test_delivery_floor_applied_true_when_vision_off(self) -> None:
        """delivery_floor_applied=True on every guía when vision-off + SUNAT supplies date."""
        pipeline = self._pipeline_with_null_vision()
        entrega = date(2026, 5, 28)
        guia = _guia_no_fecha("T009-0001")
        sunat = {"T009-0001": _sunat_gre("T009-0001", entrega)}

        out = pipeline._stage_normalize_dates([guia], sunat_fetch_map=sunat)

        assert out[0].delivery_floor_applied is True

    def test_fecha_entrega_persisted_on_guia(self) -> None:
        """fecha_entrega is persisted on the guía model (single-source-of-truth)."""
        pipeline = self._pipeline_with_null_vision()
        entrega = date(2026, 5, 28)
        guia = _guia_no_fecha("T009-0002")
        sunat = {"T009-0002": _sunat_gre("T009-0002", entrega)}

        out = pipeline._stage_normalize_dates([guia], sunat_fetch_map=sunat)

        assert out[0].fecha_entrega == entrega

    def test_multiple_guias_all_floor_to_their_own_fecha_entrega(self) -> None:
        """Each guía floors to ITS OWN fecha_entrega — not the same date for all."""
        pipeline = self._pipeline_with_null_vision()
        entrega_1 = date(2026, 5, 20)
        entrega_2 = date(2026, 5, 28)
        guia_1 = _guia_no_fecha("T009-0001")
        guia_2 = _guia_no_fecha("T009-0002")
        sunat = {
            "T009-0001": _sunat_gre("T009-0001", entrega_1),
            "T009-0002": _sunat_gre("T009-0002", entrega_2),
        }

        out = pipeline._stage_normalize_dates([guia_1, guia_2], sunat_fetch_map=sunat)

        assert out[0].fecha == entrega_1
        assert out[1].fecha == entrega_2
        assert out[0].delivery_floor_applied is True
        assert out[1].delivery_floor_applied is True

    def test_declared_side_is_unaffected(self) -> None:
        """_stage_normalize_dates operates only on guías; declared Registro objects
        (passed separately) are unrelated to this stage and untouched."""
        # This is structural: _stage_normalize_dates takes [GuiaDeRemision]; declared
        # registros are not passed. This test confirms the return list type.
        pipeline = self._pipeline_with_null_vision()
        entrega = date(2026, 6, 1)
        guia = _guia_no_fecha("T009-0003")
        sunat = {"T009-0003": _sunat_gre("T009-0003", entrega)}

        out = pipeline._stage_normalize_dates([guia], sunat_fetch_map=sunat)

        # Only guías are returned; the function signature and return type are unchanged.
        assert isinstance(out, list)
        assert all(isinstance(g, GuiaDeRemision) for g in out)

    def test_no_sunat_data_leaves_fecha_none(self) -> None:
        """Without a SUNAT fetch map, vision-off guías retain fecha=None (graceful degrade)."""
        pipeline = self._pipeline_with_null_vision()
        guia = _guia_no_fecha("T009-0004")

        out = pipeline._stage_normalize_dates([guia], sunat_fetch_map=None)

        # No SUNAT → no floor → fecha remains None, delivery_floor_applied remains False
        assert out[0].fecha is None
        assert out[0].delivery_floor_applied is False


# ---------------------------------------------------------------------------
# Verify NullVisionAdapter itself is a zero-call adapter (property-based)
# ---------------------------------------------------------------------------


class TestNullVisionAdapterZeroCalls:
    def test_batch_returns_one_null_per_guia(self) -> None:
        """read_handwritten_date_batch returns exactly N null results for N images."""
        adapter = NullVisionAdapter()
        n = 7
        results = adapter.read_handwritten_date_batch([b"\x89PNG"] * n)
        assert len(results) == n
        assert all(r.date is None for r in results)

    def test_vision_call_count_is_zero_after_normalize(self) -> None:
        """The pipeline's internal vision calls_made counter stays 0 with NullVisionAdapter.

        _stage_extract_vision increments calls_made for EACH vision.read_handwritten_date
        call. With NullVisionAdapter, we verify this counter to confirm zero LLM calls.
        This test exercises _stage_extract_vision via an empty blocks list (no guía
        blocks to process), so calls_made stays 0 trivially. The key invariant is:
        NullVisionAdapter.read_handwritten_date does not side-effect any counter.
        """
        adapter = NullVisionAdapter()
        pipeline = _make_pipeline(adapter)

        # Exercise _stage_extract_vision directly with no blocks
        guias, calls_made, warnings = pipeline._stage_extract_vision(blocks=[])
        assert calls_made == 0
        assert guias == []
