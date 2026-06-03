"""Delivery-floor predicate for guía reception dates (R9b).

Physical invariant: a guía's resolved reception date can NEVER be earlier than
the guía's SUNAT delivery date (``fecha_entrega``).  Goods cannot be received
before they are delivered.

When the resolved reception date falls before ``fecha_entrega`` (or year
inference cannot place day-month at or after it, returning ``None``), fall back
to ``fecha_entrega`` and raise a non-blocking verify WARNING
(``was_floored=True``).  The floored guía is always flagged ``requires_review``
for human review — never auto-corrected beyond the physical floor.

This function is PURE: stdlib ``datetime`` only.  No I/O, no SDK, no imports
from application or adapter layers (hexagonal invariant).  Mirrors the style of
``domain/date_inference.py`` and ``domain/date_divergence.py``.

Only active when SUNAT is enabled — ``fecha_entrega`` comes from
``OfficialGre.fecha_entrega`` via ``sunat_fetch_map``.  When ``fecha_entrega``
is ``None`` (SUNAT disabled / not fetched), the function degrades gracefully by
returning the original reception date unchanged.
"""

from __future__ import annotations

from datetime import date


def apply_delivery_floor(
    reception: date | None,
    fecha_entrega: date | None,
) -> tuple[date | None, bool]:
    """Apply the SUNAT delivery-date lower floor to a resolved reception date.

    Rules (in priority order):

    1. ``fecha_entrega is None`` → no floor data available; return
       ``(reception, False)`` unchanged.  This is the graceful-degrade path
       when SUNAT is disabled (off by default — air-gap).
    2. ``reception is None`` → vision/inference could not produce a date; fall
       back to ``fecha_entrega`` as the best available date and flag the guía
       (``was_floored=True``) for human review.
    3. ``reception < fecha_entrega`` → physical impossibility; floor to
       ``fecha_entrega`` and flag (``was_floored=True``).
    4. ``reception >= fecha_entrega`` → valid; return ``(reception, False)``
       unchanged.

    Boundary: ``reception == fecha_entrega`` satisfies condition 4 (valid
    same-day delivery and receipt; not floored).

    Args:
        reception:     The resolved reception date after year inference, or
                       ``None`` when inference failed.
        fecha_entrega: The guía's SUNAT delivery date (``OfficialGre.fecha_entrega``),
                       or ``None`` when SUNAT data is not available.

    Returns:
        ``(result_date, was_floored)`` where:
        - ``result_date`` is the floored (or unchanged) ``date``, or ``None``
          when both inputs are ``None``.
        - ``was_floored`` is ``True`` when the date was adjusted to the floor
          and the guía MUST be flagged ``requires_review``.
    """
    # Rule 1: no floor data — graceful degrade.
    if fecha_entrega is None:
        return reception, False

    # Rule 2: reception unknown — floor to delivery date.
    if reception is None:
        return fecha_entrega, True

    # Rule 3: physical impossibility — floor.
    # Defense-in-depth for the standalone pure-domain contract. This branch is
    # UNREACHABLE through ``_stage_normalize_dates``: that stage calls
    # ``infer_reception_year`` with the same ``lower=fecha_entrega``, which
    # pre-filters candidates to ``>= lower``, so the resolved ``reception`` is
    # never ``< fecha_entrega`` there (the floor activates only via Rule 2 when
    # inference returns None). Kept and unit-tested in isolation regardless.
    if reception < fecha_entrega:
        return fecha_entrega, True

    # Rule 4: valid — unchanged.
    return reception, False
