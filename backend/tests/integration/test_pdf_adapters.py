"""Integration tests for PdfStructureAdapter + DigitalTextExtractionAdapter.

These tests run against the REAL PDF file.  They are guarded by a pytest.mark
that skips when the PDF is absent, so CI without the file still passes.

File path discovery: looks for the PDF in the project root (two levels up from
the backend/ directory).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# PDF file guard
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # backend/tests/integration -> project root
_PDF_NAME = "Informe de detalle del formulario-202605311657.pdf"
_PDF_PATH = _PROJECT_ROOT / _PDF_NAME

_SKIP_NO_PDF = pytest.mark.skipif(
    not _PDF_PATH.exists(),
    reason=f"Real PDF not present at {_PDF_PATH} — skipping integration tests",
)

# ---------------------------------------------------------------------------
# Expected data (validated against real PDF during development)
# ---------------------------------------------------------------------------

# 11 registros present in the Contents page
_EXPECTED_REGISTROS = {
    "4252", "4251", "4250", "4249", "4237",
    "4236", "4225", "4223", "4221", "4216", "3507",
}

# Registro ID → (description_number, expected_line_count, expected_date)
# description_number = the "Description" field on the detail page (not the same as registro ID)
_EXPECTED_DETAIL: dict[str, tuple[str, int, date | None]] = {
    "4252": ("232", 12, date(2026, 5, 28)),
    "4251": ("231", 6,  date(2026, 5, 27)),
    "4250": ("230", 6,  date(2026, 5, 26)),
    "4249": ("229", 5,  date(2026, 5, 25)),
    "4237": ("228", 6,  date(2026, 5, 23)),
    "4236": ("227", 11, date(2026, 5, 22)),
    "4225": ("226", 7,  date(2026, 5, 21)),
    "4223": ("225", 6,  date(2026, 5, 20)),
    "4221": ("224", 6,  date(2026, 5, 19)),
    "4216": ("223", 7,  date(2026, 5, 18)),
    "3507": ("198", 10, date(2026, 4, 17)),
}

# Expected Contents page offsets (1-based start page per registro)
_EXPECTED_OFFSETS: dict[str, int] = {
    "4252": 3,
    "4251": 25,
    "4250": 37,
    "4249": 50,
    "4237": 83,
    "4236": 139,
    "4225": 224,
    "4223": 278,
    "4221": 349,
    "4216": 378,
    "3507": 454,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def pdf_adapter():
    """Open PdfStructureAdapter once for the whole test module."""
    from reconciliation.adapters.pdf.pymupdf_source import PdfStructureAdapter

    with PdfStructureAdapter(_PDF_PATH) as adapter:
        yield adapter


@pytest.fixture(scope="module")
def extractor():
    from reconciliation.adapters.pdf.digital_text_extractor import DigitalTextExtractionAdapter

    return DigitalTextExtractionAdapter()


# ---------------------------------------------------------------------------
# PdfStructureAdapter tests
# ---------------------------------------------------------------------------

@_SKIP_NO_PDF
class TestPdfStructureAdapter:
    def test_page_count(self, pdf_adapter) -> None:
        assert pdf_adapter.page_count() == 493

    def test_contents_offsets_all_registros(self, pdf_adapter) -> None:
        offsets = pdf_adapter.contents_offsets()
        assert set(offsets.keys()) == _EXPECTED_REGISTROS

    def test_contents_offsets_exact_pages(self, pdf_adapter) -> None:
        offsets = pdf_adapter.contents_offsets()
        assert offsets == _EXPECTED_OFFSETS

    def test_page_text_returns_string_for_digital_pages(self, pdf_adapter) -> None:
        # Page 3 (idx 2) is the first Form Detail page — it has digital text
        text = pdf_adapter.page_text(2)
        assert isinstance(text, str)
        assert len(text) > 10

    def test_page_text_returns_none_for_empty_pages(self, pdf_adapter) -> None:
        # Page 5 (idx 4) is a photo/scanned page with only the header overlay
        text = pdf_adapter.page_text(4)
        # These pages contain only the universal header — classified as near-empty
        # Either None or short header-only string (implementation-dependent)
        if text is not None:
            # Universal header should not contain material/form content
            assert "BARRA" not in text and "Form detail" not in text

    def test_render_page_returns_png_bytes(self, pdf_adapter) -> None:
        png = pdf_adapter.render_page(2)
        assert isinstance(png, bytes)
        # PNG magic bytes: \x89PNG\r\n\x1a\n
        assert png[:4] == b"\x89PNG"

    def test_render_page_dpi_affects_size(self, pdf_adapter) -> None:
        small = pdf_adapter.render_page(2, dpi=72)
        large = pdf_adapter.render_page(2, dpi=200)
        assert len(large) > len(small)

    def test_context_manager_closes_cleanly(self) -> None:
        from reconciliation.adapters.pdf.pymupdf_source import PdfStructureAdapter

        with PdfStructureAdapter(_PDF_PATH) as adapter:
            count = adapter.page_count()
        assert count == 493
        # After close, accessing the doc should raise (fitz behaviour)
        import fitz
        with pytest.raises(Exception):
            adapter._doc.page_count  # noqa: B018 — intentionally accessing closed doc

    def test_implements_document_source_port(self, pdf_adapter) -> None:
        from reconciliation.domain.ports import DocumentSourcePort

        assert isinstance(pdf_adapter, DocumentSourcePort)


# ---------------------------------------------------------------------------
# DigitalTextExtractionAdapter — detail page
# ---------------------------------------------------------------------------

@_SKIP_NO_PDF
class TestDigitalTextExtractionAdapterDetailPages:
    def test_all_11_registros_parsed(self, pdf_adapter, extractor) -> None:
        offsets = pdf_adapter.contents_offsets()
        parsed = {}
        for reg_id, start_page in offsets.items():
            detail_idx = start_page - 1
            text = pdf_adapter.page_text(detail_idx)
            if text:
                reg = extractor.extract_registro_from_detail_page(text, detail_idx)
                if reg:
                    parsed[reg_id] = reg
        assert set(parsed.keys()) == _EXPECTED_REGISTROS, (
            f"Missing: {_EXPECTED_REGISTROS - set(parsed.keys())}"
        )

    @pytest.mark.parametrize("reg_id,expected", list(_EXPECTED_DETAIL.items()))
    def test_detail_registro_line_count(self, pdf_adapter, extractor, reg_id, expected) -> None:
        desc_num, expected_lines, expected_date = expected
        start_page = _EXPECTED_OFFSETS[reg_id]
        detail_idx = start_page - 1
        text = pdf_adapter.page_text(detail_idx)
        assert text is not None
        reg = extractor.extract_registro_from_detail_page(text, detail_idx)
        assert reg is not None, f"#{reg_id}: failed to parse detail page"
        assert len(reg.declared_lines) == expected_lines, (
            f"#{reg_id}: expected {expected_lines} lines, got {len(reg.declared_lines)}"
        )

    @pytest.mark.parametrize("reg_id,expected", list(_EXPECTED_DETAIL.items()))
    def test_detail_registro_date(self, pdf_adapter, extractor, reg_id, expected) -> None:
        desc_num, expected_lines, expected_date = expected
        start_page = _EXPECTED_OFFSETS[reg_id]
        detail_idx = start_page - 1
        text = pdf_adapter.page_text(detail_idx)
        assert text is not None
        reg = extractor.extract_registro_from_detail_page(text, detail_idx)
        assert reg is not None
        assert reg.fecha_declarada == expected_date, (
            f"#{reg_id}: expected date {expected_date}, got {reg.fecha_declarada}"
        )

    @pytest.mark.parametrize("reg_id,expected", list(_EXPECTED_DETAIL.items()))
    def test_detail_registro_numero(self, pdf_adapter, extractor, reg_id, expected) -> None:
        desc_num, expected_lines, expected_date = expected
        start_page = _EXPECTED_OFFSETS[reg_id]
        detail_idx = start_page - 1
        text = pdf_adapter.page_text(detail_idx)
        assert text is not None
        reg = extractor.extract_registro_from_detail_page(text, detail_idx)
        assert reg is not None
        assert reg.numero == desc_num, (
            f"#{reg_id}: expected description num {desc_num!r}, got {reg.numero!r}"
        )

    def test_material_lines_have_full_descriptions(self, pdf_adapter, extractor) -> None:
        """Material descriptions must start with 'BARRA' (not truncated mid-word)."""
        offsets = pdf_adapter.contents_offsets()
        for reg_id, start_page in offsets.items():
            detail_idx = start_page - 1
            text = pdf_adapter.page_text(detail_idx)
            if not text:
                continue
            reg = extractor.extract_registro_from_detail_page(text, detail_idx)
            if not reg:
                continue
            for line in reg.declared_lines:
                assert line.description_raw.startswith("BARRA"), (
                    f"#{reg_id}: description_raw does not start with BARRA: {line.description_raw!r}"
                )

    def test_material_lines_confidence_is_none(self, pdf_adapter, extractor) -> None:
        """Digital text lines are trusted — confidence must be None."""
        offsets = pdf_adapter.contents_offsets()
        reg_id = "4252"
        start_page = _EXPECTED_OFFSETS[reg_id]
        text = pdf_adapter.page_text(start_page - 1)
        reg = extractor.extract_registro_from_detail_page(text, start_page - 1)
        assert reg is not None
        for line in reg.declared_lines:
            assert line.confidence is None

    def test_verbatim_typo_preserved(self, pdf_adapter, extractor) -> None:
        """'BARRA AG615/A706' (typo) must be kept verbatim in description_raw."""
        offsets = pdf_adapter.contents_offsets()
        reg_id = "4252"
        start_page = _EXPECTED_OFFSETS[reg_id]
        text = pdf_adapter.page_text(start_page - 1)
        reg = extractor.extract_registro_from_detail_page(text, start_page - 1)
        assert reg is not None
        raws = [l.description_raw for l in reg.declared_lines]
        assert any("AG615" in r for r in raws), (
            f"Expected 'AG615' typo preserved in {raws}"
        )

    def test_canonical_description_normalised(self, pdf_adapter, extractor) -> None:
        """description_canonical must be lowercase NFC."""
        offsets = pdf_adapter.contents_offsets()
        reg_id = "4252"
        start_page = _EXPECTED_OFFSETS[reg_id]
        text = pdf_adapter.page_text(start_page - 1)
        reg = extractor.extract_registro_from_detail_page(text, start_page - 1)
        assert reg is not None
        for line in reg.declared_lines:
            assert line.description_canonical == line.description_canonical.lower()

    def test_unit_never_modified(self, pdf_adapter, extractor) -> None:
        """Units must be exactly TN, KG, RD, or Rollo — no conversion."""
        offsets = pdf_adapter.contents_offsets()
        allowed = {"TN", "KG", "RD", "Rollo"}
        for reg_id, start_page in offsets.items():
            text = pdf_adapter.page_text(start_page - 1)
            if not text:
                continue
            reg = extractor.extract_registro_from_detail_page(text, start_page - 1)
            if not reg:
                continue
            for line in reg.declared_lines:
                assert line.unidad in allowed, (
                    f"#{reg_id}: unexpected unit {line.unidad!r}"
                )


# ---------------------------------------------------------------------------
# DigitalTextExtractionAdapter — Protocolo pages
# ---------------------------------------------------------------------------

@_SKIP_NO_PDF
class TestDigitalTextExtractionAdapterProtoPages:
    def test_all_11_protocolo_pages_parsed(self, pdf_adapter, extractor) -> None:
        offsets = pdf_adapter.contents_offsets()
        parsed = {}
        for reg_id, start_page in offsets.items():
            proto_idx = start_page  # 0-based: proto is always start_page-1+1 = start_page
            text = pdf_adapter.page_text(proto_idx)
            if text and "PROTOCOLO" in text:
                reg = extractor.extract_registro_from_proto_page(text, proto_idx)
                if reg:
                    parsed[reg_id] = reg
        assert len(parsed) == 11, f"Expected 11 protocolo registros, got {len(parsed)}: {set(parsed.keys())}"

    @pytest.mark.parametrize("reg_id,expected", list(_EXPECTED_DETAIL.items()))
    def test_proto_line_count_matches_detail(self, pdf_adapter, extractor, reg_id, expected) -> None:
        """Protocolo page must yield the same line count as the detail page."""
        _desc_num, expected_lines, _expected_date = expected
        start_page = _EXPECTED_OFFSETS[reg_id]
        proto_idx = start_page
        text = pdf_adapter.page_text(proto_idx)
        assert text is not None
        reg = extractor.extract_registro_from_proto_page(text, proto_idx)
        assert reg is not None, f"#{reg_id}: failed to parse protocolo page"
        assert len(reg.declared_lines) == expected_lines, (
            f"#{reg_id}: expected {expected_lines} lines, got {len(reg.declared_lines)}"
        )

    def test_proto_date_matches_detail_date(self, pdf_adapter, extractor) -> None:
        """The Protocolo date field must match the detail page Form date."""
        offsets = pdf_adapter.contents_offsets()
        for reg_id, start_page in offsets.items():
            detail_text = pdf_adapter.page_text(start_page - 1)
            proto_text = pdf_adapter.page_text(start_page)
            if not detail_text or not proto_text:
                continue
            reg_detail = extractor.extract_registro_from_detail_page(detail_text, start_page - 1)
            reg_proto = extractor.extract_registro_from_proto_page(proto_text, start_page)
            if reg_detail and reg_proto:
                assert reg_detail.fecha_declarada == reg_proto.fecha_declarada, (
                    f"#{reg_id}: detail date {reg_detail.fecha_declarada} != "
                    f"proto date {reg_proto.fecha_declarada}"
                )


# ---------------------------------------------------------------------------
# ExtractionPort interface
# ---------------------------------------------------------------------------

@_SKIP_NO_PDF
class TestExtractionPortConformance:
    def test_implements_extraction_port(self, extractor) -> None:
        from reconciliation.domain.ports import ExtractionPort

        assert isinstance(extractor, ExtractionPort)

    def test_extract_declared_returns_list(self, pdf_adapter, extractor) -> None:
        text = pdf_adapter.page_text(2)  # first detail page
        assert text is not None
        result = extractor.extract_declared(text)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_extract_printed_table_noop(self, extractor) -> None:
        result = extractor.extract_printed_table(b"fake image bytes")
        assert result == []
