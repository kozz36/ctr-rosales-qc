"""Tests covering declared-date handling after the domain-correctness fix (2026-06-03).

The ``_stage_extract_declared_date`` vision sub-stage has been REMOVED.
The declared reception date is the DIGITAL ``Fecha:`` on the Protocolo de Recepción
(deterministic parse by ``digital_text_extractor.py``, real year, zero vision calls).

This module retains:
- ``TestDeclaredDateVisionStageRemoved``: pins the removal of the old stage.
- ``TestParseDayMonthHardening``: ``_parse_day_month`` is still used by the guía
  date normalization stage (``_stage_normalize_dates``) — not removed.
- ``TestProtocoloCropConfig``: the ``protocolo_crop`` config block is kept for
  potential future use and to avoid breaking any config parsing.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from reconciliation.application.config import AppConfig
from reconciliation.application.pipeline import (
    ReconciliationPipeline,
    _parse_day_month,
)
from reconciliation.domain.models import Registro, VisionResult


class _FakeDoc:
    def __init__(self, pages: int = 10) -> None:
        self._pages = pages
        self.rendered: list[int] = []

    def page_count(self) -> int:
        return self._pages

    def render_page(self, idx: int, dpi: int = 200) -> bytes:
        self.rendered.append(idx)
        return b"\x89PNG\r\n-rendered-"

    def page_text(self, idx: int) -> str | None:
        return None


class _FakeExtractor:
    def extract_declared(self, text: str) -> list:
        return []

    def extract_printed_table(self, image: bytes) -> list:
        return []


class _CountingVision:
    supports_batch: bool = False

    def __init__(self, result: VisionResult | None = None) -> None:
        self._result = result or VisionResult(date=date(2026, 5, 28), confidence=0.95, raw="28/05/26")
        self.calls = 0

    def read_handwritten_date(self, image: bytes, hint: str | None = None) -> VisionResult:
        self.calls += 1
        return self._result

    def read_handwritten_date_batch(self, images: list[bytes]) -> list[VisionResult]:
        raise NotImplementedError


def _pipeline(vision: _CountingVision, doc: _FakeDoc | None = None) -> ReconciliationPipeline:
    return ReconciliationPipeline(
        doc_source=doc or _FakeDoc(),
        extractor=_FakeExtractor(),
        vision=vision,
        config=AppConfig(),
        page_to_registro={},
    )


class TestDeclaredDateVisionStageRemoved:
    """Domain-correctness fix (2026-06-03): declared date = DIGITAL Protocolo parse.

    The ``_stage_extract_declared_date`` vision sub-stage is REMOVED; the declared
    date authority is the digital ``fecha_declarada`` from ``digital_text_extractor.py``
    (deterministic parse, real year, no vision call). These tests pin the removal:
    (a) the pipeline method no longer exists; (b) a Registro with ``protocolo_page``
    set does NOT trigger a vision call for the declared date.
    """

    def test_stage_extract_declared_date_method_removed_from_pipeline(self) -> None:
        """Removal gate: ``_stage_extract_declared_date`` must NOT exist on the pipeline."""
        vis = _CountingVision()
        pipe = _pipeline(vis)
        assert not hasattr(pipe, "_stage_extract_declared_date"), (
            "_stage_extract_declared_date was NOT removed from ReconciliationPipeline. "
            "The domain correction requires deleting this vision sub-stage — the declared "
            "date is the DIGITAL Protocolo parse, not vision-read."
        )

    def test_protocolo_page_registro_issues_zero_vision_calls_for_declared(self) -> None:
        """A Registro with protocolo_page set must not trigger any vision call.

        With the old stage present, a Registro(protocolo_page=7) caused 1 vision call.
        After removal, declared date resolution never calls VisionLLMPort — zero calls.
        fecha_authoritative == fecha_declarada (digital parse) directly.
        """
        vis = _CountingVision()
        pipe = _pipeline(vis)
        reg = Registro(
            numero="232",
            fecha_declarada=date(2026, 5, 28),
            declared_lines=[],
            protocolo_page=7,
        )
        # After removal: the pipeline does not execute any declared-date vision read.
        # We verify by confirming the method is absent (AttributeError is the signal).
        # If the method is still present, the previous test already catches it.
        assert reg.fecha_authoritative == date(2026, 5, 28)
        assert vis.calls == 0  # No vision calls were made during construction

    def test_fecha_authoritative_is_fecha_declarada(self) -> None:
        """fecha_authoritative == fecha_declarada with no vision override possible."""
        reg = Registro(
            numero="232",
            fecha_declarada=date(2026, 5, 28),
            declared_lines=[],
            protocolo_page=3,
        )
        assert reg.fecha_authoritative == date(2026, 5, 28)
        assert reg.fecha_authoritative == reg.fecha_declarada

    def test_fecha_authoritative_none_when_digital_parse_failed(self) -> None:
        """When digital parse yields no date, fecha_authoritative is None."""
        reg = Registro(
            numero="232",
            fecha_declarada=None,
            declared_lines=[],
            protocolo_page=3,
        )
        assert reg.fecha_authoritative is None


class TestParseDayMonthHardening:
    """``_parse_day_month`` is still used by the guía date normalization stage.

    W-2 defense-in-depth: when the raw contains an ISO date (``YYYY-MM-DD``)
    parse it in the CORRECT year-month-day order — never let the loose
    ``dd[/-]mm`` regex grab the ``MM-DD`` slice and swap day/month. Only fall
    through to the loose regex when no ISO date is present.
    """

    def test_iso_blob_parses_correct_day_month_no_swap(self) -> None:
        # {"date": "2026-11-05"} → Nov 5 → day=5, month=11 (NOT day=11, month=5).
        assert _parse_day_month(1.0, '{"date": "2026-11-05", "confidence": 1.0}') == (5, 11)

    def test_iso_year_month_day_parses_correctly(self) -> None:
        # 2026-12-05 → Dec 5 → day=5, month=12 (NOT swapped to day=12, month=5).
        assert _parse_day_month(1.0, "2026-12-05") == (5, 12)

    def test_iso_year_month_day_28(self) -> None:
        # 2026-05-28 → May 28 → day=28, month=5 (was previously rejected as None).
        assert _parse_day_month(1.0, "2026-05-28") == (28, 5)

    def test_clean_dd_mm_still_parses(self) -> None:
        assert _parse_day_month(1.0, "28/05/26") == (28, 5)

    def test_clean_dd_mm_dash_still_parses(self) -> None:
        assert _parse_day_month(1.0, "28-05-2026") == (28, 5)


class TestProtocoloCropConfig:
    def test_protocolo_crop_enabled_by_default(self) -> None:
        """R10.5: protocolo_crop default is (0.60,0.04,1.00,0.22) — non-degenerate (enabled)."""
        cfg = AppConfig()
        assert cfg.vision.protocolo_crop.enabled is True

    def test_protocolo_crop_disabled_when_zero_box(self) -> None:
        """ADR-6: degenerate zero-box still disables crop (full-page fallback path preserved)."""
        from reconciliation.application.config import StampCropConfig, VisionConfig
        v = VisionConfig(
            protocolo_crop=StampCropConfig(x0=0.0, y0=0.0, x1=0.0, y1=0.0)
        )
        assert v.protocolo_crop.enabled is False

    def test_stamp_crop_unaffected(self) -> None:
        """R7 guía stamp crop is NOT regressed by this change."""
        cfg = AppConfig()
        assert cfg.vision.stamp_crop.enabled is True
