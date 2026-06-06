"""Tier-2 grade-tolerant reconciliation pass (material-canonical-matching fix).

A guía line whose grade token is an OCR MISREAD (e.g. ``580``/``680``/``660`` for
G60) fails deterministic parse → lands UNRESOLVED → false DECLARED_MISSING. When
its NON-grade attributes (familia, diámetro, presentación) uniquely identify a
single declared item IN THE SAME REGISTRO, the line is merged into that declared
group and flagged ``requires_review=True`` (never silently auto-accepted —
reconciliation is the validation gate).

Guards encoded here:
- EXACTLY ONE same-registro declared match → merge + requires_review + match_method.
- ZERO or MORE-THAN-ONE declared match (ambiguous) → stays UNRESOLVED (no guess).
- A line with a VALID but genuinely DIFFERENT grade (real G75) → NOT force-matched.
- No cross-registro leakage.
- Units never converted; fecha never a grouping axis.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from reconciliation.domain.material_key_normalizer import MaterialKeyNormalizer
from reconciliation.domain.models import GuiaDeRemision, MaterialLine, Registro
from reconciliation.domain.reconciliation import ReconciliationService


# ---------------------------------------------------------------------------
# Helpers — produce REALISTIC lines (canonical = group_token, as the pipeline writes)
# ---------------------------------------------------------------------------

_NORM = MaterialKeyNormalizer()


def _declared_line(raw: str, unidad: str, cantidad: str) -> MaterialLine:
    """A clean declared (Forma) line — resolved deterministically to its group_token."""
    key = _NORM.parse(raw, unidad)
    assert key is not None, f"declared fixture must resolve deterministically: {raw!r}"
    return MaterialLine(
        description_raw=raw,
        description_canonical=key.group_token,
        unidad=unidad,  # type: ignore[arg-type]
        cantidad=Decimal(cantidad),
    )


def _unresolved_guia_line(raw: str, unidad: str, cantidad: str) -> MaterialLine:
    """A guía line the deterministic normalizer could NOT resolve (grade misread)."""
    key = _NORM.parse(raw, unidad)
    assert key is None, f"guia fixture must be UNRESOLVED for this test: {raw!r}"
    from reconciliation.domain.material_key import CanonicalKey

    sentinel = CanonicalKey.unresolved(raw, unidad)  # type: ignore[arg-type]
    return MaterialLine(
        description_raw=raw,
        description_canonical=sentinel.group_token,
        unidad=unidad,  # type: ignore[arg-type]
        cantidad=Decimal(cantidad),
        match_method="unresolved",
        requires_review=True,
    )


def _registro(numero: str, lines: list[MaterialLine]) -> Registro:
    return Registro(numero=numero, fecha_declarada=None, declared_lines=lines)


def _guia(guia_id: str, registro: str, lines: list[MaterialLine]) -> GuiaDeRemision:
    return GuiaDeRemision(
        guia_id=guia_id, registro=registro, fecha=None, lines=lines, source_pages=[5]
    )


@pytest.fixture()
def svc() -> ReconciliationService:
    return ReconciliationService()


# ---------------------------------------------------------------------------
# Category B — grade misread, unique same-registro declared match → MERGE
# ---------------------------------------------------------------------------

class TestGradeTolerantMerge:
    def test_unique_match_merges_with_requires_review(self, svc: ReconciliationService) -> None:
        declared = [
            _registro("227", [
                _declared_line('BARRA A615 G60 3/4" DOB', "TN", "10.000"),
            ])
        ]
        guias = [
            _guia("T001-1", "227", [
                # OCR misread grade '680' for G60 — same diámetro/presentación.
                _unresolved_guia_line('barra a615a706 680 3/4" dob api', "TN", "10.000"),
            ])
        ]
        rows = svc.reconcile(declared, guias)
        # Exactly one row for the declared group; the guía merged into it.
        match_rows = [r for r in rows if r.material_canonical == 'BARRA A615 G60 3/4" DOB']
        assert len(match_rows) == 1
        row = match_rows[0]
        assert row.status == "MATCH"
        assert row.summed_qty == Decimal("10.000")
        assert row.requires_review is True
        assert row.match_method == "grade_tolerant"
        # No leftover UNRESOLVED row.
        assert not any(r.material_canonical.startswith("UNRESOLVED::") for r in rows)

    @pytest.mark.parametrize(
        "guia_raw,declared_raw,diam",
        [
            ('barra a6151a706 580 3/4" dob apl', 'BARRA A615 G60 3/4" DOB', '3/4"'),
            ('barra a615a706 680 5/8" dob api', 'BARRA A615 G60 5/8" DOB', '5/8"'),
            ('barra a6151a706 580 3/8" dob apl', 'BARRA A615 G60 3/8" DOB', '3/8"'),
            ('barra a6151a706 580 1/2" dob apl', 'BARRA A615 G60 1/2" DOB', '1/2"'),
            ('barra a615a706 680 3/4" dob api', 'BARRA A615 G60 3/4" DOB', '3/4"'),
            ('barra a615a706 660 1/2" dob api', 'BARRA A615 G60 1/2" DOB', '1/2"'),
            ('barra a6151a706 580 5/8" dob apl', 'BARRA A615 G60 5/8" DOB', '5/8"'),
            ('barra a615a706 680 3/8" dob api', 'BARRA A615 G60 3/8" DOB', '3/8"'),
        ],
    )
    def test_full_category_b_corpus_merges(
        self, svc: ReconciliationService, guia_raw: str, declared_raw: str, diam: str
    ) -> None:
        declared = [_registro("227", [_declared_line(declared_raw, "TN", "5.000")])]
        guias = [_guia("G1", "227", [_unresolved_guia_line(guia_raw, "TN", "5.000")])]
        rows = svc.reconcile(declared, guias)
        target = [r for r in rows if r.material_canonical == declared[0].declared_lines[0].description_canonical]
        assert len(target) == 1, f"{guia_raw!r} should merge into {declared_raw!r}"
        assert target[0].status == "MATCH"
        assert target[0].requires_review is True
        assert target[0].match_method == "grade_tolerant"
        assert not any(r.material_canonical.startswith("UNRESOLVED::") for r in rows)


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

class TestGradeTolerantGuards:
    def test_ambiguous_two_declared_grades_stays_unresolved(
        self, svc: ReconciliationService
    ) -> None:
        """Registro has BOTH G60 and G75 at the same diámetro/presentación →
        the misread line matches two declared items → leave UNRESOLVED."""
        declared = [
            _registro("227", [
                _declared_line('BARRA A615 G60 3/4" DOB', "TN", "4.000"),
                _declared_line('BARRA A615 G75 3/4" DOB', "TN", "4.000"),
            ])
        ]
        guias = [
            _guia("G1", "227", [
                _unresolved_guia_line('barra a615a706 680 3/4" dob api', "TN", "4.000"),
            ])
        ]
        rows = svc.reconcile(declared, guias)
        assert any(r.material_canonical.startswith("UNRESOLVED::") for r in rows), (
            "ambiguous grade match must NOT be force-merged"
        )

    def test_real_different_grade_not_force_matched(self, svc: ReconciliationService) -> None:
        """A guía with a VALID but DIFFERENT grade (real G75) must NOT be merged
        into a G60 declared — it resolves deterministically to its own G75 key."""
        declared = [
            _registro("227", [
                _declared_line('BARRA A615 G60 3/4" DOB', "TN", "4.000"),
            ])
        ]
        guias = [
            _guia("G1", "227", [
                # Valid G75 grade — parses deterministically, distinct key.
                _declared_line('barra a615 g75 3/4" dob', "TN", "4.000"),
            ])
        ]
        rows = svc.reconcile(declared, guias)
        g60_row = [r for r in rows if r.material_canonical == 'BARRA A615 G60 3/4" DOB']
        g75_row = [r for r in rows if r.material_canonical == 'BARRA A615 G75 3/4" DOB']
        assert len(g60_row) == 1 and g60_row[0].status == "GUIA_MISSING"
        assert len(g75_row) == 1 and g75_row[0].status == "DECLARED_MISSING"

    def test_zero_declared_match_stays_unresolved(self, svc: ReconciliationService) -> None:
        """No same-registro declared item with matching attributes → UNRESOLVED."""
        declared = [
            _registro("227", [
                _declared_line('BARRA A615 G60 1/2" DOB', "TN", "4.000"),
            ])
        ]
        guias = [
            _guia("G1", "227", [
                # diámetro 3/4" — no declared 3/4" in this registro.
                _unresolved_guia_line('barra a615a706 680 3/4" dob api', "TN", "4.000"),
            ])
        ]
        rows = svc.reconcile(declared, guias)
        assert any(r.material_canonical.startswith("UNRESOLVED::") for r in rows)

    def test_no_cross_registro_leak(self, svc: ReconciliationService) -> None:
        """The matching declared item is in a DIFFERENT registro → no merge."""
        declared = [
            _registro("227", [_declared_line('BARRA A615 G60 5/8" DOB', "TN", "4.000")]),
            _registro("999", [_declared_line('BARRA A615 G60 3/4" DOB', "TN", "4.000")]),
        ]
        guias = [
            _guia("G1", "227", [
                _unresolved_guia_line('barra a615a706 680 3/4" dob api', "TN", "4.000"),
            ])
        ]
        rows = svc.reconcile(declared, guias)
        # The 3/4" declared lives in 999; the guía is in 227 → must NOT merge.
        leaked = [
            r for r in rows
            if r.registro == "999" and r.status == "MATCH"
        ]
        assert not leaked, "Tier-2 must only match within the same registro"
        assert any(r.material_canonical.startswith("UNRESOLVED::") for r in rows)

    def test_unidad_must_match(self, svc: ReconciliationService) -> None:
        """Different unidad → not a match (units never converted)."""
        declared = [
            _registro("227", [_declared_line('BARRA A615 G60 3/4" DOB', "TN", "4.000")]),
        ]
        guias = [
            _guia("G1", "227", [
                _unresolved_guia_line('barra a615a706 680 3/4" dob api', "KG", "4.000"),
            ])
        ]
        rows = svc.reconcile(declared, guias)
        assert any(r.material_canonical.startswith("UNRESOLVED::") for r in rows)
