"""Unit tests for PrintedTableAdapter.

All tests mock the PaddleOCR engine — NO real model, NO paddleocr install.

Covered:
- Parses well-formed OCR result into MaterialLine list (3.x format)
- Confidence < 0.85 sets requires_review=True
- Confidence >= 0.85 leaves requires_review=False
- Non-matching OCR lines (headers, dates) are silently skipped
- Empty OCR result returns []
- OCR raise → graceful degradation: returns [], sets _ocr_failed=True
- _unavailable flag: returns [] without calling OCR, sets _ocr_failed=True
- Lazy-load: injected _ocr bypasses import
- extract_declared is no-op

paddleocr 3.x result format used by mocks:
  predict() returns a list of OCRResult-like dicts, one per image.
  Each dict has: {"rec_texts": [str, ...], "rec_scores": [float, ...], ...}
  (Contrast with 2.x: [[ [bbox], (text, conf) ]] nested list structure)
"""

from __future__ import annotations

import io
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from reconciliation.adapters.ocr.paddle_table import PrintedTableAdapter, _CONFIDENCE_THRESHOLD


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_png() -> bytes:
    img = Image.new("RGB", (8, 8), color=(200, 200, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_ocr(lines: list[tuple[str, float]]) -> MagicMock:
    """Build a mock OCR engine returning *lines* in paddleocr 3.x predict() format.

    The 3.x ``predict()`` API returns a list of OCRResult dict-like objects.
    Each object has ``rec_texts`` (list[str]) and ``rec_scores`` (list[float]).
    """
    mock = MagicMock()
    # 3.x format: predict() → [{"rec_texts": [...], "rec_scores": [...], ...}]
    result_item = MagicMock()
    result_item.__getitem__ = lambda self, key: (
        [t for t, _ in lines] if key == "rec_texts" else
        [c for _, c in lines] if key == "rec_scores" else
        [][0]  # KeyError for unknown keys
    )
    mock.predict.return_value = [result_item]
    return mock


# ---------------------------------------------------------------------------
# Tests — parsing
# ---------------------------------------------------------------------------


class TestPrintedTableAdapterParsing:
    def test_single_well_formed_line_kg(self) -> None:
        ocr = _make_ocr([("BARRA CORRUGADA 3/8 5.800 KG", 0.95)])
        adapter = PrintedTableAdapter(_ocr=ocr)
        lines = adapter.extract_printed_table(_make_png())
        assert len(lines) == 1
        assert lines[0].unidad == "KG"
        assert lines[0].cantidad == Decimal("5.800")
        assert lines[0].requires_review is False

    def test_single_well_formed_line_tn(self) -> None:
        ocr = _make_ocr([("FIERRO CORRUGADO 6.572 TN", 0.90)])
        adapter = PrintedTableAdapter(_ocr=ocr)
        lines = adapter.extract_printed_table(_make_png())
        assert len(lines) == 1
        assert lines[0].unidad == "TN"
        assert lines[0].cantidad == Decimal("6.572")

    def test_unit_rd(self) -> None:
        ocr = _make_ocr([("VARILLA LISA 10.0 RD", 0.95)])
        adapter = PrintedTableAdapter(_ocr=ocr)
        lines = adapter.extract_printed_table(_make_png())
        assert len(lines) == 1
        assert lines[0].unidad == "RD"

    def test_unit_rollo(self) -> None:
        ocr = _make_ocr([("ALAMBRE N8 2.0 Rollo", 0.95)])
        adapter = PrintedTableAdapter(_ocr=ocr)
        lines = adapter.extract_printed_table(_make_png())
        assert len(lines) == 1
        assert lines[0].unidad == "Rollo"

    def test_qty_with_comma_decimal(self) -> None:
        ocr = _make_ocr([("BARRA 5,750 KG", 0.95)])
        adapter = PrintedTableAdapter(_ocr=ocr)
        lines = adapter.extract_printed_table(_make_png())
        assert len(lines) == 1
        assert lines[0].cantidad == Decimal("5.750")

    def test_multiple_lines(self) -> None:
        ocr = _make_ocr([
            ("BARRA CORRUGADA 3/8 5.800 KG", 0.95),
            ("FIERRO 10.0 TN", 0.88),
        ])
        adapter = PrintedTableAdapter(_ocr=ocr)
        lines = adapter.extract_printed_table(_make_png())
        assert len(lines) == 2

    def test_non_matching_lines_skipped(self) -> None:
        """Headers and date lines should not produce MaterialLine objects."""
        ocr = _make_ocr([
            ("GUIA DE REMISION N° 001234", 0.95),
            ("FECHA: 10/05/2024", 0.95),
            ("BARRA CORRUGADA 5.800 KG", 0.95),
        ])
        adapter = PrintedTableAdapter(_ocr=ocr)
        lines = adapter.extract_printed_table(_make_png())
        assert len(lines) == 1

    def test_empty_ocr_result_returns_empty_list(self) -> None:
        """predict() returns list with item having empty rec_texts → []."""
        mock = MagicMock()
        # 3.x: empty OCRResult — item with empty rec_texts list
        result_item = MagicMock()
        result_item.__getitem__ = lambda self, key: (
            [] if key == "rec_texts" else
            [] if key == "rec_scores" else
            [][0]
        )
        mock.predict.return_value = [result_item]
        adapter = PrintedTableAdapter(_ocr=mock)
        lines = adapter.extract_printed_table(_make_png())
        assert lines == []

    def test_none_ocr_result_returns_empty_list(self) -> None:
        """predict() returns empty list (no items) → []."""
        mock = MagicMock()
        mock.predict.return_value = []
        adapter = PrintedTableAdapter(_ocr=mock)
        lines = adapter.extract_printed_table(_make_png())
        assert lines == []


# ---------------------------------------------------------------------------
# Tests — confidence threshold
# ---------------------------------------------------------------------------


class TestPrintedTableAdapterConfidence:
    def test_confidence_below_threshold_sets_requires_review(self) -> None:
        conf = _CONFIDENCE_THRESHOLD - 0.01  # just below 0.85
        ocr = _make_ocr([("BARRA 5.800 KG", conf)])
        adapter = PrintedTableAdapter(_ocr=ocr)
        lines = adapter.extract_printed_table(_make_png())
        assert len(lines) == 1
        assert lines[0].requires_review is True
        assert lines[0].confidence == pytest.approx(conf)

    def test_confidence_at_threshold_not_flagged(self) -> None:
        conf = _CONFIDENCE_THRESHOLD  # exactly 0.85
        ocr = _make_ocr([("BARRA 5.800 KG", conf)])
        adapter = PrintedTableAdapter(_ocr=ocr)
        lines = adapter.extract_printed_table(_make_png())
        assert lines[0].requires_review is False

    def test_confidence_above_threshold_not_flagged(self) -> None:
        ocr = _make_ocr([("BARRA 5.800 KG", 0.99)])
        adapter = PrintedTableAdapter(_ocr=ocr)
        lines = adapter.extract_printed_table(_make_png())
        assert lines[0].requires_review is False

    def test_low_confidence_line_still_included(self) -> None:
        """Lines with low confidence are NOT dropped — they are flagged."""
        ocr = _make_ocr([("BARRA 5.800 KG", 0.40)])
        adapter = PrintedTableAdapter(_ocr=ocr)
        lines = adapter.extract_printed_table(_make_png())
        assert len(lines) == 1
        assert lines[0].requires_review is True


# ---------------------------------------------------------------------------
# Tests — error handling
# ---------------------------------------------------------------------------


class TestPrintedTableAdapterErrorHandling:
    """Graceful degradation: OCR errors return [] and set _ocr_failed=True.

    As of the paddleocr-3.x compat fix, extract_printed_table NEVER raises.
    Load failures, predict() errors, and the _unavailable flag all result in
    an empty list (guía quantities empty → MISMATCH flagged for review).
    """

    def test_ocr_predict_raises_returns_empty_and_sets_flag(self) -> None:
        """predict() error → [] returned, _ocr_failed=True, no raise."""
        mock = MagicMock()
        mock.predict.side_effect = RuntimeError("cuda died")
        adapter = PrintedTableAdapter(_ocr=mock)
        result = adapter.extract_printed_table(_make_png())
        assert result == []
        assert adapter._ocr_failed is True

    def test_unavailable_returns_empty_and_sets_flag(self) -> None:
        """_unavailable=True → [] returned without calling predict()."""
        adapter = PrintedTableAdapter()
        adapter._unavailable = True
        result = adapter.extract_printed_table(_make_png())
        assert result == []
        assert adapter._ocr_failed is True

    def test_import_failure_returns_empty_and_sets_unavailable(self) -> None:
        """Import failure (load error) → [] returned, _unavailable set permanently."""
        adapter = PrintedTableAdapter()
        with patch.object(
            adapter, "_get_ocr", side_effect=ImportError("paddleocr missing")
        ):
            result = adapter.extract_printed_table(_make_png())
        assert result == []
        assert adapter._ocr_failed is True
        assert adapter._unavailable is True

    def test_ocr_failed_reset_on_successful_call(self) -> None:
        """_ocr_failed is reset to False at the start of each call."""
        lines_data = [("BARRA 5.800 KG", 0.95)]
        ocr = _make_ocr(lines_data)
        adapter = PrintedTableAdapter(_ocr=ocr)
        # Simulate a prior failure state
        adapter._ocr_failed = True
        result = adapter.extract_printed_table(_make_png())
        # Successful call resets the flag
        assert adapter._ocr_failed is False
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Tests — ExtractionPort no-op stubs
# ---------------------------------------------------------------------------


class TestPrintedTableAdapterStubs:
    def test_extract_declared_returns_empty_list(self) -> None:
        adapter = PrintedTableAdapter(_ocr=MagicMock())
        assert adapter.extract_declared("any text") == []

    def test_lazy_load_not_triggered_at_instantiation(self) -> None:
        adapter = PrintedTableAdapter()
        assert adapter._ocr is None
        assert adapter._unavailable is False
        assert adapter._ocr_failed is False
