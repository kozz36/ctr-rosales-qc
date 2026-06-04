"""Unit tests for MaterialKeyNormalizer deterministic regex parser (R8.2).

Spec: MAT-003, MAT-004, MAT-005, MAT-009, MAT-S01–S04, ADR-1, ADR-3.
"""

from __future__ import annotations

import pytest

from reconciliation.domain.material_key import CanonicalKey
from reconciliation.domain.material_key_normalizer import MaterialKeyNormalizer


@pytest.fixture()
def normalizer() -> MaterialKeyNormalizer:
    return MaterialKeyNormalizer()


class TestGradeNormalization:
    """MAT-S01: all grade variants collapse to 'A615 G60'."""

    @pytest.mark.parametrize(
        "raw",
        [
            "BARRA A615/A706 G60 1/2\" x 9M",
            "BARRA AG615/A706 G60 1/2\" x 9M",
            "BARRA A A615-G60 1/2\" X 9M",
            "barra a615 g60 1/2\" x 9m",
            # additional variants
            "BARRA A615 G60 1\" (DOB)",
            "barra a615/a706 g60 3/8\" x 9m",
        ],
    )
    def test_grade_collapses_to_a615_g60(self, normalizer: MaterialKeyNormalizer, raw: str) -> None:
        result = normalizer.parse(raw, "TN")
        assert result is not None, f"Expected non-None for {raw!r}"
        assert result.grado == "A615 G60", f"Expected 'A615 G60' for {raw!r}, got {result.grado!r}"

    def test_grade_deterministic_method(self, normalizer: MaterialKeyNormalizer) -> None:
        result = normalizer.parse("BARRA A615 G60 1/2\" x 9M", "TN")
        assert result is not None
        assert result.method == "deterministic"
        assert result.requires_review is False

    def test_unknown_grade_returns_none(self, normalizer: MaterialKeyNormalizer) -> None:
        """Descriptions with no known grade pattern → parse returns None (ambiguous)."""
        result = normalizer.parse("BARRA ACERO ESPECIAL 1/2\" x 9M", "TN")
        assert result is None


class TestDiameterNormalization:
    """MAT-S02: compound fraction detected before simple fraction."""

    def test_compound_fraction_1_3_8(self, normalizer: MaterialKeyNormalizer) -> None:
        """'1 3/8\"' must not be confused with '1\"' or '3/8\"' — compound first."""
        result = normalizer.parse('BARRA A615 G60 1 3/8" x 9M', "TN")
        assert result is not None
        assert result.diametro == '1 3/8"'

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ('BARRA A615 G60 1" (DOB)', '1"'),
            ('BARRA A615 G60 3/4" x 9M', '3/4"'),
            ('BARRA A615 G60 5/8" x 9M', '5/8"'),
            ('BARRA A615 G60 1/2" x 9M', '1/2"'),
            ('BARRA A615 G60 3/8" x 9M', '3/8"'),
            ("BARRA A615 G60 8mm x 9M", "8mm"),
        ],
    )
    def test_diameter_canonical_set(
        self, normalizer: MaterialKeyNormalizer, raw: str, expected: str
    ) -> None:
        result = normalizer.parse(raw, "TN")
        assert result is not None, f"Expected non-None for {raw!r}"
        assert result.diametro == expected, f"Expected {expected!r} for {raw!r}, got {result.diametro!r}"

    def test_unknown_diameter_returns_none(self, normalizer: MaterialKeyNormalizer) -> None:
        result = normalizer.parse("BARRA A615 G60 2\" x 9M", "TN")
        assert result is None


class TestCompoundFractionSeparators:
    """Issue #28: SUNAT GRE writes the compound fraction with a DOT ('1.3/8'),
    not the whitespace Forma uses ('1 3/8'). The compound pattern must accept
    dot/hyphen/no separator so the guía matches its declared row instead of
    being mis-canonicalized to the bare '3/8"'.
    """

    def test_real_sunat_dot_separator(self, normalizer: MaterialKeyNormalizer) -> None:
        """The REAL SUNAT string (Corporación Aceros Arequipa)."""
        raw = 'ACERO DIMENSIONADO - BARRA A615 G60 1.3/8" DOB Apl'
        result = normalizer.parse(raw, "TN")
        assert result is not None
        assert result.diametro == '1 3/8"', f"Expected '1 3/8\"' for {raw!r}, got {result.diametro!r}"
        assert result.presentacion == "DOB"

    def test_dot_separator_diameter(self, normalizer: MaterialKeyNormalizer) -> None:
        result = normalizer.parse('BARRA A615 G60 1.3/8" DOB', "TN")
        assert result is not None
        assert result.diametro == '1 3/8"', f"got {result.diametro!r}"

    def test_space_separator_still_works(self, normalizer: MaterialKeyNormalizer) -> None:
        """REGRESSION: the original Forma space form must stay '1 3/8\"'."""
        result = normalizer.parse('BARRA A615 G60 1 3/8" DOB', "TN")
        assert result is not None
        assert result.diametro == '1 3/8"', f"got {result.diametro!r}"

    def test_bare_three_eighths_not_promoted(self, normalizer: MaterialKeyNormalizer) -> None:
        """REGRESSION: bare '3/8\"' must NOT be promoted to '1 3/8\"'."""
        result = normalizer.parse('BARRA A615 G60 3/8" DOB', "TN")
        assert result is not None
        assert result.diametro == '3/8"', f"got {result.diametro!r}"

    @pytest.mark.parametrize(
        "raw",
        [
            'BARRA A615 G60 1-3/8" DOB',
            'BARRA A615 G60 13/8" DOB',
        ],
    )
    def test_hyphen_and_no_separator(self, normalizer: MaterialKeyNormalizer, raw: str) -> None:
        """Separator robustness: hyphen and no-separator forms also → '1 3/8\"'."""
        result = normalizer.parse(raw, "TN")
        assert result is not None
        assert result.diametro == '1 3/8"', f"Expected '1 3/8\"' for {raw!r}, got {result.diametro!r}"


class TestPresentacionNormalization:
    """MAT-S03: 9M vs DOB signals — NEVER merged."""

    def test_9m_signal(self, normalizer: MaterialKeyNormalizer) -> None:
        result = normalizer.parse('BARRA A615 G60 1/2" x 9M', "TN")
        assert result is not None
        assert result.presentacion == "9M"

    def test_dob_signal(self, normalizer: MaterialKeyNormalizer) -> None:
        result = normalizer.parse('BARRA A615 G60 1/2" (DOB)', "TN")
        assert result is not None
        assert result.presentacion == "DOB"

    def test_dimensionado_is_dob(self, normalizer: MaterialKeyNormalizer) -> None:
        result = normalizer.parse('BARRA A615 G60 1/2" DIMENSIONADO', "TN")
        assert result is not None
        assert result.presentacion == "DOB"

    def test_apl_is_dob(self, normalizer: MaterialKeyNormalizer) -> None:
        result = normalizer.parse('BARRA A615 G60 1/2" DOB APL', "TN")
        assert result is not None
        assert result.presentacion == "DOB"

    def test_both_9m_and_dob_signals_returns_none(self, normalizer: MaterialKeyNormalizer) -> None:
        """Contradictory: both signals present → ambiguous → None."""
        result = normalizer.parse('BARRA A615 G60 1/2" x 9M DOB', "TN")
        assert result is None

    def test_neither_signal_returns_none(self, normalizer: MaterialKeyNormalizer) -> None:
        """No presentación signal → ambiguous → None."""
        result = normalizer.parse('BARRA A615 G60 1/2"', "TN")
        assert result is None


class TestAceroDimensionado:
    """MAT-S04: 'ACERO DIMENSIONADO - BARRA A615 G60 1" DOB APL' → presentacion=DOB, familia=BARRA."""

    def test_acero_dimensionado_prefix(self, normalizer: MaterialKeyNormalizer) -> None:
        raw = 'ACERO DIMENSIONADO - BARRA A615 G60 1" DOB APL'
        result = normalizer.parse(raw, "TN")
        assert result is not None
        assert result.presentacion == "DOB"
        assert result.familia == "BARRA"
        assert result.method == "deterministic"

    def test_acero_dimensionado_alone_is_barra_dob(self, normalizer: MaterialKeyNormalizer) -> None:
        """'acero dimensionado' alone signals both BARRA and DOB."""
        raw = 'acero dimensionado barra a615 g60 5/8" dimensionado'
        result = normalizer.parse(raw, "TN")
        assert result is not None
        assert result.familia == "BARRA"
        assert result.presentacion == "DOB"


class TestRealPairs:
    """Real declared↔SUNAT pairs must produce equal CanonicalKey instances."""

    def test_declared_side(self, normalizer: MaterialKeyNormalizer) -> None:
        """Real declared text from #4252."""
        result = normalizer.parse('BARRA AG615/A706 G60 1/2" x 9M', "TN")
        assert result is not None
        expected = CanonicalKey(
            familia="BARRA",
            grado="A615 G60",
            diametro='1/2"',
            presentacion="9M",
            unidad="TN",
        )
        assert result == expected

    def test_guia_side_variant_a(self, normalizer: MaterialKeyNormalizer) -> None:
        """Guía variant: 'BARRA A A615-G60 1/2" X 9M'."""
        result = normalizer.parse('BARRA A A615-G60 1/2" X 9M', "TN")
        assert result is not None
        assert result.grado == "A615 G60"
        assert result.diametro == '1/2"'
        assert result.presentacion == "9M"

    def test_guia_side_variant_b(self, normalizer: MaterialKeyNormalizer) -> None:
        result = normalizer.parse('BARRA A615/A706 G60 1/2" X 9M', "TN")
        assert result is not None
        assert result.grado == "A615 G60"
        assert result.diametro == '1/2"'
        assert result.presentacion == "9M"

    def test_guia_side_variant_c(self, normalizer: MaterialKeyNormalizer) -> None:
        result = normalizer.parse('barra a615 g60 1/2" x 9m', "TN")
        assert result is not None
        assert result.grado == "A615 G60"

    def test_declared_equals_guia(self, normalizer: MaterialKeyNormalizer) -> None:
        """Core MAT-013 acceptance case: declared and guía sides produce the same key."""
        declared = normalizer.parse('BARRA AG615/A706 G60 1/2" x 9M', "TN")
        guia = normalizer.parse('BARRA A A615-G60 1/2" X 9M', "TN")
        assert declared is not None
        assert guia is not None
        assert declared == guia

    def test_no_llm_call(self, normalizer: MaterialKeyNormalizer) -> None:
        """Pure regex path — no inference port injected; no crash."""
        # If this test passes without raising AttributeError or similar,
        # it confirms the normalizer is self-contained.
        for raw in [
            'BARRA AG615/A706 G60 1/2" x 9M',
            'BARRA A A615-G60 1/2" X 9M',
            'BARRA A615/A706 G60 1/2" X 9M',
            'barra a615 g60 1/2" x 9m',
        ]:
            result = normalizer.parse(raw, "TN")
            assert result is not None
            assert result.method == "deterministic"


class TestFamilia:
    def test_barra_detected(self, normalizer: MaterialKeyNormalizer) -> None:
        result = normalizer.parse('BARRA A615 G60 1/2" x 9M', "TN")
        assert result is not None
        assert result.familia == "BARRA"

    def test_unknown_familia_returns_none(self, normalizer: MaterialKeyNormalizer) -> None:
        result = normalizer.parse('A615 G60 1/2" x 9M', "TN")
        # No 'BARRA' or 'ACERO DIMENSIONADO' prefix → familia=None → None
        assert result is None
