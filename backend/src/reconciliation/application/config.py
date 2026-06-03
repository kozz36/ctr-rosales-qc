"""AppConfig — pydantic-settings configuration for the reconciliation pipeline.

Hierarchy (highest wins):
  1. Environment variables (prefixed RECONCILIATION__)
  2. .env file (loaded via pydantic-settings env_file; default: backend/.env)
  3. config.yaml (loaded via AppConfig.from_yaml)
  4. Coded defaults

Secrets (api_key) are intentionally env-only; they are never written to config.yaml.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

# ---------------------------------------------------------------------------
# Sub-config blocks
# ---------------------------------------------------------------------------


class VisionProviderConfig(BaseSettings):
    """Settings for a single vision provider."""

    model_config = SettingsConfigDict(extra="allow")

    model: str = "claude-3-5-sonnet-20241022"
    base_url: str | None = None
    # api_key is env-only; never serialise to disk
    api_key: str | None = Field(default=None, exclude=True)


class StampCropConfig(BaseSettings):
    """Stamp-region crop box for the VisionLLMPort date-extraction call (D4 / EXT-020).

    Defines a fractional crop box ``(x0, y0, x1, y1)`` relative to the full
    rendered page dimensions (values in [0.0, 1.0]).

    R7 fix: empirical tuning on the real CTR PDF confirmed the "Recibí conforme"
    stamp is in the UPPER-RIGHT region.  The default now targets x ∈ [55%, 100%],
    y ∈ [5%, 45%] — proven to yield day-month on guía pages 4, 5, 6, 8, 20, 25, 30
    of the production PDF subset.

    Set all four values to ``0.0`` to disable cropping (falls back to Option B:
    >=300 dpi full-page render).
    """

    model_config = SettingsConfigDict(extra="allow")

    # Fractional coordinates relative to rendered page (0.0 – 1.0).
    # Default: upper-right quadrant (x: 55–100%, y: 5–45%) — R7 bake-off winner.
    x0: float = Field(default=0.55, ge=0.0, le=1.0)
    y0: float = Field(default=0.05, ge=0.0, le=1.0)
    x1: float = Field(default=1.0, ge=0.0, le=1.0)
    y1: float = Field(default=0.45, ge=0.0, le=1.0)

    @property
    def enabled(self) -> bool:
        """True when the crop box is non-degenerate (x1 > x0 and y1 > y0)."""
        return self.x1 > self.x0 and self.y1 > self.y0


class VisionConfig(BaseSettings):
    """Vision LLM selection and cost cap."""

    model_config = SettingsConfigDict(extra="allow")

    provider: Literal["anthropic", "openai", "ollama"] = "anthropic"
    # Per-provider sub-configs with sensible defaults
    anthropic: VisionProviderConfig = Field(
        default_factory=lambda: VisionProviderConfig(
            model="claude-3-5-sonnet-20241022"
        )
    )
    openai: VisionProviderConfig = Field(
        default_factory=lambda: VisionProviderConfig(
            model="gpt-4o"
        )
    )
    ollama: VisionProviderConfig = Field(
        default_factory=lambda: VisionProviderConfig(
            model="llava:latest",
            base_url="http://localhost:11434/v1",
        )
    )
    max_vision_calls: int = Field(default=500, gt=0)
    # Rev-3 D4: stamp-crop config — lower-right quadrant default (EXT-020)
    stamp_crop: StampCropConfig = Field(default_factory=StampCropConfig)
    # R9.5 (ADR-6): Protocolo "Fecha:" crop box for the declared-date read.
    # The Protocolo layout differs from the guía stamp, so it gets its own crop.
    # R10.9: calibrated to (0.60,0.14,1.00,0.22) — targets only Registro N° + Fecha rows,
    # excluding the printed template revision date (Rev:02 Fecha:08/09/2025) which would
    # confuse the model.  Proven on qwen3.5:397b-cloud → 2026-05-28, confidence=1.00,
    # total_tokens=717.  Env-tunable via RECONCILIATION__VISION__PROTOCOLO_CROP__X0 etc.
    protocolo_crop: StampCropConfig = Field(
        default_factory=lambda: StampCropConfig(x0=0.60, y0=0.14, x1=1.00, y1=0.22)
    )
    # Rev-3 D4 Option B: DPI for full-page fallback when stamp_crop is disabled
    fallback_dpi: int = Field(default=300, gt=0)
    # R10.5: max_tokens for vision calls — env-tunable floor at 512-768 to survive
    # the <think> phase from extended-thinking models (qwen3.5 family).
    # Passed to the adapter constructor in the factory; overrides the adapter's default.
    max_tokens: int = Field(default=640, gt=0)
    # Request timeout for OpenAI-compatible adapters (openai + ollama providers).
    # A stalled cloud socket (ESTABLISHED but no bytes) would hang indefinitely with the
    # SDK default (~600s + unbounded retries).  Normal no-think calls finish in 2-3s;
    # 30s bounds the worst-case hang to one timeout window without retry amplification.
    # Env: RECONCILIATION__VISION__TIMEOUT_S
    timeout_s: float = Field(default=30.0, gt=0)
    # When True, appends /no_think to the user message so Qwen3.5 (and compatible
    # models served via Ollama's OpenAI-compat API) skips its <think> phase.
    # The literal token /no_think in the user message is the Qwen convention for
    # disabling extended thinking via the OpenAI-compatible/Ollama path.
    # Env: RECONCILIATION__VISION__DISABLE_THINKING
    disable_thinking: bool = Field(default=False)

    @model_validator(mode="after")
    def _inject_env_api_keys(self) -> VisionConfig:
        """Pull per-provider api_key from env if not already set via settings."""
        env_keys = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "ollama": None,  # no key needed
        }
        for provider, env_var in env_keys.items():
            sub: VisionProviderConfig = getattr(self, provider)
            if sub.api_key is None and env_var:
                sub.api_key = os.environ.get(env_var)
        return self


class InferenceConfig(BaseSettings):
    """LLM material inference settings (R8.8, MAT-007, ADR-2).

    OFF BY DEFAULT — enabling this falls back to local Ollama only (air-gap preserved).
    The Ollama base_url defaults to localhost so no network egress occurs unless
    explicitly changed.

    Mirrors the SunatConfig pattern: off-by-default, separate from vision config
    (ISP/SoC: vision reads images→date, inference reads text→tuple; ADR-2).
    """

    model_config = SettingsConfigDict(extra="allow")

    enabled: bool = False
    provider: Literal["ollama", "openai"] = "ollama"
    model: str = "qwen3.5:9b"
    base_url: str | None = "http://localhost:11434/v1"
    # api_key is env-only; never serialise to disk (mirrors VisionProviderConfig pattern)
    api_key: str | None = Field(default=None, exclude=True)
    temperature: float = 0.0
    timeout_s: float = 30.0


class SunatConfig(BaseSettings):
    """SUNAT descargaqr opt-in fetch settings (rev-3, EXT-023 / D3).

    OFF BY DEFAULT — enabling this breaks the local-first / air-gap invariant
    and is the ONLY network egress in the system.  Document in DECISIONS.md
    whenever this is enabled in a committed config.

    ``enabled``: master switch.  When False, no network call is ever made.
    ``timeout_s``: HTTP request timeout in seconds (per fetch).
    ``cache``: when True, the downloaded GRE PDF is stored in the run dir
               (``<run_dir>/sunat/{guia_id}.pdf``) and reused on re-run.
    """

    model_config = SettingsConfigDict(extra="allow")

    enabled: bool = False
    timeout_s: float = Field(default=10.0, gt=0)
    cache: bool = True
    # R10.5: optional stable cross-run cache directory (D4 CONT-S10).
    # When set, container.py routes the SUNAT adapter to this path instead of the
    # per-run dir. Default None preserves existing per-run behavior (backward-compat).
    # Env: RECONCILIATION__SUNAT__CACHE_DIR=/data/sunat-cache
    cache_dir: Path | None = Field(default=None)


class OcrConfig(BaseSettings):
    """PaddleOCR on/off switch (local-first / broken-paddle recovery).

    OFF state (``enabled=False``): the pipeline injects a NullOcrExtractor
    whose ``extract_printed_table`` always returns ``[]`` WITHOUT importing or
    instantiating PaddleOCR.  Deskew is also disabled (``deskew=None``).
    Classification falls back to the primary QR Condition-A / heuristic
    Condition-B path — title-OCR (Condition C) is the only loss.

    ON state (``enabled=True``, default): byte-for-byte identical to the
    previous behaviour.  PaddleOCR is loaded lazily by PrintedTableAdapter and
    DeskewAdapter on first call.

    Intended use-case: machines where PaddleOCR crashes on load (oneDNN / PIR
    initialisation failure) while SUNAT supplies authoritative quantities via
    ``sunat.enabled=True``.

    Env override: ``RECONCILIATION__OCR__ENABLED=false``.
    """

    model_config = SettingsConfigDict(extra="allow")

    enabled: bool = True


class DeskewConfig(BaseSettings):
    """Deskew scope and fallback settings (locked: guia_only)."""

    model_config = SettingsConfigDict(extra="allow")

    # Locked: only guía pages are deskewed, not all scanned pages (EXT-003).
    scope: Literal["guia_only"] = "guia_only"


class ConfidenceConfig(BaseSettings):
    """Confidence threshold settings (locked: 0.85 per EXT-002)."""

    model_config = SettingsConfigDict(extra="allow")

    # EXT-002: locked at 0.85 — must not be changed without spec amendment.
    threshold: float = Field(default=0.85, frozen=True)

    @field_validator("threshold")
    @classmethod
    def _must_be_locked(cls, v: float) -> float:
        if v != 0.85:
            raise ValueError(
                "confidence.threshold is locked at 0.85 (EXT-002). "
                "Amend the spec before changing this value."
            )
        return v


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------


class AppConfig(BaseSettings):
    """Root application configuration.

    Source priority (highest → lowest):
      1. Environment variables  (prefix: RECONCILIATION__, delimiter: __)
      2. config.yaml            (path passed to ``from_yaml``; never read automatically)
      3. Coded defaults

    Example environment variables:
        RECONCILIATION__VISION__PROVIDER=ollama
        RECONCILIATION__VISION__MAX_VISION_CALLS=100
        ANTHROPIC_API_KEY=sk-ant-...        # injected by VisionConfig validator
    """

    model_config = SettingsConfigDict(
        env_prefix="RECONCILIATION__",
        env_nested_delimiter="__",
        extra="allow",
        # Load .env automatically so local developers only need to copy .env.example → .env.
        # Shell env vars always take priority over .env (pydantic-settings guarantee).
        env_file=".env",
        env_file_encoding="utf-8",
    )

    vision: VisionConfig = Field(default_factory=VisionConfig)
    deskew: DeskewConfig = Field(default_factory=DeskewConfig)
    confidence: ConfidenceConfig = Field(default_factory=ConfidenceConfig)
    sunat: SunatConfig = Field(default_factory=SunatConfig)
    # R8.8 (ADR-2): LLM inference for ambiguous material descriptions. Off by default.
    inference: InferenceConfig = Field(default_factory=InferenceConfig)
    # OCR on/off switch — ON by default; set False to skip PaddleOCR entirely
    # when paddle is broken (oneDNN/PIR) and SUNAT supplies quantities instead.
    ocr: OcrConfig = Field(default_factory=OcrConfig)

    # Base directory under which per-run directories are created.
    output_dir: Path = Field(default=Path("runs"))

    @classmethod
    def from_yaml(cls, path: Path | str | None = None) -> AppConfig:
        """Load config from a YAML file, then apply env overrides on top.

        Priority order (highest → lowest):
          1. Environment variables (RECONCILIATION__*)
          2. config.yaml values
          3. Coded field defaults

        Uses pydantic-settings v2's ``YamlConfigSettingsSource`` injected as a
        lower-priority source than ``EnvSettingsSource`` via a one-shot subclass
        with ``settings_customise_sources``.

        Args:
            path: Path to config.yaml.  Defaults to ``./config.yaml`` if None.
                  If the file does not exist, falls back to coded defaults +
                  environment variables only.

        Returns:
            A fully resolved AppConfig instance.
        """
        config_path = Path(path) if path else Path("config.yaml")

        # Build a one-shot subclass carrying the resolved yaml_file path so that
        # settings_customise_sources can inject it at the right priority level.
        yaml_path: Path | None = config_path if config_path.exists() else None

        class _AppConfigWithYaml(AppConfig):  # type: ignore[valid-type]
            model_config = SettingsConfigDict(
                env_prefix="RECONCILIATION__",
                env_nested_delimiter="__",
                extra="allow",
                env_file=".env",
                env_file_encoding="utf-8",
                # yaml_file is consumed by YamlConfigSettingsSource.
                yaml_file=str(yaml_path) if yaml_path else None,
            )

            @classmethod
            def settings_customise_sources(
                cls,
                settings_cls: type[BaseSettings],
                init_settings: PydanticBaseSettingsSource,
                env_settings: PydanticBaseSettingsSource,
                dotenv_settings: PydanticBaseSettingsSource,
                file_secret_settings: PydanticBaseSettingsSource,
            ) -> tuple[PydanticBaseSettingsSource, ...]:
                # Priority: init > env > yaml > defaults
                sources: list[PydanticBaseSettingsSource] = [
                    init_settings,
                    env_settings,
                ]
                if yaml_path is not None:
                    sources.append(YamlConfigSettingsSource(settings_cls))
                return tuple(sources)

        return _AppConfigWithYaml()
