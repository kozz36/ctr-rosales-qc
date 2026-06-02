"""Bounded year inference for handwritten reception dates (D5 / EXT-021).

Local vision models reliably read DAY-MONTH but not YEAR from the handwritten
"Recibí conforme" stamp.  This pure domain function reconstructs the full date
from two-sided bounds:

    delivery_GRE_date <= date(Y, MM, DD) <= reference_date

where:
  - ``lower`` = printed GRE delivery date (from OCR on the guía header), or
    ``None`` when OCR did not find it (SUNAT fetch is in R3, off by default).
  - ``upper`` = PDF document/export date, or run timestamp when the document
    date is unknown.

Rules (spec EXT-021 / engram #2748):
  1. If ``lower`` is provided: collect all years Y where
     ``date(Y, MM, DD)`` is in [lower, upper].  The most recent valid year is
     chosen (uniqueness is expected for real-world documents; if ambiguous,
     prefer the latest).
  2. If ``lower`` is absent (None): collect years Y where
     ``date(Y, MM, DD) <= upper``.  Again, pick the most recent.
  3. If no valid year exists within the search window (±5 years around upper):
     return ``(None, True)`` — caller must flag for human review.
  4. A returned year always yields ``year_inferred = True``.

The function is PURE: no I/O, no imports outside stdlib, no side effects.
Import from application or adapter layers is PROHIBITED (hexagonal invariant).
"""

from __future__ import annotations

from datetime import date


def infer_reception_year(
    day: int,
    month: int,
    lower: date | None,
    upper: date,
) -> tuple[date | None, bool]:
    """Infer the full reception date from day/month and bounding dates.

    The function tries candidate years in a ±5-year window relative to
    ``upper``.  This window is wide enough to handle misfiled documents but
    narrow enough to avoid implausible dates.

    Args:
        day:    Day component from vision (1–31).
        month:  Month component from vision (1–12).
        lower:  Lower bound: GRE delivery date from OCR, or ``None`` when
                unavailable.  When provided, the returned date MUST satisfy
                ``returned_date >= lower``.
        upper:  Upper bound: PDF document/export date (or run timestamp).
                The returned date MUST satisfy ``returned_date <= upper``.

    Returns:
        ``(full_date, year_inferred)`` where:
        - ``full_date`` is the reconstructed ``date`` object, or ``None``
          when no candidate year satisfies the bounds.
        - ``year_inferred`` is always ``True`` when the function is called
          (the caller already knows the year was absent from vision output).

    Raises:
        ValueError: if ``day``, ``month``, or ``lower > upper`` would produce
                    an impossible date.  Callers should guard against invalid
                    day/month values from raw vision output before calling.
    """
    if lower is not None and lower > upper:
        # Degenerate: bounds cross — return None, flag for review.
        return None, True

    # Search window: up to 5 years before upper's year, and up to 1 year ahead
    # (to be safe with docs exported at year end).  We clamp to the upper bound
    # so we never return a future date.
    upper_year = upper.year
    search_years = range(upper_year - 5, upper_year + 2)

    candidates: list[date] = []
    for year in search_years:
        try:
            candidate = date(year, month, day)
        except ValueError:
            # e.g. day=29, month=2 on a non-leap year
            continue

        # Must not exceed the upper bound
        if candidate > upper:
            continue

        # Must satisfy the lower bound when provided
        if lower is not None and candidate < lower:
            continue

        candidates.append(candidate)

    if not candidates:
        return None, True

    # Pick the most recent valid candidate (EXT-021: "most recent year")
    chosen = max(candidates)
    return chosen, True
