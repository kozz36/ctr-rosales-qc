"""Unit tests for SectionIdPredicate (S1.3, EXT-018).

Verifies that the predicate correctly identifies PDF Contents/section IDs
(e.g. "4252") and does NOT treat real Registro N° values (e.g. "232") as
section IDs.
"""

from __future__ import annotations

import pytest

from reconciliation.domain.section_id_guard import SectionIdPredicate, is_section_id


class TestSectionIdPredicateDefault:
    """Tests using the default pattern (^4\\d{3}$)."""

    @pytest.mark.parametrize("value", ["4252", "4251", "4000", "4999", "4100"])
    def test_known_section_ids_return_true(self, value: str) -> None:
        assert is_section_id(value) is True

    @pytest.mark.parametrize("value", ["232", "231", "100", "1", "99", "9999", "40000"])
    def test_real_registro_numbers_return_false(self, value: str) -> None:
        assert is_section_id(value) is False

    def test_none_returns_false(self) -> None:
        assert is_section_id(None) is False  # type: ignore[arg-type]

    def test_empty_string_returns_false(self) -> None:
        assert is_section_id("") is False

    def test_non_numeric_string_returns_false(self) -> None:
        assert is_section_id("GUIA") is False
        assert is_section_id("abc") is False

    def test_string_with_leading_spaces_returns_false(self) -> None:
        # Predicate must match the entire value, not a substring.
        assert is_section_id(" 4252") is False

    def test_five_digit_number_returns_false(self) -> None:
        # 5-digit: not in the 4xxx pattern.
        assert is_section_id("42520") is False

    def test_three_digit_number_returns_false(self) -> None:
        assert is_section_id("425") is False


class TestSectionIdPredicateCustomPattern:
    """Tests for configurable pattern override."""

    def test_custom_pattern_matches_custom_ids(self) -> None:
        # Suppose a different PDF uses section IDs like "S001", "S002".
        pred = SectionIdPredicate(pattern=r"^S\d{3}$")
        assert pred("S001") is True
        assert pred("S999") is True
        assert pred("232") is False

    def test_inclusion_set_pattern(self) -> None:
        """Literal inclusion set via alternation."""
        pred = SectionIdPredicate(pattern=r"^(4250|4251|4252)$")
        assert pred("4250") is True
        assert pred("4252") is True
        assert pred("4253") is False
        assert pred("232") is False

    def test_callable_interface(self) -> None:
        pred = SectionIdPredicate()
        # SectionIdPredicate implements __call__ → usable as a plain function.
        assert callable(pred)
        assert pred("4252") is True
        assert pred("232") is False


class TestSectionIdGuard_EXT_S20_Regression:
    """EXT-S20: section ID '4252' MUST NEVER be treated as a Registro N°.

    This class exists as a named regression anchor so the CI report clearly
    surfaces EXT-018/EXT-S20 coverage regardless of how tests are filtered.
    """

    def test_4252_is_detected_as_section_id(self) -> None:
        """Primary regression guard: 4252 is a Contents ID, never a registro."""
        assert is_section_id("4252") is True

    def test_232_is_not_detected_as_section_id(self) -> None:
        """The real registro numero for the section containing 4252 is 232."""
        assert is_section_id("232") is False
