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
        # numpy is in the identity extra, not base deps — parser must not pull it
        assert "numpy" not in sys.modules


# ---------------------------------------------------------------------------
# 1.1.10  count_valid_rows orientation oracle — Design §2.1
# ---------------------------------------------------------------------------


class TestCountValidRowsOrientationOracle:
    """count_valid_rows(cells, dpi) must equal len(parse_box_rows(cells, dpi))."""

    def test_count_equals_parse_len_populated(self) -> None:
        cells = [
            _cell("BARRA A615 3/8\"", cx=100, cy=150),
            _cell("0.008", cx=280, cy=151),
            _cell("TN", cx=350, cy=150),
            _cell("BARRA A615 1/2\"", cx=100, cy=200),
            _cell("0.136", cx=280, cy=201),
            _cell("TN", cx=350, cy=200),
        ]
        assert count_valid_rows(cells, 200) == len(parse_box_rows(cells, 200))

    def test_count_equals_parse_len_empty(self) -> None:
        assert count_valid_rows([], 200) == len(parse_box_rows([], 200))

    def test_count_equals_parse_len_no_pairs(self) -> None:
        cells = [_cell("FECHA: 10/05/2024", cx=100, cy=100)]
        assert count_valid_rows(cells, 200) == len(parse_box_rows(cells, 200))


# ---------------------------------------------------------------------------
# 1.1.11  Geometry guard: QTY must be right of DESC — Design §2.1 qcx > dcx
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
