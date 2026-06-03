"""Tests for the delivery_dates wiring in ``_stage_reconcile``.

Single-source-of-truth refactor (R9b review-path fix): ``_stage_reconcile`` now
builds ``delivery_dates`` (``guia_id`` → SUNAT ``fecha_entrega``) from the GUÍAS
THEMSELVES (``guia.fecha_entrega``, persisted by ``_stage_normalize_dates``) — NOT
from a separate ``sunat_fetch_map`` param.  This lets the crossed-bounds bracket
survive the cache round-trip and the ReviewService re-reconcile.  It is forwarded
to ``ReconciliationService.reconcile`` so the crossed-bounds anomaly
(``fecha_entrega > Protocolo``) is detected.

Backward-compat: a guía with ``fecha_entrega is None`` contributes nothing →
``delivery_dates`` stays empty (graceful — air-gap default).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from reconciliation.application.config import AppConfig
from reconciliation.application.pipeline import ReconciliationPipeline
from reconciliation.domain.models import (
    GuiaDeRemision,
    MaterialLine,
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


def _guia(guia_id: str, fecha_entrega: date | None = None) -> GuiaDeRemision:
    return GuiaDeRemision(
        guia_id=guia_id,
        registro="232",
        fecha=date(2026, 6, 10),
        lines=[_line()],
        source_pages=[0],
        fecha_entrega=fecha_entrega,
    )


def _registro() -> Registro:
    return Registro(
        numero="232",
        fecha_declarada=date(2026, 5, 28),
        declared_lines=[_line()],
    )


def test_stage_reconcile_builds_and_passes_delivery_dates() -> None:
    """fecha_entrega carried on the guía is forwarded as delivery_dates."""
    spy = _SpyReconciler()
    pipe = _pipeline(spy)
    entrega = date(2026, 6, 5)

    pipe._stage_reconcile([_registro()], [_guia("T009-0001", fecha_entrega=entrega)])

    assert spy.last_delivery_dates == {"T009-0001": entrega}


def test_stage_reconcile_skips_none_fecha_entrega() -> None:
    """A guía with fecha_entrega=None is excluded from delivery_dates."""
    spy = _SpyReconciler()
    pipe = _pipeline(spy)

    pipe._stage_reconcile([_registro()], [_guia("T009-0001", fecha_entrega=None)])

    assert spy.last_delivery_dates == {}


def test_stage_reconcile_no_fecha_entrega_empty_delivery_dates() -> None:
    """No guía carries fecha_entrega → delivery_dates is an empty dict (graceful)."""
    spy = _SpyReconciler()
    pipe = _pipeline(spy)

    pipe._stage_reconcile([_registro()], [_guia("T009-0001")])

    assert spy.last_delivery_dates == {}
