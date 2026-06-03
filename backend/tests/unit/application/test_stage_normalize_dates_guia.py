"""Tests for the guía-side date-normalization stage (R9 / EXT-021).

``_stage_normalize_dates`` reconstructs the year from the day/month of each
guía's vision-read reception date.  The day/month MUST come from the
already-parsed ``GuiaDeRemision.fecha`` — NOT from ``fecha_raw``, which is the
full vision JSON (e.g. ``{"date": "2026-11-05", ...}``).  Feeding that JSON to
the loose ``_parse_day_month`` regex grabs the ISO ``MM-DD`` slice and SWAPS
day/month for any true date whose day <= 12, corrupting the guía reception date
and producing FALSE R9 divergences on correctly-filed guías
(the ``reception-date-authority`` skill forbids this all-guías-falsely-diverge mode).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from reconciliation.application.config import AppConfig
from reconciliation.application.pipeline import ReconciliationPipeline
from reconciliation.domain.models import GuiaDeRemision, MaterialLine, VisionResult


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


class TestGuiaDateNormalizationDoesNotSwapDayMonth:
    def test_full_json_raw_does_not_swap_day_month(self) -> None:
        """W-1: a guía whose vision date is Nov 5 must normalize to day=5, month=11.

        ``fecha_raw`` carries the FULL vision JSON ``{"date": "2026-11-05", ...}``.
        The legacy ``guia.fecha_raw or ...`` path fed the ISO ``11-05`` slice to the
        loose regex and produced day=11, month=5 — swapping the date (Nov 5 → May 11)
        and faking an R9 divergence against a declared 05/11 baseline.

        The vision year is deliberately wrong (2016) so the bounded-year
        reconstruction path runs and the swap, if present, surfaces in the output.
        """
        guia = GuiaDeRemision(
            guia_id="G1",
            registro="232",
            fecha=date(2016, 11, 5),
            fecha_confidence=1.0,
            lines=[_line()],
            source_pages=[0],
            fecha_raw='{"date": "2016-11-05", "confidence": 1.0}',
        )
        out = _pipeline()._stage_normalize_dates([guia])
        assert len(out) == 1
        normalized = out[0].fecha
        assert normalized is not None
        # The reception date must remain Nov 5 — NOT swapped to May 11.
        assert (normalized.day, normalized.month) == (5, 11)
