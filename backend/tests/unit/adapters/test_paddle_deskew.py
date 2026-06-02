"""Unit tests for DeskewAdapter.

All tests mock the PaddleOCR classifier and PIL/numpy — NO real model
download, NO PaddleOCR install required.

Covered:
- correct_orientation returns original bytes for 0-degree angle
- correct_orientation rotates for 90 / 180 / 270
- Fallback on classification failure (returns original bytes, no raise)
- Fallback when import fails (_unavailable flag path)
- Lazy-load: classifier injected via _classifier param bypasses import

paddleocr 3.x result format used by mocks:
  predict() → list of OCRResult-like dicts, one per image.
  For orientation: result[0]["doc_preprocessor_res"]["angle"] = int (degrees)
  For full OCR text: result[0]["rec_texts"] = list[str], result[0]["rec_scores"] = list[float]
  (Contrast with 2.x: ocr(cls=True) → [[[None, (str(angle), conf)]]])
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from reconciliation.adapters.ocr.paddle_deskew import DeskewAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_png(width: int = 8, height: int = 8) -> bytes:
    """Return minimal PNG bytes for testing."""
    img = Image.new("RGB", (width, height), color=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_classifier_returning(angle: int) -> MagicMock:
    """Return a mock classifier whose predict() returns the given orientation angle.

    3.x format: predict() → [{"doc_preprocessor_res": {"angle": int}, ...}]
    """
    mock = MagicMock()
    result_item = MagicMock()
    result_item.__getitem__ = lambda self, key: (
        {"angle": angle} if key == "doc_preprocessor_res" else [][0]
    )
    mock.predict.return_value = [result_item]
    return mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDeskewAdapterOrientationCorrection:
    def test_zero_angle_returns_original_bytes(self) -> None:
        image = _make_png()
        clf = _make_classifier_returning(0)
        adapter = DeskewAdapter(_classifier=clf)
        result = adapter.correct_orientation(image)
        assert result == image

    def test_90_degree_rotates_image(self) -> None:
        image = _make_png(4, 8)  # non-square so rotation is detectable
        clf = _make_classifier_returning(90)
        adapter = DeskewAdapter(_classifier=clf)
        result = adapter.correct_orientation(image)
        assert result != image
        # After -90 rotation of a 4×8 image the dimensions swap
        rotated = Image.open(io.BytesIO(result))
        assert rotated.size == (8, 4)

    def test_180_degree_returns_same_dimensions(self) -> None:
        # Use a non-uniform image so 180° rotation produces different bytes
        img = Image.new("RGB", (6, 6))
        for x in range(6):
            img.putpixel((x, 0), (x * 40, 0, 0))  # gradient on top row
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        image = buf.getvalue()

        clf = _make_classifier_returning(180)
        adapter = DeskewAdapter(_classifier=clf)
        result = adapter.correct_orientation(image)
        rotated = Image.open(io.BytesIO(result))
        assert rotated.size == (6, 6)
        # Pixel (0,0) original should now be at (5,5)
        orig = Image.open(io.BytesIO(image))
        assert rotated.getpixel((5, 5)) == orig.getpixel((0, 0))

    def test_270_degree_rotates_image(self) -> None:
        image = _make_png(4, 8)
        clf = _make_classifier_returning(270)
        adapter = DeskewAdapter(_classifier=clf)
        result = adapter.correct_orientation(image)
        rotated = Image.open(io.BytesIO(result))
        assert rotated.size == (8, 4)


class TestDeskewAdapterFallbacks:
    def test_classifier_returning_invalid_label_falls_back_to_zero(self) -> None:
        """3.x: doc_preprocessor_res["angle"] not in {0,90,180,270} → return original."""
        image = _make_png()
        clf = MagicMock()
        result_item = MagicMock()
        result_item.__getitem__ = lambda self, key: (
            {"angle": 999} if key == "doc_preprocessor_res" else [][0]
        )
        clf.predict.return_value = [result_item]
        adapter = DeskewAdapter(_classifier=clf)
        result = adapter.correct_orientation(image)
        assert result == image

    def test_classifier_returning_empty_result_falls_back(self) -> None:
        """3.x: predict() returns [] → fall back to 0 degrees (original bytes)."""
        image = _make_png()
        clf = MagicMock()
        clf.predict.return_value = []
        adapter = DeskewAdapter(_classifier=clf)
        result = adapter.correct_orientation(image)
        assert result == image

    def test_classifier_ocr_raises_returns_original(self) -> None:
        """predict() raises → _classify_angle returns 0, correct_orientation returns original."""
        image = _make_png()
        clf = MagicMock()
        clf.predict.side_effect = RuntimeError("ocr exploded")
        adapter = DeskewAdapter(_classifier=clf)
        result = adapter.correct_orientation(image)
        assert result == image

    def test_unavailable_flag_skips_classifier(self) -> None:
        image = _make_png()
        clf = MagicMock()
        adapter = DeskewAdapter(_classifier=clf)
        adapter._unavailable = True
        result = adapter.correct_orientation(image)
        clf.ocr.assert_not_called()
        assert result == image

    def test_import_failure_sets_unavailable_flag(self) -> None:
        """If get_classifier raises, _unavailable is set and original returned."""
        image = _make_png()
        adapter = DeskewAdapter()  # no injected classifier

        with patch.object(
            adapter, "_get_classifier", side_effect=ImportError("paddleocr not installed")
        ):
            result = adapter.correct_orientation(image)

        assert result == image
        assert adapter._unavailable is True

    def test_second_call_after_unavailable_skips_get_classifier(self) -> None:
        """Once _unavailable=True, _get_classifier is never called again."""
        image = _make_png()
        adapter = DeskewAdapter()
        adapter._unavailable = True

        with patch.object(adapter, "_get_classifier") as mock_get:
            result = adapter.correct_orientation(image)

        mock_get.assert_not_called()
        assert result == image


class TestDeskewAdapterLazyLoad:
    def test_classifier_not_loaded_at_instantiation(self) -> None:
        """Adapter does not call PaddleOCR at __init__ time."""
        adapter = DeskewAdapter()
        assert adapter._classifier is None
        assert adapter._unavailable is False

    def test_injected_classifier_used_directly(self) -> None:
        """Injected classifier bypasses lazy-load path entirely; predict() called."""
        clf = _make_classifier_returning(0)
        adapter = DeskewAdapter(_classifier=clf)
        image = _make_png()
        adapter.correct_orientation(image)
        clf.predict.assert_called_once()


# ---------------------------------------------------------------------------
# H-5: extract_title — title-OCR seam for scanned pages
# ---------------------------------------------------------------------------


def _make_classifier_returning_text(lines: list[str]) -> MagicMock:
    """Return a mock classifier whose predict() returns the given text lines.

    3.x format: predict() → [{"rec_texts": [str, ...], "rec_scores": [float, ...]}]
    """
    mock = MagicMock()
    result_item = MagicMock()
    result_item.__getitem__ = lambda self, key: (
        lines if key == "rec_texts" else
        [0.95] * len(lines) if key == "rec_scores" else
        [][0]
    )
    mock.predict.return_value = [result_item]
    return mock


class TestDeskewAdapterExtractTitle:
    """DeskewAdapter.extract_title — H-5 seam tests."""

    def test_returns_guia_title_when_present(self) -> None:
        """OCR result containing 'GUIA DE REMISION' → returns that string."""
        clf = _make_classifier_returning_text(["SOME OTHER LINE", "GUIA DE REMISION"])
        adapter = DeskewAdapter(_classifier=clf)
        result = adapter.extract_title(_make_png())
        assert result is not None
        assert "GUIA DE REMISION" in result.upper()

    def test_returns_protocolo_title_when_present(self) -> None:
        """OCR result containing 'PROTOCOLO DE RECEPCION' → returns that string."""
        clf = _make_classifier_returning_text(["PROTOCOLO DE RECEPCION DEL MATERIAL"])
        adapter = DeskewAdapter(_classifier=clf)
        result = adapter.extract_title(_make_png())
        assert result is not None
        assert "PROTOCOLO DE RECEPCI" in result.upper()

    def test_returns_none_when_no_known_title(self) -> None:
        """OCR result with no known title keyword → returns None."""
        clf = _make_classifier_returning_text(["SOME RANDOM TEXT", "MORE TEXT"])
        adapter = DeskewAdapter(_classifier=clf)
        result = adapter.extract_title(_make_png())
        assert result is None

    def test_returns_none_when_unavailable(self) -> None:
        """If _unavailable is True, extract_title returns None without calling OCR."""
        clf = _make_classifier_returning_text(["GUIA DE REMISION"])
        adapter = DeskewAdapter(_classifier=clf)
        adapter._unavailable = True
        result = adapter.extract_title(_make_png())
        assert result is None
        clf.ocr.assert_not_called()

    def test_returns_none_on_empty_ocr_result(self) -> None:
        """predict() returns [] (no items) → None."""
        clf = MagicMock()
        clf.predict.return_value = []
        adapter = DeskewAdapter(_classifier=clf)
        result = adapter.extract_title(_make_png())
        assert result is None

    def test_returns_none_when_get_classifier_raises(self) -> None:
        """Import/model failure → None, _unavailable set."""
        adapter = DeskewAdapter()
        with patch.object(
            adapter, "_get_classifier", side_effect=ImportError("paddleocr not installed")
        ):
            result = adapter.extract_title(_make_png())
        assert result is None
        assert adapter._unavailable is True
