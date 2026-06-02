"""Tests for the pure-domain fecha-divergence predicate (R9.3 / ADR-3).

These tests run with ZERO mocks/patches — the predicate is a pure function with
no I/O, no SDK, no port (FDR-S18).
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from reconciliation.domain.date_divergence import (
    DivergenceResult,
    check_fecha_divergence,
)


class TestCheckFechaDivergence:
    def test_same_day_month_same_year_not_divergent(self) -> None:
        """FDR-S06: identical dates → no divergence."""
        r = check_fecha_divergence(date(2026, 5, 28), date(2026, 5, 28))
        assert r.diverges is False
        assert r.reason is None

    def test_same_day_month_different_year_not_divergent(self) -> None:
        """FDR-S04 (CRITICAL, ADR-3): year-only difference is NOT a divergence.

        Year-inference asymmetry between declared (lower=None) and guía
        (SUNAT lower bound) sides causes spurious year divergence (#2753);
        the predicate compares day+month only.
        """
        r = check_fecha_divergence(date(2026, 5, 28), date(2025, 5, 28))
        assert r.diverges is False
        assert r.reason is None

    def test_different_day_same_month_divergent(self) -> None:
        """FDR-S05: day mismatch → divergence."""
        r = check_fecha_divergence(date(2026, 5, 28), date(2026, 5, 27))
        assert r.diverges is True
        assert r.reason == "fecha_divergence"

    def test_same_day_different_month_divergent(self) -> None:
        r = check_fecha_divergence(date(2026, 5, 28), date(2026, 4, 28))
        assert r.diverges is True
        assert r.reason == "fecha_divergence"

    def test_different_day_and_month_divergent(self) -> None:
        r = check_fecha_divergence(date(2026, 5, 28), date(2026, 4, 15))
        assert r.diverges is True
        assert r.reason == "fecha_divergence"

    def test_declared_none_not_divergent(self) -> None:
        """FDR-S10: null declared baseline must never paint guías red."""
        r = check_fecha_divergence(None, date(2026, 5, 28))
        assert r.diverges is False
        assert r.reason is None

    def test_guia_none_not_divergent(self) -> None:
        """FDR-S11: null guía date → unknown, not divergent."""
        r = check_fecha_divergence(date(2026, 5, 28), None)
        assert r.diverges is False
        assert r.reason is None

    def test_both_none_not_divergent(self) -> None:
        r = check_fecha_divergence(None, None)
        assert r.diverges is False
        assert r.reason is None

    def test_result_carries_input_dates(self) -> None:
        r = check_fecha_divergence(date(2026, 5, 28), date(2026, 4, 15))
        assert r.declared_fecha == date(2026, 5, 28)
        assert r.guia_fecha == date(2026, 4, 15)

    def test_result_is_frozen(self) -> None:
        """DivergenceResult is immutable (frozen dataclass)."""
        r = check_fecha_divergence(date(2026, 5, 28), date(2026, 5, 28))
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.diverges = True  # type: ignore[misc]
