"""Tests for the pure-domain delivery-floor predicate (R9b / date_floor.py).

These tests run with ZERO mocks/patches — the function is a pure domain function
with no I/O, no SDK, and no port (mirrors test_date_divergence.py style).

Covers all four branches of ``apply_delivery_floor``:
  1. ``fecha_entrega is None``  → passthrough, no flag.
  2. ``reception is None``      → floor to fecha_entrega + flag.
  3. ``reception < fecha_entrega`` → floor to fecha_entrega + flag.
  4. ``reception >= fecha_entrega`` → unchanged, no flag.

Boundary: ``reception == fecha_entrega`` → unchanged, False (>= case).
"""

from __future__ import annotations

from datetime import date

from reconciliation.domain.date_floor import apply_delivery_floor


class TestApplyDeliveryFloor:
    def test_no_fecha_entrega_passthrough(self) -> None:
        """Branch 1: no SUNAT data → no floor, no flag."""
        reception = date(2026, 5, 28)
        result_date, was_floored = apply_delivery_floor(reception, None)
        assert result_date == reception
        assert was_floored is False

    def test_no_fecha_entrega_none_reception_passthrough(self) -> None:
        """Branch 1: both None → return (None, False); no floor data at all."""
        result_date, was_floored = apply_delivery_floor(None, None)
        assert result_date is None
        assert was_floored is False

    def test_reception_none_floors_to_entrega(self) -> None:
        """Branch 2: vision returned no date; floor to fecha_entrega + flag."""
        entrega = date(2026, 5, 20)
        result_date, was_floored = apply_delivery_floor(None, entrega)
        assert result_date == entrega
        assert was_floored is True

    def test_reception_before_entrega_floors_and_flags(self) -> None:
        """Branch 3: resolved date is before delivery — physical impossibility.

        Goods cannot be received before they are delivered.  The floor
        replaces the invalid reception date and sets was_floored=True.
        """
        reception = date(2026, 5, 10)
        entrega = date(2026, 5, 20)
        result_date, was_floored = apply_delivery_floor(reception, entrega)
        assert result_date == entrega
        assert was_floored is True

    def test_reception_after_entrega_unchanged(self) -> None:
        """Branch 4: resolved date is after delivery — valid, no flag."""
        reception = date(2026, 5, 28)
        entrega = date(2026, 5, 20)
        result_date, was_floored = apply_delivery_floor(reception, entrega)
        assert result_date == reception
        assert was_floored is False

    def test_reception_equal_entrega_unchanged(self) -> None:
        """Boundary: reception == fecha_entrega is valid (same-day delivery+receipt).

        The floor condition is strictly ``reception < fecha_entrega``.
        Equal dates are accepted without flagging.
        """
        same_day = date(2026, 5, 20)
        result_date, was_floored = apply_delivery_floor(same_day, same_day)
        assert result_date == same_day
        assert was_floored is False

    def test_return_type_is_tuple(self) -> None:
        """Return value is always a 2-tuple (date | None, bool)."""
        result = apply_delivery_floor(date(2026, 5, 28), date(2026, 5, 20))
        assert isinstance(result, tuple)
        assert len(result) == 2
