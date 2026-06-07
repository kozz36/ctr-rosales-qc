"""Unit tests for the pure box-row parser (box_row_parser.py).

STRICT TDD: these tests were written BEFORE the implementation.
All tests must FAIL on import of a non-existent module, then pass
once box_row_parser.py is implemented.

Covered scenarios (EXT-029, EXT-032 Phase 1):
- S029g — DPI-scaled row band: band(200)=40, band(300)=60, band(100)=20
- S029a — Multi-row pairing: 4 rows at distinct Y positions, never cross-band
- S029b — Unit cell association same band: TNE→TN; KG/RD/Rollo unchanged
- S029b edge — Unit fallback row-scan when no geometrically-associated unit cell
- S029b + EXT-032 — TNE→TN is label-only; cantidad never touched
- S029d — Incidental numbers (lote, diameter) never classified as QTY
- S029e — Generalized DESC matcher: non-rebar materials recognised
- S029c — Columnar-table input (what _LINE_RE would miss) yields ≥1 row
- S029f + S032c — SDK purity: rapidocr/onnxruntime/paddleocr not in sys.modules
- orientation oracle — count_valid_rows == len(parse_box_rows)
- geometry guard — QTY left of DESC is never associated
- edge case — empty input returns []
"""

from __future__ import annotations

import sys
from decimal import Decimal

import pytest

from reconciliation.adapters.ocr.box_row_parser import Cell, count_valid_rows, parse_box_rows

# ---------------------------------------------------------------------------
# Helpers — build synthetic Cell lists
# ---------------------------------------------------------------------------

_POLY_STUB: list[tuple[float, float]] = [(0, 0), (10, 0), (10, 10), (0, 10)]


def _cell(text: str, cx: float, cy: float, conf: float = 0.95) -> Cell:
    """Build a Cell with a unit polygon; only cx/cy matter for the parser."""
    return Cell(
        polygon=_POLY_STUB,
        text=text,
        conf=conf,
        cx=cx,
        cy=cy,
    )


# ---------------------------------------------------------------------------
# 1.1.1  DPI-scaled band — EXT-029/S029g, Design §4
# ---------------------------------------------------------------------------


class TestBandPxScaling:
    """band_px = round(40 * dpi / 200) scales linearly with DPI."""

    def test_band_200dpi_is_40(self) -> None:
        # 4 cells on the same row; qty right of desc; no UNIT cell
        cells = [
            _cell("BARRA A615 3/8\"", cx=100, cy=150),
            _cell("0.136", cx=250, cy=150),
        ]
        # With band=40 both cells are within 40 px of each other → 1 row
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1

    def test_band_300dpi_is_60(self) -> None:
        # Cells 55 px apart in Y — inside band(300)=60 but outside band(200)=40
        cells = [
            _cell("BARRA A615 3/8\"", cx=100, cy=100),
            _cell("0.136", cx=250, cy=154),  # delta_y=54 < 60
        ]
        rows_300 = parse_box_rows(cells, dpi=300)
        rows_200 = parse_box_rows(cells, dpi=200)
        assert len(rows_300) == 1
        assert len(rows_200) == 0

    def test_band_150dpi_is_30(self) -> None:
        # band(150) = round(40*150/200) = 30
        cells = [
            _cell("BARRA A615 3/8\"", cx=100, cy=100),
            _cell("0.136", cx=250, cy=128),  # delta_y=28 < 30
        ]
        rows_150 = parse_box_rows(cells, dpi=150)
        rows_100 = parse_box_rows(cells, dpi=100)  # band=20, delta_y=28 > 20
        assert len(rows_150) == 1
        assert len(rows_100) == 0

    def test_band_100dpi_is_20(self) -> None:
        cells = [
            _cell("BARRA A615 3/8\"", cx=100, cy=100),
            _cell("0.041", cx=250, cy=118),  # delta_y=18 < 20
        ]
        assert len(parse_box_rows(cells, dpi=100)) == 1


# ---------------------------------------------------------------------------
# 1.1.2  Multi-row pairing — EXT-029/S029a, Design §2.1
# ---------------------------------------------------------------------------


class TestDescQtyPairingMultiRow:
    """4 rows at Y=[120,160,200,240] at DPI=200 (band=40) must pair cleanly."""

    def test_four_rows_paired_no_cross_band(self) -> None:
        cells = [
            # Row 1 — Y≈120
            _cell("BARRA A615 G60 3/8\"", cx=80, cy=120),
            _cell("0.008", cx=300, cy=122),
            # Row 2 — Y≈160
            _cell("BARRA A615 G60 1/2\"", cx=80, cy=160),
            _cell("0.136", cx=300, cy=161),
            # Row 3 — Y≈200
            _cell("BARRA A615 G60 5/8\"", cx=80, cy=200),
            _cell("0.191", cx=300, cy=202),
            # Row 4 — Y≈240
            _cell("BARRA A615 G60 3/4\"", cx=80, cy=240),
            _cell("0.041", cx=300, cy=239),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 4
        qtys = {row.cantidad for row in rows}
        assert qtys == {Decimal("0.008"), Decimal("0.136"), Decimal("0.191"), Decimal("0.041")}

    def test_rows_never_cross_band(self) -> None:
        """Rows 40 px apart must NEVER be associated across band boundaries."""
        # Two rows exactly 41 px apart — must not merge (band=40)
        cells = [
            _cell("BARRA A615 3/8\"", cx=80, cy=100),
            _cell("0.008", cx=300, cy=100),
            _cell("BARRA A615 1/2\"", cx=80, cy=141),
            _cell("0.136", cx=300, cy=141),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 2
        # Each row should map to its own qty
        row_qtys = sorted(row.cantidad for row in rows)
        assert row_qtys == [Decimal("0.008"), Decimal("0.136")]


# ---------------------------------------------------------------------------
# 1.1.3  Unit cell association same band — EXT-029/S029b, Design §5
# ---------------------------------------------------------------------------


class TestUnitCellAssociationSameBand:
    """TNE→TN label normalization; KG/RD/Rollo unchanged."""

    def test_tne_normalised_to_tn(self) -> None:
        cells = [
            _cell("BARRA A615 G60 1/2\"", cx=100, cy=150),
            _cell("0.136", cx=250, cy=152),
            _cell("TNE", cx=320, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].unidad == "TN"

    def test_kg_unchanged(self) -> None:
        cells = [
            _cell("BARRA A615 G60 3/8\"", cx=100, cy=150),
            _cell("5.800", cx=250, cy=152),
            _cell("KG", cx=320, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].unidad == "KG"

    def test_rd_unchanged(self) -> None:
        cells = [
            _cell("VARILLA LISA 10mm", cx=100, cy=150),
            _cell("10.000", cx=250, cy=152),
            _cell("RD", cx=320, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].unidad == "RD"

    def test_rollo_unchanged(self) -> None:
        cells = [
            _cell("ALAMBRE N8", cx=100, cy=150),
            _cell("2.000", cx=250, cy=152),
            _cell("Rollo", cx=320, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].unidad == "Rollo"


# ---------------------------------------------------------------------------
# 1.1.4  Unit cell row-scan fallback — Design §2.1 edge case
# ---------------------------------------------------------------------------


class TestUnitCellRowScanFallback:
    """When no UNIT cell is found geometrically, parser uses fallback heuristic.

    Per design: if no unit cell associates geometrically → either requires_review=True
    or row dropped.  Either outcome is acceptable; what matters is that an ambiguous
    row is NOT silently emitted as a MaterialLine with a fabricated unit.
    """

    def test_missing_unit_cell_handled_gracefully(self) -> None:
        # DESC + QTY only — no unit cell anywhere on the page
        cells = [
            _cell("BARRA A615 G60 1/2\"", cx=100, cy=150),
            _cell("0.136", cx=250, cy=152),
        ]
        rows = parse_box_rows(cells, dpi=200)
        # Either the row is dropped, or emitted with requires_review=True
        if rows:
            assert rows[0].requires_review is True


# ---------------------------------------------------------------------------
# 1.1.5  TNE is label-only — EXT-029/S029b + EXT-032
# ---------------------------------------------------------------------------


class TestTneNotANumericConversion:
    """TNE→TN is a label change; cantidad must never be multiplied/divided."""

    def test_tne_label_only_cantidad_unchanged(self) -> None:
        cells = [
            _cell("BARRA A615 G60 1/2\"", cx=100, cy=150),
            _cell("0.136", cx=250, cy=152),
            _cell("TNE", cx=320, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].unidad == "TN"
        assert rows[0].cantidad == Decimal("0.136")  # NOT 136 kg, NOT 0.000136


# ---------------------------------------------------------------------------
# 1.1.6  Incidental numbers guard — EXT-029/S029d, Design §5
# ---------------------------------------------------------------------------


class TestIncidentalNumbersNotQty:
    """lote 119, 1\", 408916, etc. must NEVER be classified as QTY."""

    def test_only_valid_qty_classified(self) -> None:
        cells = [
            _cell("1", cx=20, cy=150),           # single digit, leftmost — not a QTY fraction
            _cell("1\"", cx=60, cy=150),           # diameter — not a qty fraction
            _cell("408916", cx=100, cy=150),       # code / guía number — 6 digits, no fraction
            _cell("BARRA A615 G60 1/2\"", cx=150, cy=150),  # description
            _cell("0.037", cx=280, cy=150),        # valid qty: has fraction
            _cell("KG", cx=350, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].cantidad == Decimal("0.037")

    def test_lote_number_in_desc_not_qty(self) -> None:
        """'lote 119' as part of a desc cell must not yield '119' as qty."""
        cells = [
            _cell("BARRA A615 G60 1/2\" lote 119", cx=100, cy=150),
            _cell("0.191", cx=300, cy=151),
            _cell("TN", cx=370, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].cantidad == Decimal("0.191")

    def test_compound_diameter_fraction_not_qty(self) -> None:
        """Named contract case (design §5): a compound diameter like '1 3/8\"'
        (whole number + fraction) must NEVER be classified as a quantity."""
        cells = [
            _cell("1 3/8\"", cx=60, cy=150),       # compound diameter — not a qty
            _cell("BARRA A615 G60 1 3/8\"", cx=150, cy=150),  # description
            _cell("0.041", cx=300, cy=150),         # the real qty (has fraction)
            _cell("TN", cx=370, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].cantidad == Decimal("0.041")


# ---------------------------------------------------------------------------
# 1.1.7  Generalized DESC matcher — EXT-029/S029e, Design §5
# ---------------------------------------------------------------------------


class TestGeneralisedDescMatcher:
    """Non-rebar materials must be classified as DESC, not dropped."""

    def test_fierro_corrugado(self) -> None:
        cells = [
            _cell("FIERRO CORRUGADO 1/2\"", cx=100, cy=150),
            _cell("6.572", cx=280, cy=151),
            _cell("TN", cx=350, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert "FIERRO" in rows[0].description_raw.upper()

    def test_alambre_negro(self) -> None:
        cells = [
            _cell("ALAMBRE NEGRO N8", cx=100, cy=150),
            _cell("2.000", cx=280, cy=151),
            _cell("Rollo", cx=350, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1

    def test_acero_dimensionado(self) -> None:
        cells = [
            _cell("ACERO DIMENSIONADO 3/8\"", cx=100, cy=150),
            _cell("1.616", cx=280, cy=151),
            _cell("TN", cx=350, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# 1.1.8  Columnar-table input — EXT-029/S029c
# ---------------------------------------------------------------------------


class TestColumnarTableYieldsRows:
    """Cells from a columnar GRE table (desc/qty/unit in separate columns).

    The old _LINE_RE expected a single text line like 'BARRA 5.800 KG'.
    In a columnar OCR output the three tokens are separate cells.
    parse_box_rows must associate them geometrically and yield ≥1 row.
    """

    def test_columnar_cells_produce_row(self) -> None:
        # Simulate what RapidOCR would return for a columnar GRE table:
        # three separate cells per material row, NOT one long line
        cells = [
            _cell("BARRA A615/A706 G60 1/2\"", cx=80, cy=100),   # desc col
            _cell("4.124", cx=260, cy=101),                        # qty col
            _cell("TNE", cx=340, cy=100),                          # unit col
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) >= 1

    def test_old_line_re_would_miss_these(self) -> None:
        """Demonstrates why _LINE_RE fails on separate-column OCR output.

        _LINE_RE anchors on full-line text like 'BARRA 5.800 KG'.
        When desc, qty, and unit are separate OCR cells, no single cell
        matches _LINE_RE → 0 lines. parse_box_rows uses geometry instead.
        """
        import re
        _LINE_RE = re.compile(
            r"^(.+?)\s+(\d+(?:[.,]\d+)?)\s+(TN|KG|RD|Rollo)\s*$",
            re.IGNORECASE,
        )
        cells = [
            "BARRA A615/A706 G60 1/2\"",  # desc — no qty/unit on same text
            "4.124",                        # qty — no desc/unit on same text
            "TNE",                          # unit — no desc/qty on same text
        ]
        # None of the separate cells match _LINE_RE
        for text in cells:
            assert _LINE_RE.match(text) is None, (
                f"_LINE_RE unexpectedly matched '{text}' — "
                "test premise broken; update the demonstration test"
            )


# ---------------------------------------------------------------------------
# 1.1.9  SDK purity gate — EXT-029/S029f + EXT-032/S032c
# ---------------------------------------------------------------------------


class TestPureImportNoSdk:
    """Importing box_row_parser must NOT load any OCR SDK."""

    def test_rapidocr_not_imported(self) -> None:
        assert "rapidocr" not in sys.modules

    def test_onnxruntime_not_imported(self) -> None:
        assert "onnxruntime" not in sys.modules

    def test_paddleocr_not_imported(self) -> None:
        assert "paddleocr" not in sys.modules

    def test_numpy_not_imported(self) -> None:
        # numpy is in the identity extra, not base deps — parser must not pull it.
        #
        # W2: the prior `assert "numpy" not in sys.modules` was order-dependent —
        # sibling tests in this package load PIL, which pulls numpy into the
        # process-global sys.modules, so the assertion passed in isolation but
        # FAILED in the combined `tests/unit/adapters/` run. Importing
        # box_row_parser in a FRESH subprocess proves the REAL contract (the
        # module itself does not import numpy) independent of what other tests
        # loaded earlier in this process.
        import subprocess  # noqa: PLC0415

        code = (
            "import importlib, sys; "
            "importlib.import_module("
            "'reconciliation.adapters.ocr.box_row_parser'); "
            "assert 'numpy' not in sys.modules, "
            "'box_row_parser must NOT import numpy'"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, (
            "Importing box_row_parser pulled numpy into sys.modules "
            f"(stdout={proc.stdout!r} stderr={proc.stderr!r})"
        )


# ---------------------------------------------------------------------------
# 1.1.10  Geometry guard: QTY must be right of DESC — Design §2.1 qcx > dcx
# ---------------------------------------------------------------------------


class TestQtyRightOfDescGeometryGuard:
    """A QTY cell to the LEFT of the DESC cell must NOT be associated."""

    def test_qty_left_of_desc_not_associated(self) -> None:
        cells = [
            _cell("0.136", cx=50, cy=150),        # QTY at x=50 — LEFT of desc
            _cell("BARRA A615 1/2\"", cx=200, cy=150),  # DESC at x=200
            _cell("TN", cx=300, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        # The pair is invalid (qty is left of desc) — row must NOT be emitted
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# 1.1.12  Empty input — edge case
# ---------------------------------------------------------------------------


class TestEmptyCellsReturnsEmptyList:
    def test_empty_input(self) -> None:
        assert parse_box_rows([], 200) == []

    def test_empty_count(self) -> None:
        assert count_valid_rows([], 200) == 0


# ---------------------------------------------------------------------------
# 1.1.13  FIX 1 — corrected quantity contract (JD CRITICAL)
# ---------------------------------------------------------------------------


class TestCorrectedQuantityContract:
    """The qty contract is: decimal-shape ANY digits OR unit-adjacent integer.

    The previous `^\\d{1,3}[.,]\\d{2,3}$` regex silently dropped real declared
    data: `2.5 TN` (one fractional digit) and `5800.00 KG` (>=1000 integer part).
    The declared-side extractor accepts `(\\d+(?:[.,]\\d+)?)` — the OCR side MUST
    align. Empirically (177 real qty tokens, pages 378-379) no thousands
    separators exist; `,`->`.` is a SAFE decimal normalization.

    NOTE (domain-authority, NOT corpus-validated): the integer+unit cases
    (`25 RD`, `5800 KG`) are synthetic. This corpus is TN-only with no integer
    quantities; the domain model declares KG/TN/RD/Rollo, so the contract MUST
    accept them. Labelled synthetic so a future reader knows they aren't
    corpus-validated.
    """

    def test_single_fractional_digit_accepted(self) -> None:
        # `2.5 TN` — one fractional digit. The old {2,3} minimum dropped it.
        cells = [
            _cell("BARRA A615 G60 1/2\"", cx=100, cy=150),
            _cell("2.5", cx=250, cy=152),
            _cell("TN", cx=320, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].cantidad == Decimal("2.5")

    def test_large_decimal_qty_accepted(self) -> None:
        # `5800.00 KG` — 4-digit integer part. The old {1,3} cap dropped it.
        cells = [
            _cell("BARRA A615 G60 3/8\"", cx=100, cy=150),
            _cell("5800.00", cx=250, cy=152),
            _cell("KG", cx=320, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].cantidad == Decimal("5800.00")

    def test_bare_integer_with_adjacent_unit_accepted(self) -> None:
        # SYNTHETIC (domain-authority, not corpus): `25 RD` — bare integer
        # disambiguated by the adjacent unit cell (the unit-suffix rule).
        cells = [
            _cell("VARILLA LISA 10mm", cx=100, cy=150),
            _cell("25", cx=250, cy=152),
            _cell("RD", cx=320, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].cantidad == Decimal("25")
        assert rows[0].unidad == "RD"

    def test_bare_integer_kg_with_adjacent_unit_accepted(self) -> None:
        # SYNTHETIC (domain-authority, not corpus): `5800 KG` — bare integer
        # disambiguated by the adjacent unit cell.
        cells = [
            _cell("BARRA A615 G60 3/8\"", cx=100, cy=150),
            _cell("5800", cx=250, cy=152),
            _cell("KG", cx=320, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].cantidad == Decimal("5800")
        assert rows[0].unidad == "KG"

    def test_bare_integer_without_unit_still_rejected(self) -> None:
        # Incidental-number guard MUST hold: a bare integer with NO adjacent
        # unit (`119`, `408916`) is NOT a quantity.
        cells = [
            _cell("119", cx=20, cy=150),
            _cell("408916", cx=60, cy=150),
            _cell("BARRA A615 G60 1/2\"", cx=150, cy=150),
            _cell("0.037", cx=280, cy=150),
            _cell("KG", cx=350, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].cantidad == Decimal("0.037")

    def test_compound_diameter_still_rejected(self) -> None:
        # Incidental-number guard MUST hold: `1 3/8"` is never a quantity.
        cells = [
            _cell("1 3/8\"", cx=60, cy=150),
            _cell("BARRA A615 G60 1 3/8\"", cx=150, cy=150),
            _cell("0.041", cx=300, cy=150),
            _cell("TN", cx=370, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].cantidad == Decimal("0.041")

    def test_malformed_numeric_token_does_not_crash(self) -> None:
        # A degenerate qty-shaped token must drop-with-log, never raise.
        cells = [
            _cell("BARRA A615 G60 1/2\"", cx=100, cy=150),
            _cell("1.2.3", cx=250, cy=152),  # not a valid Decimal
            _cell("TN", cx=320, cy=150),
        ]
        # Must not raise; the malformed row is simply not emitted.
        rows = parse_box_rows(cells, dpi=200)
        assert rows == []


# ---------------------------------------------------------------------------
# 1.1.14  FIX 2 — relaxed-unit fallback must flag review; no cross-row theft
# ---------------------------------------------------------------------------


class TestRelaxedUnitFallbackFlagsReview:
    """A unit claimed OUT of column order is positional-evidence-violating and
    MUST NOT produce a confident line — it sets requires_review=True.
    """

    def test_left_column_unit_flags_review(self) -> None:
        # UNIT sits LEFT of the qty (cx=10) — wrong column. The relaxed
        # fallback may still adopt it, but MUST flag requires_review=True.
        cells = [
            _cell("KG", cx=10, cy=150),
            _cell("BARRA A615 G60 3/8\"", cx=100, cy=150),
            _cell("0.037", cx=300, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].requires_review is True


class TestNoCrossRowUnitTheft:
    """3 rows packed tighter than the band — each keeps its own unit; a
    mis-OCR'd unit on row1 MUST NOT cause row2/row3 units to be stolen.
    """

    def test_packed_rows_keep_own_units(self) -> None:
        # band(200)=40. Rows 30px apart (packed tighter than the band).
        # Row1 unit is mis-OCR'd (not a recognised unit token) → row1 may flag
        # review, but rows 2 and 3 MUST each keep their own correct unit and
        # MUST NOT have it stolen by the greedy nearest-across-bands fallback.
        cells = [
            # Row 1 — cy=100, unit garbled
            _cell("BARRA A615 G60 3/8\"", cx=80, cy=100),
            _cell("0.008", cx=300, cy=100),
            _cell("T1N", cx=380, cy=100),  # garbled unit (not recognised)
            # Row 2 — cy=130
            _cell("BARRA A615 G60 1/2\"", cx=80, cy=130),
            _cell("0.136", cx=300, cy=130),
            _cell("KG", cx=380, cy=130),
            # Row 3 — cy=160
            _cell("BARRA A615 G60 5/8\"", cx=80, cy=160),
            _cell("0.191", cx=300, cy=160),
            _cell("RD", cx=380, cy=160),
        ]
        rows = parse_box_rows(cells, dpi=200)
        by_qty = {r.cantidad: r for r in rows}
        # Row 2 keeps KG; row 3 keeps RD — neither stolen by row 1.
        assert by_qty[Decimal("0.136")].unidad == "KG"
        assert by_qty[Decimal("0.191")].unidad == "RD"


# ---------------------------------------------------------------------------
# 1.1.15  FIX 3 — adversarial corpus (degenerate / tie-break / oracle meaning)
# ---------------------------------------------------------------------------


class TestAdversarialCorpus:
    """Cases the original mock-theatre suite could not exercise."""

    def test_degenerate_only_units(self) -> None:
        # Only unit cells, no desc/qty → empty, no crash.
        cells = [_cell("KG", cx=100, cy=150), _cell("TN", cx=200, cy=150)]
        assert parse_box_rows(cells, dpi=200) == []

    def test_equidistant_qty_deterministic_tiebreak(self) -> None:
        # A qty exactly equidistant (in cy) between two desc rows must
        # associate deterministically (same result across runs), and exactly
        # one desc claims it — never both.
        cells = [
            _cell("BARRA A615 G60 3/8\"", cx=80, cy=140),
            _cell("BARRA A615 G60 1/2\"", cx=80, cy=160),
            _cell("0.136", cx=300, cy=150),  # equidistant: |150-140|=|160-150|=10
            _cell("TN", cx=380, cy=150),
        ]
        first = parse_box_rows(cells, dpi=200)
        second = parse_box_rows(cells, dpi=200)
        # Deterministic: identical across calls.
        assert [r.cantidad for r in first] == [r.cantidad for r in second]
        # The single qty is claimed exactly once.
        claimed = [r for r in first if r.cantidad == Decimal("0.136")]
        assert len(claimed) == 1


class TestOrientationOracleMeaningful:
    """The orientation oracle must distinguish a correctly-oriented page from
    a degenerate one: a cell set that parses 0 rows scores lower than one that
    parses its rows correctly.
    """

    def test_correct_orientation_scores_higher_than_degenerate(self) -> None:
        good = [
            _cell("BARRA A615 G60 3/8\"", cx=80, cy=120),
            _cell("0.008", cx=300, cy=121),
            _cell("TN", cx=380, cy=120),
            _cell("BARRA A615 G60 1/2\"", cx=80, cy=160),
            _cell("0.136", cx=300, cy=161),
            _cell("TN", cx=380, cy=160),
        ]
        # Degenerate: qty cells are LEFT of desc (a wrong rotation scenario)
        # → no valid pairing → 0 rows.
        degenerate = [
            _cell("0.008", cx=80, cy=120),
            _cell("BARRA A615 G60 3/8\"", cx=300, cy=120),
            _cell("0.136", cx=80, cy=160),
            _cell("BARRA A615 G60 1/2\"", cx=300, cy=160),
        ]
        assert count_valid_rows(good, 200) > count_valid_rows(degenerate, 200)
        assert count_valid_rows(degenerate, 200) == 0


# ---------------------------------------------------------------------------
# 1.1.16  FIX A1 — date/code-shape 4-digit fractions are NOT quantities
# ---------------------------------------------------------------------------


class TestDateShapeFractionNotQty:
    """A 4-digit fraction (year shape `12.2024`, `2024.12`, `01.2025`) must be
    STRUCTURALLY rejected as a quantity. The corpus declared max is 3 decimals,
    so `_QTY_DECIMAL_RE` caps the fractional part at `\\d{1,3}`. This neutralizes
    the decimal date/code confident-false-positive leak (round-2 finding A.2).
    """

    def test_year_fraction_12_2024_not_qty(self) -> None:
        # `12.2024` (a month.year date shape) must NOT pair as a quantity.
        cells = [
            _cell("BARRA A615 G60 1/2\"", cx=100, cy=150),
            _cell("12.2024", cx=250, cy=152),  # 4-digit fraction — date, not qty
            _cell("TN", cx=320, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        # No valid decimal qty present → the desc cannot pair → no row emitted.
        assert rows == []

    def test_year_fraction_2024_12_not_qty(self) -> None:
        # `2024.12` (year.month) — integer part is fine but it is still a date
        # shape; with only this token there is no real qty → no row.
        cells = [
            _cell("BARRA A615 G60 1/2\"", cx=100, cy=150),
            _cell("2024.12", cx=250, cy=152),
            _cell("TN", cx=320, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        # `2024.12` IS shape-valid (2-digit fraction) but its integer part >=4
        # digits → off-profile → MUST be flagged, never confident. The A1 cap
        # only rejects the 4-digit FRACTION case (`12.2024`). `2024.12` is
        # handled by the A2 confidence-gate (off-profile → requires_review).
        if rows:
            assert rows[0].requires_review is True

    def test_decimal_date_code_408916_00_not_confident(self) -> None:
        # `408916.00` (código.00 shape) — integer part 6 digits, fraction 2.
        # Shape-valid (A1 passes) but off the in-corpus profile (integer >=4) →
        # A2 confidence-gate MUST flag it requires_review=True, never confident.
        cells = [
            _cell("BARRA A615 G60 1/2\"", cx=100, cy=150),
            _cell("408916.00", cx=250, cy=152),
            _cell("TN", cx=320, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].requires_review is True


# ---------------------------------------------------------------------------
# 1.1.17  FIX A2 — confidence-gate to the validated-corpus profile
# ---------------------------------------------------------------------------


class TestConfidenceGateToCorpusProfile:
    """A quantity is emitted CONFIDENT (`requires_review=False`) ONLY when it
    matches the empirically-validated profile: a DECIMAL with integer-part
    1-3 digits AND fractional 1-3 digits (`\\d{1,3}[.,]\\d{1,3}`). Anything
    outside that profile — bare-integer-promoted qty, or decimal with
    integer-part >=4 digits — is EXTRACTED but flagged `requires_review=True`
    (off the TN-only validated corpus and/or not column-anchored yet; PR#2).
    """

    def test_in_profile_decimal_is_confident(self) -> None:
        # `0.136 TN` — canonical in-corpus shape → confident (requires_review=False).
        cells = [
            _cell("BARRA A615 G60 1/2\"", cx=100, cy=150),
            _cell("0.136", cx=250, cy=152),
            _cell("TN", cx=320, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].requires_review is False

    def test_in_profile_single_fractional_digit_is_confident(self) -> None:
        # `2.5 TN` — 1 integer digit, 1 fractional digit → in-profile → confident.
        cells = [
            _cell("BARRA A615 G60 1/2\"", cx=100, cy=150),
            _cell("2.5", cx=250, cy=152),
            _cell("TN", cx=320, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].requires_review is False

    def test_large_integer_part_decimal_flagged(self) -> None:
        # `5800.00 KG` — integer part 4 digits → off-profile → flagged, NOT confident.
        # (Still EXTRACTED — never dropped.)
        cells = [
            _cell("BARRA A615 G60 3/8\"", cx=100, cy=150),
            _cell("5800.00", cx=250, cy=152),
            _cell("KG", cx=320, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].cantidad == Decimal("5800.00")
        assert rows[0].requires_review is True

    def test_bare_integer_promoted_qty_flagged(self) -> None:
        # `25 RD` — bare-integer-promoted qty (off the decimal TN corpus) →
        # extracted but flagged requires_review=True.
        cells = [
            _cell("VARILLA LISA 10mm", cx=100, cy=150),
            _cell("25", cx=250, cy=152),
            _cell("RD", cx=320, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].cantidad == Decimal("25")
        assert rows[0].requires_review is True

    def test_codigo_integer_with_unit_extracted_but_flagged(self) -> None:
        # `408916` + adjacent unit — integer código promoted by the unit-suffix
        # rule. Round-2 leak A.1: it reached requires_review=False. The gate now
        # forces requires_review=True (extracted, never silently confident).
        cells = [
            _cell("BARRA A615 G60 1/2\"", cx=100, cy=150),
            _cell("408916", cx=250, cy=152),
            _cell("TN", cx=320, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].cantidad == Decimal("408916")
        assert rows[0].requires_review is True

    def test_bare_integer_without_unit_still_dropped_entirely(self) -> None:
        # The incidental-number guard MUST still hold: a bare `408916` with NO
        # adjacent unit is rejected ENTIRELY (not even an extracted-flagged row).
        cells = [
            _cell("408916", cx=20, cy=150),
            _cell("BARRA A615 G60 1/2\"", cx=150, cy=150),
            _cell("0.037", cx=280, cy=150),
            _cell("KG", cx=350, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].cantidad == Decimal("0.037")
        assert rows[0].requires_review is False


# ---------------------------------------------------------------------------
# 1.1.18  FIX B — greedy DESC claim must not silently drop a real material row
# ---------------------------------------------------------------------------


class TestNoSilentDropOnNoiseDescContention:
    """A noise/header DESC (`OBSERVACIONES`, stamp text) with a >=3-letter run
    must NOT greedily claim the real material's qty and cause the real BARRA row
    to silently vanish. Each qty is assigned to the GEOMETRICALLY NEAREST desc,
    so the BARRA row (sharing the unit's row) wins its qty (round-2 WARNING-3,
    never-silent-drop invariant).
    """

    def test_observaciones_does_not_steal_barra_qty(self) -> None:
        cells = [
            _cell("OBSERVACIONES", cx=80, cy=140),       # noise desc, higher up
            _cell("BARRA A615 3/8\"", cx=80, cy=150),    # real material, nearer qty
            _cell("0.136", cx=300, cy=145),
            _cell("TN", cx=380, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        # The real BARRA row MUST survive with its 0.136 TN — never dropped.
        barra = [r for r in rows if "BARRA" in r.description_raw.upper()]
        assert len(barra) == 1
        assert barra[0].cantidad == Decimal("0.136")
        assert barra[0].unidad == "TN"


# ---------------------------------------------------------------------------
# PR#3 real-data fixes — integer-promotion column guard + noise-floor drop
# ---------------------------------------------------------------------------


class TestIntegerPromotionColumnGuard:
    """Integer candidates must be RIGHT of ALL desc cells in their row band.

    Real-data failure on page 0148/0156: a long footer desc at cx≈67 (far left)
    was in the same row band as the material rows. Item/código integers at
    cx≈347/426 were right of the footer desc (cx=67) → promoted. But they were
    LEFT of the BARRA desc at cx≈600. The fix: right-of-ALL-descs in band.
    """

    def test_left_column_integer_with_far_left_footer_desc_not_promoted(self) -> None:
        # Footer desc far left (cx=67), código integer (cx=426), BARRA (cx=605).
        # 408916 is right of the footer (67) but LEFT of BARRA (605) →
        # must NOT be promoted to quantity.
        cells = [
            _cell("Created by Sandra Sopla with Autodesk Forma", cx=67, cy=589),  # footer
            _cell("408916", cx=426, cy=595),          # código — left of BARRA
            _cell("BARRA A615 G60 3/8\" DOB", cx=605, cy=595),
            _cell("TNE", cx=1143, cy=598),
            _cell("0.037", cx=1295, cy=598),
        ]
        rows = parse_box_rows(cells, dpi=200)
        quantities = [r.cantidad for r in rows]
        # 0.037 must be extracted; 408916 must NOT appear
        assert Decimal("0.037") in quantities
        assert Decimal("408916") not in quantities
        assert len(rows) == 1

    def test_right_column_integer_is_still_promoted(self) -> None:
        # Integer in the QTY column (far right, cx=1295), right of ALL descs.
        # This is the valid "25 RD" case — must still be promoted.
        cells = [
            _cell("VARILLA LISA 10mm", cx=100, cy=150),
            _cell("25", cx=280, cy=152),
            _cell("RD", cx=340, cy=150),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].cantidad == Decimal("25")
        assert rows[0].unidad == "RD"


class TestSemanticNoiseFilterNotConfidenceDrop:
    """CRITICAL-A (dual-judge): a low-confidence REAL material row MUST be
    emitted with requires_review=True — NEVER silently dropped on a confidence
    number. Footer/stamp NOISE is excluded SEMANTICALLY (a denylist of
    non-material GRE/Forma labels), not by a confidence floor.

    This INVERTS the prior `_MIN_EMIT_CONFIDENCE` noise-floor drop, which
    silently dropped any row with row_conf < 0.65 — a never-silent-drop and
    false-MATCH-hole violation (a dropped MISMATCH row becomes a confident
    false MATCH with no review signal; defeats reconciliation-is-the-gate).
    """

    def test_low_confidence_real_material_row_emitted_with_review(self) -> None:
        # CRITICAL-A test #1: a REAL material row at qty conf 0.60 (row_conf 0.60,
        # below the old 0.65 floor) MUST now be EMITTED with requires_review=True.
        # Previously returned [] (silent drop — the bug).
        cells = [
            _cell("BARRA A615 G60 1/2\"", cx=100, cy=150, conf=0.95),
            _cell("0.408", cx=250, cy=152, conf=0.60),
            _cell("TNE", cx=320, cy=150, conf=0.95),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].cantidad == Decimal("0.408")
        assert rows[0].requires_review is True

    def test_low_confidence_bare_integer_real_row_emitted_with_review(self) -> None:
        # CRITICAL-A test #2: a bare-integer real row (`VARILLA` + `25` + `RD`)
        # at conf 0.60 → emitted with requires_review=True (not dropped on the
        # confidence number). Scoped to the CONFIDENCE path: the bare integer is
        # already off-profile so requires_review=True via the A2 gate too; the
        # point of THIS test is that the low confidence does NOT cause a drop.
        cells = [
            _cell("VARILLA LISA 10mm", cx=100, cy=150, conf=0.60),
            _cell("25", cx=250, cy=152, conf=0.60),
            _cell("RD", cx=320, cy=150, conf=0.95),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].cantidad == Decimal("25")
        assert rows[0].unidad == "RD"
        assert rows[0].requires_review is True

    def test_footer_noise_revisado_por_excluded_semantically(self) -> None:
        # CRITICAL-A test #3: a footer-noise row (`REVISADO POR` + `4.8`) is
        # EXCLUDED as non-material via the SEMANTIC denylist — NOT via confidence.
        # Even at HIGH confidence the footer label must never become a material
        # row (proving the exclusion is semantic, not a confidence artifact).
        cells = [
            _cell("REVISADO POR", cx=726, cy=999, conf=0.95),
            _cell("4.8", cx=1121, cy=981, conf=0.95),
            _cell("TN", cx=1200, cy=990, conf=0.95),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert rows == []

    def test_footer_noise_at_low_confidence_also_excluded(self) -> None:
        # The original page-156 observation: "REVISADO POR + 4.8" at conf 0.584.
        # Still excluded — but now by the denylist, not the (removed) floor.
        cells = [
            _cell("REVISADO POR", cx=726, cy=999, conf=0.60),
            _cell("4.8", cx=1121, cy=981, conf=0.55),
            _cell("TN", cx=1200, cy=990, conf=0.90),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert rows == []

    def test_recibido_conforme_footer_excluded(self) -> None:
        # Another real GRE footer label — accent/case-insensitive substring match.
        cells = [
            _cell("Recibido Conforme", cx=80, cy=900, conf=0.90),
            _cell("0.500", cx=300, cy=900, conf=0.90),
            _cell("TN", cx=380, cy=900, conf=0.90),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert rows == []

    def test_observaciones_footer_excluded(self) -> None:
        # `OBSERVACIONES` is a GRE section label, not a material descriptor.
        cells = [
            _cell("OBSERVACIONES", cx=80, cy=900, conf=0.90),
            _cell("0.500", cx=300, cy=900, conf=0.90),
            _cell("TN", cx=380, cy=900, conf=0.90),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert rows == []

    def test_garbled_stamp_noise_below_conf_no_anchor_excluded(self) -> None:
        # CRITICAL-A real-data (page 156): the reception-stamp paragraph OCRs to
        # gibberish (`acacpen enfuin aeococl vignte` @0.573) that no CLEAN
        # denylist token survives, paired with a stray `4.8`. Secondary signal:
        # BELOW _NOISE_CONFIDENCE (0.65) AND NO material anchor → excluded.
        cells = [
            _cell("acacpen enfuin aeococl vignte", cx=80, cy=999, conf=0.573),
            _cell("4.8", cx=1121, cy=999, conf=0.90),
            _cell("TN", cx=1200, cy=999, conf=0.90),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert rows == []

    def test_low_conf_garbled_text_WITH_material_anchor_still_emitted(self) -> None:
        # The secondary signal MUST NOT drop a real material row: a low-confidence
        # DESC that carries a material anchor (BARRA) is emitted with review even
        # if the rest of the OCR text is garbled. Never-silent-drop holds.
        cells = [
            _cell("BARRA a615 g6d garbledtail", cx=80, cy=999, conf=0.55),
            _cell("0.408", cx=1121, cy=999, conf=0.55),
            _cell("TN", cx=1200, cy=999, conf=0.55),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].cantidad == Decimal("0.408")
        assert rows[0].requires_review is True

    def test_border_confidence_row_above_floor_emitted_with_review(self) -> None:
        # row_conf = min(0.70, 0.68) = 0.68 < 0.85 threshold → emitted with
        # requires_review=True (unchanged behavior).
        cells = [
            _cell("BARRA A615 G60 5/8\"", cx=100, cy=150, conf=0.70),
            _cell("0.191", cx=250, cy=152, conf=0.68),
            _cell("TN", cx=320, cy=150, conf=0.95),
        ]
        rows = parse_box_rows(cells, dpi=200)
        assert len(rows) == 1
        assert rows[0].requires_review is True
        assert rows[0].cantidad == Decimal("0.191")
