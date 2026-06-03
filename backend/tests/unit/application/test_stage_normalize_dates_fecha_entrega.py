"""Tests for fecha_entrega propagation in ``_stage_normalize_dates`` (R9b).

The pipeline already reads ``sunat_fetch_map[guia_id].fecha_entrega`` to compute
the delivery-floor lower bound.  This stage must ALSO persist that delivery date
ON the guía (``GuiaDeRemision.fecha_entrega``) so the crossed-bounds bracket
survives the extraction-cache round-trip and the ReviewService re-reconcile.

Without SUNAT data (air-gap default) the guía carries ``fecha_entrega is None``.
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


def _guia(guia_id: str, fecha: date | None) -> GuiaDeRemision:
    return GuiaDeRemision(
        guia_id=guia_id,
        registro="232",
        fecha=fecha,
        fecha_confidence=1.0,
        lines=[_line()],
        source_pages=[0],
    )


class TestFechaEntregaPropagation:
    def test_fecha_entrega_carried_when_sunat_supplies_it(self) -> None:
        """SUNAT fecha_entrega → output guía carries it on the model."""
        pipeline = _pipeline()
        guia = _guia("T009-0001", date(2026, 6, 10))
        sunat = {
            "T009-0001": OfficialGre(
                guia_id="T009-0001",
                serie="T009",
                numero="0001",
                ruc_emisor="20",
                ruc_receptor="20",
                fecha_entrega=date(2026, 6, 5),
            )
        }
        out = pipeline._stage_normalize_dates([guia], sunat_fetch_map=sunat)
        assert out[0].fecha_entrega == date(2026, 6, 5)

    def test_fecha_entrega_carried_on_floored_path(self) -> None:
        """Even when the floor applies (read < entrega), fecha_entrega is carried."""
        pipeline = _pipeline()
        # vision read earlier than delivery → floored to fecha_entrega
        guia = _guia("T009-0002", date(2026, 6, 1))
        sunat = {
            "T009-0002": OfficialGre(
                guia_id="T009-0002",
                serie="T009",
                numero="0002",
                ruc_emisor="20",
                ruc_receptor="20",
                fecha_entrega=date(2026, 6, 5),
            )
        }
        out = pipeline._stage_normalize_dates([guia], sunat_fetch_map=sunat)
        assert out[0].fecha_entrega == date(2026, 6, 5)

    def test_fecha_entrega_none_without_sunat(self) -> None:
        """No SUNAT data (air-gap default) → fecha_entrega stays None."""
        pipeline = _pipeline()
        guia = _guia("T009-0003", date(2026, 6, 10))
        out = pipeline._stage_normalize_dates([guia], sunat_fetch_map=None)
        assert out[0].fecha_entrega is None
