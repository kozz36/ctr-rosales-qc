"""NullVisionAdapter — no-op VisionLLMPort for vision.enabled=False mode.

When ``config.vision.enabled=False``, ``build_pipeline`` (container.py) injects
this adapter in place of the real vision adapter (Anthropic/OpenAI/Ollama).

Contract:
  - ``read_handwritten_date`` always returns ``VisionResult(date=None, confidence=0.0,
    raw="")`` — no LLM call, no SDK import, no IO.
  - ``read_handwritten_date_batch`` returns a list of null VisionResults sized to the
    input, one per image — no LLM calls made.
  - ``supports_batch`` is False (simplest; batch returns a list of nulls regardless).
  - The adapter is pure Python with no heavy dependencies; it is safe to import in
    any environment, including air-gap machines.

Downstream effect (architecture): because vision produces ``date=None`` for every
guía, ``_stage_normalize_dates`` falls into the ``day is None or month is None``
branch (R9b Rule-2) and applies ``apply_delivery_floor(None, fecha_entrega)``,
resolving each guía's fecha to its SUNAT ``fecha_entrega`` (``delivery_floor_applied=True``).
This is the existing rule — no new date logic was added.

This is a Null Object pattern implementation of ``VisionLLMPort``.
"""

from __future__ import annotations

from reconciliation.domain.models import VisionResult


class NullVisionAdapter:
    """No-op vision adapter — returns null-date results; never calls any LLM."""

    supports_batch: bool = False

    def read_handwritten_date(
        self,
        image: bytes,
        hint: str | None = None,
    ) -> VisionResult:
        """Return a null-date VisionResult without performing any LLM call."""
        return VisionResult(date=None, confidence=0.0, raw="")

    def read_handwritten_date_batch(
        self,
        images: list[bytes],
    ) -> list[VisionResult]:
        """Return one null-date VisionResult per input image; no LLM calls made."""
        return [VisionResult(date=None, confidence=0.0, raw="") for _ in images]
