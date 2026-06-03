"""Tests for the delivery_dates wiring in ``_stage_reconcile``.

``_stage_reconcile`` must build ``delivery_dates`` (``guia_id`` → SUNAT
``fecha_entrega``) from the ``sunat_fetch_map`` and forward it to
``ReconciliationService.reconcile`` so the crossed-bounds anomaly
(``fecha_entrega > Protocolo``) can be detected.

Backward-compat: when ``sunat_fetch_map`` is None/empty, ``delivery_dates`` is
an empty dict (graceful — reconcile behaves identically to the current branch).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from reconciliation.application.config import AppConfig
from reconciliation.application.pipeline import ReconciliationPipeline
from reconciliation.domain.models import (
    GuiaDeRemision,
    MaterialLine,
    OfficialGre,
    Registro,
    VisionResult,
)


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


class _SpyReconciler:
    """Captures the delivery_dates kwarg passed to reconcile."""

    def __init__(self) -> None:
        self.last_delivery_dates: object = "UNSET"

    def reconcile(
        self,
        declared: list,
        guias: list,
        delivery_dates: dict | None = None,
    ) -> list:
        self.last_delivery_dates = delivery_dates
        return []


def _pipeline(reconciler: _SpyReconciler) -> ReconciliationPipeline:
    pipe = ReconciliationPipeline(
        doc_source=_FakeDoc(),
        extractor=_FakeExtractor(),
        vision=_FakeVision(),
        config=AppConfig(),
        page_to_registro={},
    )
    pipe._reconciler = reconciler  # type: ignore[assignment]
    return pipe


def _line() -> MaterialLine:
    return MaterialLine(
        description_raw="x",
        description_canonical="x",
        unidad="TN",
        cantidad=Decimal("1"),
    )


def _official_gre(guia_id: str, fecha_entrega: date | None) -> OfficialGre:
    parts = guia_id.split("-", 1)
    return OfficialGre(
        guia_id=guia_id,
        serie=parts[0],
        numero=parts[1] if len(parts) > 1 else "",
        ruc_emisor="12345678901",
        ruc_receptor="98765432100",
        fecha_entrega=fecha_entrega,
    )


def _guia(guia_id: str) -> GuiaDeRemision:
    return GuiaDeRemision(
        guia_id=guia_id,
        registro="232",
        fecha=date(2026, 6, 10),
        lines=[_line()],
        source_pages=[0],
    )


def _registro() -> Registro:
    return Registro(
        numero="232",
        fecha_declarada=date(2026, 5, 28),
        declared_lines=[_line()],
    )


def test_stage_reconcile_builds_and_passes_delivery_dates() -> None:
    """fecha_entrega from sunat_fetch_map is forwarded as delivery_dates."""
    spy = _SpyReconciler()
    pipe = _pipeline(spy)
    entrega = date(2026, 6, 5)
    sunat_map = {"T009-0001": _official_gre("T009-0001", entrega)}

    pipe._stage_reconcile([_registro()], [_guia("T009-0001")], sunat_fetch_map=sunat_map)

    assert spy.last_delivery_dates == {"T009-0001": entrega}


def test_stage_reconcile_skips_none_fecha_entrega() -> None:
    """OfficialGre with fecha_entrega=None is excluded from delivery_dates."""
    spy = _SpyReconciler()
    pipe = _pipeline(spy)
    sunat_map = {"T009-0001": _official_gre("T009-0001", None)}

    pipe._stage_reconcile([_registro()], [_guia("T009-0001")], sunat_fetch_map=sunat_map)

    assert spy.last_delivery_dates == {}


def test_stage_reconcile_no_sunat_map_empty_delivery_dates() -> None:
    """No sunat_fetch_map → delivery_dates is an empty dict (graceful)."""
    spy = _SpyReconciler()
    pipe = _pipeline(spy)

    pipe._stage_reconcile([_registro()], [_guia("T009-0001")])

    assert spy.last_delivery_dates == {}
