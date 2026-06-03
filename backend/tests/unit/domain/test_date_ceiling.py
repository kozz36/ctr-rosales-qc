"""Tests for the pure-domain reception-ceiling predicate (date_ceiling.py).

These tests run with ZERO mocks/patches — the function is a pure domain function
with no I/O, no SDK, and no port (mirrors test_date_floor.py style).

Covers all branches of ``apply_reception_ceiling``:
  1. ``ceiling is None``             → passthrough, no flag.
  2. ``reception is None``           → passthrough, no flag (nothing to clamp).
  3. ``reception > ceiling``         → clamp to ceiling + flag.
  4. ``reception < ceiling``         → unchanged, no flag.
  5. ``reception == ceiling``        → unchanged, no flag (boundary).
"""

from __future__ import annotations

from datetime import date

import pytest

from reconciliation.domain.date_ceiling import apply_reception_ceiling


class TestApplyReceptionCeiling:
    def test_no_ceiling_passthrough(self) -> None:
        """Branch 1: no ceiling data → no clamp, no flag."""
        reception = date(2026, 5, 30)
        result_date, was_clamped = apply_reception_ceiling(reception, None)
        assert result_date == reception
        assert was_clamped is False

    def test_no_ceiling_none_reception_passthrough(self) -> None:
        """Branch 1 + 2: both None → return (None, False)."""
        result_date, was_clamped = apply_reception_ceiling(None, None)
        assert result_date is None
        assert was_clamped is False

    def test_reception_none_passthrough(self) -> None:
        """Branch 2: reception unknown; nothing to clamp → (None, False)."""
        ceiling = date(2026, 5, 28)
        result_date, was_clamped = apply_reception_ceiling(None, ceiling)
        assert result_date is None
        assert was_clamped is False

    def test_reception_after_ceiling_clamped_and_flagged(self) -> None:
        """Branch 3: reception later than Protocolo date → clamp to ceiling + flag.

        Physical semantics: a guía cannot be received AFTER the authoritative
        Protocolo declared date.  The ceiling is the upper authority (límite máximo).
        """
        reception = date(2026, 6, 5)
        ceiling = date(2026, 5, 28)
        result_date, was_clamped = apply_reception_ceiling(reception, ceiling)
        assert result_date == ceiling
        assert was_clamped is True

    def test_reception_before_ceiling_unchanged(self) -> None:
        """Branch 4: reception before Protocolo date → valid, no clamp."""
        reception = date(2026, 5, 20)
        ceiling = date(2026, 5, 28)
        result_date, was_clamped = apply_reception_ceiling(reception, ceiling)
        assert result_date == reception
        assert was_clamped is False

    def test_reception_equal_ceiling_unchanged(self) -> None:
        """Boundary: reception == ceiling is valid (same date); not clamped.

        The ceiling condition is strictly ``reception > ceiling``.
        Equal dates are accepted without flagging.
        """
        same_day = date(2026, 5, 28)
        result_date, was_clamped = apply_reception_ceiling(same_day, same_day)
        assert result_date == same_day
        assert was_clamped is False

    def test_return_type_is_tuple(self) -> None:
        """Return value is always a 2-tuple (date | None, bool)."""
        result = apply_reception_ceiling(date(2026, 5, 28), date(2026, 5, 30))
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_clamped_value_is_ceiling_not_reception(self) -> None:
        """When clamped, the returned date is EXACTLY the ceiling, not a copy."""
        reception = date(2026, 6, 15)
        ceiling = date(2026, 5, 28)
        result_date, was_clamped = apply_reception_ceiling(reception, ceiling)
        assert result_date == ceiling
        assert result_date is ceiling  # same object (ceiling is returned directly)
        assert was_clamped is True

    @pytest.mark.parametrize(
        "reception, ceiling, expected_clamped",
        [
            (date(2026, 5, 29), date(2026, 5, 28), True),   # 1 day after → clamp
            (date(2026, 5, 28), date(2026, 5, 28), False),  # same day → no clamp
            (date(2026, 5, 27), date(2026, 5, 28), False),  # 1 day before → no clamp
        ],
        ids=["one-day-after", "same-day", "one-day-before"],
    )
    def test_boundary_parametrize(
        self, reception: date, ceiling: date, expected_clamped: bool
    ) -> None:
        """Parametrized boundary check around the ceiling boundary."""
        _, was_clamped = apply_reception_ceiling(reception, ceiling)
        assert was_clamped is expected_clamped
