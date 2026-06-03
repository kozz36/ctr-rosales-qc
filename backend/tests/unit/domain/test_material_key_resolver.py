"""Unit tests for MaterialKeyResolver strategy (R8.3, ADR-3, ADR-4).

Spec: MAT-006, MAT-012, ADR-3, ADR-4.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from reconciliation.domain.material_key import CanonicalKey
from reconciliation.domain.material_key_normalizer import MaterialKeyNormalizer
from reconciliation.domain.material_key_resolver import MaterialKeyResolver


def _make_resolver(inference=None) -> MaterialKeyResolver:
    return MaterialKeyResolver(MaterialKeyNormalizer(), inference=inference)


class TestDeterministicPath:
    def test_deterministic_returns_canonical_key(self) -> None:
        resolver = _make_resolver()
        result = resolver.resolve('BARRA AG615/A706 G60 1/2" x 9M', "TN")
        assert result.method == "deterministic"
        assert result.familia == "BARRA"
        assert result.grado == "A615 G60"

    def test_deterministic_with_inference_injected_does_not_call_infer(self) -> None:
        mock_inference = MagicMock()
        resolver = _make_resolver(mock_inference)
        result = resolver.resolve('BARRA AG615/A706 G60 1/2" x 9M', "TN")
        assert result.method == "deterministic"
        mock_inference.infer.assert_not_called()

    def test_deterministic_result_not_cached(self) -> None:
        """Deterministic path is cheap — cache is only for LLM results."""
        mock_inference = MagicMock()
        resolver = _make_resolver(mock_inference)
        resolver.resolve('BARRA AG615/A706 G60 1/2" x 9M', "TN")
        resolver.resolve('BARRA AG615/A706 G60 1/2" x 9M', "TN")
        # infer should never be called for deterministic descriptions
        mock_inference.infer.assert_not_called()


class TestLLMFallbackPath:
    def test_ambiguous_calls_infer(self) -> None:
        """Ambiguous description (no presentacion) → infer() is called."""
        mock_inference = MagicMock()
        mock_inference.infer.return_value = MagicMock(
            familia="BARRA",
            grado="A615 G60",
            diametro='1/2"',
            presentacion="9M",
            confidence=0.9,
        )
        resolver = _make_resolver(mock_inference)
        result = resolver.resolve('BARRA AG615/A706 G60 1/2"', "TN")
        mock_inference.infer.assert_called_once()
        assert result.method == "llm_inferred"
        assert result.requires_review is True

    def test_llm_result_memoized(self) -> None:
        """LLM result is cached — second call with same raw uses cache."""
        mock_inference = MagicMock()
        mock_inference.infer.return_value = MagicMock(
            familia="BARRA",
            grado="A615 G60",
            diametro='1/2"',
            presentacion="9M",
            confidence=0.9,
        )
        resolver = _make_resolver(mock_inference)
        raw = 'BARRA AG615/A706 G60 1/2"'
        result1 = resolver.resolve(raw, "TN")
        result2 = resolver.resolve(raw, "TN")
        # infer() called exactly once — second call uses cache
        assert mock_inference.infer.call_count == 1
        assert result1 == result2
        assert result1.method == "llm_inferred"

    def test_llm_returns_none_falls_to_unresolved(self) -> None:
        """If infer() returns None → unresolved sentinel."""
        mock_inference = MagicMock()
        mock_inference.infer.return_value = None
        resolver = _make_resolver(mock_inference)
        result = resolver.resolve('BARRA AG615/A706 G60 1/2"', "TN")
        assert result.method == "unresolved"
        assert result.requires_review is True

    def test_llm_down_returns_unresolved(self) -> None:
        """Ollama down (infer returns None) → unresolved; run continues."""
        mock_inference = MagicMock()
        mock_inference.infer.return_value = None
        resolver = _make_resolver(mock_inference)
        result = resolver.resolve("some totally unknown description", "KG")
        assert result.method == "unresolved"
        assert result.requires_review is True


class TestHallucinationGuard:
    def test_invalid_diameter_falls_to_unresolved(self) -> None:
        """LLM returns diameter not in canonical set → falls to unresolved."""
        mock_inference = MagicMock()
        mock_inference.infer.return_value = MagicMock(
            familia="BARRA",
            grado="A615 G60",
            diametro='99"',  # invalid
            presentacion="9M",
            confidence=0.9,
        )
        resolver = _make_resolver(mock_inference)
        result = resolver.resolve("some ambiguous description", "TN")
        assert result.method == "unresolved"

    def test_invalid_presentacion_falls_to_unresolved(self) -> None:
        """LLM returns presentacion not in {9M, DOB} → falls to unresolved."""
        mock_inference = MagicMock()
        mock_inference.infer.return_value = MagicMock(
            familia="BARRA",
            grado="A615 G60",
            diametro='1/2"',
            presentacion="RECTO",  # not in valid set
            confidence=0.9,
        )
        resolver = _make_resolver(mock_inference)
        result = resolver.resolve("some ambiguous description", "TN")
        assert result.method == "unresolved"

    def test_valid_llm_result_passes_guard(self) -> None:
        """Valid LLM result passes hallucination guard and returns llm_inferred."""
        mock_inference = MagicMock()
        mock_inference.infer.return_value = MagicMock(
            familia="BARRA",
            grado="A615 G60",
            diametro='3/4"',
            presentacion="DOB",
            confidence=0.85,
        )
        resolver = _make_resolver(mock_inference)
        result = resolver.resolve("some ambiguous description", "TN")
        assert result.method == "llm_inferred"


class TestNoInferenceFallback:
    def test_no_inference_returns_unresolved_for_ambiguous(self) -> None:
        """inference=None (default) → unresolved when normalizer returns None."""
        resolver = _make_resolver(inference=None)
        result = resolver.resolve('BARRA AG615/A706 G60 1/2"', "TN")
        # No presentacion signal → deterministic fails → unresolved (no crash)
        assert result.method == "unresolved"
        assert result.requires_review is True

    def test_no_inference_no_crash_on_unknown(self) -> None:
        """MAT-012: inference=None → deterministic-only; no crash on unknown."""
        resolver = _make_resolver(inference=None)
        result = resolver.resolve("COMPLETELY UNKNOWN MATERIAL XYZ", "KG")
        assert result.method == "unresolved"
        assert result.requires_review is True

    def test_inference_none_attribute(self) -> None:
        resolver = _make_resolver(inference=None)
        assert resolver._inference is None
