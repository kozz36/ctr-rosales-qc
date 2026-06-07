"""Pure box-row parser for columnar OCR output (EXT-029).

Converts a list of raw OCR cells (polygon, text, confidence) produced by
any OCR engine into a list of :class:`~reconciliation.domain.models.MaterialLine`
objects using geometric row-band grouping.

**Design rationale (Humble Object)**:
The parser is placed in the adapter package because box geometry and DPI are
OCR output-shape concerns, not domain invariants.  However, it imports ZERO OCR
SDK symbols — only stdlib + domain.models + domain.normalizer — so it is
importable and testable without rapidocr or onnxruntime installed.

**Algorithm (Design §2.1, §4, §5)**:
1. Compute centroid (cx, cy) per cell from the raw polygon.
2. Classify each cell into DESC / QTY / UNIT / OTHER using positive-only
   regex patterns (_QTY_RE, _UNIT_RE).  The DESC classifier is the remainder
   (cells that have a >=3-char alphabetic run) to avoid the corpus-specific
   keyword allowlist from the PoC.
3. For each DESC cell, find the nearest QTY cell satisfying:
   - |cy_desc - cy_qty| <= band_px  (row-band, DPI-scaled)
   - cx_qty > cx_desc               (quantity column is to the RIGHT)
4. For each paired DESC+QTY, find the UNIT cell on the same row band:
   - |cy_desc - cy_unit| <= band_px
   - cx_unit > cx_qty               (unit column is furthest right) — preferred
   - Fallback: scan any UNIT cell within band regardless of column order
5. Normalise TNE → TN (label only; cantidad is NEVER changed).
6. Emit MaterialLine(description_raw, description_canonical, unidad, cantidad,
   confidence, requires_review).

**Incidental-number guard**:
The QTY pattern _QTY_RE requires a mandatory decimal fraction (\\d{1,3}[.,]\\d{2,3}).
This excludes:
- Single/multi digit integers without fractions: ``1``, ``119``, ``408916``
- Diameter leads that begin with digits: ``1"``  (no fraction → not matched)
- Any cell whose text contains non-digit characters: text classification as
  DESC handles those automatically.

**Units (domain invariant)**:
KG / TN / RD / Rollo are summed independently by the reconciliation engine.
TNE → TN is a label normalization of a SUNAT-printed abbreviation for Tonelada.
The quantity value is NEVER touched.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

from reconciliation.domain.models import MaterialLine
from reconciliation.domain.normalizer import MaterialNormalizer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONFIDENCE_THRESHOLD: Final[float] = 0.85

# QTY: mandatory decimal fraction — integers, codes, and diameter leads excluded.
# Pattern: 1–3 leading digits, decimal separator (. or ,), 2–3 fractional digits.
# This guards lote 119 (no fraction), 408916 (no fraction), 1" (no fraction).
_QTY_RE: Final[re.Pattern[str]] = re.compile(
    r"^\d{1,3}[.,]\d{2,3}$"
)

# UNIT: exact match for recognised unit tokens (case-insensitive).
# TNE is the SUNAT abbreviation for Tonelada (metric ton) — normalised → TN.
_UNIT_CELL_RE: Final[re.Pattern[str]] = re.compile(
    r"^(TNE|TN|KG|RD|Rollo)$",
    re.IGNORECASE,
)

# DESC: a cell is a descriptor if it contains a run of >=3 consecutive letters.
# This includes all material family names (BARRA, FIERRO, ALAMBRE, ACERO, VARILLA,
# diameter notation like 3/8", A615/A706, lote labels, etc.) while excluding
# pure-numeric cells not matched by _QTY_RE.
_DESC_ALPHA_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-záéíóúÁÉÍÓÚñÑ]{3,}")

# TNE normalisation map
_UNIT_NORMALISE: Final[dict[str, str]] = {
    "TNE": "TN",
    "TN": "TN",
    "KG": "KG",
    "RD": "RD",
    "ROLLO": "Rollo",
}

_NORMALIZER: Final[MaterialNormalizer] = MaterialNormalizer()


# ---------------------------------------------------------------------------
# Public data contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    """A single OCR-detected text cell.

    Attributes:
        polygon: List of (x, y) corner points from the OCR engine.
                 Used to compute the centroid (cx, cy).
        text:    Recognised text string.
        conf:    Recognition confidence in [0, 1].
        cx:      Pre-computed centroid x (set by the adapter; parser trusts it).
        cy:      Pre-computed centroid y (set by the adapter; parser trusts it).
    """

    polygon: list[tuple[float, float]]
    text: str
    conf: float
    cx: float
    cy: float


# ---------------------------------------------------------------------------
# Cell classification helpers
# ---------------------------------------------------------------------------


def _is_qty(text: str) -> bool:
    """Return True iff *text* matches the QTY pattern (mandatory fraction)."""
    return bool(_QTY_RE.match(text.strip()))


def _is_unit(text: str) -> bool:
    """Return True iff *text* is a recognised unit token."""
    return bool(_UNIT_CELL_RE.match(text.strip()))


def _is_desc(text: str) -> bool:
    """Return True iff *text* contains a >=3-letter run (descriptor heuristic).

    This is a POSITIVE classifier for the remainder cells after QTY/UNIT are
    identified.  It accepts non-rebar materials (FIERRO, ALAMBRE, ACERO,
    VARILLA) and any cell with alphabetic content, while rejecting pure integers,
    codes, and diameter-only tokens.
    """
    return bool(_DESC_ALPHA_RE.search(text))


def _normalise_unit(raw: str) -> str | None:
    """Normalise a raw unit token to the canonical domain literal.

    Returns None if the token is not a recognised unit.
    TNE → TN (label normalisation; no numeric conversion).
    """
    key = raw.strip().upper()
    return _UNIT_NORMALISE.get(key)


# ---------------------------------------------------------------------------
# DPI formula
# ---------------------------------------------------------------------------


def _band_px(dpi: int) -> int:
    """Return the row-band height in pixels for a given *dpi*.

    Formula (Design §4, EXT-029/S029g):
        band_px = round(40 * dpi / 200)

    This is linear because the row-band is a physical page distance (a point
    in millimetres); the pixel count scales proportionally with DPI.
    Baseline: 40 px at 200 DPI.
    """
    return round(40 * dpi / 200)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_box_rows(cells: list[Cell], dpi: int) -> list[MaterialLine]:
    """Parse OCR cells into material lines using geometric row-band grouping.

    This is a pure function: no I/O, no side effects, no SDK imports.
    All cell-geometry concerns (centroid computation, DPI scaling) are
    handled here so the adapter (rapid_table.py) only needs to build Cell
    objects from raw engine output.

    Args:
        cells: OCR cells, each carrying pre-computed centroid (cx, cy).
        dpi:   Render DPI of the page image (default 200 at pipeline.py:813).

    Returns:
        List of :class:`~reconciliation.domain.models.MaterialLine`, one per
        recognised material row.  Empty list when no valid rows are found.
    """
    if not cells:
        return []

    band = _band_px(dpi)

    # Partition cells by role.
    desc_cells: list[Cell] = []
    qty_cells: list[Cell] = []
    unit_cells: list[Cell] = []

    for cell in cells:
        text = cell.text.strip()
        if not text:
            continue
        if _is_qty(text):
            qty_cells.append(cell)
        elif _is_unit(text):
            unit_cells.append(cell)
        elif _is_desc(text):
            desc_cells.append(cell)
        # OTHER cells (short tokens, codes without fractions) are discarded.

    if not desc_cells or not qty_cells:
        return []

    lines: list[MaterialLine] = []

    used_qty: set[int] = set()
    used_unit: set[int] = set()

    # Sort DESC cells by cy so we process rows top-to-bottom, giving each
    # DESC cell first claim on the nearest QTY in its band.
    for desc in sorted(desc_cells, key=lambda c: c.cy):
        # Find the nearest QTY cell in the row band, to the right of desc,
        # and not already claimed by a previous DESC row.
        candidates = [
            (i, q) for i, q in enumerate(qty_cells)
            if abs(q.cy - desc.cy) <= band and q.cx > desc.cx and i not in used_qty
        ]
        if not candidates:
            continue

        # Nearest by vertical proximity first (same row), then by horizontal
        # distance (prefer the closer column when vertical ties exist).
        idx, qty = min(candidates, key=lambda iq: (abs(iq[1].cy - desc.cy), iq[1].cx - desc.cx))
        used_qty.add(idx)

        # Resolve unit: look for a UNIT cell in the same row band, to the
        # right of the QTY cell (preferred column order: DESC | QTY | UNIT).
        unit_cell: Cell | None = None
        unit_cell_idx: int | None = None
        unit_candidates_right = [
            (i, u) for i, u in enumerate(unit_cells)
            if abs(u.cy - desc.cy) <= band and u.cx > qty.cx and i not in used_unit
        ]
        if unit_candidates_right:
            unit_cell_idx, unit_cell = min(
                unit_candidates_right, key=lambda iu: (abs(iu[1].cy - desc.cy), iu[1].cx - qty.cx)
            )
        else:
            # Fallback: any UNIT cell within the row band (relaxed column order).
            unit_candidates_any = [
                (i, u) for i, u in enumerate(unit_cells)
                if abs(u.cy - desc.cy) <= band and i not in used_unit
            ]
            if unit_candidates_any:
                unit_cell_idx, unit_cell = min(
                    unit_candidates_any, key=lambda iu: abs(iu[1].cy - desc.cy)
                )

        if unit_cell is None:
            # No unit resolved — flag for review but emit the row with
            # requires_review=True rather than silently dropping it.
            # Use TN as a placeholder (lowest-impact assumption); the review
            # flag ensures this is never auto-accepted.
            logger.debug(
                "box_row_parser: no unit cell found for desc '%s' at cy=%.1f — "
                "emitting with requires_review=True",
                desc.text,
                desc.cy,
            )
            unit_literal = "TN"
            requires_review = True
        else:
            normalised = _normalise_unit(unit_cell.text)
            if normalised is None:
                logger.debug(
                    "box_row_parser: unrecognised unit token '%s' — skipping row",
                    unit_cell.text,
                )
                continue
            unit_literal = normalised
            requires_review = False
            if unit_cell_idx is not None:
                used_unit.add(unit_cell_idx)

        # Parse the quantity.
        qty_str = qty.text.strip().replace(",", ".")
        try:
            cantidad = Decimal(qty_str)
        except InvalidOperation:
            logger.debug("box_row_parser: invalid qty '%s' — skipping", qty_str)
            continue

        # Apply confidence threshold from EXT-004 (retained for all engines).
        # Use the minimum of desc and qty confidences for the row.
        row_conf = min(desc.conf, qty.conf)
        if unit_cell is not None:
            row_conf = min(row_conf, unit_cell.conf)
        if row_conf < _CONFIDENCE_THRESHOLD:
            requires_review = True

        desc_raw = desc.text.strip()
        lines.append(
            MaterialLine(
                description_raw=desc_raw,
                description_canonical=_NORMALIZER.canonicalize(desc_raw),
                unidad=unit_literal,  # type: ignore[arg-type]
                cantidad=cantidad,
                confidence=row_conf,
                requires_review=requires_review,
            )
        )

    return lines


def count_valid_rows(cells: list[Cell], dpi: int) -> int:
    """Return the number of valid material rows that would be parsed.

    This is the orientation oracle used by :class:`RapidOCRAdapter`'s
    retry loop (Design §2.1, §6).  It is defined as::

        count_valid_rows(cells, dpi) == len(parse_box_rows(cells, dpi))

    A separate function is provided so callers can compute the count without
    materialising the full list in orientation-scoring contexts.
    """
    return len(parse_box_rows(cells, dpi))
