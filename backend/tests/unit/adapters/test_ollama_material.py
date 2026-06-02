"""Unit tests for OllamaMaterialInferenceAdapter (R8.7, MAT-007, MAT-S11, MAT-012).

Tests:
- MAT-S11: think-block stripping
- Happy path: well-formed JSON → MaterialKeyInference
- Malformed JSON → None (no crash)
- Missing fields → None
- Lazy-import guard: importing module without openai does not crash
- build_inference_adapter factory: enabled/disabled
"""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from reconciliation.domain.models import MaterialKeyInference


class TestThinkBlockStripping:
    """MAT-S11: <think>...</think> blocks must be stripped before JSON parse."""

    def test_think_block_stripped(self) -> None:
        from reconciliation.adapters.inference.ollama_material import (
            OllamaMaterialInferenceAdapter,
        )

        adapter = OllamaMaterialInferenceAdapter(
            model="qwen3.5:9b",
            base_url="http://localhost:11434/v1",
            temperature=0.0,
            timeout_s=30.0,
        )
        raw = (
            "<think>Let me analyze this description carefully...</think>\n"
            '{"familia":"BARRA","grado":"A615 G60","diametro":"1/2\\"","presentacion":"9M","unidad":"TN","needs_review":false}'
        )
        result = adapter._parse_response(raw)
        assert result is not None
        assert result.familia == "BARRA"
        assert result.grado == "A615 G60"

    def test_multiline_think_block_stripped(self) -> None:
        from reconciliation.adapters.inference.ollama_material import (
            OllamaMaterialInferenceAdapter,
        )

        adapter = OllamaMaterialInferenceAdapter(
            model="qwen3.5:9b",
            base_url="http://localhost:11434/v1",
            temperature=0.0,
            timeout_s=30.0,
        )
        raw = (
            "<think>\nI see this is a rebar product.\nLet me extract the fields.\n</think>\n"
            '{"familia":"BARRA","grado":"A615 G60","diametro":"5/8\\"","presentacion":"DOB","unidad":"TN","needs_review":false}'
        )
        result = adapter._parse_response(raw)
        assert result is not None
        assert result.presentacion == "DOB"

    def test_no_think_block_still_works(self) -> None:
        from reconciliation.adapters.inference.ollama_material import (
            OllamaMaterialInferenceAdapter,
        )

        adapter = OllamaMaterialInferenceAdapter(
            model="qwen3.5:9b",
            base_url="http://localhost:11434/v1",
            temperature=0.0,
            timeout_s=30.0,
        )
        raw = '{"familia":"BARRA","grado":"A615 G60","diametro":"1\\"","presentacion":"9M","unidad":"KG","needs_review":false}'
        result = adapter._parse_response(raw)
        assert result is not None
        assert result.diametro == '1"'


class TestParseResponse:
    def test_happy_path_all_fields(self) -> None:
        from reconciliation.adapters.inference.ollama_material import (
            OllamaMaterialInferenceAdapter,
        )

        adapter = OllamaMaterialInferenceAdapter(
            model="qwen3.5:9b",
            base_url="http://localhost:11434/v1",
            temperature=0.0,
            timeout_s=30.0,
        )
        raw = '{"familia":"BARRA","grado":"A615 G60","diametro":"1/2\\"","presentacion":"9M","unidad":"TN","needs_review":false}'
        result = adapter._parse_response(raw)
        assert result is not None
        assert isinstance(result, MaterialKeyInference)
        assert result.familia == "BARRA"
        assert result.grado == "A615 G60"
        assert result.diametro == '1/2"'
        assert result.presentacion == "9M"

    def test_malformed_json_returns_none(self) -> None:
        from reconciliation.adapters.inference.ollama_material import (
            OllamaMaterialInferenceAdapter,
        )

        adapter = OllamaMaterialInferenceAdapter(
            model="qwen3.5:9b",
            base_url="http://localhost:11434/v1",
            temperature=0.0,
            timeout_s=30.0,
        )
        result = adapter._parse_response("this is not json at all")
        assert result is None

    def test_empty_response_returns_none(self) -> None:
        from reconciliation.adapters.inference.ollama_material import (
            OllamaMaterialInferenceAdapter,
        )

        adapter = OllamaMaterialInferenceAdapter(
            model="qwen3.5:9b",
            base_url="http://localhost:11434/v1",
            temperature=0.0,
            timeout_s=30.0,
        )
        result = adapter._parse_response("")
        assert result is None

    def test_missing_familia_returns_none(self) -> None:
        from reconciliation.adapters.inference.ollama_material import (
            OllamaMaterialInferenceAdapter,
        )

        adapter = OllamaMaterialInferenceAdapter(
            model="qwen3.5:9b",
            base_url="http://localhost:11434/v1",
            temperature=0.0,
            timeout_s=30.0,
        )
        raw = '{"grado":"A615 G60","diametro":"1/2\\"","presentacion":"9M"}'
        # familia is required field — schema mismatch → None
        result = adapter._parse_response(raw)
        assert result is None


class TestLazyImportGuard:
    """Importing the adapter module without openai installed must not raise at load time."""

    def test_module_importable_without_openai_at_module_level(self) -> None:
        """The adapter module-level code must not import openai."""
        import importlib

        # Temporarily hide openai from sys.modules to simulate absence
        saved = sys.modules.pop("openai", None)
        try:
            # Re-import the module — should succeed even without openai
            if "reconciliation.adapters.inference.ollama_material" in sys.modules:
                del sys.modules["reconciliation.adapters.inference.ollama_material"]
            mod = importlib.import_module("reconciliation.adapters.inference.ollama_material")
            # Class must be importable
            assert hasattr(mod, "OllamaMaterialInferenceAdapter")
        except ImportError as e:
            pytest.fail(f"Module import failed without openai: {e}")
        finally:
            if saved is not None:
                sys.modules["openai"] = saved


class TestBuildInferenceAdapterFactory:
    def test_factory_returns_none_when_disabled(self) -> None:
        from reconciliation.adapters.inference.factory import build_inference_adapter
        from reconciliation.application.config import AppConfig

        config = AppConfig()
        assert config.inference.enabled is False
        result = build_inference_adapter(config)
        assert result is None

    def test_factory_returns_adapter_when_enabled(self) -> None:
        from reconciliation.adapters.inference.factory import build_inference_adapter
        from reconciliation.adapters.inference.ollama_material import (
            OllamaMaterialInferenceAdapter,
        )
        from reconciliation.application.config import AppConfig, InferenceConfig

        config = AppConfig(inference=InferenceConfig(enabled=True, model="qwen3.5:9b"))
        result = build_inference_adapter(config)
        assert result is not None
        assert isinstance(result, OllamaMaterialInferenceAdapter)
