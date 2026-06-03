"""Unit tests for _TokenMeter — token-consumption metering hook (R10.6 / CONT-S08).

Validates per-call and aggregate token accounting in OpenAICompatibleVisionAdapter
without live API calls.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import pytest
from PIL import Image

from reconciliation.adapters.vision.openai_compatible import (
    OpenAICompatibleVisionAdapter,
    _TokenMeter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_png() -> bytes:
    img = Image.new("RGB", (4, 4), color=(100, 100, 100))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_openai_response(text: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> MagicMock:
    """Build a mock OpenAI chat.completions.create() response with usage."""
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens

    message = MagicMock()
    message.content = text
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


# ---------------------------------------------------------------------------
# _TokenMeter unit tests
# ---------------------------------------------------------------------------


class TestTokenMeterDirectly:
    def test_initial_state_zero(self) -> None:
        m = _TokenMeter()
        assert m.prompt_tokens == 0
        assert m.completion_tokens == 0
        assert m.calls == 0
        assert m.total_tokens == 0

    def test_record_single_usage(self) -> None:
        m = _TokenMeter()
        usage = MagicMock()
        usage.prompt_tokens = 150
        usage.completion_tokens = 30
        m.record(usage)
        assert m.calls == 1
        assert m.prompt_tokens == 150
        assert m.completion_tokens == 30
        assert m.total_tokens == 180

    def test_record_accumulates_across_calls(self) -> None:
        m = _TokenMeter()
        usage1 = MagicMock()
        usage1.prompt_tokens = 100
        usage1.completion_tokens = 20
        usage2 = MagicMock()
        usage2.prompt_tokens = 200
        usage2.completion_tokens = 40
        m.record(usage1)
        m.record(usage2)
        assert m.calls == 2
        assert m.prompt_tokens == 300
        assert m.completion_tokens == 60
        assert m.total_tokens == 360

    def test_record_none_is_graceful(self) -> None:
        """usage=None must not crash; calls counter stays at 0."""
        m = _TokenMeter()
        m.record(None)
        assert m.calls == 0
        assert m.total_tokens == 0

    def test_record_usage_missing_prompt_tokens(self) -> None:
        """usage object without prompt_tokens attr → falls back to 0."""
        m = _TokenMeter()
        usage = MagicMock(spec=[])  # no attributes on spec
        m.record(usage)
        assert m.calls == 1
        assert m.prompt_tokens == 0
        assert m.completion_tokens == 0


# ---------------------------------------------------------------------------
# OpenAICompatibleVisionAdapter metering integration
# ---------------------------------------------------------------------------


class TestAdapterMeterIntegration:
    def test_meter_initialises_with_adapter(self) -> None:
        """Adapter exposes a _meter at instantiation with zero counts."""
        adapter = OpenAICompatibleVisionAdapter()
        assert adapter.meter.calls == 0
        assert adapter.meter.total_tokens == 0

    def test_single_call_records_tokens(self) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = _make_openai_response(
            '{"date": "2026-05-28", "confidence": 0.95}',
            prompt_tokens=150,
            completion_tokens=30,
        )
        adapter = OpenAICompatibleVisionAdapter(client=client)
        adapter.read_handwritten_date(_make_png())

        assert adapter.meter.calls == 1
        assert adapter.meter.prompt_tokens == 150
        assert adapter.meter.completion_tokens == 30
        assert adapter.meter.total_tokens == 180

    def test_two_calls_accumulate(self) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = _make_openai_response(
            '{"date": null, "confidence": 0.0}',
            prompt_tokens=100,
            completion_tokens=10,
        )
        adapter = OpenAICompatibleVisionAdapter(client=client)
        adapter.read_handwritten_date(_make_png())
        adapter.read_handwritten_date(_make_png())

        assert adapter.meter.calls == 2
        assert adapter.meter.total_tokens == 220

    def test_response_without_usage_graceful(self) -> None:
        """Response with usage=None → meter.calls remains 0 (graceful)."""
        client = MagicMock()
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = '{"date": null, "confidence": 0.0}'
        resp.usage = None
        client.chat.completions.create.return_value = resp
        adapter = OpenAICompatibleVisionAdapter(client=client)
        adapter.read_handwritten_date(_make_png())

        assert adapter.meter.calls == 0
        assert adapter.meter.total_tokens == 0

    def test_meter_property_is_read_only_reference(self) -> None:
        """meter property returns the same _TokenMeter instance (not a copy)."""
        adapter = OpenAICompatibleVisionAdapter()
        assert adapter.meter is adapter._meter
