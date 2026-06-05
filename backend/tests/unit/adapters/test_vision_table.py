"""T3 / REV-R10 — read_material_table in all three vision adapters.

Strict-TDD: tests written FIRST (RED) before implementation.

Covers:
- NullVisionAdapter.read_material_table → []
- AnthropicVisionAdapter.read_material_table: success + parse failures + SDK exc
- OpenAICompatibleVisionAdapter.read_material_table: success + parse failures + SDK exc
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from reconciliation.domain.models import MaterialLine
from reconciliation.domain.ports import VisionLLMPort


# ---------------------------------------------------------------------------
# NullVisionAdapter
# ---------------------------------------------------------------------------


class TestNullVisionAdapterTable:
    def test_read_material_table_returns_empty_list(self) -> None:
        """NullVisionAdapter.read_material_table always returns []."""
        from reconciliation.adapters.vision.null_vision import NullVisionAdapter

        adapter = NullVisionAdapter()
        result = adapter.read_material_table(b"fake-image-bytes")
        assert result == []

    def test_read_material_table_with_hint_returns_empty(self) -> None:
        """hint parameter accepted but ignored."""
        from reconciliation.adapters.vision.null_vision import NullVisionAdapter

        adapter = NullVisionAdapter()
        result = adapter.read_material_table(b"fake", hint="some hint")
        assert result == []

    def test_null_adapter_satisfies_vision_llm_port_with_table(self) -> None:
        """NullVisionAdapter must fully satisfy VisionLLMPort after T3."""
        from reconciliation.adapters.vision.null_vision import NullVisionAdapter

        adapter = NullVisionAdapter()
        assert isinstance(adapter, VisionLLMPort)


# ---------------------------------------------------------------------------
# Helpers for Anthropic adapter
# ---------------------------------------------------------------------------


def _make_anthropic_fake_client(response_text: str) -> MagicMock:
    """Build a MagicMock that mimics an anthropic.Anthropic client."""
    fake_content = MagicMock()
    fake_content.text = response_text
    fake_response = MagicMock()
    fake_response.content = [fake_content]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response
    return fake_client


def _make_openai_fake_client(response_text: str) -> MagicMock:
    """Build a MagicMock that mimics an openai.OpenAI client."""
    fake_choice = MagicMock()
    fake_choice.message.content = response_text
    fake_response = MagicMock()
    fake_response.choices = [fake_choice]
    fake_response.usage = None
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_response
    return fake_client


# ---------------------------------------------------------------------------
# AnthropicVisionAdapter
# ---------------------------------------------------------------------------


class TestAnthropicVisionAdapterTable:
    def test_read_material_table_success(self) -> None:
        """Successful JSON response → list of MaterialLine objects."""
        from reconciliation.adapters.vision.anthropic_vision import AnthropicVisionAdapter

        payload = json.dumps({
            "lines": [
                {"descripcion": "ALAMBRE N°16", "cantidad": 50, "unidad": "KG"},
                {"descripcion": "BARRA 1/2\" 9M", "cantidad": 2.5, "unidad": "TN"},
            ],
            "confidence": 0.95,
        })
        adapter = AnthropicVisionAdapter(client=_make_anthropic_fake_client(payload))
        result = adapter.read_material_table(b"image")

        assert len(result) == 2
        assert result[0].description_raw == "ALAMBRE N°16"
        assert result[0].cantidad == 50
        assert result[0].unidad == "KG"
        assert result[1].description_raw == "BARRA 1/2\" 9M"
        assert result[1].unidad == "TN"

    def test_read_material_table_malformed_json(self) -> None:
        """Non-JSON response → [] (never raises)."""
        from reconciliation.adapters.vision.anthropic_vision import AnthropicVisionAdapter

        adapter = AnthropicVisionAdapter(client=_make_anthropic_fake_client("not json at all"))
        result = adapter.read_material_table(b"image")
        assert result == []

    def test_read_material_table_missing_lines_key(self) -> None:
        """JSON without 'lines' key → []."""
        from reconciliation.adapters.vision.anthropic_vision import AnthropicVisionAdapter

        adapter = AnthropicVisionAdapter(
            client=_make_anthropic_fake_client(json.dumps({"confidence": 0.5}))
        )
        result = adapter.read_material_table(b"image")
        assert result == []

    def test_read_material_table_sdk_exception(self) -> None:
        """SDK exception → [] (never raises)."""
        from reconciliation.adapters.vision.anthropic_vision import AnthropicVisionAdapter

        fake_client = MagicMock()
        fake_client.messages.create.side_effect = RuntimeError("network error")
        adapter = AnthropicVisionAdapter(client=fake_client)
        result = adapter.read_material_table(b"image")
        assert result == []

    def test_read_material_table_empty_lines(self) -> None:
        """JSON with lines=[] → []."""
        from reconciliation.adapters.vision.anthropic_vision import AnthropicVisionAdapter

        adapter = AnthropicVisionAdapter(
            client=_make_anthropic_fake_client(json.dumps({"lines": [], "confidence": 0.9}))
        )
        result = adapter.read_material_table(b"image")
        assert result == []

    def test_read_material_table_confidence_propagated(self) -> None:
        """Envelope confidence propagates to MaterialLine.confidence."""
        from reconciliation.adapters.vision.anthropic_vision import AnthropicVisionAdapter

        payload = json.dumps({
            "lines": [{"descripcion": "X", "cantidad": 1, "unidad": "KG"}],
            "confidence": 0.88,
        })
        adapter = AnthropicVisionAdapter(client=_make_anthropic_fake_client(payload))
        result = adapter.read_material_table(b"image")
        assert len(result) == 1
        assert result[0].confidence == pytest.approx(0.88, abs=1e-3)

    def test_read_material_table_markdown_fences_stripped(self) -> None:
        """Markdown code fences around JSON are stripped before parsing."""
        from reconciliation.adapters.vision.anthropic_vision import AnthropicVisionAdapter

        payload = (
            "```json\n"
            + json.dumps({"lines": [{"descripcion": "Y", "cantidad": 3, "unidad": "TN"}], "confidence": 0.9})
            + "\n```"
        )
        adapter = AnthropicVisionAdapter(client=_make_anthropic_fake_client(payload))
        result = adapter.read_material_table(b"image")
        assert len(result) == 1
        assert result[0].description_raw == "Y"


# ---------------------------------------------------------------------------
# OpenAICompatibleVisionAdapter
# ---------------------------------------------------------------------------


class TestOpenAICompatibleVisionAdapterTable:
    def test_read_material_table_success(self) -> None:
        """Successful JSON response → list of MaterialLine objects."""
        from reconciliation.adapters.vision.openai_compatible import (
            OpenAICompatibleVisionAdapter,
        )

        payload = json.dumps({
            "lines": [
                {"descripcion": "BARRA 3/8\" 9M", "cantidad": 1.5, "unidad": "TN"},
            ],
            "confidence": 0.92,
        })
        adapter = OpenAICompatibleVisionAdapter(client=_make_openai_fake_client(payload))
        result = adapter.read_material_table(b"image")

        assert len(result) == 1
        assert result[0].description_raw == 'BARRA 3/8" 9M'
        assert result[0].unidad == "TN"

    def test_read_material_table_malformed_json(self) -> None:
        """Non-JSON → []."""
        from reconciliation.adapters.vision.openai_compatible import (
            OpenAICompatibleVisionAdapter,
        )

        adapter = OpenAICompatibleVisionAdapter(
            client=_make_openai_fake_client("not json")
        )
        result = adapter.read_material_table(b"image")
        assert result == []

    def test_read_material_table_think_block_stripped(self) -> None:
        """<think>…</think> blocks are stripped before JSON parse."""
        from reconciliation.adapters.vision.openai_compatible import (
            OpenAICompatibleVisionAdapter,
        )

        payload = (
            "<think>Analyzing the table structure...</think>\n"
            + json.dumps({
                "lines": [{"descripcion": "ALAMBRE", "cantidad": 100, "unidad": "KG"}],
                "confidence": 0.9,
            })
        )
        adapter = OpenAICompatibleVisionAdapter(client=_make_openai_fake_client(payload))
        result = adapter.read_material_table(b"image")
        assert len(result) == 1
        assert result[0].description_raw == "ALAMBRE"

    def test_read_material_table_sdk_exception(self) -> None:
        """SDK exception → [] (never raises)."""
        from reconciliation.adapters.vision.openai_compatible import (
            OpenAICompatibleVisionAdapter,
        )

        fake_client = MagicMock()
        fake_client.chat.completions.create.side_effect = RuntimeError("timeout")
        adapter = OpenAICompatibleVisionAdapter(client=fake_client)
        result = adapter.read_material_table(b"image")
        assert result == []

    def test_read_material_table_missing_lines_key(self) -> None:
        """JSON without 'lines' → []."""
        from reconciliation.adapters.vision.openai_compatible import (
            OpenAICompatibleVisionAdapter,
        )

        adapter = OpenAICompatibleVisionAdapter(
            client=_make_openai_fake_client(json.dumps({"confidence": 0.5}))
        )
        result = adapter.read_material_table(b"image")
        assert result == []

    def test_read_material_table_confidence_propagated(self) -> None:
        """Envelope confidence propagates to MaterialLine.confidence."""
        from reconciliation.adapters.vision.openai_compatible import (
            OpenAICompatibleVisionAdapter,
        )

        payload = json.dumps({
            "lines": [{"descripcion": "Z", "cantidad": 5, "unidad": "RD"}],
            "confidence": 0.77,
        })
        adapter = OpenAICompatibleVisionAdapter(client=_make_openai_fake_client(payload))
        result = adapter.read_material_table(b"image")
        assert len(result) == 1
        assert result[0].confidence == pytest.approx(0.77, abs=1e-3)
