"""Tests for infrastructure/container.py.

Covers:
- CompositeExtractionAdapter routing (no ML deps — fake adapters injected)
- build_page_to_registro_map: section↔registro correlation logic
- build_pipeline / build_review_service: smoke tests with fakes

No real PDF, no ML deps, no SDK deps required.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from reconciliation.domain.models import (
    GuiaDeRemision,
    MaterialLine,
    PageClassification,
    ReconciliationRow,
    Registro,
    VisionResult,
)
from reconciliation.domain.ports import ExtractionPort
from reconciliation.infrastructure.container import (
    CompositeExtractionAdapter,
    build_page_to_registro_map,
)


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------


def _make_line(desc: str = "BARRA 3/8", qty: str = "1.0") -> MaterialLine:
    return MaterialLine(
        description_raw=desc,
        description_canonical=desc.lower(),
        unidad="TN",
        cantidad=Decimal(qty),
    )


# ---------------------------------------------------------------------------
# CompositeExtractionAdapter — routing tests
# ---------------------------------------------------------------------------


class TestCompositeExtractionAdapterRouting:
    """Verify that calls are routed to the correct inner adapter."""

    def _make_composite_with_fakes(
        self,
        declared_lines: list[MaterialLine] | None = None,
        ocr_lines: list[MaterialLine] | None = None,
    ) -> CompositeExtractionAdapter:
        """Build a CompositeExtractionAdapter with fake inner adapters."""
        fake_declared = MagicMock()
        fake_declared.extract_declared.return_value = declared_lines or []
        fake_declared.extract_printed_table.return_value = []  # unused

        fake_ocr = MagicMock()
        fake_ocr.extract_printed_table.return_value = ocr_lines or []
        fake_ocr.extract_declared.return_value = []  # unused

        adapter = CompositeExtractionAdapter.__new__(CompositeExtractionAdapter)
        adapter._declared_adapter = fake_declared  # type: ignore[attr-defined]
        adapter._ocr_adapter = fake_ocr  # type: ignore[attr-defined]
        return adapter

    def test_extract_declared_routes_to_digital_adapter(self) -> None:
        expected = [_make_line("VARILLA", "2.5")]
        composite = self._make_composite_with_fakes(declared_lines=expected)
        result = composite.extract_declared("some text")
        assert result == expected
        composite._declared_adapter.extract_declared.assert_called_once_with("some text")

    def test_extract_declared_does_not_call_ocr(self) -> None:
        composite = self._make_composite_with_fakes()
        composite.extract_declared("text")
        composite._ocr_adapter.extract_printed_table.assert_not_called()

    def test_extract_printed_table_routes_to_ocr_adapter(self) -> None:
        expected = [_make_line("BARRA 1/2", "10.0")]
        composite = self._make_composite_with_fakes(ocr_lines=expected)
        fake_image = b"\x89PNG"
        result = composite.extract_printed_table(fake_image)
        assert result == expected
        composite._ocr_adapter.extract_printed_table.assert_called_once_with(fake_image)

    def test_extract_printed_table_does_not_call_declared(self) -> None:
        composite = self._make_composite_with_fakes()
        composite.extract_printed_table(b"\x89PNG")
        composite._declared_adapter.extract_declared.assert_not_called()

    def test_composite_conforms_to_extraction_port(self) -> None:
        """CompositeExtractionAdapter must satisfy ExtractionPort structural check."""
        composite = self._make_composite_with_fakes()
        assert isinstance(composite, ExtractionPort)

    def test_extract_declared_returns_empty_when_adapter_returns_empty(self) -> None:
        composite = self._make_composite_with_fakes(declared_lines=[])
        assert composite.extract_declared("nothing") == []

    def test_extract_printed_table_returns_empty_on_no_ocr_result(self) -> None:
        composite = self._make_composite_with_fakes(ocr_lines=[])
        assert composite.extract_printed_table(b"image") == []


# ---------------------------------------------------------------------------
# build_page_to_registro_map — section↔registro correlation
# ---------------------------------------------------------------------------


class TestBuildPageToRegistroMap:
    """Verify the page range derivation from Contents offsets."""

    def test_empty_offsets_returns_empty_map(self) -> None:
        result = build_page_to_registro_map({}, total_pages=10)
        assert result == {}

    def test_single_section_owns_all_pages_from_start_to_eof(self) -> None:
        # Section "100" starts at page 3 (1-based); 10 pages total.
        # Owns 0-based pages 2..9 (inclusive).
        result = build_page_to_registro_map({"100": 3}, total_pages=10)
        assert result == {2: "100", 3: "100", 4: "100", 5: "100",
                          6: "100", 7: "100", 8: "100", 9: "100"}

    def test_two_sections_non_overlapping(self) -> None:
        # "A" starts at 1-based page 1 → 0-based 0; "B" starts at 1-based 4 → 0-based 3.
        # Total 6 pages.
        result = build_page_to_registro_map({"A": 1, "B": 4}, total_pages=6)
        assert result[0] == "A"
        assert result[1] == "A"
        assert result[2] == "A"
        assert result[3] == "B"
        assert result[4] == "B"
        assert result[5] == "B"

    def test_section_boundary_is_exclusive(self) -> None:
        # "A" starts at 1-based page 1 (0-based=0); "B" starts at 1-based page 3 (0-based=2).
        # A owns 0-based pages 0..1 (exclusive upper bound = 2).
        # B owns 0-based pages 2..4.
        result = build_page_to_registro_map({"A": 1, "B": 3}, total_pages=5)
        assert result[0] == "A"
        assert result[1] == "A"
        assert result[2] == "B"
        assert result[3] == "B"
        assert result[4] == "B"

    def test_registro_ids_are_string_keys(self) -> None:
        result = build_page_to_registro_map({"4252": 1}, total_pages=3)
        for v in result.values():
            assert isinstance(v, str)

    def test_three_sections_middle_section_bounded(self) -> None:
        # A: pages 0-1 (0-based), B: 2-3, C: 4-5
        result = build_page_to_registro_map({"A": 1, "B": 3, "C": 5}, total_pages=6)
        assert result[0] == "A"
        assert result[1] == "A"
        assert result[2] == "B"
        assert result[3] == "B"
        assert result[4] == "C"
        assert result[5] == "C"

    def test_pages_before_first_section_are_unmapped(self) -> None:
        # Sections start at page 3 (1-based). Pages 0-1 (0-based) are not in any section.
        result = build_page_to_registro_map({"X": 3}, total_pages=5)
        assert 0 not in result
        assert 1 not in result
        assert result[2] == "X"

    def test_out_of_order_offsets_sorted_correctly(self) -> None:
        # Offsets passed in reverse order — must be sorted by start page.
        result = build_page_to_registro_map({"B": 4, "A": 1}, total_pages=6)
        assert result[0] == "A"
        assert result[3] == "B"

    def test_total_pages_zero_returns_empty(self) -> None:
        result = build_page_to_registro_map({"A": 1}, total_pages=0)
        # Start page 0-based = 0; end = 0 → range(0, 0) is empty
        assert result == {}


# ---------------------------------------------------------------------------
# Integration smoke: build_page_to_registro_map with real PDF-like offsets
# ---------------------------------------------------------------------------


class TestSectionRegistroCorrelationIntegration:
    """Simulate the real PDF's 11-registro structure with fake offsets."""

    def test_11_registros_each_own_contiguous_range(self) -> None:
        # Simulate 11 registros across a 44-page PDF (4 pages each).
        offsets = {str(i): 1 + i * 4 for i in range(11)}  # 1-based start pages
        total_pages = 44
        result = build_page_to_registro_map(offsets, total_pages=total_pages)

        for i in range(11):
            registro_id = str(i)
            for page_0 in range(i * 4, (i + 1) * 4):
                assert result.get(page_0) == registro_id, (
                    f"page {page_0} should map to registro {registro_id!r}"
                )

    def test_guia_page_in_section_range_maps_to_correct_registro(self) -> None:
        # Section "4252" starts at page 3 (1-based), "4253" at page 7.
        offsets = {"4252": 3, "4253": 7}
        result = build_page_to_registro_map(offsets, total_pages=12)
        # 0-based pages 2-5 belong to "4252"; pages 6-11 to "4253"
        for page in range(2, 6):
            assert result[page] == "4252"
        for page in range(6, 12):
            assert result[page] == "4253"


# ---------------------------------------------------------------------------
# C-3 fix: numero derivation from real parsers
# ---------------------------------------------------------------------------


class FakeDocSource:
    """Minimal DocumentSourcePort fake for container-level tests."""

    def __init__(self, pages: dict[int, str]) -> None:
        # page_idx → text
        self._pages = pages
        self._total = max(pages) + 1 if pages else 0

    def page_count(self) -> int:
        return self._total

    def page_text(self, idx: int) -> str | None:
        return self._pages.get(idx)

    def render_page(self, idx: int, dpi: int = 200) -> bytes:
        return b"\x89PNG"


class FakeDeclaredExtractor:
    """Minimal duck-type for DeclaredExtractorPort."""

    def __init__(self, proto_map: dict[int, Registro], detail_map: dict[int, Registro]) -> None:
        self._proto = proto_map
        self._detail = detail_map

    def extract_registro_from_proto_page(self, text: str, source_page: int) -> Registro | None:
        return self._proto.get(source_page)

    def extract_registro_from_detail_page(self, text: str, source_page: int) -> Registro | None:
        return self._detail.get(source_page)


def _make_registro(numero: str) -> Registro:
    return Registro(numero=numero, fecha_declarada=None, declared_lines=[])


class TestBuildPageToRegistroMapWithDerivedNumero:
    """Verify numero derivation from real parsers (C-3 fix)."""

    def test_proto_page_provides_numero(self) -> None:
        """When a PROTO page is in the range, its Description numero is used as key."""
        # Contents ID "4252" → 1-based start 3.  Section range: pages 2..5 (0-based).
        # Page 2 has PROTOCOLO text → parser returns Registro(numero="232").
        doc = FakeDocSource({
            2: "PROTOCOLO DE RECEPCI\nsome text",
            3: "GUIA DE REMISION\nsome text",
        })
        extractor = FakeDeclaredExtractor(
            proto_map={2: _make_registro("232")},
            detail_map={},
        )
        result = build_page_to_registro_map(
            {"4252": 3},  # 1-based start page 3 → 0-based 2
            total_pages=6,
            doc_source=doc,
            declared_extractor=extractor,
        )
        # All pages in the section should map to "232", NOT "4252"
        for page in range(2, 6):
            assert result[page] == "232", f"page {page} should map to '232', got {result.get(page)!r}"

    def test_detail_page_used_as_fallback_when_no_proto(self) -> None:
        """When only a DETAIL page is in the range, its numero is used."""
        doc = FakeDocSource({
            2: "Form detail\nsome text",
        })
        extractor = FakeDeclaredExtractor(
            proto_map={},
            detail_map={2: _make_registro("232")},
        )
        result = build_page_to_registro_map(
            {"4252": 3},
            total_pages=6,
            doc_source=doc,
            declared_extractor=extractor,
        )
        for page in range(2, 6):
            assert result[page] == "232"

    def test_fallback_to_contents_id_when_no_declared_page(self) -> None:
        """When no DECLARED page is found in range, the Contents ID is the fallback key."""
        doc = FakeDocSource({
            2: None,  # type: ignore[dict-item]  # scanned / empty
            3: "GUIA DE REMISION\nsome text",
        })
        extractor = FakeDeclaredExtractor(proto_map={}, detail_map={})
        result = build_page_to_registro_map(
            {"4252": 3},
            total_pages=6,
            doc_source=doc,
            declared_extractor=extractor,
        )
        for page in range(2, 6):
            assert result[page] == "4252"

    def test_two_sections_each_get_correct_numero(self) -> None:
        """Multi-section PDF: each section resolves its own Description numero."""
        # Section A: "4252" → 1-based 1 (0-based 0..4).  PROTO on page 0 → "232".
        # Section B: "4251" → 1-based 6 (0-based 5..9).  PROTO on page 5 → "231".
        doc = FakeDocSource({
            0: "PROTOCOLO DE RECEPCI\n",
            5: "PROTOCOLO DE RECEPCI\n",
        })
        extractor = FakeDeclaredExtractor(
            proto_map={
                0: _make_registro("232"),
                5: _make_registro("231"),
            },
            detail_map={},
        )
        result = build_page_to_registro_map(
            {"4252": 1, "4251": 6},
            total_pages=10,
            doc_source=doc,
            declared_extractor=extractor,
        )
        for page in range(0, 5):
            assert result[page] == "232"
        for page in range(5, 10):
            assert result[page] == "231"

    def test_without_doc_source_uses_contents_id_legacy(self) -> None:
        """When doc_source is None, original Contents-ID behaviour is preserved."""
        result = build_page_to_registro_map(
            {"4252": 3, "4251": 7},
            total_pages=12,
        )
        for page in range(2, 6):
            assert result[page] == "4252"
        for page in range(6, 12):
            assert result[page] == "4251"


# ---------------------------------------------------------------------------
# CompositeExtractionAdapter — DeclaredExtractorPort delegation (C-1 fix)
# ---------------------------------------------------------------------------


class TestCompositeExtractorDelegation:
    """Verify CompositeExtractionAdapter delegates Registro-level methods."""

    def _make_composite_with_fake_declared(self, proto_result, detail_result) -> CompositeExtractionAdapter:
        fake_declared = MagicMock()
        fake_declared.extract_registro_from_proto_page.return_value = proto_result
        fake_declared.extract_registro_from_detail_page.return_value = detail_result
        fake_declared.extract_declared.return_value = []
        fake_ocr = MagicMock()
        fake_ocr.extract_printed_table.return_value = []

        adapter = CompositeExtractionAdapter.__new__(CompositeExtractionAdapter)
        adapter._declared_adapter = fake_declared
        adapter._ocr_adapter = fake_ocr
        return adapter

    def test_extract_registro_from_proto_page_delegates(self) -> None:
        reg = _make_registro("232")
        composite = self._make_composite_with_fake_declared(reg, None)
        result = composite.extract_registro_from_proto_page("text", 5)
        assert result is reg
        composite._declared_adapter.extract_registro_from_proto_page.assert_called_once_with("text", 5)

    def test_extract_registro_from_detail_page_delegates(self) -> None:
        reg = _make_registro("232")
        composite = self._make_composite_with_fake_declared(None, reg)
        result = composite.extract_registro_from_detail_page("text", 3)
        assert result is reg
        composite._declared_adapter.extract_registro_from_detail_page.assert_called_once_with("text", 3)
