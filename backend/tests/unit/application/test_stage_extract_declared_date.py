"""Tests for the declared-date vision sub-stage (R9.5 / ADR-1/6/7).

``_stage_extract_declared_date`` reads the handwritten Protocolo "Fecha:" via
the existing VisionLLMPort and stores it on ``Registro.fecha_declarada_handwritten``
with the ADR-7 confidence gate.  Pure-stage tests use in-memory fakes only.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from reconciliation.application.config import AppConfig
from reconciliation.application.pipeline import (
    ReconciliationPipeline,
    _parse_day_month,
    _prepare_vision_image_proto,
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

    def __init__(self, result: VisionResult) -> None:
        self._result = result
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


class TestStageExtractDeclaredDate:
    def test_protocolo_page_none_skips_vision(self) -> None:
        vis = _CountingVision(VisionResult(date=date(2026, 5, 28), confidence=0.95, raw="28/05/26"))
        pipe = _pipeline(vis)
        regs = [Registro(numero="232", fecha_declarada=date(2026, 5, 1), declared_lines=[])]
        out, _ = pipe._stage_extract_declared_date(regs)
        assert vis.calls == 0
        assert out[0].fecha_declarada_handwritten is None

    def test_low_confidence_flags_registro_no_baseline(self) -> None:
        """ADR-7 / FDR-S12: confidence < 0.85 → handwritten=None, confidence recorded."""
        vis = _CountingVision(VisionResult(date=date(2026, 5, 28), confidence=0.72, raw="28/05/26"))
        pipe = _pipeline(vis)
        regs = [Registro(numero="232", fecha_declarada=date(2026, 5, 1), declared_lines=[], protocolo_page=7)]
        out, _ = pipe._stage_extract_declared_date(regs)
        assert vis.calls == 1
        assert out[0].fecha_declarada_handwritten is None
        assert out[0].fecha_declarada_confidence == 0.72
        # fecha_authoritative falls back to electronic (ADR-2).
        assert out[0].fecha_authoritative == date(2026, 5, 1)

    def test_high_confidence_sets_handwritten_with_year_inference(self) -> None:
        """FDR-S01/S03: confidence >= 0.85 → handwritten date reconstructed."""
        vis = _CountingVision(VisionResult(date=date(2016, 5, 28), confidence=0.92, raw="28/05/26"))
        pipe = _pipeline(vis)
        regs = [Registro(numero="232", fecha_declarada=date(2026, 5, 1), declared_lines=[], protocolo_page=7)]
        out, _ = pipe._stage_extract_declared_date(regs)
        assert vis.calls == 1
        hw = out[0].fecha_declarada_handwritten
        assert hw is not None
        # Year reconstructed from day/month via bounded inference (most recent ≤ today).
        assert (hw.month, hw.day) == (5, 28)
        assert out[0].fecha_declarada_confidence == 0.92
        assert out[0].fecha_authoritative == hw

    def test_only_registros_with_protocolo_page_call_vision(self) -> None:
        vis = _CountingVision(VisionResult(date=date(2026, 5, 28), confidence=0.95, raw="28/05/26"))
        pipe = _pipeline(vis)
        regs = [
            Registro(numero="232", fecha_declarada=None, declared_lines=[], protocolo_page=7),
            Registro(numero="233", fecha_declarada=None, declared_lines=[]),  # detail-only
        ]
        out, _ = pipe._stage_extract_declared_date(regs)
        assert vis.calls == 1
        assert len(out) == 2

    def test_renders_the_protocolo_page(self) -> None:
        vis = _CountingVision(VisionResult(date=date(2026, 5, 28), confidence=0.95, raw="28/05/26"))
        doc = _FakeDoc()
        pipe = _pipeline(vis, doc=doc)
        regs = [Registro(numero="232", fecha_declarada=None, declared_lines=[], protocolo_page=4)]
        pipe._stage_extract_declared_date(regs)
        assert 4 in doc.rendered

    def test_raw_json_does_not_corrupt_day_month(self) -> None:
        """C2-B: when ``raw`` is the model's full JSON (e.g.
        ``{"date": "2026-05-28", ...}``), the day/month MUST come from the
        already-parsed ``vr.date`` (28/05), NOT from the ISO year ``2026-05``
        which the legacy regex matched as day=26, month=05. A wrong baseline
        makes every correct guía falsely diverge (R9 inverted).
        """
        vis = _CountingVision(
            VisionResult(
                date=date(2026, 5, 28),
                confidence=1.0,
                raw='{"date": "2026-05-28", "confidence": 1.0}',
            )
        )
        pipe = _pipeline(vis)
        regs = [
            Registro(
                numero="232",
                fecha_declarada=date(2026, 5, 1),
                declared_lines=[],
                protocolo_page=7,
            )
        ]
        out, _ = pipe._stage_extract_declared_date(regs)
        hw = out[0].fecha_declarada_handwritten
        assert hw is not None
        assert (hw.day, hw.month) == (28, 5)


class TestParseDayMonthHardening:
    """C2-B defense-in-depth: a day digit embedded in a 4-digit run (an ISO year)
    must NOT be matched. ``2026-05`` should not parse as day=26, month=05.
    """

    def test_iso_year_month_not_matched_as_day_month(self) -> None:
        # The leading "20" of the ISO year precedes "26-05"; "26" must not be
        # treated as the day because it is preceded by a digit.
        assert _parse_day_month(1.0, "2026-05-28") == (None, None)

    def test_iso_json_blob_not_matched_as_day_month(self) -> None:
        assert _parse_day_month(1.0, '{"date": "2026-05-28"}') == (None, None)

    def test_clean_dd_mm_still_parses(self) -> None:
        assert _parse_day_month(1.0, "28/05/26") == (28, 5)

    def test_clean_dd_mm_dash_still_parses(self) -> None:
        assert _parse_day_month(1.0, "28-05-26") == (28, 5)


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
        """R7 guía stamp crop is NOT regressed by adding protocolo_crop."""
        cfg = AppConfig()
        assert cfg.vision.stamp_crop.enabled is True

    def test_prepare_vision_image_proto_returns_nonempty_on_disabled(self) -> None:
        """ADR-6: disabled crop → full-page fallback returns the original bytes."""
        from reconciliation.application.config import StampCropConfig, VisionConfig
        cfg_disabled = AppConfig(
            vision=VisionConfig(
                protocolo_crop=StampCropConfig(x0=0.0, y0=0.0, x1=0.0, y1=0.0)
            )
        )
        img = b"\x89PNG\r\n-full-page-"
        out = _prepare_vision_image_proto(img, cfg_disabled)
        assert out == img
        assert len(out) > 0
