"""Reception-ceiling predicate for guía dates (date bracket upper bound).

Physical invariant: a guía's resolved reception date can NEVER be LATER than
the authoritative Protocolo de Recepción declared date for the same Registro N°.
The Protocolo declared (digital) date is the upper authority (límite máximo).

When a guía's reception date falls AFTER the Protocolo declared date, clamp it
to the Protocolo date and flag the guía (``was_clamped=True``) for human review.
The clamp is a non-blocking advisory side-channel — it NEVER alters the group
key, status, delta, or quantity math.

This function is PURE: stdlib ``datetime`` only.  No I/O, no SDK, no imports
from application or adapter layers (hexagonal invariant).  Mirrors the style of
``domain/date_floor.py`` and ``domain/date_divergence.py``.

The ceiling is the symmetric counterpart to the delivery-floor (R9b):
  - Floor (lower): reception >= SUNAT fecha_entrega (apply_delivery_floor)
  - Ceiling (upper): reception <= Protocolo declared date (apply_reception_ceiling)

CRITICAL SEQUENCING: the R9 fecha_divergence check in ReconciliationService MUST
run on the ORIGINAL (un-clamped) guía date BEFORE this ceiling is applied.
Applying the ceiling first would mask a "guía later than Protocolo" divergence
signal.  The reconciler sequences: divergence check first, ceiling clamp second.
"""

from __future__ import annotations

from datetime import date


def apply_reception_ceiling(
    reception: date | None,
    ceiling: date | None,
) -> tuple[date | None, bool]:
    """Apply the Protocolo declared-date upper ceiling to a guía reception date.

    Rules (in priority order):

    1. ``ceiling is None`` → no ceiling data available; return
       ``(reception, False)`` unchanged.  Graceful degrade when the Protocolo
       declared date could not be read (e.g., digital parse yielded no date,
       SUNAT-only mode, no Protocolo page for this registro).
    2. ``reception is None`` → nothing to clamp; return ``(None, False)``
       unchanged.  A None reception is handled elsewhere (null-fecha guard in
       the reconciler flags it ``requires_review``); the ceiling does not apply.
    3. ``reception > ceiling`` → physical/administrative impossibility; clamp
       to ``ceiling`` and flag (``was_clamped=True``).  The guía MUST be flagged
       ``requires_review`` for human review.
    4. ``reception <= ceiling`` → valid; return ``(reception, False)`` unchanged.
       Boundary: ``reception == ceiling`` satisfies condition 4 (same-day
       Protocolo and reception; not clamped).

    Args:
        reception: The guía's resolved reception date (after year inference and
                   delivery-floor), or ``None`` when inference failed.
        ceiling:   The Protocolo declared date for the guía's Registro N°
                   (``Registro.fecha_authoritative``), or ``None`` when not
                   available (low confidence or missing Protocolo page).

    Returns:
        ``(result_date, was_clamped)`` where:
        - ``result_date`` is the clamped (or unchanged) ``date``, or ``None``
          when ``reception`` was ``None``.
        - ``was_clamped`` is ``True`` when the date was adjusted to the ceiling
          and the guía MUST be flagged ``requires_review``.
    """
    # Rule 1: no ceiling data — graceful degrade.
    if ceiling is None:
        return reception, False

    # Rule 2: reception unknown — nothing to clamp.
    if reception is None:
        return None, False

    # Rule 3: administrative impossibility — clamp to ceiling.
    if reception > ceiling:
        return ceiling, True

    # Rule 4: valid (reception <= ceiling) — unchanged.
    return reception, False
