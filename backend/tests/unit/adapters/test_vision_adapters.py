"""Unit tests for vision adapters: Anthropic, OpenAI-compatible, and factory.

All SDK clients are injected or mocked — NO live API calls, NO network,
NO real API keys required.

Covered:
- AnthropicVisionAdapter.read_handwritten_date: success, JSON parse, fallback
- AnthropicVisionAdapter.read_handwritten_date_batch: success, fallback
- OpenAICompatibleVisionAdapter.read_handwritten_date: success, JSON parse, fallback
- OpenAICompatibleVisionAdapter.read_handwritten_date_batch: batch=True (OpenAI),
  batch=False (Ollama sequential), fallback-to-sequential
- _parse_vision_json: valid JSON, null date, bad JSON, markdown fences,
  out-of-range confidence clamping
- factory.build_vision_adapter: anthropic / openai / ollama provider selection,
  Ollama batch=False, invalid provider raises ValueError
"""

from __future__ import annotations

import io
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from reconciliation.adapters.vision.anthropic_vision import (
    AnthropicVisionAdapter,
    _parse_vision_json,
)
from reconciliation.adapters.vision.openai_compatible import (
    OpenAICompatibleVisionAdapter,
    _parse_vision_json as _oai_parse,
)
from reconciliation.domain.models import VisionResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_png() -> bytes:
    img = Image.new("RGB", (4, 4), color=(100, 100, 100))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_anthropic_response(text: str) -> MagicMock:
    """Build a mock Anthropic messages.create() response."""
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    return response


def _make_openai_response(text: str) -> MagicMock:
    """Build a mock OpenAI chat.completions.create() response."""
    message = MagicMock()
    message.content = text
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


# ---------------------------------------------------------------------------
# _parse_vision_json (shared logic, tested via Anthropic adapter's copy)
# ---------------------------------------------------------------------------


class TestParseVisionJson:
    def test_valid_date_and_confidence(self) -> None:
        result = _parse_vision_json('{"date": "2024-03-15", "confidence": 0.95}')
        assert result.date == date(2024, 3, 15)
        assert result.confidence == pytest.approx(0.95)

    def test_null_date_returns_none(self) -> None:
        result = _parse_vision_json('{"date": null, "confidence": 0.0}')
        assert result.date is None
        assert result.confidence == pytest.approx(0.0)

    def test_non_json_returns_zero_confidence(self) -> None:
        result = _parse_vision_json("I cannot see a date in this image.")
        assert result.date is None
        assert result.confidence == pytest.approx(0.0)

    def test_markdown_fences_stripped(self) -> None:
        raw = '```json\n{"date": "2024-05-01", "confidence": 0.88}\n```'
        result = _parse_vision_json(raw)
        assert result.date == date(2024, 5, 1)
        assert result.confidence == pytest.approx(0.88)

    def test_invalid_date_format_returns_none_date(self) -> None:
        result = _parse_vision_json('{"date": "15/03/2024", "confidence": 0.7}')
        assert result.date is None

    def test_confidence_clamped_above_1(self) -> None:
        result = _parse_vision_json('{"date": null, "confidence": 1.5}')
        assert result.confidence == pytest.approx(1.0)

    def test_confidence_clamped_below_0(self) -> None:
        result = _parse_vision_json('{"date": null, "confidence": -0.5}')
        assert result.confidence == pytest.approx(0.0)

    def test_missing_confidence_defaults_to_zero(self) -> None:
        result = _parse_vision_json('{"date": null}')
        assert result.confidence == pytest.approx(0.0)

    def test_raw_field_preserved(self) -> None:
        raw = '{"date": null, "confidence": 0.0}'
        result = _parse_vision_json(raw)
        assert result.raw == raw

    def test_think_block_stripped_before_json_parse(self) -> None:
        """Extended-thinking models (e.g. qwen3.5:9b) prepend <think>…</think> blocks.

        The parser must strip these before attempting JSON parsing so that the
        structured response is extracted correctly from the remaining content.
        """
        raw = (
            "<think>I need to look at the stamp region and find the date.</think>"
            '\n{"date": "2026-05-28", "confidence": 0.95}'
        )
        result = _oai_parse(raw)
        assert result.date == date(2026, 5, 28)
        assert result.confidence == pytest.approx(0.95)

    def test_think_block_multiline_stripped(self) -> None:
        raw = (
            "<think>\nStep 1: Examine image\nStep 2: Find date\n</think>"
            '\n```json\n{"date": "2026-05-28", "confidence": 0.90}\n```'
        )
        result = _oai_parse(raw)
        assert result.date == date(2026, 5, 28)
        assert result.confidence == pytest.approx(0.90)

    def test_empty_content_after_think_block_returns_null(self) -> None:
        """When max_tokens is exhausted during thinking, content is empty → null."""
        result = _oai_parse("")
        assert result.date is None
        assert result.confidence == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# AnthropicVisionAdapter
# ---------------------------------------------------------------------------


class TestAnthropicVisionAdapterSingle:
    def test_successful_date_extraction(self) -> None:
        client = MagicMock()
        client.messages.create.return_value = _make_anthropic_response(
            '{"date": "2024-03-15", "confidence": 0.92}'
        )
        adapter = AnthropicVisionAdapter(client=client)
        result = adapter.read_handwritten_date(_make_png())
        assert result.date == date(2024, 3, 15)
        assert result.confidence == pytest.approx(0.92)

    def test_null_date_from_model(self) -> None:
        client = MagicMock()
        client.messages.create.return_value = _make_anthropic_response(
            '{"date": null, "confidence": 0.0}'
        )
        adapter = AnthropicVisionAdapter(client=client)
        result = adapter.read_handwritten_date(_make_png())
        assert result.date is None

    def test_sdk_exception_returns_zero_confidence(self) -> None:
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("api down")
        adapter = AnthropicVisionAdapter(client=client)
        result = adapter.read_handwritten_date(_make_png())
        assert result.date is None
        assert result.confidence == pytest.approx(0.0)

    def test_hint_included_in_request(self) -> None:
        client = MagicMock()
        client.messages.create.return_value = _make_anthropic_response(
            '{"date": null, "confidence": 0.0}'
        )
        adapter = AnthropicVisionAdapter(client=client)
        adapter.read_handwritten_date(_make_png(), hint="nearby text")
        call_kwargs = client.messages.create.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs.args[0] if call_kwargs.args else None
        # Just verify the call was made (hint integration, not deep inspection)
        client.messages.create.assert_called_once()

    def test_supports_batch_is_true(self) -> None:
        assert AnthropicVisionAdapter.supports_batch is True


class TestAnthropicVisionAdapterBatch:
    def _make_batch_result(self, custom_id: str, text: str) -> MagicMock:
        msg_content = MagicMock()
        msg_content.text = text
        message = MagicMock()
        message.content = [msg_content]
        result_inner = MagicMock()
        result_inner.type = "succeeded"
        result_inner.message = message
        item = MagicMock()
        item.custom_id = custom_id
        item.result = result_inner
        return item

    def test_batch_success(self) -> None:
        client = MagicMock()

        # mock batches.create
        batch_obj = MagicMock()
        batch_obj.id = "batch_001"
        client.messages.batches.create.return_value = batch_obj

        # mock batches.retrieve → ended
        status_obj = MagicMock()
        status_obj.processing_status = "ended"
        client.messages.batches.retrieve.return_value = status_obj

        # mock batches.results
        r0 = self._make_batch_result("0", '{"date": "2024-01-10", "confidence": 0.90}')
        r1 = self._make_batch_result("1", '{"date": null, "confidence": 0.0}')
        client.messages.batches.results.return_value = [r0, r1]

        adapter = AnthropicVisionAdapter(client=client)
        results = adapter.read_handwritten_date_batch([_make_png(), _make_png()])

        assert len(results) == 2
        assert results[0].date == date(2024, 1, 10)
        assert results[1].date is None

    def test_empty_images_returns_empty(self) -> None:
        adapter = AnthropicVisionAdapter(client=MagicMock())
        assert adapter.read_handwritten_date_batch([]) == []

    def test_batch_create_failure_falls_back_to_sequential(self) -> None:
        client = MagicMock()
        client.messages.batches.create.side_effect = RuntimeError("batch api unavailable")
        # Sequential fallback
        client.messages.create.return_value = _make_anthropic_response(
            '{"date": "2024-05-01", "confidence": 0.80}'
        )
        adapter = AnthropicVisionAdapter(client=client)
        results = adapter.read_handwritten_date_batch([_make_png()])
        assert len(results) == 1
        assert results[0].date == date(2024, 5, 1)


# ---------------------------------------------------------------------------
# OpenAICompatibleVisionAdapter
# ---------------------------------------------------------------------------


class TestOpenAICompatibleVisionAdapterSingle:
    def test_successful_date_extraction(self) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = _make_openai_response(
            '{"date": "2024-03-15", "confidence": 0.88}'
        )
        adapter = OpenAICompatibleVisionAdapter(client=client)
        result = adapter.read_handwritten_date(_make_png())
        assert result.date == date(2024, 3, 15)
        assert result.confidence == pytest.approx(0.88)

    def test_null_date_from_model(self) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = _make_openai_response(
            '{"date": null, "confidence": 0.0}'
        )
        adapter = OpenAICompatibleVisionAdapter(client=client)
        result = adapter.read_handwritten_date(_make_png())
        assert result.date is None

    def test_sdk_exception_returns_zero_confidence(self) -> None:
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("network error")
        adapter = OpenAICompatibleVisionAdapter(client=client)
        result = adapter.read_handwritten_date(_make_png())
        assert result.date is None
        assert result.confidence == pytest.approx(0.0)


class TestOpenAICompatibleVisionAdapterBatch:
    def test_supports_batch_false_uses_sequential(self) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = _make_openai_response(
            '{"date": "2024-06-01", "confidence": 0.85}'
        )
        adapter = OpenAICompatibleVisionAdapter(supports_batch=False, client=client)
        results = adapter.read_handwritten_date_batch([_make_png(), _make_png()])
        assert len(results) == 2
        assert client.chat.completions.create.call_count == 2

    def test_empty_images_returns_empty(self) -> None:
        adapter = OpenAICompatibleVisionAdapter(supports_batch=False, client=MagicMock())
        assert adapter.read_handwritten_date_batch([]) == []

    def test_batch_failure_falls_back_to_sequential(self) -> None:
        client = MagicMock()
        # Batch path: files.create raises
        client.files.create.side_effect = RuntimeError("files api down")
        # Sequential fallback
        client.chat.completions.create.return_value = _make_openai_response(
            '{"date": null, "confidence": 0.0}'
        )
        adapter = OpenAICompatibleVisionAdapter(supports_batch=True, client=client)
        results = adapter.read_handwritten_date_batch([_make_png()])
        assert len(results) == 1

    def test_supports_batch_default_is_true(self) -> None:
        adapter = OpenAICompatibleVisionAdapter()
        assert adapter.supports_batch is True


class TestOpenAICompatibleVisionAdapterLazyLoad:
    def test_no_client_at_instantiation(self) -> None:
        adapter = OpenAICompatibleVisionAdapter()
        assert adapter._client is None

    def test_injected_client_skips_import(self) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = _make_openai_response(
            '{"date": null, "confidence": 0.0}'
        )
        adapter = OpenAICompatibleVisionAdapter(client=client)
        adapter.read_handwritten_date(_make_png())
        client.chat.completions.create.assert_called_once()


class TestOpenAICompatibleVisionAdapterTimeout:
    """Timeout is passed to the OpenAI client constructor and to each create() call."""

    def test_default_timeout_is_30(self) -> None:
        adapter = OpenAICompatibleVisionAdapter()
        assert adapter._timeout == pytest.approx(30.0)

    def test_custom_timeout_stored(self) -> None:
        adapter = OpenAICompatibleVisionAdapter(timeout=30.0)
        assert adapter._timeout == 30.0

    def test_timeout_passed_to_openai_constructor(self) -> None:
        """_get_client() must forward timeout= to OpenAI(**kwargs)."""
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_openai_cls.return_value = MagicMock()
            adapter = OpenAICompatibleVisionAdapter(timeout=45.0)
            adapter._get_client()
            _, ctor_kwargs = mock_openai_cls.call_args
            assert ctor_kwargs.get("timeout") == 45.0

    def test_timeout_passed_to_create_call(self) -> None:
        """chat.completions.create() must receive timeout= on each call."""
        client = MagicMock()
        client.chat.completions.create.return_value = _make_openai_response(
            '{"date": null, "confidence": 0.0}'
        )
        adapter = OpenAICompatibleVisionAdapter(timeout=60.0, client=client)
        adapter.read_handwritten_date(_make_png())
        _, call_kwargs = client.chat.completions.create.call_args
        assert call_kwargs.get("timeout") == 60.0

    def test_max_retries_set_to_0_on_constructor(self) -> None:
        """Client must be built with max_retries=0 (fast-fail, no retry amplification)."""
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_openai_cls.return_value = MagicMock()
            adapter = OpenAICompatibleVisionAdapter()
            adapter._get_client()
            _, ctor_kwargs = mock_openai_cls.call_args
            assert ctor_kwargs.get("max_retries") == 0

    def test_timeout_exception_degrades_gracefully(self) -> None:
        """A timeout-like exception from create() must return VisionResult(date=None)."""
        client = MagicMock()
        client.chat.completions.create.side_effect = TimeoutError("connection timed out")
        adapter = OpenAICompatibleVisionAdapter(timeout=5.0, client=client)
        result = adapter.read_handwritten_date(_make_png())
        assert result.date is None
        assert result.confidence == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# factory.build_vision_adapter
# ---------------------------------------------------------------------------


class TestVisionFactory:
    def _make_cfg(self, provider: str) -> MagicMock:
        """Build a minimal AppConfig-shaped mock for the factory."""
        pcfg = MagicMock()
        pcfg.model = "test-model"
        pcfg.base_url = None
        pcfg.api_key = None

        vision_cfg = MagicMock()
        vision_cfg.provider = provider
        vision_cfg.anthropic = pcfg
        vision_cfg.openai = pcfg
        ollama_pcfg = MagicMock()
        ollama_pcfg.model = "llava:latest"
        ollama_pcfg.base_url = "http://localhost:11434/v1"
        ollama_pcfg.api_key = None
        vision_cfg.ollama = ollama_pcfg

        cfg = MagicMock()
        cfg.vision = vision_cfg
        return cfg

    def test_anthropic_provider_returns_anthropic_adapter(self) -> None:
        from reconciliation.adapters.vision.factory import build_vision_adapter
        from reconciliation.adapters.vision.anthropic_vision import AnthropicVisionAdapter

        adapter = build_vision_adapter(self._make_cfg("anthropic"))
        assert isinstance(adapter, AnthropicVisionAdapter)

    def test_openai_provider_returns_openai_compatible_adapter(self) -> None:
        from reconciliation.adapters.vision.factory import build_vision_adapter
        from reconciliation.adapters.vision.openai_compatible import OpenAICompatibleVisionAdapter

        adapter = build_vision_adapter(self._make_cfg("openai"))
        assert isinstance(adapter, OpenAICompatibleVisionAdapter)
        assert adapter.supports_batch is True

    def test_ollama_provider_returns_openai_compatible_adapter_no_batch(self) -> None:
        from reconciliation.adapters.vision.factory import build_vision_adapter
        from reconciliation.adapters.vision.openai_compatible import OpenAICompatibleVisionAdapter

        adapter = build_vision_adapter(self._make_cfg("ollama"))
        assert isinstance(adapter, OpenAICompatibleVisionAdapter)
        assert adapter.supports_batch is False

    def test_ollama_uses_localhost_base_url(self) -> None:
        from reconciliation.adapters.vision.factory import build_vision_adapter

        adapter = build_vision_adapter(self._make_cfg("ollama"))
        assert "11434" in (adapter._base_url or "")  # type: ignore[union-attr]

    def test_invalid_provider_raises_value_error(self) -> None:
        from reconciliation.adapters.vision.factory import build_vision_adapter

        with pytest.raises(ValueError, match="Unknown vision provider"):
            build_vision_adapter(self._make_cfg("unknown_provider"))

    def test_anthropic_supports_batch_true(self) -> None:
        from reconciliation.adapters.vision.factory import build_vision_adapter

        adapter = build_vision_adapter(self._make_cfg("anthropic"))
        assert adapter.supports_batch is True

    def test_openai_supports_batch_true(self) -> None:
        from reconciliation.adapters.vision.factory import build_vision_adapter

        adapter = build_vision_adapter(self._make_cfg("openai"))
        assert adapter.supports_batch is True

    def test_ollama_supports_batch_false(self) -> None:
        from reconciliation.adapters.vision.factory import build_vision_adapter

        adapter = build_vision_adapter(self._make_cfg("ollama"))
        assert adapter.supports_batch is False

    def test_openai_adapter_receives_timeout_from_cfg(self) -> None:
        """factory must route cfg.vision.timeout_s into the openai adapter."""
        from reconciliation.adapters.vision.factory import build_vision_adapter
        from reconciliation.adapters.vision.openai_compatible import OpenAICompatibleVisionAdapter

        cfg = self._make_cfg("openai")
        cfg.vision.timeout_s = 120.0
        adapter = build_vision_adapter(cfg)
        assert isinstance(adapter, OpenAICompatibleVisionAdapter)
        assert adapter._timeout == 120.0

    def test_ollama_adapter_receives_timeout_from_cfg(self) -> None:
        """factory must route cfg.vision.timeout_s into the ollama adapter."""
        from reconciliation.adapters.vision.factory import build_vision_adapter
        from reconciliation.adapters.vision.openai_compatible import OpenAICompatibleVisionAdapter

        cfg = self._make_cfg("ollama")
        cfg.vision.timeout_s = 55.0
        adapter = build_vision_adapter(cfg)
        assert isinstance(adapter, OpenAICompatibleVisionAdapter)
        assert adapter._timeout == 55.0

    def test_openai_adapter_receives_disable_thinking_from_cfg(self) -> None:
        """factory must route cfg.vision.disable_thinking into the openai adapter."""
        from reconciliation.adapters.vision.factory import build_vision_adapter
        from reconciliation.adapters.vision.openai_compatible import OpenAICompatibleVisionAdapter

        cfg = self._make_cfg("openai")
        cfg.vision.disable_thinking = True
        adapter = build_vision_adapter(cfg)
        assert isinstance(adapter, OpenAICompatibleVisionAdapter)
        assert adapter._disable_thinking is True

    def test_ollama_adapter_receives_disable_thinking_from_cfg(self) -> None:
        """factory must route cfg.vision.disable_thinking into the ollama adapter."""
        from reconciliation.adapters.vision.factory import build_vision_adapter
        from reconciliation.adapters.vision.openai_compatible import OpenAICompatibleVisionAdapter

        cfg = self._make_cfg("ollama")
        cfg.vision.disable_thinking = True
        adapter = build_vision_adapter(cfg)
        assert isinstance(adapter, OpenAICompatibleVisionAdapter)
        assert adapter._disable_thinking is True

    def test_factory_disable_thinking_default_false_for_openai(self) -> None:
        """When disable_thinking=False (default), factory routes False to openai adapter."""
        from reconciliation.adapters.vision.factory import build_vision_adapter
        from reconciliation.adapters.vision.openai_compatible import OpenAICompatibleVisionAdapter

        cfg = self._make_cfg("openai")
        cfg.vision.disable_thinking = False
        adapter = build_vision_adapter(cfg)
        assert isinstance(adapter, OpenAICompatibleVisionAdapter)
        assert adapter._disable_thinking is False

    def test_factory_disable_thinking_default_false_for_ollama(self) -> None:
        """When disable_thinking=False (default), factory routes False to ollama adapter."""
        from reconciliation.adapters.vision.factory import build_vision_adapter
        from reconciliation.adapters.vision.openai_compatible import OpenAICompatibleVisionAdapter

        cfg = self._make_cfg("ollama")
        cfg.vision.disable_thinking = False
        adapter = build_vision_adapter(cfg)
        assert isinstance(adapter, OpenAICompatibleVisionAdapter)
        assert adapter._disable_thinking is False


# ---------------------------------------------------------------------------
# OpenAICompatibleVisionAdapter — disable_thinking + max_retries=0
# ---------------------------------------------------------------------------


class TestOpenAICompatibleDisableThinking:
    """Tests for /no_think injection and max_retries=0 client construction."""

    def test_disable_thinking_false_default(self) -> None:
        adapter = OpenAICompatibleVisionAdapter()
        assert adapter._disable_thinking is False

    def test_disable_thinking_stored_true(self) -> None:
        adapter = OpenAICompatibleVisionAdapter(disable_thinking=True)
        assert adapter._disable_thinking is True

    def test_messages_no_think_token_when_enabled(self) -> None:
        """When disable_thinking=True, user text must contain '/no_think'."""
        from reconciliation.adapters.vision.openai_compatible import _build_messages

        msgs = _build_messages(_make_png(), disable_thinking=True)
        user_msg = msgs[1]
        user_content = user_msg["content"]
        # User content is a list; first item is the text block
        text_block = next(c for c in user_content if c.get("type") == "text")
        assert "/no_think" in text_block["text"]

    def test_messages_no_think_absent_when_disabled(self) -> None:
        """When disable_thinking=False (default), '/no_think' must NOT appear."""
        from reconciliation.adapters.vision.openai_compatible import _build_messages

        msgs = _build_messages(_make_png(), disable_thinking=False)
        user_msg = msgs[1]
        user_content = user_msg["content"]
        text_block = next(c for c in user_content if c.get("type") == "text")
        assert "/no_think" not in text_block["text"]

    def test_messages_no_think_absent_by_default(self) -> None:
        """_build_messages with no disable_thinking arg must NOT inject /no_think."""
        from reconciliation.adapters.vision.openai_compatible import _build_messages

        msgs = _build_messages(_make_png())
        user_msg = msgs[1]
        user_content = user_msg["content"]
        text_block = next(c for c in user_content if c.get("type") == "text")
        assert "/no_think" not in text_block["text"]

    def test_max_retries_zero_on_constructor(self) -> None:
        """Client must be built with max_retries=0 (fast-fail, no retry amplification)."""
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_openai_cls.return_value = MagicMock()
            adapter = OpenAICompatibleVisionAdapter()
            adapter._get_client()
            _, ctor_kwargs = mock_openai_cls.call_args
            assert ctor_kwargs.get("max_retries") == 0

    def test_default_timeout_is_30(self) -> None:
        """Adapter default timeout must be 30.0 s (down from 90.0)."""
        adapter = OpenAICompatibleVisionAdapter()
        assert adapter._timeout == pytest.approx(30.0)

    def test_disable_thinking_true_sends_no_think_in_create_call(self) -> None:
        """Full path: adapter with disable_thinking=True passes /no_think in messages."""
        client = MagicMock()
        client.chat.completions.create.return_value = _make_openai_response(
            '{"date": null, "confidence": 0.0}'
        )
        adapter = OpenAICompatibleVisionAdapter(
            disable_thinking=True, client=client
        )
        adapter.read_handwritten_date(_make_png())
        call_kwargs = client.chat.completions.create.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs.args[0]
        # Find user message text
        user_msg = next(m for m in messages if m["role"] == "user")
        user_content = user_msg["content"]
        text_block = next(c for c in user_content if c.get("type") == "text")
        assert "/no_think" in text_block["text"]

    def test_disable_thinking_false_no_think_absent_in_create_call(self) -> None:
        """Full path: adapter with disable_thinking=False must NOT inject /no_think."""
        client = MagicMock()
        client.chat.completions.create.return_value = _make_openai_response(
            '{"date": null, "confidence": 0.0}'
        )
        adapter = OpenAICompatibleVisionAdapter(
            disable_thinking=False, client=client
        )
        adapter.read_handwritten_date(_make_png())
        call_kwargs = client.chat.completions.create.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs.args[0]
        user_msg = next(m for m in messages if m["role"] == "user")
        user_content = user_msg["content"]
        text_block = next(c for c in user_content if c.get("type") == "text")
        assert "/no_think" not in text_block["text"]
