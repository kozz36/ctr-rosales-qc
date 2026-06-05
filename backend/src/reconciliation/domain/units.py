"""Canonical unit codes + long-form normalization (pure domain, no IO).

Single source of truth for the domain unit enum ``Literal["KG","TN","RD","Rollo"]``
and the long-form → canonical-code mapping used to normalize unit labels coming
from heterogeneous sources (SUNAT GRE long-form, vision-read table cells).

Layering: this lives in ``domain/`` so adapters (``adapters/vision/*``) may import
it WITHOUT depending on ``application/`` (hexagonal: adapters → domain, never
adapters → application).  ``application/`` (pipeline, reprocess_service) may also
import it.

CRITICAL invariant: this is a STRING canonicalization of the unit LABEL only
("TONELADAS" → "TN").  Quantities are NEVER converted — KG/TN/RD/Rollo are summed
independently and stay distinct group-key axes.
"""

from __future__ import annotations

#: The canonical domain unit codes (mirrors MaterialLine.unidad Literal).
VALID_UNITS: frozenset[str] = frozenset({"KG", "TN", "RD", "Rollo"})

#: Long-form / alias unit label → canonical domain code.  Keys are UPPERCASE;
#: callers must upper() the raw label before lookup (see normalize_unit_label).
#: Shared by the SUNAT path (reprocess_service / pipeline) and the vision path
#: (vision adapters) so a long-form label never silently drops a material line.
UNIT_LABEL_MAP: dict[str, str] = {
    # Toneladas
    "TONELADAS": "TN",
    "TONELADA": "TN",
    "TNE": "TN",
    "TN": "TN",
    # Kilogramos
    "KILOGRAMOS": "KG",
    "KILOGRAMO": "KG",
    "KILOS": "KG",
    "KILO": "KG",
    "KGM": "KG",
    "KG": "KG",
    # Rollo
    "ROLLO": "Rollo",
    "ROLLOS": "Rollo",
    "ROL": "Rollo",
    # Varilla / unidad (rod count in Peru usage)
    "VARILLA": "RD",
    "VARILLAS": "RD",
    "UNIDAD": "RD",
    "UNIDADES": "RD",
    "UND": "RD",
    "UNID": "RD",
    "RD": "RD",
}


def normalize_unit_label(raw_unit: str) -> str:
    """Normalize a raw unit label to a canonical domain code.

    Case-insensitive; trims surrounding whitespace.  Unknown labels are returned
    unchanged (the caller decides whether to keep or flag the line) — this lets
    callers distinguish "mapped to a valid code" from "still unmappable".

    Args:
        raw_unit: The raw unit string from SUNAT or vision (e.g. "TONELADAS").

    Returns:
        The canonical code ("KG"/"TN"/"RD"/"Rollo") when recognized, else the
        original (stripped) string unchanged.
    """
    key = raw_unit.strip().upper()
    return UNIT_LABEL_MAP.get(key, raw_unit.strip())
