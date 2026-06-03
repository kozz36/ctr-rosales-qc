"""Unit tests for domain/date_inference.py — bounded year inference (D5 / EXT-021).

Coverage (R2.7 / EXT-S27 / EXT-S28 / REC-C08 / REC-C09):

- Truth table: lower+upper, upper-only, ambiguous candidates (→ most recent).
- Boundary years: 31-Dec, 01-Jan, leap-day (Feb 29).
- Cross-bound degenerate (lower > upper) → (None, True).
- No valid candidate within the search window → (None, True).
- Provenance: year_inferred always True when function is called.
"""

from __future__ import annotations

from datetime import date

import pytest

from reconciliation.domain.date_inference import infer_reception_year


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _d(y: int, m: int, d: int) -> date:
    return date(y, m, d)


# ---------------------------------------------------------------------------
# EXT-S27 — Year inferred from both bounds
# ---------------------------------------------------------------------------

class TestBothBoundsProvided:
    def test_standard_case_28_05_upper_june_2026(self) -> None:
        """Ground-truth scenario for registro 232 guías (engram #2747/#2748)."""
        result, inferred = infer_reception_year(
            day=28, month=5,
            lower=_d(2026, 5, 20),  # GRE delivery date
            upper=_d(2026, 6, 1),   # doc export date
        )
        assert result == _d(2026, 5, 28)
        assert inferred is True

    def test_candidate_must_satisfy_lower_bound(self) -> None:
        """Candidate date must be >= lower."""
        # day=10, month=5, lower=2026-05-20 → 2026-05-10 violates lower bound
        # → no valid 2026 candidate; fall to most recent prior year
        result, inferred = infer_reception_year(
            day=10, month=5,
            lower=_d(2026, 5, 20),
            upper=_d(2026, 6, 1),
        )
        # 2026-05-10 < 2026-05-20 (lower violated) → 2025-05-10 satisfies both
        # but 2025-05-10 < 2026-05-20? No — lower is a 2026 date, 2025-05-10 < it.
        # So no candidate satisfies both bounds → (None, True).
        assert result is None
        assert inferred is True

    def test_candidate_must_satisfy_upper_bound(self) -> None:
        """Candidate date must be <= upper."""
        # day=15, month=7, upper=2026-06-01 → 2026-07-15 > upper
        result, inferred = infer_reception_year(
            day=15, month=7,
            lower=_d(2025, 1, 1),
            upper=_d(2026, 6, 1),
        )
        # 2026-07-15 > 2026-06-01 → invalid; 2025-07-15 satisfies both
        assert result == _d(2025, 7, 15)
        assert inferred is True

    def test_most_recent_candidate_chosen(self) -> None:
        """When multiple years satisfy bounds, the most recent is picked."""
        # day=1, month=3, lower=2020-01-01, upper=2026-06-01
        # Candidates: 2020-03-01, 2021-03-01, ..., 2026-03-01 (all satisfy)
        result, inferred = infer_reception_year(
            day=1, month=3,
            lower=_d(2020, 1, 1),
            upper=_d(2026, 6, 1),
        )
        assert result == _d(2026, 3, 1)
        assert inferred is True

    def test_degenerate_lower_greater_than_upper(self) -> None:
        """When lower > upper the function returns (None, True) for review."""
        result, inferred = infer_reception_year(
            day=1, month=1,
            lower=_d(2026, 6, 2),
            upper=_d(2026, 6, 1),
        )
        assert result is None
        assert inferred is True


# ---------------------------------------------------------------------------
# EXT-S28 — Year inferred with upper-bound only (no lower)
# ---------------------------------------------------------------------------

class TestUpperBoundOnly:
    def test_standard_upper_only_case(self) -> None:
        """EXT-S28: DD=15, MM=03, no lower, upper=2026-06-01 → 2026-03-15."""
        result, inferred = infer_reception_year(
            day=15, month=3,
            lower=None,
            upper=_d(2026, 6, 1),
        )
        assert result == _d(2026, 3, 15)
        assert inferred is True

    def test_future_day_month_clamped_to_prior_year(self) -> None:
        """day=15, month=8, upper=2026-06-01 → 2026-08-15 > upper → use 2025-08-15."""
        result, inferred = infer_reception_year(
            day=15, month=8,
            lower=None,
            upper=_d(2026, 6, 1),
        )
        assert result == _d(2025, 8, 15)
        assert inferred is True

    def test_exact_upper_bound_date_valid(self) -> None:
        """date(Y,MM,DD) == upper is a valid candidate."""
        result, inferred = infer_reception_year(
            day=1, month=6,
            lower=None,
            upper=_d(2026, 6, 1),
        )
        assert result == _d(2026, 6, 1)
        assert inferred is True

    def test_year_inferred_always_true(self) -> None:
        """year_inferred is True even when a single unambiguous candidate exists."""
        _result, inferred = infer_reception_year(
            day=28, month=5,
            lower=None,
            upper=_d(2026, 6, 1),
        )
        assert inferred is True


# ---------------------------------------------------------------------------
# Boundary year and leap-day edge cases
# ---------------------------------------------------------------------------

class TestBoundaryYears:
    def test_new_year_eve_dec_31(self) -> None:
        """Dec 31 satisfies an upper bound of Dec 31 same year."""
        result, inferred = infer_reception_year(
            day=31, month=12,
            lower=None,
            upper=_d(2026, 12, 31),
        )
        assert result == _d(2026, 12, 31)
        assert inferred is True

    def test_jan_1_satisfies_upper_in_same_year(self) -> None:
        result, inferred = infer_reception_year(
            day=1, month=1,
            lower=None,
            upper=_d(2026, 6, 1),
        )
        assert result == _d(2026, 1, 1)
        assert inferred is True

    def test_leap_day_feb_29_non_leap_year_skipped(self) -> None:
        """Feb 29 is skipped for non-leap years; picks nearest leap year."""
        result, inferred = infer_reception_year(
            day=29, month=2,
            lower=None,
            upper=_d(2026, 6, 1),
        )
        # 2026 is not a leap year; 2025 is not; 2024 IS (divisible by 4).
        # 2024-02-29 <= 2026-06-01 ✓
        assert result == _d(2024, 2, 29)
        assert inferred is True

    def test_feb_29_on_leap_year_upper(self) -> None:
        """Feb 29 with upper = 2024-03-01 → 2024-02-29 selected."""
        result, inferred = infer_reception_year(
            day=29, month=2,
            lower=None,
            upper=_d(2024, 3, 1),
        )
        assert result == _d(2024, 2, 29)
        assert inferred is True

    def test_no_valid_candidate_in_window(self) -> None:
        """When the search window has no valid candidates, return (None, True)."""
        # day=1, month=7, lower=2026-06-01, upper=2026-06-01
        # No date(Y,7,1) satisfies both bounds (lower > upper would fail before,
        # but here bounds are equal; 2026-07-01 > 2026-06-01 → invalid).
        result, inferred = infer_reception_year(
            day=1, month=7,
            lower=_d(2026, 6, 1),
            upper=_d(2026, 6, 1),
        )
        # 2026-07-01 > 2026-06-01 → fails upper; 2025-07-01 < 2026-06-01 lower → fails lower
        assert result is None
        assert inferred is True


# ---------------------------------------------------------------------------
# Provenance propagation (REC-C08 / REC-C09 domain integration)
# ---------------------------------------------------------------------------

class TestProvenancePropagation:
    def test_guia_year_inferred_propagates_to_contribution(self) -> None:
        """REC-C08: year_inferred=True on GuiaDeRemision → GuiaContribution carries it."""
        from decimal import Decimal  # noqa: PLC0415

        from reconciliation.domain.models import (  # noqa: PLC0415
            GuiaContribution,
            GuiaDeRemision,
            MaterialLine,
            Registro,
            ReconciliationRow,
        )
        from reconciliation.domain.reconciliation import ReconciliationService  # noqa: PLC0415

        guia = GuiaDeRemision(
            guia_id="T009-0741770",
            registro="232",
            fecha=_d(2026, 5, 28),
            year_inferred=True,
            lines=[
                MaterialLine(
                    description_raw="BARRA CORRUGADA 1/2",
                    description_canonical="BARRA CORRUGADA 1/2",
                    unidad="KG",
                    cantidad=Decimal("100"),
                    confidence=0.95,
                )
            ],
            source_pages=[4],
        )
        registro = Registro(
            numero="232",
            fecha_declarada=_d(2026, 5, 28),
            declared_lines=[
                MaterialLine(
                    description_raw="BARRA CORRUGADA 1/2",
                    description_canonical="BARRA CORRUGADA 1/2",
                    unidad="KG",
                    cantidad=Decimal("100"),
                    confidence=0.99,
                )
            ],
        )

        svc = ReconciliationService()
        rows = svc.reconcile([registro], [guia])

        assert len(rows) == 1
        row = rows[0]
        assert len(row.guias) == 1
        assert row.guias[0].year_inferred is True
        assert row.any_year_inferred is True

    def test_any_year_inferred_false_when_all_direct(self) -> None:
        """REC-C09: any_year_inferred=False when all GuiaContributions have year_inferred=False."""
        from decimal import Decimal  # noqa: PLC0415

        from reconciliation.domain.models import (  # noqa: PLC0415
            GuiaDeRemision,
            MaterialLine,
            Registro,
        )
        from reconciliation.domain.reconciliation import ReconciliationService  # noqa: PLC0415

        guia = GuiaDeRemision(
            guia_id="T009-0741770",
            registro="232",
            fecha=_d(2026, 5, 28),
            year_inferred=False,  # vision read the full date directly
            lines=[
                MaterialLine(
                    description_raw="MAT",
                    description_canonical="MAT",
                    unidad="KG",
                    cantidad=Decimal("50"),
                    confidence=0.95,
                )
            ],
            source_pages=[4],
        )
        registro = Registro(
            numero="232",
            fecha_declarada=_d(2026, 5, 28),
            declared_lines=[
                MaterialLine(
                    description_raw="MAT",
                    description_canonical="MAT",
                    unidad="KG",
                    cantidad=Decimal("50"),
                    confidence=0.99,
                )
            ],
        )

        svc = ReconciliationService()
        rows = svc.reconcile([registro], [guia])

        assert rows[0].guias[0].year_inferred is False
        assert rows[0].any_year_inferred is False


# ---------------------------------------------------------------------------
# Stamp-crop region selection (R2.1 — _prepare_vision_image)
# ---------------------------------------------------------------------------

class TestStampCropRegionSelection:
    def test_crop_enabled_returns_smaller_image(self) -> None:
        """Option A: crop returns a PNG smaller than the original full-page image."""
        import io  # noqa: PLC0415

        from PIL import Image  # noqa: PLC0415

        from reconciliation.application.config import AppConfig  # noqa: PLC0415
        from reconciliation.application.pipeline import _prepare_vision_image  # noqa: PLC0415

        # Create a 400×600 white image (simulating a rendered page)
        img = Image.new("RGB", (400, 600), color=(255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        original_bytes = buf.getvalue()

        cfg = AppConfig()
        # R7 default stamp_crop: x0=0.55, y0=0.05, x1=1.0, y1=0.45 (upper-right)
        result = _prepare_vision_image(original_bytes, cfg)

        # Result must be different (cropped region smaller)
        assert result != original_bytes

        # Verify cropped dimensions match the R7 upper-right defaults on a 400×600 image.
        # left=int(0.55*400)=220, upper=int(0.05*600)=30, right=400, lower=int(0.45*600)=270
        # → width=180, height=240
        with Image.open(io.BytesIO(result)) as cropped:
            assert cropped.width == 180   # 400 - int(0.55 * 400) = 400 - 220
            assert cropped.height == 240  # int(0.45 * 600) - int(0.05 * 600) = 270 - 30

    def test_crop_disabled_returns_original(self) -> None:
        """Option B: when stamp_crop is disabled (degenerate box), original bytes returned."""
        import io  # noqa: PLC0415

        from PIL import Image  # noqa: PLC0415

        from reconciliation.application.config import AppConfig, StampCropConfig  # noqa: PLC0415
        from reconciliation.application.pipeline import _prepare_vision_image  # noqa: PLC0415

        img = Image.new("RGB", (400, 600), color=(128, 128, 128))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        original_bytes = buf.getvalue()

        # Build config with degenerate crop (x0==x1)
        cfg = AppConfig()
        cfg.vision.stamp_crop = StampCropConfig(x0=0.0, y0=0.0, x1=0.0, y1=0.0)
        result = _prepare_vision_image(original_bytes, cfg)
        assert result == original_bytes

    def test_crop_fallback_on_invalid_image(self) -> None:
        """If PIL fails (corrupted bytes), the function falls back to original bytes."""
        from reconciliation.application.config import AppConfig  # noqa: PLC0415
        from reconciliation.application.pipeline import _prepare_vision_image  # noqa: PLC0415

        bad_bytes = b"not-a-png"
        cfg = AppConfig()
        result = _prepare_vision_image(bad_bytes, cfg)
        assert result == bad_bytes
