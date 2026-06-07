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
   regex patterns (_QTY_DECIMAL_RE / _QTY_INTEGER_RE, _UNIT_CELL_RE).
   The DESC classifier is the remainder
   (cells that have a >=3-char alphabetic run) to avoid the corpus-specific
   keyword allowlist from the PoC.
3. For each DESC cell, find the nearest QTY cell satisfying:
   - |cy_desc - cy_qty| <= band_px  (row-band, DPI-scaled)
   - cx_qty > cx_desc               (quantity column is to the RIGHT)
4. For each paired DESC+QTY, find the UNIT cell on the same row band:
   - |cy_desc - cy_unit| <= band_px
   - cx_unit > cx_qty               (unit column is furthest right) — preferred,
     yields a CONFIDENT line
   - Relaxed fallback: any UNIT cell within band regardless of column order →
     positional evidence violated → requires_review=True (never confident).
   - A unit is only claimed by the desc that OWNS it (band-nearest desc), so a
     unit is never stolen across rows packed tighter than the band.
5. Normalise TNE → TN (label only; cantidad is NEVER changed).
6. Emit MaterialLine(description_raw, description_canonical, unidad, cantidad,
   confidence, requires_review).

**Quantity contract (corrected — JD CRITICAL)**:
A cell is a QUANTITY iff:
  (a) it matches the decimal shape ``^\\d+[.,]\\d+$`` (one-or-more integer
      digits, one-or-more fractional digits — NO artificial caps). This admits
      ``2.5`` (one fractional digit, real declared data), ``0.008``, ``7.163``,
      ``5800.00`` (>=1000), ``1234.56`` — aligned with the declared-side
      extractor (``digital_text_extractor._MATERIAL_LINE_RE``); OR
  (b) it is a BARE INTEGER ``^\\d+$`` AND has an adjacent UNIT cell in its row
      band (the unit-suffix disambiguator) — admits ``25 RD`` / ``5800 KG``.

**Incidental-number guard** (still holds): a bare integer with NO adjacent unit
is NOT a quantity. This excludes:
- Line-item / lote numbers and guía codes: ``1``, ``119``, ``408916``.
- Diameter leads: ``1"``, ``3/8"`` (non-digit chars → DESC classification).

**Decimal separator** (evidence-backed, 177 real qty tokens, full PDF): NO
thousands separators exist anywhere; ``.`` is always the decimal separator, so
a ``,`` is treated as a DECIMAL separator (``replace(",", ".")``). A malformed
token is dropped-with-log, never raised.

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

# QTY (decimal shape): one-or-more integer digits, a decimal separator (. or ,),
# one-or-more fractional digits — NO artificial caps. This admits 2.5 (one
# fractional digit, real declared data pages 378-379), 0.008, 7.163, 5800.00
# (>=1000 KG), 1234.56. The declared-side extractor uses the same any-digit
# shape (digital_text_extractor._MATERIAL_LINE_RE: (\d+(?:[.,]\d+)?)); the OCR
# side MUST align so quantities are never silently dropped.
#
# Empirical (177 real qty tokens, full 493-page PDF): NO thousands separators
# anywhere; `.` is always the decimal separator, so a `,` is treated as a
# DECIMAL separator (replace ","->".") — evidence-backed, not an assumption.
#
# Incidental-number guard preserved: a BARE integer (no decimal separator) is
# NOT matched here. It is only accepted as a quantity when it has an adjacent
# UNIT cell in its row band (the unit-suffix disambiguator, _is_qty_integer).
# This keeps lote 119 / guía code 408916 / diameter lead 1" out, while
# admitting 25 RD / 5800 KG.
_QTY_DECIMAL_RE: Final[re.Pattern[str]] = re.compile(
    r"^\d+[.,]\d+$"
)

# Bare integer (no decimal separator). Only treated as a QTY when a UNIT cell
# is adjacent in the row band (resolved geometrically in parse_box_rows).
_QTY_INTEGER_RE: Final[re.Pattern[str]] = re.compile(
    r"^\d+$"
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
# pure-numeric cells not matched by the qty patterns.
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


def _is_qty_decimal(text: str) -> bool:
    """Return True iff *text* is a decimal-shape quantity (any digit count).

    Admits 2.5, 0.008, 5800.00, 1234.56. A bare integer is NOT a decimal
    quantity — see :func:`_is_qty_integer` for the unit-adjacent integer path.
    """
    return bool(_QTY_DECIMAL_RE.match(text.strip()))


def _is_qty_integer(text: str) -> bool:
    """Return True iff *text* is a BARE integer (no decimal separator).

    A bare integer is only a QUANTITY when an adjacent UNIT cell disambiguates
    it (resolved geometrically in :func:`parse_box_rows`). Standalone, it is an
    incidental number (lote 119, guía code 408916) and is NOT a quantity.
    """
    return bool(_QTY_INTEGER_RE.match(text.strip()))


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
    #   - decimal qty cells: unconditional quantities (2.5, 0.008, 5800.00).
    #   - integer qty cells: CANDIDATE quantities — only promoted when a UNIT
    #     cell is adjacent in their row band (the unit-suffix disambiguator).
    #   - unit cells / desc cells as before.
    desc_cells: list[Cell] = []
    qty_cells: list[Cell] = []
    int_qty_cells: list[Cell] = []
    unit_cells: list[Cell] = []

    for cell in cells:
        text = cell.text.strip()
        if not text:
            continue
        if _is_qty_decimal(text):
            qty_cells.append(cell)
        elif _is_unit(text):
            unit_cells.append(cell)
        elif _is_qty_integer(text):
            int_qty_cells.append(cell)  # provisional; needs adjacent unit
        elif _is_desc(text):
            desc_cells.append(cell)
        # OTHER cells (short alpha-less tokens) are discarded.

    # Promote integer candidates to quantities only when a unit cell sits in
    # their row band (unit-suffix disambiguator). Without an adjacent unit an
    # integer stays an incidental number (lote 119, code 408916) and is dropped.
    for int_cell in int_qty_cells:
        if any(abs(u.cy - int_cell.cy) <= band for u in unit_cells):
            qty_cells.append(int_cell)

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

        # Resolve unit. Preferred column order is DESC | QTY | UNIT, so the
        # unit cell should be in the same row band AND right of the qty column.
        # A unit found there is positional evidence → confident.
        #
        # Cross-row-theft guard: a unit cell is only eligible for THIS desc if
        # this desc is the band-nearest desc to that unit. Otherwise the unit
        # belongs to another row and must not be stolen by a greedy
        # nearest-across-bands pick (rows packed tighter than the band).
        def _owns(u: Cell, _desc: Cell = desc) -> bool:
            my_dy = abs(u.cy - _desc.cy)
            return all(
                my_dy <= abs(u.cy - other.cy)
                for other in desc_cells
                if other is not _desc
            )

        unit_cell: Cell | None = None
        unit_cell_idx: int | None = None
        relaxed = False
        unit_candidates_right = [
            (i, u) for i, u in enumerate(unit_cells)
            if abs(u.cy - desc.cy) <= band
            and u.cx > qty.cx
            and i not in used_unit
            and _owns(u)
        ]
        if unit_candidates_right:
            unit_cell_idx, unit_cell = min(
                unit_candidates_right, key=lambda iu: (abs(iu[1].cy - desc.cy), iu[1].cx - qty.cx)
            )
        else:
            # Relaxed fallback: any UNIT cell within the band that this desc
            # owns, regardless of column order. Positional evidence is violated
            # → the resulting line is NOT confident (requires_review=True).
            unit_candidates_any = [
                (i, u) for i, u in enumerate(unit_cells)
                if abs(u.cy - desc.cy) <= band and i not in used_unit and _owns(u)
            ]
            if unit_candidates_any:
                unit_cell_idx, unit_cell = min(
                    unit_candidates_any, key=lambda iu: (abs(iu[1].cy - desc.cy), iu[1].cx)
                )
                relaxed = True

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
            # A relaxed (out-of-column) unit pick violated positional evidence
            # → must NOT be a confident line. The preferred-column path is
            # confident; the no-unit path above already flags review.
            requires_review = relaxed
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
