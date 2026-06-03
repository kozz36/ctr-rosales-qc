"""Unit tests for CanonicalKey VO and MatchMethod literal (R8.1, MAT-002).

Spec: MAT-002, MAT-005, MAT-008, MAT-010, ADR-1.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from reconciliation.domain.material_key import CanonicalKey, MatchMethod


class TestMatchMethod:
    def test_valid_literals(self) -> None:
        for method in ("deterministic", "llm_inferred", "codigo_sunat", "unresolved"):
            # Should not raise when used as a MatchMethod value
            key = CanonicalKey(
                familia="BARRA",
                grado="A615 G60",
                diametro='1/2"',
                presentacion="9M",
                unidad="TN",
                method=method,  # type: ignore[arg-type]
            )
            assert key.method == method


class TestCanonicalKeyFrozen:
    def test_mutating_field_raises(self) -> None:
        key = CanonicalKey(
            familia="BARRA",
            grado="A615 G60",
            diametro='1/2"',
            presentacion="9M",
            unidad="TN",
        )
        with pytest.raises((ValidationError, TypeError)):
            key.familia = "CABLE"  # type: ignore[misc]


class TestCanonicalKeyRequiresReview:
    def test_deterministic_not_requires_review(self) -> None:
        key = CanonicalKey(
            familia="BARRA",
            grado="A615 G60",
            diametro='1/2"',
            presentacion="9M",
            unidad="TN",
            method="deterministic",
        )
        assert key.requires_review is False

    def test_llm_inferred_requires_review(self) -> None:
        key = CanonicalKey(
            familia="BARRA",
            grado="A615 G60",
            diametro='1/2"',
            presentacion="9M",
            unidad="TN",
            method="llm_inferred",
        )
        assert key.requires_review is True

    def test_unresolved_requires_review(self) -> None:
        key = CanonicalKey(
            familia="BARRA",
            grado=None,
            diametro=None,
            presentacion=None,
            unidad="KG",
            method="unresolved",
        )
        assert key.requires_review is True

    def test_codigo_sunat_not_requires_review(self) -> None:
        key = CanonicalKey(
            familia="BARRA",
            grado="A615 G60",
            diametro='1/2"',
            presentacion="9M",
            unidad="TN",
            method="codigo_sunat",
        )
        assert key.requires_review is False


class TestCanonicalKeyGroupToken:
    def test_group_token_resolved_format(self) -> None:
        key = CanonicalKey(
            familia="BARRA",
            grado="A615 G60",
            diametro='1/2"',
            presentacion="9M",
            unidad="TN",
        )
        token = key.group_token
        assert "BARRA" in token
        assert "A615 G60" in token
        assert '1/2"' in token
        assert "9M" in token
        # unidad MUST be excluded from group_token (ADR-1: _GroupKey carries unidad)
        assert "TN" not in token

    def test_group_token_unresolved_starts_with_prefix(self) -> None:
        key = CanonicalKey.unresolved("some weird text", "KG")
        assert key.group_token.startswith("UNRESOLVED::")

    def test_group_token_none_fields_use_placeholder(self) -> None:
        key = CanonicalKey(
            familia="BARRA",
            grado=None,
            diametro=None,
            presentacion=None,
            unidad="TN",
            method="llm_inferred",
        )
        token = key.group_token
        # For non-unresolved keys with None fields, uses "?" placeholder per spec
        assert "?" in token

    def test_unidad_excluded_from_group_token(self) -> None:
        """Explicit check: unidad must not appear in group_token (ADR-1 invariant)."""
        for unit in ("KG", "TN", "RD", "Rollo"):
            key = CanonicalKey(
                familia="BARRA",
                grado="A615 G60",
                diametro='3/8"',
                presentacion="DOB",
                unidad=unit,  # type: ignore[arg-type]
            )
            assert unit not in key.group_token, f"unit {unit!r} must not be in group_token"


class TestCanonicalKeyEquality:
    def test_equal_instances_compare_equal(self) -> None:
        key1 = CanonicalKey(
            familia="BARRA",
            grado="A615 G60",
            diametro='1/2"',
            presentacion="9M",
            unidad="TN",
        )
        key2 = CanonicalKey(
            familia="BARRA",
            grado="A615 G60",
            diametro='1/2"',
            presentacion="9M",
            unidad="TN",
        )
        assert key1 == key2

    def test_different_presentacion_not_equal(self) -> None:
        """MAT-005: 9M and DOB are NEVER merged — different presentacion → different key."""
        key_9m = CanonicalKey(
            familia="BARRA",
            grado="A615 G60",
            diametro='1/2"',
            presentacion="9M",
            unidad="TN",
        )
        key_dob = CanonicalKey(
            familia="BARRA",
            grado="A615 G60",
            diametro='1/2"',
            presentacion="DOB",
            unidad="TN",
        )
        assert key_9m != key_dob

    def test_different_unidad_not_equal(self) -> None:
        key_kg = CanonicalKey(
            familia="BARRA",
            grado="A615 G60",
            diametro='1/2"',
            presentacion="9M",
            unidad="KG",
        )
        key_tn = CanonicalKey(
            familia="BARRA",
            grado="A615 G60",
            diametro='1/2"',
            presentacion="9M",
            unidad="TN",
        )
        assert key_kg != key_tn


class TestCanonicalKeyUnresolvedFactory:
    def test_unresolved_factory_sets_method(self) -> None:
        key = CanonicalKey.unresolved("weird description X", "TN")
        assert key.method == "unresolved"

    def test_unresolved_factory_requires_review(self) -> None:
        key = CanonicalKey.unresolved("weird description X", "TN")
        assert key.requires_review is True

    def test_unresolved_raw_preserved(self) -> None:
        raw = "some ambiguous text"
        key = CanonicalKey.unresolved(raw, "KG")
        assert raw in key.group_token

    def test_unresolved_valid_unidad(self) -> None:
        for unit in ("KG", "TN", "RD", "Rollo"):
            key = CanonicalKey.unresolved("some raw", unit)  # type: ignore[arg-type]
            assert key.unidad == unit
