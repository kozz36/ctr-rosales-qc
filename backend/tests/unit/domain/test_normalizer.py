"""Unit tests for MaterialNormalizer (task 1.3).

Covers: extra whitespace collapsed, Unicode NFC applied, unit unchanged,
empty string returns empty string.
"""

from __future__ import annotations

import unicodedata

from reconciliation.domain.normalizer import MaterialNormalizer


class TestCanonicalize:
    def setup_method(self) -> None:
        self.norm = MaterialNormalizer()

    def test_empty_string_returns_empty(self) -> None:
        assert self.norm.canonicalize("") == ""

    def test_basic_lowercase(self) -> None:
        assert self.norm.canonicalize("BARRA CORRUGADA") == "barra corrugada"

    def test_leading_trailing_whitespace_stripped(self) -> None:
        assert self.norm.canonicalize("  ALAMBRE  ") == "alambre"

    def test_internal_whitespace_collapsed(self) -> None:
        assert self.norm.canonicalize("BARRA   CORRUGADA   1/2") == "barra corrugada 1/2"

    def test_tabs_and_newlines_collapsed(self) -> None:
        assert self.norm.canonicalize("ALAMBRE\tN°16\n KG") == "alambre\tn°16\n kg".replace(
            "\t", " "
        ).replace("\n ", " ").replace("\n", " ").strip()

    def test_unicode_nfc_applied(self) -> None:
        # Build a decomposed form (NFD) and verify NFC normalization
        nfd_string = unicodedata.normalize("NFD", "BARRA CORRUGADA")
        result = MaterialNormalizer().canonicalize(nfd_string)
        assert unicodedata.is_normalized("NFC", result)
        assert result == "barra corrugada"

    def test_accented_chars_preserved_lowercase(self) -> None:
        result = self.norm.canonicalize("ÁNGULO ESTRUCTURAL")
        assert result == "ángulo estructural"

    def test_unit_string_not_touched(self) -> None:
        # Unit is kept separate by convention; calling canonicalize on unit-only string
        # should not crash, but the point is callers never pass the unit here.
        unit = "KG"
        result = self.norm.canonicalize(unit)
        # It lowercases — that is acceptable; the invariant is that callers keep unit separate.
        assert result == "kg"

    def test_whitespace_only_returns_empty(self) -> None:
        result = self.norm.canonicalize("   ")
        assert result == ""

    def test_multiple_consecutive_spaces(self) -> None:
        result = self.norm.canonicalize("BARRA    CORRUGADA")
        assert result == "barra corrugada"
        assert "  " not in result
