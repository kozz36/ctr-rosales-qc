"""Factory for the material inference adapter (R8.7/R8.8, ADR-2).

Returns None when inference is disabled (deterministic-only default).
Returns OllamaMaterialInferenceAdapter when enabled.

Pattern mirrors build_vision_adapter in adapters/vision/factory.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reconciliation.application.config import AppConfig
    from reconciliation.domain.ports import MaterialInferencePort


def build_inference_adapter(config: "AppConfig") -> "MaterialInferencePort | None":
    """Build and return the material inference adapter, or None if disabled.

    Args:
        config: AppConfig with inference sub-config.

    Returns:
        OllamaMaterialInferenceAdapter when config.inference.enabled is True.
        None otherwise (deterministic-only safe default).
    """
    if not config.inference.enabled:
        return None

    # Lazy import — keeps the adapter's heavy deps deferred to first use
    from reconciliation.adapters.inference.ollama_material import (  # noqa: PLC0415
        OllamaMaterialInferenceAdapter,
    )

    base_url = config.inference.base_url or "http://localhost:11434/v1"

    return OllamaMaterialInferenceAdapter(
        model=config.inference.model,
        base_url=base_url,
        temperature=config.inference.temperature,
        timeout_s=config.inference.timeout_s,
    )
