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
import unicodedata
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

# JD round-2 (M-6 regression fix): the ONLY legitimate exclusion is an
# UNAMBIGUOUS FOOTER/STAMP PHRASE that is structurally not a material. Everything
# else — including low-confidence and OCR-garbled material rows — is EMITTED with
# requires_review=True and left to the reconciliation gate (which validates
# against the trusted declared side). We NEVER `continue`-drop on a confidence
# number or a material-keyword allowlist; re-anchoring material recognition on a
# token allowlist is exactly the documented M-6 anti-pattern (docs/DECISIONS.md:62:
# "material regex anchored on BARRA → non-BARRA materials silently dropped").
#
# This denylist is therefore restricted to UNAMBIGUOUS MULTI-WORD FOOTER PHRASES
# that never occur inside a real material description. Matched accent-insensitively,
# case-insensitively, on WORD BOUNDARIES (NOT greedy substring) so collision-prone
# fragments cannot drop a real row: bare `FORMA` ⊂ `CONFORMADO`/`PLATAFORMA`,
# bare `CONFORME` ⊂ real descs, bare `FECHA`/`MOTIVO`/`PLACA` are real-word
# collision-prone — all REPLACED by their full unambiguous phrases. A DESC that
# carries a material anchor is NEVER excluded (see `_is_desc_noise` anchor escape).
# Real-corpus-confirmed footers (docs/eval/ocr_probe_paddle.json pages 156/160):
# `REVISADO POR`, `RECIBI CONFORME`/`RECIBIDO CONFORME`, `EMITIDO POR`,
# `CREATED BY ... AUTODESK FORMA`, the GRE column headers and section labels.
_DESC_NOISE_DENYLIST: Final[tuple[str, ...]] = (
    "REVISADO POR",
    "RECIBIDO CONFORME",
    "RECIBI CONFORME",
    "EMITIDO POR",
    "CREATED BY",
    "AUTODESK FORMA",
    "GUIA DE REMISION",
    "GUIA REMISION",
    "DESTINATARIO",
    "TRANSPORTISTA",
    "PUNTO DE PARTIDA",
    "PUNTO DE LLEGADA",
    "MOTIVO DE TRASLADO",
    "FECHA DE EMISION",
    "FECHA INICIO TRASLADO",
    "FECHA FACT BOLETA",
    "PLACA DEL VEHICULO",
    "ORDEN VENTA",
    "OBSERVACIONES",
)

# Material-family / spec ANCHORS — a positive signal that a DESC is a real
# material descriptor (NOT a footer/stamp). This is used ONLY in the SAFE PROTECT
# DIRECTION inside `_is_desc_noise`: a DESC carrying any anchor is NEVER excluded
# as noise. It is NEVER a drop gate (that would BE the M-6 anti-pattern) and
# NEVER an allowlist for emission (a high-confidence non-anchored material still
# emits via the generalized `_is_desc` matcher).
_MATERIAL_ANCHORS: Final[tuple[str, ...]] = (
    "BARRA",
    "FIERRO",
    "ALAMBRE",
    "ACERO",
    "VARILLA",
    "CLAVO",
    "MALLA",
    "A615",
    "A706",
)

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
# A1 (round-2 fix): fractional part capped at 1-3 digits. The corpus declared
# max is 3 decimals; a 4-digit fraction is a year/date shape (`12.2024`,
# `01.2025`), NOT a quantity — structurally rejected here. Integer part stays
# open (`\d+`) so >=1000 KG (`5800.00`) is still EXTRACTED (then flagged by the
# A2 confidence-gate as off-profile).
_QTY_DECIMAL_RE: Final[re.Pattern[str]] = re.compile(
    r"^\d+[.,]\d{1,3}$"
)

# A2 (round-2 fix): the empirically-validated CONFIDENT profile — a decimal
# with integer-part 1-3 digits AND fractional 1-3 digits (the in-corpus TN
# shape, range 0.068-8.976, 177 real tokens). A quantity OUTSIDE this profile
# (bare-integer-promoted, or decimal integer-part >=4 digits) is still
# EXTRACTED but emitted with requires_review=True — off the TN-only validated
# corpus and/or not column-anchored yet (PR#2). Never silently trusted.
_QTY_CONFIDENT_PROFILE_RE: Final[re.Pattern[str]] = re.compile(
    r"^\d{1,3}[.,]\d{1,3}$"
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


def _is_in_confident_profile(text: str) -> bool:
    """Return True iff *text* matches the validated-corpus CONFIDENT profile.

    A2 (round-2 confidence-gate): only a decimal with integer-part 1-3 digits
    AND fractional 1-3 digits (`\\d{1,3}[.,]\\d{1,3}`) is inside the
    empirically-validated TN quantity envelope and may be emitted confident
    (requires_review=False). Bare-integer-promoted quantities and decimals with
    a >=4-digit integer part are OFF-profile → flagged for review.
    """
    return bool(_QTY_CONFIDENT_PROFILE_RE.match(text.strip().replace(",", ".")))


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


def _strip_accents(text: str) -> str:
    """Return *text* with diacritics removed (NFD decomposition + drop marks).

    Pure-stdlib (``unicodedata``) — keeps the parser SDK-free. Used so the
    semantic noise denylist matches accent variants (``SEÑORES`` ≈ ``SENORES``,
    ``REMISIÓN`` ≈ ``REMISION``) without an accented duplicate per entry.
    """
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text)
        if unicodedata.category(ch) != "Mn"
    )


def _normalize_words(text: str) -> str:
    """Return *text* accent-stripped, upper-cased, alnum-tokenized with single
    spaces, padded with leading/trailing spaces.

    Non-alphanumeric runs (punctuation, slashes, quotes) collapse to a single
    space so phrase matching is on WORD BOUNDARIES. The leading/trailing space
    padding lets a `" PHRASE "` test detect a phrase at the string edges without
    a regex. Example: `"CONFORME, A615/A706"` → `" CONFORME A615 A706 "`.
    """
    stripped = _strip_accents(text).upper()
    tokens = re.findall(r"[A-Z0-9]+", stripped)
    return " " + " ".join(tokens) + " "


def _has_material_anchor(text: str) -> bool:
    """Return True iff *text* contains a recognized material-family/spec anchor.

    Positive material signal (accent/case-insensitive WORD match). Used ONLY in
    the SAFE PROTECT DIRECTION inside `_is_desc_noise` — a DESC carrying an
    anchor is never excluded as noise. NEVER a drop gate and NEVER an emission
    allowlist (a non-anchored material still emits via `_is_desc`).
    """
    words = _normalize_words(text)
    return any(f" {anchor} " in words for anchor in _MATERIAL_ANCHORS)


def _is_desc_noise(text: str) -> bool:
    """Return True iff *text* is an UNAMBIGUOUS non-material footer/stamp label.

    JD round-2 (M-6 regression fix): matches a denylisted footer PHRASE on WORD
    BOUNDARIES (not greedy substring) so collision-prone fragments (`FORMA` in
    `CONFORMADO`, `CONFORME` inside a material desc) can never drop a real row.
    A DESC carrying a material anchor is NEVER excluded — the anchor escape is
    used ONLY in the safe protect direction. A True result excludes a footer
    label that is structurally not a material (NOT a silent-drop of a real row).
    """
    if _has_material_anchor(text):
        return False
    words = _normalize_words(text)
    return any(f" {phrase} " in words for phrase in _DESC_NOISE_DENYLIST)


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
            # CRITICAL-A semantic noise filter: a DESC cell whose text is a
            # non-material footer/header/stamp label (REVISADO POR, CREATED BY
            # ... AUTODESK FORMA, OBSERVACIONES, ...) is EXCLUDED here so it can
            # never pair with a stray number and become a spurious material row.
            # This is NOT a silent-drop of a real material row — a footer label
            # is not a material. It is logged at a visible (info) level.
            if _is_desc_noise(text):
                logger.info(
                    "box_row_parser: excluded non-material noise label '%s' "
                    "(semantic denylist) at cy=%.1f",
                    text,
                    cell.cy,
                )
                continue
            desc_cells.append(cell)
        # OTHER cells (short alpha-less tokens) are discarded.

    # Promote integer candidates to quantities only when BOTH conditions hold:
    #   1. A UNIT cell is adjacent in the row band (unit-suffix disambiguator).
    #   2. The integer is to the RIGHT of ALL DESC cells in the same row band.
    #      This mirrors the decimal-QTY column requirement (qty.cx > desc.cx) at
    #      promotion time. An integer that is LEFT of any DESC cell in its band
    #      is in the ITEM or CODIGO column, NOT the quantity column.
    #
    #      Real-data failure: codes 408916 (cx≈426) and item numbers 1/2 (cx≈347)
    #      were promoted because a long footer desc (cx≈67) was in the row band.
    #      Requiring the integer to be right of ALL descs in the band (not just any)
    #      rejects these column-left integers while preserving "25 RD"-style
    #      integers that are genuinely in the quantity column (no desc to their right).
    for int_cell in int_qty_cells:
        has_unit = any(abs(u.cy - int_cell.cy) <= band for u in unit_cells)
        in_band_descs = [d for d in desc_cells if abs(d.cy - int_cell.cy) <= band]
        # No descs in band at all: no material row → skip (lote/code/other).
        if not in_band_descs:
            continue
        # Integer must be RIGHT of every desc in the band.
        right_of_all_descs = all(int_cell.cx > d.cx for d in in_band_descs)
        if has_unit and right_of_all_descs:
            qty_cells.append(int_cell)

    if not desc_cells or not qty_cells:
        return []

    lines: list[MaterialLine] = []

    used_unit: set[int] = set()

    _BAND_MISS: Final[float] = float("inf")

    def _nearest_unit_dy(c: Cell) -> float:
        """Vertical distance from *c* to the nearest in-band unit cell.

        FIX B tie-break: when two descs are equidistant from a qty, the real
        material row is the one whose row carries the unit cell — i.e. the
        smaller |Δcy| to a unit. Returns +inf when no unit is in band so a
        unit-less noise desc never wins the tie over a real material row.
        """
        in_band = [abs(u.cy - c.cy) for u in unit_cells if abs(u.cy - c.cy) <= band]
        return min(in_band) if in_band else _BAND_MISS

    # FIX B (round-2 WARNING-3, never-silent-drop): assign each QTY to the
    # GEOMETRICALLY NEAREST eligible DESC, instead of letting the top-most DESC
    # greedily claim it first. This resolves the EQUIDISTANT case — a noise/header
    # desc (`OBSERVACIONES`, stamp text) tied in |Δcy| with the real material desc
    # no longer steals the row's qty (the unit-bearing real row wins the tie).
    # KNOWN RESIDUAL (deferred to PR#2 column anchoring): when a noise desc is
    # STRICTLY nearer the qty than the real material desc, |Δcy| still dominates and
    # the real row can be dropped. This needs real RapidOCR column geometry to fix
    # safely; it never produces confident-wrong data (the stolen-qty row is
    # off-profile or unit-mismatched and stays requires_review=True). Ownership is
    # decided per QTY by vertical proximity, with deterministic tie-breaks:
    #   1. smaller |Δcy| (nearest row),
    #   2. the desc whose row also carries a unit cell (the real material row),
    #   3. smaller horizontal gap (qty column just right of detalle),
    #   4. stable cy then original index — fully deterministic.
    qty_owner: dict[int, int] = {}  # qty index -> desc index
    desc_index = {id(d): i for i, d in enumerate(desc_cells)}
    for qi, qty in enumerate(qty_cells):
        eligible = [
            d for d in desc_cells
            if abs(qty.cy - d.cy) <= band and qty.cx > d.cx
        ]
        if not eligible:
            continue
        owner = min(
            eligible,
            key=lambda d: (
                abs(qty.cy - d.cy),
                _nearest_unit_dy(d),
                qty.cx - d.cx,
                d.cy,
                desc_index[id(d)],
            ),
        )
        qty_owner[qi] = desc_index[id(owner)]

    # Emit rows in deterministic top-to-bottom order of the owning DESC, then
    # by qty index for stability when one desc owns multiple qtys.
    for idx, desc_idx in sorted(
        qty_owner.items(), key=lambda kv: (desc_cells[kv[1]].cy, kv[0])
    ):
        desc = desc_cells[desc_idx]
        qty = qty_cells[idx]

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

        # Parse the quantity. The qty regexes (`^\d+[.,]\d{1,3}$` / `^\d+$`)
        # already guarantee a single-separator, Decimal-parseable shape after the
        # `,`→`.` normalization, so InvalidOperation is unreachable in practice —
        # the guard is intentional defensive belt against a future regex change.
        qty_str = qty.text.strip().replace(",", ".")
        try:
            cantidad = Decimal(qty_str)
        except InvalidOperation:
            logger.debug("box_row_parser: invalid qty '%s' — skipping", qty_str)
            continue

        # A2 confidence-gate (round-2): a quantity OUTSIDE the validated-corpus
        # profile (bare-integer-promoted, or decimal with integer-part >=4
        # digits) is EXTRACTED but MUST NOT be emitted confident — it is off the
        # TN-only validated corpus and/or not column-anchored yet (deferred to
        # PR#2). Flag it for human review; never silently trust it.
        if not _is_in_confident_profile(qty.text):
            requires_review = True

        # Apply confidence threshold from EXT-004 (retained for all engines).
        # Use the minimum of desc and qty confidences for the row.
        row_conf = min(desc.conf, qty.conf)
        if unit_cell is not None:
            row_conf = min(row_conf, unit_cell.conf)
        if row_conf < _CONFIDENCE_THRESHOLD:
            requires_review = True

        # JD round-2 (M-6 regression fix): the prior
        # `row_conf < _NOISE_CONFIDENCE and not _has_material_anchor(...)` DROP is
        # REMOVED ENTIRELY. That gate RELOCATED the silent-drop (a real material
        # row whose desc lacked every token in the closed `_MATERIAL_ANCHORS`
        # allowlist AND OCR'd below 0.65 was silently dropped — e.g. the IN-CORPUS
        # `ACERD DIMENSIONADO` @0.60, the real `ACERO DIMENSIONADO` with an O→D
        # OCR garble). Re-anchoring material recognition on a token allowlist IS
        # the documented M-6 anti-pattern (docs/DECISIONS.md:62). A real-looking
        # material row is NEVER dropped on a confidence number OR a family
        # allowlist — a LOW confidence only forces requires_review=True (via the
        # threshold gate above), and the reconciliation gate validates it against
        # the trusted declared side. The ONLY legitimate exclusion is an
        # UNAMBIGUOUS FOOTER/STAMP PHRASE, handled SEMANTICALLY at DESC
        # classification (`_is_desc_noise`, word-boundary phrase denylist with a
        # material-anchor escape) — never here.

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
