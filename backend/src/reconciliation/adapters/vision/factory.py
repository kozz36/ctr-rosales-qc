"""Vision adapter factory.

Builds the correct :class:`~reconciliation.domain.ports.VisionLLMPort`
implementation from :class:`~reconciliation.application.config.AppConfig`.

The factory is the ONLY place in the codebase that imports concrete vision
adapters.  The domain and application layers depend only on ``VisionLLMPort``
(a Protocol).

Strategy pattern: ``build_vision_adapter(cfg)`` selects the implementation
based on ``cfg.vision.provider``:

- ``"anthropic"`` → :class:`AnthropicVisionAdapter`
- ``"openai"``    → :class:`OpenAICompatibleVisionAdapter` (cloud, batch=True)
- ``"ollama"``    → :class:`OpenAICompatibleVisionAdapter` (base_url swap,
                    batch=False — Ollama does not support the Batch API)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reconciliation.application.config import AppConfig
    from reconciliation.domain.ports import VisionLLMPort


def build_vision_adapter(cfg: "AppConfig") -> "VisionLLMPort":
    """Construct and return the configured VisionLLMPort implementation.

    Args:
        cfg: Root application config.  ``cfg.vision.provider`` selects the
             backend; per-provider sub-config supplies model/base_url/api_key.

    Returns:
        A ready-to-use VisionLLMPort instance.

    Raises:
        ValueError: if ``cfg.vision.provider`` is not one of the three
                    recognised values.
    """
    # Adapters are imported here (inside the function) so the factory module
    # itself can be imported without pulling in any SDK at module load time.
    provider = cfg.vision.provider

    if provider == "anthropic":
        from reconciliation.adapters.vision.anthropic_vision import (  # noqa: PLC0415
            AnthropicVisionAdapter,
        )

        pcfg = cfg.vision.anthropic
        return AnthropicVisionAdapter(
            model=pcfg.model,
            # api_key is env-injected by VisionConfig._inject_env_api_keys;
            # the SDK reads ANTHROPIC_API_KEY automatically when client=None,
            # so no explicit pass-through needed here unless overriding.
        )

    if provider == "openai":
        from reconciliation.adapters.vision.openai_compatible import (  # noqa: PLC0415
            OpenAICompatibleVisionAdapter,
        )

        pcfg = cfg.vision.openai
        return OpenAICompatibleVisionAdapter(
            model=pcfg.model,
            base_url=pcfg.base_url,
            api_key=pcfg.api_key,
            # R10.5: route config.vision.max_tokens into the adapter (env-tunable)
            max_tokens=cfg.vision.max_tokens,
            supports_batch=True,
            # Route timeout so a stalled cloud call fails fast (EXT-0XX / hang fix)
            timeout=cfg.vision.timeout_s,
        )

    if provider == "ollama":
        from reconciliation.adapters.vision.openai_compatible import (  # noqa: PLC0415
            OpenAICompatibleVisionAdapter,
        )

        pcfg = cfg.vision.ollama
        return OpenAICompatibleVisionAdapter(
            model=pcfg.model,
            # Use configured base_url; fall back to local default
            base_url=pcfg.base_url or "http://localhost:11434/v1",
            # Ollama accepts any api_key; use "ollama" as a placeholder
            api_key=pcfg.api_key or "ollama",
            # R10.5: route config.vision.max_tokens into the adapter (env-tunable)
            max_tokens=cfg.vision.max_tokens,
            supports_batch=False,
            # Route timeout so a stalled local call fails fast (EXT-0XX / hang fix)
            timeout=cfg.vision.timeout_s,
        )

    raise ValueError(
        f"Unknown vision provider: {provider!r}. "
        "Expected one of: 'anthropic', 'openai', 'ollama'."
    )
