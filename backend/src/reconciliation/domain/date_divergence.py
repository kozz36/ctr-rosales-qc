"""Pure domain fecha-divergence predicate (r9 / ADR-3).

Sibling to ``date_inference.py``.  No I/O, no SDK, no adapter imports.

Predicate: compare day + month only (tolerance 0).  Year comparison is
explicitly excluded — year-inference asymmetry between the declared and guía
sides (#2753: declared has ``lower=None``, guía has the SUNAT lower bound)
causes spurious year divergence; day-month is the trusted signal.

Null safety (FDR-005, FDR-006): if EITHER side is None the pair cannot be
validated → NOT divergent.  A null baseline must never paint all guías red.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

DivergenceReason = Literal["fecha_divergence"]


@dataclass(frozen=True)
class DivergenceResult:
    """Outcome of a single (declared, guía) date-divergence check."""

    diverges: bool
    reason: DivergenceReason | None
    declared_fecha: date | None
    guia_fecha: date | None


def check_fecha_divergence(
    declared_fecha: date | None,
    guia_fecha: date | None,
) -> DivergenceResult:
    """Return the divergence result for a single (declared, guía) date pair.

    Compares ``(month, day)`` only with tolerance 0 (strict equality).
    Either side ``None`` → ``diverges=False`` (cannot validate; null-safe).
    """
    if declared_fecha is None or guia_fecha is None:
        return DivergenceResult(False, None, declared_fecha, guia_fecha)

    diverges = (declared_fecha.month, declared_fecha.day) != (
        guia_fecha.month,
        guia_fecha.day,
    )
    return DivergenceResult(
        diverges=diverges,
        reason="fecha_divergence" if diverges else None,
        declared_fecha=declared_fecha,
        guia_fecha=guia_fecha,
    )
