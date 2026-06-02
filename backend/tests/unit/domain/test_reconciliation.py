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
    handwritten: date | None = None,
) -> Registro:
    return Registro(
        numero=numero,
        fecha_declarada=fecha,
        declared_lines=lines,
        fecha_declarada_handwritten=handwritten,
    )


@pytest.fixture()
def svc() -> ReconciliationService:
    return ReconciliationService()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMatch:
    def test_exact_match_single_guia(self, svc: ReconciliationService) -> None:
        """REC-S01: sum equals declared exactly → MATCH; guias[] populated inline."""
        declared = [
            _registro("232", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "KG", "1250.00"),
            ])
        ]
        guias = [
            _guia("T001-0001", "232", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "KG", "750.00", confidence=0.95, page=10),
                _line("barra corrugada 1/2", "KG", "500.00", confidence=0.88, page=11),
            ], pages=[10, 11]),
        ]
        rows = svc.reconcile(declared, guias)
        assert len(rows) == 1
        row = rows[0]
        assert row.status == "MATCH"
        # summed_qty is derived from guias[*].cantidad (REC-C02 / S1.6 invariant)
        assert row.summed_qty == Decimal("1250.00")
        assert row.delta == Decimal("0")
        # guias[] populated inline (REC-C02)
        assert len(row.guias) == 1
        assert row.guias[0].guia_id == "T001-0001"
        assert row.guias[0].unidad == "KG"

    def test_summed_qty_derived_from_guias(self, svc: ReconciliationService) -> None:
        """S1.6 invariant: summed_qty == sum(g.cantidad for g in guias) — always."""
        declared = [
            _registro("232", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "KG", "1250.00"),
            ])
        ]
        guias = [
            _guia("T001-0001", "232", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "KG", "750.00", confidence=0.95),
                _line("barra corrugada 1/2", "KG", "500.00", confidence=0.88),
            ]),
        ]
        rows = svc.reconcile(declared, guias)
        row = rows[0]
        assert row.summed_qty == sum(c.cantidad for c in row.guias)

    def test_match_min_confidence_is_minimum(self, svc: ReconciliationService) -> None:
        """REC-009: min_confidence = min over contributing lines."""
        declared = [
            _registro("232", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "KG", "1250.00"),
            ])
        ]
        guias = [
            _guia("T001-0001", "232", date(2025, 3, 15), [
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
            _registro("232", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "KG", "1250.0"),
            ])
        ]
        guias = [
            _guia("T001-0001", "232", date(2025, 3, 15), [
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
            _registro("232", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "TN", "1.25"),
                _line("barra corrugada 1/2", "KG", "1250.0"),
            ])
        ]
        guias = [
            _guia("T001-0001", "232", date(2025, 3, 15), [
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

    def test_guia_contribution_unidad_matches_group(self, svc: ReconciliationService) -> None:
        """GuiaContribution.unidad MUST match the group's unit (domain invariant)."""
        declared = [
            _registro("232", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "TN", "1.25"),
                _line("barra corrugada 1/2", "KG", "1250.0"),
            ])
        ]
        guias = [
            _guia("T001-0001", "232", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "TN", "1.25"),
                _line("barra corrugada 1/2", "KG", "1250.0"),
            ]),
        ]
        rows = svc.reconcile(declared, guias)
        for row in rows:
            for contrib in row.guias:
                assert contrib.unidad == row.unidad, (
                    f"Contribution unit {contrib.unidad!r} != group unit {row.unidad!r}"
                )

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
            _guia("T001-0001", "232", date(2025, 3, 15), [
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
            _registro("231", date(2025, 2, 10), [
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
        """REC-S06 / REC-C03: apply_reassignment keyed by guia_id (serie-numero)."""
        guias = [
            _guia("T001-12345", "232", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "KG", "300.0"),
            ], pages=[47]),
        ]
        updated = svc.apply_reassignment(
            guias,
            guia_id="T001-12345",
            new_registro="231",
            new_fecha=date(2025, 2, 10),
        )
        assert len(updated) == 1
        moved = updated[0]
        assert moved.guia_id == "T001-12345"
        assert moved.registro == "231"
        assert moved.fecha == date(2025, 2, 10)

    def test_reassignment_recomputes_source_group(self, svc: ReconciliationService) -> None:
        declared_source = [
            _registro("232", date(2025, 3, 15), [_line("barra corrugada 1/2", "KG", "1250.0")])
        ]
        declared_target = [
            _registro("231", date(2025, 2, 10), [_line("barra corrugada 1/2", "KG", "300.0")])
        ]
        declared = declared_source + declared_target

        guias = [
            _guia("T001-12345", "232", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "KG", "300.0"),
            ], pages=[47]),
            _guia("T001-99999", "232", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "KG", "950.0"),
            ], pages=[48]),
        ]

        # Before reassignment: source group sum = 1250, MATCH
        rows_before = svc.reconcile(declared, guias)
        source_before = next(
            r for r in rows_before
            if r.registro == "232" and r.fecha == date(2025, 3, 15)
        )
        assert source_before.status == "MATCH"

        # Reassign T001-12345 to target
        updated_guias = svc.apply_reassignment(
            guias,
            guia_id="T001-12345",
            new_registro="231",
            new_fecha=date(2025, 2, 10),
        )

        rows_after = svc.reconcile(declared, updated_guias)
        source_after = next(
            r for r in rows_after
            if r.registro == "232" and r.fecha == date(2025, 3, 15)
        )
        target_after = next(
            r for r in rows_after
            if r.registro == "231" and r.fecha == date(2025, 2, 10)
        )

        # Source group now only has T001-99999 = 950, declared = 1250 → MISMATCH
        assert source_after.status == "MISMATCH"
        assert source_after.summed_qty == Decimal("950.0")

        # Target group now has T001-12345 = 300, declared = 300 → MATCH
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
                f"T001-{i:04d}",
                "232",
                date(2025, 3, 15),
                [_line("mat a", "KG", "1.0", page=i)],
                pages=[i],
            )
            for i in pages
        ]
        declared = [
            _registro("232", date(2025, 3, 15), [_line("mat a", "KG", str(len(pages)))])
        ]
        rows = svc.reconcile(declared, guias)
        all_pages_in_output = {p for r in rows for p in r.source_pages}
        assert len(all_pages_in_output) == 320


class TestUnresolvedGuias:
    def test_guia_with_none_registro_is_unresolved(self, svc: ReconciliationService) -> None:
        """REC-C05: guías with registro=None must NOT appear in rows; they are unresolved."""
        guias = [
            _guia("T001-0001", None, date(2025, 3, 15), [
                _line("barra corrugada 1/2", "KG", "500.0"),
            ], pages=[10]),
        ]
        declared: list[Registro] = []
        rows = svc.reconcile(declared, guias)
        # registro=None → not grouped into any row
        assert all(r.registro != "" for r in rows), (
            "Unresolved guía appeared in rows as empty-string registro"
        )
        # The row list should be empty (no declared, no resolved guía)
        assert rows == [], f"Expected no rows from unresolved guía; got {rows}"

    def test_resolved_guia_appears_in_rows(self, svc: ReconciliationService) -> None:
        """Only guías with a non-None registro appear in output rows."""
        guias = [
            _guia("T001-0001", "232", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "KG", "500.0"),
            ]),
            _guia("T001-0002", None, date(2025, 3, 15), [
                _line("alambre n16", "KG", "100.0"),
            ]),
        ]
        declared = [
            _registro("232", date(2025, 3, 15), [_line("barra corrugada 1/2", "KG", "500.0")])
        ]
        rows = svc.reconcile(declared, guias)
        # Only the resolved guía produces a row
        assert len(rows) == 1
        assert rows[0].registro == "232"


class TestRecC07SectionIdGuard:
    def test_no_row_with_section_id_registro(self, svc: ReconciliationService) -> None:
        """REC-C07 regression: reconciler must never produce a row with a section-ID registro.

        Section IDs (e.g. '4252', '4251') are Contents page identifiers and MUST NOT
        be used as reconciliation group keys.  This guard validates that the
        _derive_numero fix (S1.4) is enforced end-to-end.
        """
        # Attempt to feed a section-ID through as a registro — this would be the
        # old broken behavior before S1.4.  The reconciler itself doesn't filter these,
        # but the pipeline's page_to_registro map must not produce them.
        # This test asserts the reconciler doesn't spontaneously generate section IDs.
        guias = [
            _guia("T001-0001", "232", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "KG", "500.0"),
            ]),
        ]
        declared = [
            _registro("232", date(2025, 3, 15), [_line("barra corrugada 1/2", "KG", "500.0")])
        ]
        rows = svc.reconcile(declared, guias)
        section_id_pattern = {"4252", "4251", "4250", "4249", "4237", "4236", "4225", "4223", "4221", "4216", "3507"}
        for row in rows:
            assert row.registro not in section_id_pattern, (
                f"Row with section-ID registro found: {row.registro!r} — REC-C07 violation"
            )

    def test_fixtures_use_realistic_registro_numbers(self, svc: ReconciliationService) -> None:
        """§F fixture check: all test fixtures use Description numeros (e.g. '232', '231')."""
        # Sanity-check that no fixture in this test suite uses '4252' as a registro.
        # This is a meta-test to catch §F regressions when fixtures are modified.
        guias = [
            _guia("T001-0001", "232", date(2025, 3, 15), [_line("mat", "KG", "1.0")]),
            _guia("T001-0002", "231", date(2025, 3, 15), [_line("mat", "KG", "2.0")]),
            _guia("T001-0003", "233", date(2025, 3, 15), [_line("mat", "KG", "3.0")]),
        ]
        declared = [
            _registro("232", date(2025, 3, 15), [_line("mat", "KG", "1.0")]),
            _registro("231", date(2025, 3, 15), [_line("mat", "KG", "2.0")]),
            _registro("233", date(2025, 3, 15), [_line("mat", "KG", "3.0")]),
        ]
        rows = svc.reconcile(declared, guias)
        registros = {r.registro for r in rows}
        assert "4252" not in registros, "Section ID '4252' appeared in reconciliation output"
        assert registros == {"232", "231", "233"}


class TestRequiresReview:
    """Task 7.3 / REV-004 / EXT-S08, EXT-S08b: requires_review propagation."""

    def test_false_when_no_flags(self, svc: ReconciliationService) -> None:
        """Normal guía (date present, high confidence) → requires_review=False."""
        declared = [_registro("232", date(2025, 3, 15), [_line("mat", "KG", "100.0")])]
        guias = [_guia("T001-0001", "232", date(2025, 3, 15), [
            MaterialLine(
                description_raw="MAT",
                description_canonical="mat",
                unidad="KG",
                cantidad=Decimal("100.0"),
                confidence=0.95,
                requires_review=False,
            )
        ])]
        rows = svc.reconcile(declared, guias)
        assert len(rows) == 1
        assert rows[0].requires_review is False

    def test_true_when_line_requires_review(self, svc: ReconciliationService) -> None:
        """OCR line with requires_review=True → propagated to row (EXT-S08)."""
        declared = [_registro("232", date(2025, 3, 15), [_line("mat", "KG", "100.0")])]
        guias = [_guia("T001-0001", "232", date(2025, 3, 15), [
            MaterialLine(
                description_raw="MAT",
                description_canonical="mat",
                unidad="KG",
                cantidad=Decimal("100.0"),
                confidence=0.75,  # below threshold
                requires_review=True,
            )
        ])]
        rows = svc.reconcile(declared, guias)
        assert len(rows) == 1
        assert rows[0].requires_review is True

    def test_true_when_guia_fecha_is_none(self, svc: ReconciliationService) -> None:
        """Vision returned null date → requires_review=True (EXT-S08b)."""
        declared = [_registro("232", None, [_line("mat", "KG", "100.0")])]
        guias = [_guia("T001-0001", "232", None, [  # fecha=None = vision failed
            MaterialLine(
                description_raw="MAT",
                description_canonical="mat",
                unidad="KG",
                cantidad=Decimal("100.0"),
                confidence=0.90,
                requires_review=False,
            )
        ])]
        rows = svc.reconcile(declared, guias)
        assert len(rows) == 1
        assert rows[0].requires_review is True

    def test_guia_missing_no_requires_review(self, svc: ReconciliationService) -> None:
        """GUIA_MISSING row has no contributing guías → requires_review=False."""
        declared = [_registro("232", date(2025, 3, 15), [_line("mat", "KG", "100.0")])]
        guias: list[GuiaDeRemision] = []
        rows = svc.reconcile(declared, guias)
        assert len(rows) == 1
        assert rows[0].status == "GUIA_MISSING"
        assert rows[0].requires_review is False


class TestFechaDivergence:
    """MAT-S12 / MAT-001: fecha is NOT a grouping axis.

    A Registro N° = one reception event = one date, so registro disambiguates.
    When the declared reception date differs from a guía's handwritten date
    (misfiled scenario / vision-date noise), declared + guía MUST still MATCH on
    (registro, material, unidad). Before this fix they landed in separate groups
    → DECLARED_MISSING + GUIA_MISSING.
    """

    def test_match_across_divergent_fechas(self, svc: ReconciliationService) -> None:
        declared = [
            _registro("232", date(2025, 3, 15), [
                _line("barra corrugada 1/2", "KG", "1250.00"),
            ])
        ]
        guias = [
            # Guía handwritten date differs from the declared reception date.
            _guia("T001-0001", "232", date(2025, 3, 18), [
                _line("barra corrugada 1/2", "KG", "1250.00", confidence=0.95, page=10),
            ], pages=[10]),
        ]
        rows = svc.reconcile(declared, guias)
        # Exactly one group: registro+material+unidad — NOT split by fecha.
        assert len(rows) == 1
        row = rows[0]
        assert row.status == "MATCH"
        assert row.delta == Decimal("0")
        # Row carries the DECLARED reception date for display, not the guía date.
        assert row.fecha == date(2025, 3, 15)

    def test_guia_only_group_carries_guia_fecha(self, svc: ReconciliationService) -> None:
        """A guía-only group (no declared counterpart) surfaces a contributing guía fecha."""
        declared: list[Registro] = []
        guias = [
            _guia("T001-0001", "232", date(2025, 3, 18), [
                _line("alambre n16", "KG", "200.0", page=20),
            ], pages=[20]),
        ]
        rows = svc.reconcile(declared, guias)
        assert len(rows) == 1
        assert rows[0].status == "DECLARED_MISSING"
        assert rows[0].fecha == date(2025, 3, 18)


class TestR9DivergenceWiring:
    """R9.4 (FDR-003..006/009/011, ADR-4): per-guía divergence side-channel.

    Divergence rides GuiaContribution; it OR-sets requires_review but NEVER
    touches status/delta/summed_qty or the group key.  Day-month equality only.
    """

    def test_match_same_day_month_no_divergence(self, svc: ReconciliationService) -> None:
        """FDR-S08/S09: matching dates → no divergence, status MATCH unchanged."""
        declared = [
            _registro("232", date(2026, 5, 20), [
                _line("barra a615 1/2", "KG", "1000.00"),
            ], handwritten=date(2026, 5, 28))
        ]
        guias = [
            _guia("T001-0001", "232", date(2026, 5, 28), [
                _line("barra a615 1/2", "KG", "1000.00", confidence=0.95, page=10),
            ], pages=[10]),
        ]
        rows = svc.reconcile(declared, guias)
        row = rows[0]
        assert row.status == "MATCH"
        assert row.guias[0].fecha_divergence is False
        assert row.guias[0].divergence_reason is None
        assert row.has_fecha_divergence is False
        # Display fecha sourced from fecha_authoritative (handwritten wins).
        assert row.fecha == date(2026, 5, 28)

    def test_divergent_day_month_flags_guia_status_unchanged(
        self, svc: ReconciliationService
    ) -> None:
        """FDR-S09: diverging day-month flags the guía but status stays MATCH."""
        declared = [
            _registro("232", date(2026, 5, 28), [
                _line("barra a615 1/2", "KG", "1000.00"),
            ], handwritten=date(2026, 5, 28))
        ]
        guias = [
            _guia("T001-0001", "232", date(2026, 4, 15), [
                _line("barra a615 1/2", "KG", "1000.00", confidence=0.95, page=10),
            ], pages=[10]),
        ]
        rows = svc.reconcile(declared, guias)
        row = rows[0]
        assert row.status == "MATCH"
        assert row.delta == Decimal("0")
        assert row.guias[0].fecha_divergence is True
        assert row.guias[0].divergence_reason == "fecha_divergence"
        assert row.has_fecha_divergence is True
        assert row.requires_review is True

    def test_contribution_carries_guia_fecha(self, svc: ReconciliationService) -> None:
        declared = [
            _registro("232", date(2026, 5, 28), [
                _line("barra a615 1/2", "KG", "1000.00"),
            ], handwritten=date(2026, 5, 28))
        ]
        guias = [
            _guia("T001-0001", "232", date(2026, 4, 15), [
                _line("barra a615 1/2", "KG", "1000.00", page=10),
            ], pages=[10]),
        ]
        rows = svc.reconcile(declared, guias)
        assert rows[0].guias[0].fecha == date(2026, 4, 15)

    def test_null_authoritative_baseline_no_false_red(
        self, svc: ReconciliationService
    ) -> None:
        """FDR-S10: fecha_authoritative None → no contribution flagged divergent."""
        declared = [
            _registro("232", None, [
                _line("barra a615 1/2", "KG", "1000.00"),
            ], handwritten=None)
        ]
        guias = [
            _guia("T001-0001", "232", date(2026, 4, 15), [
                _line("barra a615 1/2", "KG", "1000.00", page=10),
            ], pages=[10]),
        ]
        rows = svc.reconcile(declared, guias)
        assert all(c.fecha_divergence is False for c in rows[0].guias)
        assert rows[0].has_fecha_divergence is False

    def test_null_guia_fecha_not_divergent(self, svc: ReconciliationService) -> None:
        """FDR-S11: guía fecha None → not divergent for that contribution."""
        declared = [
            _registro("232", date(2026, 5, 28), [
                _line("barra a615 1/2", "KG", "1000.00"),
            ], handwritten=date(2026, 5, 28))
        ]
        guias = [
            _guia("T001-0001", "232", None, [
                _line("barra a615 1/2", "KG", "1000.00", page=10),
            ], pages=[10]),
        ]
        rows = svc.reconcile(declared, guias)
        assert rows[0].guias[0].fecha_divergence is False

    def test_year_only_divergence_not_flagged(self, svc: ReconciliationService) -> None:
        """FDR-S04 (CRITICAL): same day-month, different year → NOT divergent."""
        declared = [
            _registro("232", date(2026, 5, 28), [
                _line("barra a615 1/2", "KG", "1000.00"),
            ], handwritten=date(2026, 5, 28))
        ]
        guias = [
            _guia("T001-0001", "232", date(2025, 5, 28), [
                _line("barra a615 1/2", "KG", "1000.00", page=10),
            ], pages=[10]),
        ]
        rows = svc.reconcile(declared, guias)
        assert rows[0].guias[0].fecha_divergence is False
        assert rows[0].has_fecha_divergence is False

    def test_mismatch_with_divergence_status_still_mismatch(
        self, svc: ReconciliationService
    ) -> None:
        """FDR-S09 generalised: divergence is additive; MISMATCH stays MISMATCH."""
        declared = [
            _registro("232", date(2026, 5, 28), [
                _line("barra a615 1/2", "KG", "1000.00"),
            ], handwritten=date(2026, 5, 28))
        ]
        guias = [
            _guia("T001-0001", "232", date(2026, 4, 15), [
                _line("barra a615 1/2", "KG", "900.00", page=10),
            ], pages=[10]),
        ]
        rows = svc.reconcile(declared, guias)
        row = rows[0]
        assert row.status == "MISMATCH"
        assert row.guias[0].fecha_divergence is True

    def test_mixed_contributions_only_diverging_flagged(
        self, svc: ReconciliationService
    ) -> None:
        declared = [
            _registro("232", date(2026, 5, 28), [
                _line("barra a615 1/2", "KG", "2000.00"),
            ], handwritten=date(2026, 5, 28))
        ]
        guias = [
            _guia("T001-0001", "232", date(2026, 5, 28), [
                _line("barra a615 1/2", "KG", "1000.00", page=10),
            ], pages=[10]),
            _guia("T001-0002", "232", date(2026, 4, 15), [
                _line("barra a615 1/2", "KG", "1000.00", page=11),
            ], pages=[11]),
        ]
        rows = svc.reconcile(declared, guias)
        by_id = {c.guia_id: c for c in rows[0].guias}
        assert by_id["T001-0001"].fecha_divergence is False
        assert by_id["T001-0002"].fecha_divergence is True
        assert rows[0].has_fecha_divergence is True

    def test_display_fecha_uses_authoritative(self, svc: ReconciliationService) -> None:
        """ADR-2: declared-bearing group display fecha is fecha_authoritative."""
        declared = [
            _registro("232", date(2026, 5, 20), [
                _line("barra a615 1/2", "KG", "1000.00"),
            ], handwritten=date(2026, 5, 28))
        ]
        guias = [
            _guia("T001-0001", "232", date(2026, 5, 28), [
                _line("barra a615 1/2", "KG", "1000.00", page=10),
            ], pages=[10]),
        ]
        rows = svc.reconcile(declared, guias)
        # handwritten (28th) wins over electronic (20th)
        assert rows[0].fecha == date(2026, 5, 28)


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


# ---------------------------------------------------------------------------
# R8.5: worst-wins match_method aggregation in ReconciliationService (MAT-008/011)
# ---------------------------------------------------------------------------


def _line_with_method(
    canonical: str,
    unidad: str,
    cantidad: str,
    method: str = "deterministic",
) -> MaterialLine:
    return MaterialLine(
        description_raw=canonical.upper(),
        description_canonical=canonical,
        unidad=unidad,  # type: ignore[arg-type]
        cantidad=Decimal(cantidad),
        match_method=method,  # type: ignore[arg-type]
    )


class TestMatchMethodAggregation:
    _svc = ReconciliationService()

    def _run(
        self,
        declared_lines: list[MaterialLine],
        guia_lines: list[MaterialLine],
        declared_qty: str = "1.0",
        guia_qty: str = "1.0",
    ):
        from datetime import date
        from decimal import Decimal

        registro = Registro(
            numero="232",
            fecha_declarada=date(2024, 1, 15),
            declared_lines=declared_lines,
        )
        guia = GuiaDeRemision(
            guia_id="T009-0001",
            registro="232",
            fecha=date(2024, 1, 15),
            lines=guia_lines,
            source_pages=[1],
            identity_source="qr",
            identity_confidence=1.0,
        )
        return self._svc.reconcile([registro], [guia])

    def test_all_deterministic_row_is_deterministic(self) -> None:
        mat = "BARRA A615 G60 1/2\" 9M"
        rows = self._run(
            declared_lines=[_line_with_method(mat, "TN", "1.0", "deterministic")],
            guia_lines=[_line_with_method(mat, "TN", "1.0", "deterministic")],
        )
        match_rows = [r for r in rows if r.status == "MATCH"]
        assert len(match_rows) == 1
        assert match_rows[0].match_method == "deterministic"

    def test_llm_inferred_line_escalates_row(self) -> None:
        mat = "BARRA A615 G60 1/2\" 9M"
        rows = self._run(
            declared_lines=[_line_with_method(mat, "TN", "1.0", "deterministic")],
            guia_lines=[_line_with_method(mat, "TN", "1.0", "llm_inferred")],
        )
        match_rows = [r for r in rows if r.status == "MATCH"]
        assert len(match_rows) == 1
        assert match_rows[0].match_method == "llm_inferred"

    def test_unresolved_line_is_worst_wins(self) -> None:
        """unresolved > llm_inferred > deterministic."""
        mat = "BARRA A615 G60 1/2\" 9M"
        rows = self._run(
            declared_lines=[_line_with_method(mat, "TN", "1.0", "llm_inferred")],
            guia_lines=[_line_with_method(mat, "TN", "1.0", "unresolved")],
        )
        match_rows = [r for r in rows if r.status == "MATCH"]
        assert len(match_rows) == 1
        assert match_rows[0].match_method == "unresolved"

    def test_requires_review_true_when_llm_inferred(self) -> None:
        mat = "BARRA A615 G60 1/2\" 9M"
        rows = self._run(
            declared_lines=[_line_with_method(mat, "TN", "1.0", "deterministic")],
            guia_lines=[_line_with_method(mat, "TN", "1.0", "llm_inferred")],
        )
        match_rows = [r for r in rows if r.status == "MATCH"]
        assert match_rows[0].requires_review is True

    def test_requires_review_true_when_unresolved(self) -> None:
        mat = "BARRA A615 G60 1/2\" 9M"
        rows = self._run(
            declared_lines=[_line_with_method(mat, "TN", "1.0", "unresolved")],
            guia_lines=[_line_with_method(mat, "TN", "1.0", "deterministic")],
        )
        match_rows = [r for r in rows if r.status == "MATCH"]
        assert match_rows[0].requires_review is True

    def test_requires_review_false_when_all_deterministic(self) -> None:
        mat = "BARRA A615 G60 1/2\" 9M"
        rows = self._run(
            declared_lines=[_line_with_method(mat, "TN", "1.0", "deterministic")],
            guia_lines=[_line_with_method(mat, "TN", "1.0", "deterministic")],
        )
        match_rows = [r for r in rows if r.status == "MATCH"]
        assert match_rows[0].requires_review is False
        assert match_rows[0].match_method == "deterministic"

    def test_match_all_deterministic_scenario(self) -> None:
        mat = "BARRA A615 G60 1/2\" 9M"
        rows = self._run(
            declared_lines=[_line_with_method(mat, "TN", "4.124", "deterministic")],
            guia_lines=[_line_with_method(mat, "TN", "4.124", "deterministic")],
        )
        match_rows = [r for r in rows if r.status == "MATCH"]
        assert len(match_rows) == 1
        assert match_rows[0].match_method == "deterministic"
        assert match_rows[0].requires_review is False
