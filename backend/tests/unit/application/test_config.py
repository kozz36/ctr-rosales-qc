"""Tests for AppConfig — pydantic-settings configuration.

All tests run without any real environment variables; monkeypatching is used
to inject values where needed.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from reconciliation.application.config import (
    AppConfig,
    ConfidenceConfig,
    DeskewConfig,
    InferenceConfig,
    OcrConfig,
    SunatConfig,
    VisionConfig,
)


# ---------------------------------------------------------------------------
# AppConfig defaults
# ---------------------------------------------------------------------------


class TestAppConfigDefaults:
    def test_vision_provider_default(self) -> None:
        cfg = AppConfig()
        assert cfg.vision.provider == "anthropic"

    def test_max_vision_calls_default(self) -> None:
        cfg = AppConfig()
        assert cfg.vision.max_vision_calls == 500

    def test_deskew_scope_locked(self) -> None:
        cfg = AppConfig()
        assert cfg.deskew.scope == "guia_only"

    def test_confidence_threshold_default(self) -> None:
        cfg = AppConfig()
        assert cfg.confidence.threshold == 0.85

    def test_output_dir_default(self) -> None:
        cfg = AppConfig()
        assert cfg.output_dir == Path("runs")

    def test_anthropic_model_default(self) -> None:
        cfg = AppConfig()
        assert cfg.vision.anthropic.model == "claude-3-5-sonnet-20241022"

    def test_ollama_base_url_default(self) -> None:
        cfg = AppConfig()
        assert cfg.vision.ollama.base_url == "http://localhost:11434/v1"


# ---------------------------------------------------------------------------
# ConfidenceConfig locked threshold
# ---------------------------------------------------------------------------


class TestConfidenceConfigLocked:
    def test_locked_at_0_85(self) -> None:
        """threshold=0.85 is accepted."""
        c = ConfidenceConfig(threshold=0.85)
        assert c.threshold == 0.85

    def test_raises_on_non_locked_value(self) -> None:
        """Any value != 0.85 raises a validation error."""
        with pytest.raises(Exception):  # pydantic ValidationError
            ConfidenceConfig(threshold=0.90)

    def test_raises_on_lower_value(self) -> None:
        with pytest.raises(Exception):
            ConfidenceConfig(threshold=0.50)


# ---------------------------------------------------------------------------
# DeskewConfig
# ---------------------------------------------------------------------------


class TestDeskewConfig:
    def test_scope_is_guia_only(self) -> None:
        d = DeskewConfig()
        assert d.scope == "guia_only"


# ---------------------------------------------------------------------------
# VisionConfig provider selection
# ---------------------------------------------------------------------------


class TestVisionConfig:
    def test_provider_anthropic(self) -> None:
        v = VisionConfig(provider="anthropic")
        assert v.provider == "anthropic"

    def test_provider_openai(self) -> None:
        v = VisionConfig(provider="openai")
        assert v.provider == "openai"

    def test_provider_ollama(self) -> None:
        v = VisionConfig(provider="ollama")
        assert v.provider == "ollama"

    def test_max_vision_calls_positive(self) -> None:
        v = VisionConfig(max_vision_calls=100)
        assert v.max_vision_calls == 100

    def test_max_vision_calls_must_be_positive(self) -> None:
        with pytest.raises(Exception):
            VisionConfig(max_vision_calls=0)


# ---------------------------------------------------------------------------
# AppConfig.from_yaml
# ---------------------------------------------------------------------------


class TestFromYaml:
    def test_loads_from_yaml_file(self, tmp_path: Path) -> None:
        yaml_content = textwrap.dedent("""\
            vision:
              provider: ollama
              max_vision_calls: 10
            output_dir: /tmp/runs
        """)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_content, encoding="utf-8")

        cfg = AppConfig.from_yaml(cfg_file)
        assert cfg.vision.provider == "ollama"
        assert cfg.vision.max_vision_calls == 10

    def test_missing_file_uses_defaults(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.yaml"
        cfg = AppConfig.from_yaml(missing)
        assert cfg.vision.provider == "anthropic"

    def test_empty_yaml_uses_defaults(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "empty.yaml"
        cfg_file.write_text("", encoding="utf-8")
        cfg = AppConfig.from_yaml(cfg_file)
        assert cfg.vision.provider == "anthropic"

    def test_env_overrides_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Environment variables must win over yaml values."""
        yaml_content = "vision:\n  max_vision_calls: 50\n"
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_content, encoding="utf-8")

        monkeypatch.setenv("RECONCILIATION__VISION__MAX_VISION_CALLS", "999")
        cfg = AppConfig.from_yaml(cfg_file)
        assert cfg.vision.max_vision_calls == 999


# ---------------------------------------------------------------------------
# API key injection from env (api_key is never serialised to disk)
# ---------------------------------------------------------------------------


class TestApiKeyEnvInjection:
    def test_anthropic_api_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-anthropic")
        cfg = AppConfig()
        assert cfg.vision.anthropic.api_key == "sk-test-anthropic"

    def test_openai_api_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
        cfg = AppConfig()
        assert cfg.vision.openai.api_key == "sk-test-openai"

    def test_no_api_key_when_env_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        cfg = AppConfig()
        assert cfg.vision.anthropic.api_key is None


# ---------------------------------------------------------------------------
# R8.8: InferenceConfig (MAT-007, ADR-2)
# ---------------------------------------------------------------------------


class TestInferenceConfig:
    def test_default_inference_disabled(self) -> None:
        """Default AppConfig has inference.enabled=False (air-gap safe default)."""
        cfg = AppConfig()
        assert cfg.inference.enabled is False

    def test_default_model(self) -> None:
        cfg = AppConfig()
        assert cfg.inference.model == "qwen3.5:9b"

    def test_default_base_url(self) -> None:
        cfg = AppConfig()
        assert cfg.inference.base_url == "http://localhost:11434/v1"

    def test_default_temperature(self) -> None:
        cfg = AppConfig()
        assert cfg.inference.temperature == 0.0

    def test_custom_inference_config(self) -> None:
        cfg = AppConfig(
            inference=InferenceConfig(enabled=True, model="custom-model")
        )
        assert cfg.inference.enabled is True
        assert cfg.inference.model == "custom-model"

    def test_api_key_excluded_from_model_dump(self) -> None:
        """api_key must not appear in model_dump() (secret exclusion)."""
        cfg = AppConfig(
            inference=InferenceConfig(enabled=True, model="qwen3.5:9b")
        )
        # The full AppConfig inference block must not leak api_key
        dumped = cfg.inference.model_dump()
        assert "api_key" not in dumped
        assert cfg.vision.openai.api_key is None


# ---------------------------------------------------------------------------
# OcrConfig — ocr.enabled flag (broken-paddle / SUNAT-quantities use-case)
# ---------------------------------------------------------------------------


class TestOcrConfig:
    def test_default_ocr_enabled_is_true(self) -> None:
        """Default AppConfig has ocr.enabled=True — preserves existing behaviour."""
        cfg = AppConfig()
        assert cfg.ocr.enabled is True

    def test_ocr_config_directly(self) -> None:
        """OcrConfig can be constructed directly with enabled=False."""
        c = OcrConfig(enabled=False)
        assert c.enabled is False

    def test_app_config_ocr_enabled_false_inline(self) -> None:
        """AppConfig accepts ocr sub-config with enabled=False."""
        cfg = AppConfig(ocr=OcrConfig(enabled=False))
        assert cfg.ocr.enabled is False

    def test_env_override_ocr_enabled_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """RECONCILIATION__OCR__ENABLED=false overrides the default True."""
        monkeypatch.setenv("RECONCILIATION__OCR__ENABLED", "false")
        cfg = AppConfig()
        assert cfg.ocr.enabled is False

    def test_env_override_ocr_enabled_true_explicit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """RECONCILIATION__OCR__ENABLED=true keeps the default True."""
        monkeypatch.setenv("RECONCILIATION__OCR__ENABLED", "true")
        cfg = AppConfig()
        assert cfg.ocr.enabled is True


# ---------------------------------------------------------------------------
# R10.5: protocolo_crop default, VisionConfig.max_tokens, SunatConfig.cache_dir
# ---------------------------------------------------------------------------


class TestProtocoloCropDefault:
    def test_protocolo_crop_default_is_non_zero(self) -> None:
        """R10.9: protocolo_crop default is calibrated (0.60,0.14,1.00,0.22) — non-degenerate.
        Calibrated to exclude the printed template revision date row.
        """
        cfg = AppConfig()
        assert cfg.vision.protocolo_crop.x0 == pytest.approx(0.60)
        assert cfg.vision.protocolo_crop.y0 == pytest.approx(0.14)
        assert cfg.vision.protocolo_crop.x1 == pytest.approx(1.00)
        assert cfg.vision.protocolo_crop.y1 == pytest.approx(0.22)

    def test_protocolo_crop_enabled_is_true(self) -> None:
        """R10.5: default protocolo_crop box is non-degenerate → enabled=True."""
        cfg = AppConfig()
        assert cfg.vision.protocolo_crop.enabled is True


class TestVisionMaxTokens:
    def test_max_tokens_default(self) -> None:
        """R10.5: VisionConfig.max_tokens defaults to 640."""
        cfg = AppConfig()
        assert cfg.vision.max_tokens == 640

    def test_max_tokens_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """R10.5: RECONCILIATION__VISION__MAX_TOKENS env overrides default."""
        monkeypatch.setenv("RECONCILIATION__VISION__MAX_TOKENS", "512")
        cfg = AppConfig()
        assert cfg.vision.max_tokens == 512

    def test_max_tokens_must_be_positive(self) -> None:
        """R10.5: max_tokens=0 is rejected."""
        with pytest.raises(Exception):
            VisionConfig(max_tokens=0)


class TestSunatCacheDir:
    def test_cache_dir_default_is_none(self) -> None:
        """R10.5: SunatConfig.cache_dir defaults to None (backward-compat)."""
        cfg = AppConfig()
        assert cfg.sunat.cache_dir is None

    def test_sunat_config_directly_default_none(self) -> None:
        """SunatConfig() without cache_dir → None."""
        s = SunatConfig()
        assert s.cache_dir is None

    def test_cache_dir_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """R10.5: RECONCILIATION__SUNAT__CACHE_DIR=/data/sunat-cache → Path."""
        monkeypatch.setenv("RECONCILIATION__SUNAT__CACHE_DIR", "/data/sunat-cache")
        cfg = AppConfig()
        assert cfg.sunat.cache_dir == Path("/data/sunat-cache")

    def test_cache_dir_yaml_missing_defaults_none(self, tmp_path: Path) -> None:
        """R10.5: config.yaml without cache_dir → None (backward-compat)."""
        yaml_content = "sunat:\n  enabled: false\n"
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_content, encoding="utf-8")
        cfg = AppConfig.from_yaml(cfg_file)
        assert cfg.sunat.cache_dir is None
