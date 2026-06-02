"""Tests for NullOcrExtractor — no-op OCR adapter for ocr.enabled=False mode.

Verifies that the adapter:
  - Returns empty lists from both ExtractionPort methods.
  - Never imports or references PaddleOCR.
  - Satisfies the ExtractionPort structural protocol.
"""

from __future__ import annotations

import sys

import pytest

from reconciliation.adapters.ocr.null_extractor import NullOcrExtractor
from reconciliation.domain.ports import ExtractionPort


class TestNullOcrExtractor:
    def test_extract_printed_table_returns_empty_list(self) -> None:
        extractor = NullOcrExtractor()
        result = extractor.extract_printed_table(b"\x89PNG\r\n")
        assert result == []

    def test_extract_printed_table_always_empty_regardless_of_input(self) -> None:
        extractor = NullOcrExtractor()
        # Any bytes input — should always return []
        assert extractor.extract_printed_table(b"") == []
        assert extractor.extract_printed_table(b"\x00" * 1024) == []

    def test_extract_declared_returns_empty_list(self) -> None:
        extractor = NullOcrExtractor()
        result = extractor.extract_declared("some material text")
        assert result == []

    def test_satisfies_extraction_port_protocol(self) -> None:
        """NullOcrExtractor must satisfy ExtractionPort runtime check."""
        extractor = NullOcrExtractor()
        assert isinstance(extractor, ExtractionPort)

    def test_paddle_not_imported_by_null_extractor_module(self) -> None:
        """Importing NullOcrExtractor must NOT import PaddleOCR.

        This guards the invariant that ocr.enabled=False never touches paddle.
        We check sys.modules for any paddle-related key after constructing the
        extractor.
        """
        # Remove any pre-existing paddle entries so we get a clean slate.
        paddle_keys = [k for k in sys.modules if "paddle" in k.lower()]
        for k in paddle_keys:
            sys.modules.pop(k, None)

        # Construct (and call) the extractor
        extractor = NullOcrExtractor()
        extractor.extract_printed_table(b"\x89PNG")
        extractor.extract_declared("text")

        # After construction and calls, no paddle module should have been imported.
        post_paddle_keys = [k for k in sys.modules if "paddle" in k.lower()]
        assert post_paddle_keys == [], (
            f"NullOcrExtractor imported paddle-related modules: {post_paddle_keys}"
        )
