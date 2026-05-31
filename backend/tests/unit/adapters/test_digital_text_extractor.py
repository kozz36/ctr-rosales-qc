"""Unit tests for DigitalTextExtractionAdapter — covers M-6 regex fix and edge cases.

Tests do NOT require the real PDF; they use synthetic text fragments that mirror
the relevant page structures.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from reconciliation.adapters.pdf.digital_text_extractor import (
    DigitalTextExtractionAdapter,
    _parse_proto_material_block,
    _PROTO_BLOCK_RE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _proto_block(lines: list[str]) -> str:
    """Build a minimal Protocolo page text with the given material lines."""
    material_body = "\n".join(lines)
    return f"PROTOCOLO DE RECEPCION\nRegistro N°:\nCONTRATANTE\n:\nCONSTRUCTORA XYZ\n232\n28-05-26\n\x14\n\x14\n{material_body}\n\x14\n"


# ---------------------------------------------------------------------------
# M-6: _PROTO_BLOCK_RE must NOT anchor on BARRA
# ---------------------------------------------------------------------------


class TestProtoBlockRegexDeAnchor:
    """M-6: non-BARRA materials must not be silently dropped."""

    def test_barra_material_still_parsed(self) -> None:
        """Baseline: BARRA lines still work after de-anchoring."""
        text = _proto_block(["BARRA A615/A706 G60 3/8\" DOB - 6.0 TN"])
        lines = _parse_proto_material_block(text)
        assert len(lines) == 1
        assert "BARRA" in lines[0].description_raw

    def test_alambre_material_not_dropped(self) -> None:
        """ALAMBRE (non-BARRA) must be captured — was silently dropped before M-6."""
        text = _proto_block(["ALAMBRE NEGRO N°16 - 50.0 KG"])
        lines = _parse_proto_material_block(text)
        assert len(lines) == 1, f"Expected 1 line, got {len(lines)}: {lines}"
        assert "ALAMBRE" in lines[0].description_raw

    def test_malla_material_not_dropped(self) -> None:
        text = _proto_block(["MALLA ELECTROSOLDADA 15x15 4.5mm - 12.0 KG"])
        lines = _parse_proto_material_block(text)
        assert len(lines) == 1
        assert "MALLA" in lines[0].description_raw

    def test_clavo_material_not_dropped(self) -> None:
        text = _proto_block(["CLAVO CON CABEZA 3\" - 25.0 KG"])
        lines = _parse_proto_material_block(text)
        assert len(lines) == 1
        assert "CLAVO" in lines[0].description_raw

    def test_mixed_barra_and_non_barra_all_captured(self) -> None:
        """BARRA + ALAMBRE + MALLA in same block — all three must be parsed."""
        text = _proto_block([
            "BARRA A615/A706 G60 1/2\" DOB - 10.0 TN",
            "ALAMBRE NEGRO N°16 - 50.0 KG",
            "MALLA ELECTROSOLDADA 15x15 4.5mm - 12.0 KG",
        ])
        lines = _parse_proto_material_block(text)
        assert len(lines) == 3, f"Expected 3 lines, got {len(lines)}: {[l.description_raw for l in lines]}"
        descs = {l.description_raw for l in lines}
        assert any("BARRA" in d for d in descs)
        assert any("ALAMBRE" in d for d in descs)
        assert any("MALLA" in d for d in descs)

    def test_empty_block_returns_empty_list(self) -> None:
        text = "PROTOCOLO DE RECEPCION\nRegistro N°:\nCONTRATANTE\n:\nXYZ\n232\n28-05-26\n"
        lines = _parse_proto_material_block(text)
        assert lines == []

    def test_quantities_parsed_correctly(self) -> None:
        text = _proto_block(["ALAMBRE NEGRO N°16 - 50.5 KG"])
        lines = _parse_proto_material_block(text)
        assert len(lines) == 1
        assert lines[0].cantidad == Decimal("50.5")
        assert lines[0].unidad == "KG"


# ---------------------------------------------------------------------------
# DigitalTextExtractionAdapter — extract_registro_from_proto_page
# ---------------------------------------------------------------------------


class TestExtractRegistroFromProtoPage:
    """Adapter-level tests for the Protocolo page parser."""

    def _adapter(self) -> DigitalTextExtractionAdapter:
        return DigitalTextExtractionAdapter()

    def test_parses_numero_and_date(self) -> None:
        text = (
            "PROTOCOLO DE RECEPCION\n"
            "Registro N°:\nCONTRATANTE\n:\nCONSTRUCTORA XYZ\n232\n28-05-26\n"
            "\x14\n\x14\n"
            "BARRA A615/A706 G60 3/8\" DOB - 6.0 TN\n"
            "\x14\n"
        )
        reg = self._adapter().extract_registro_from_proto_page(text, 5)
        assert reg is not None
        assert reg.numero == "232"
        assert reg.fecha_declarada == date(2026, 5, 28)
        assert len(reg.declared_lines) == 1

    def test_non_barra_line_captured(self) -> None:
        """M-6 fix: ALAMBRE line is returned by extract_registro_from_proto_page."""
        text = (
            "PROTOCOLO DE RECEPCION\n"
            "Registro N°:\nCONTRATANTE\n:\nCONSTRUCTORA XYZ\n232\n28-05-26\n"
            "\x14\n\x14\n"
            "ALAMBRE NEGRO N°16 - 50.0 KG\n"
            "\x14\n"
        )
        reg = self._adapter().extract_registro_from_proto_page(text, 5)
        assert reg is not None
        assert any("ALAMBRE" in l.description_raw for l in reg.declared_lines), (
            f"Expected ALAMBRE in lines: {[l.description_raw for l in reg.declared_lines]}"
        )

    def test_returns_none_when_registro_num_absent(self) -> None:
        text = "PROTOCOLO DE RECEPCION\nsome text without registro"
        reg = self._adapter().extract_registro_from_proto_page(text, 0)
        assert reg is None


# ---------------------------------------------------------------------------
# DigitalTextExtractionAdapter — extract_registro_from_detail_page
# ---------------------------------------------------------------------------


class TestExtractRegistroFromDetailPage:
    def _adapter(self) -> DigitalTextExtractionAdapter:
        return DigitalTextExtractionAdapter()

    def test_parses_numero_and_date(self) -> None:
        text = (
            "PTR001-TORRE ROSALES\n"
            "Informe de detalle del formulario\n"
            "FORM DETAIL\n"
            "#4252: CTR-PLC01-FR001\n"
            "\nDescription\n232\n"
            "Form date\nMay 28, 2026\n"
            "\nNotes\nBARRA A615/A706 G60 3/8\" DOB - 6.0 TN\nCreated by\n"
        )
        reg = self._adapter().extract_registro_from_detail_page(text, 2)
        assert reg is not None
        assert reg.numero == "232"
        assert reg.fecha_declarada == date(2026, 5, 28)
        assert len(reg.declared_lines) == 1

    def test_returns_none_when_description_absent(self) -> None:
        text = "FORM DETAIL\n#4252: something\nForm date\nMay 28, 2026\n"
        reg = self._adapter().extract_registro_from_detail_page(text, 0)
        assert reg is None
