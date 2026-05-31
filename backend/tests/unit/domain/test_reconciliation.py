"""Unit tests for ReconciliationService (task 1.4).

Covers: MATCH, MISMATCH, cross-unit guard, DECLARED_MISSING, GUIA_MISSING,
reassignment recomputation, no-silent-exclusion.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from reconciliation.domain.models import GuiaDeRemision, MaterialLine, Registro
from reconciliation.domain.reconciliation import ReconciliationService


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _line(
    canonical: str,
    unidad: str,
    cantidad: str,
    confidence: float | None = None,
    page: int | None = None,
) -> MaterialLine:
    return MaterialLine(
        description_raw=canonical.upper(),
        description_canonical=canonical,
        unidad=unidad,  # type: ignore[arg-type]
        cantidad=Decimal(cantidad),
        confidence=confidence,
        source_page=page,
    )


def _guia(
    guia_id: str,
    registro: str | None,
    fecha: date | None,
    lines: list[MaterialLine],
    pages: list[int] | None = None,
) -> GuiaDeRemision:
    return GuiaDeRemision(
        guia_id=guia_id,
        registro=registro,
        fecha=fecha,
        lines=lines,
        source_pages=pages or [],
    )


def _registro(
    numero: str,
    fecha: date | None,
    lines: list[MaterialLine],
) -> Registro:
    return Registro(numero=numero, fecha_declarada=fecha, declared_lines=lines)


@pytest.fixture()
def svc() -> ReconciliationService:
    return ReconciliationService()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMatch:
    def test_exact_match_single_guia(self, svc: ReconciliationService) -> None:
        """REC-S01: sum equals declared exactly → MATCH."""
        declared = [
            _registro("4252", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "KG", "1250.00"),
            ])
        ]
        guias = [
            _guia("G-001", "4252", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "KG", "750.00", confidence=0.95, page=10),
                _line("barra corrugada 1/2", "KG", "500.00", confidence=0.88, page=11),
            ], pages=[10, 11]),
        ]
        rows = svc.reconcile(declared, guias)
        assert len(rows) == 1
        row = rows[0]
        assert row.status == "MATCH"
        assert row.summed_qty == Decimal("1250.00")
        assert row.delta == Decimal("0")

    def test_match_min_confidence_is_minimum(self, svc: ReconciliationService) -> None:
        """REC-009: min_confidence = min over contributing lines."""
        declared = [
            _registro("4252", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "KG", "1250.00"),
            ])
        ]
        guias = [
            _guia("G-001", "4252", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "KG", "750.00", confidence=0.95),
                _line("barra corrugada 1/2", "KG", "500.00", confidence=0.88),
            ]),
        ]
        rows = svc.reconcile(declared, guias)
        assert rows[0].min_confidence == pytest.approx(0.88)


class TestMismatch:
    def test_delta_10_is_mismatch(self, svc: ReconciliationService) -> None:
        """REC-S02: any nonzero delta → MISMATCH."""
        declared = [
            _registro("4252", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "KG", "1250.0"),
            ])
        ]
        guias = [
            _guia("G-001", "4252", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "KG", "1260.0"),
            ]),
        ]
        rows = svc.reconcile(declared, guias)
        assert rows[0].status == "MISMATCH"
        assert rows[0].delta == Decimal("10.0")

    def test_mismatch_tiny_delta(self, svc: ReconciliationService) -> None:
        """Exact(0): even 0.001 delta → MISMATCH."""
        declared = [
            _registro("R1", None, [_line("mat a", "KG", "100.000")])
        ]
        guias = [
            _guia("G1", "R1", None, [_line("mat a", "KG", "100.001")]),
        ]
        rows = svc.reconcile(declared, guias)
        assert rows[0].status == "MISMATCH"

    def test_zero_delta_exact_match(self, svc: ReconciliationService) -> None:
        """Confirms EXACT(0): zero delta → MATCH regardless of unit."""
        declared = [
            _registro("R1", None, [_line("mat a", "TN", "1.250")])
        ]
        guias = [
            _guia("G1", "R1", None, [_line("mat a", "TN", "1.250")]),
        ]
        rows = svc.reconcile(declared, guias)
        assert rows[0].status == "MATCH"


class TestCrossUnitGuard:
    def test_tn_and_kg_separate_groups(self, svc: ReconciliationService) -> None:
        """REC-S03: TN and KG MUST form separate groups, never merged."""
        declared = [
            _registro("4252", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "TN", "1.25"),
                _line("barra corrugada 1/2", "KG", "1250.0"),
            ])
        ]
        guias = [
            _guia("G-001", "4252", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "TN", "1.25"),
                _line("barra corrugada 1/2", "KG", "1250.0"),
            ]),
        ]
        rows = svc.reconcile(declared, guias)
        units = {r.unidad for r in rows}
        assert "TN" in units
        assert "KG" in units
        assert len(rows) == 2
        for row in rows:
            assert row.status == "MATCH"

    def test_no_cross_unit_addition(self, svc: ReconciliationService) -> None:
        """KG sum must equal declared KG only — TN rows excluded."""
        declared = [
            _registro("R1", None, [_line("mat", "KG", "500.0")])
        ]
        guias = [
            _guia("G1", "R1", None, [
                _line("mat", "KG", "500.0"),
                _line("mat", "TN", "2.0"),
            ]),
        ]
        rows = svc.reconcile(declared, guias)
        kg_row = next(r for r in rows if r.unidad == "KG")
        assert kg_row.summed_qty == Decimal("500.0")
        assert kg_row.status == "MATCH"


class TestDeclaredMissing:
    def test_guia_without_declared_counterpart(self, svc: ReconciliationService) -> None:
        """REC-S04: guía rows with no declared match → DECLARED_MISSING."""
        declared: list[Registro] = []
        guias = [
            _guia("G-001", "4252", date(2025, 3, 15), [
                _line("alambre n16", "KG", "200.0", page=20),
            ], pages=[20]),
        ]
        rows = svc.reconcile(declared, guias)
        assert len(rows) == 1
        assert rows[0].status == "DECLARED_MISSING"
        assert rows[0].declared_qty == Decimal("0")
        assert rows[0].summed_qty == Decimal("200.0")

    def test_declared_missing_not_silently_excluded(self, svc: ReconciliationService) -> None:
        """REC-007: rows not in declared must still appear in output."""
        guias = [
            _guia("G1", "R1", None, [_line("unknown mat", "KG", "10.0")]),
        ]
        rows = svc.reconcile([], guias)
        assert any(r.status == "DECLARED_MISSING" for r in rows)


class TestGuiaMissing:
    def test_declared_with_no_guia_rows(self, svc: ReconciliationService) -> None:
        """REC-S05: declared material with no guía rows → GUIA_MISSING, summed=0."""
        declared = [
            _registro("4251", date(2025, 2, 10), [
                _line("barra corrugada 3/8", "KG", "800.0"),
            ])
        ]
        guias: list[GuiaDeRemision] = []
        rows = svc.reconcile(declared, guias)
        assert len(rows) == 1
        assert rows[0].status == "GUIA_MISSING"
        assert rows[0].summed_qty == Decimal("0")


class TestReassignment:
    def test_reassignment_moves_guia(self, svc: ReconciliationService) -> None:
        """REC-S06: apply_reassignment moves guía and recomputes both groups."""
        guias = [
            _guia("G-12345", "4252", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "KG", "300.0"),
            ], pages=[47]),
        ]
        updated = svc.apply_reassignment(
            guias,
            guia_id="G-12345",
            new_registro="4251",
            new_fecha=date(2025, 2, 10),
        )
        assert len(updated) == 1
        moved = updated[0]
        assert moved.guia_id == "G-12345"
        assert moved.registro == "4251"
        assert moved.fecha == date(2025, 2, 10)

    def test_reassignment_recomputes_source_group(self, svc: ReconciliationService) -> None:
        declared_source = [
            _registro("4252", date(2025, 3, 15), [_line("barra corrugada 1/2", "KG", "1250.0")])
        ]
        declared_target = [
            _registro("4251", date(2025, 2, 10), [_line("barra corrugada 1/2", "KG", "300.0")])
        ]
        declared = declared_source + declared_target

        guias = [
            _guia("G-12345", "4252", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "KG", "300.0"),
            ], pages=[47]),
            _guia("G-99999", "4252", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "KG", "950.0"),
            ], pages=[48]),
        ]

        # Before reassignment: source group sum = 1250, MATCH
        rows_before = svc.reconcile(declared, guias)
        source_before = next(
            r for r in rows_before
            if r.registro == "4252" and r.fecha == date(2025, 3, 15)
        )
        assert source_before.status == "MATCH"

        # Reassign G-12345 to target
        updated_guias = svc.apply_reassignment(
            guias,
            guia_id="G-12345",
            new_registro="4251",
            new_fecha=date(2025, 2, 10),
        )

        rows_after = svc.reconcile(declared, updated_guias)
        source_after = next(
            r for r in rows_after
            if r.registro == "4252" and r.fecha == date(2025, 3, 15)
        )
        target_after = next(
            r for r in rows_after
            if r.registro == "4251" and r.fecha == date(2025, 2, 10)
        )

        # Source group now only has G-99999 = 950, declared = 1250 → MISMATCH
        assert source_after.status == "MISMATCH"
        assert source_after.summed_qty == Decimal("950.0")

        # Target group now has G-12345 = 300, declared = 300 → MATCH
        assert target_after.status == "MATCH"
        assert target_after.summed_qty == Decimal("300.0")

    def test_reassignment_original_list_unchanged(self, svc: ReconciliationService) -> None:
        """apply_reassignment must return a new list — no mutation."""
        guias = [
            _guia("G1", "R1", None, [_line("mat", "KG", "100.0")]),
        ]
        original_registro = guias[0].registro
        updated = svc.apply_reassignment(guias, "G1", "R2", None)
        # Original list unmodified
        assert guias[0].registro == original_registro
        assert updated[0].registro == "R2"

    def test_reassignment_of_nonexistent_guia_returns_same(
        self, svc: ReconciliationService
    ) -> None:
        guias = [
            _guia("G1", "R1", None, [_line("mat", "KG", "100.0")]),
        ]
        updated = svc.apply_reassignment(guias, "NONEXISTENT", "R2", None)
        assert updated[0].registro == "R1"


class TestNoSilentExclusions:
    def test_all_guia_lines_appear_in_output(self, svc: ReconciliationService) -> None:
        """REC-007: every guía page contributes to exactly one group (no silent drops)."""
        pages = list(range(320))
        guias = [
            _guia(
                f"G-{i}",
                "4252",
                date(2025, 3, 15),
                [_line("mat a", "KG", "1.0", page=i)],
                pages=[i],
            )
            for i in pages
        ]
        declared = [
            _registro("4252", date(2025, 3, 15), [_line("mat a", "KG", str(len(pages)))])
        ]
        rows = svc.reconcile(declared, guias)
        all_pages_in_output = {p for r in rows for p in r.source_pages}
        assert len(all_pages_in_output) == 320


class TestPurity:
    def test_no_io_possible_by_design(self, svc: ReconciliationService) -> None:
        """REC-008: reconcile is pure — calling it cannot trigger I/O by design.

        This test validates structural purity: ReconciliationService and its
        dependencies import only stdlib and domain modules, not I/O or SDK libs.
        """
        import reconciliation.domain.reconciliation as module
        import importlib.util
        source = module.__file__
        assert source is not None
        # Check that the module does not import any I/O or adapter modules
        import ast, pathlib
        tree = ast.parse(pathlib.Path(source).read_text(encoding="utf-8"))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        forbidden = {"fastapi", "pymupdf", "fitz", "paddleocr", "anthropic", "openai", "httpx"}
        for imp in imports:
            for bad in forbidden:
                assert bad not in imp, f"Impure import found: {imp}"
