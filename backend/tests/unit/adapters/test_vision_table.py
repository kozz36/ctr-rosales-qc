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


# ---------------------------------------------------------------------------
# FIX #3 — vision long-form unit normalization parity (silent line-loss guard)
#
# When the vision model emits a long-form unit ("TONELADAS", "KILOS", "UND",
# "UNIDAD") the MaterialLine Literal["KG","TN","RD","Rollo"] coercion used to
# raise → the line was SILENTLY skipped → summed_qty short → spurious MISMATCH.
# The SUNAT path normalizes long-form→canonical BEFORE the valid-unit check;
# the vision path must mirror that (domain/units.normalize_unit_label).
# ---------------------------------------------------------------------------


class TestVisionLongFormUnitNormalization:
    @pytest.mark.parametrize(
        ("raw_unit", "expected"),
        [
            ("TONELADAS", "TN"),
            ("TNE", "TN"),
            ("KILOGRAMOS", "KG"),
            ("KGM", "KG"),
            ("VARILLA", "RD"),
            ("ROLLO", "Rollo"),
        ],
    )
    def test_anthropic_retains_longform_unit_line(
        self, raw_unit: str, expected: str
    ) -> None:
        """A valid long-form unit line is RETAINED with the canonical unit.

        R2-W1: the vision map mirrors the authoritative SUNAT map EXACTLY.
        Only genuine steel units (TONELADAS/KILOS/KILOGRAMOS/VARILLA/ROLLO + the
        long-forms) are retained.
        """
        from reconciliation.adapters.vision.anthropic_vision import AnthropicVisionAdapter

        payload = json.dumps({
            "lines": [{"descripcion": "BARRA 1/2\" 9M", "cantidad": 4.124, "unidad": raw_unit}],
            "confidence": 0.9,
        })
        adapter = AnthropicVisionAdapter(client=_make_anthropic_fake_client(payload))
        result = adapter.read_material_table(b"image")
        assert len(result) == 1, f"line with unit {raw_unit!r} was silently dropped"
        assert result[0].unidad == expected
        assert float(result[0].cantidad) == pytest.approx(4.124, abs=1e-3)

    @pytest.mark.parametrize("raw_unit", ["UND", "UNIDAD", "UNIDADES", "UNID"])
    def test_anthropic_drops_und_unidad_mirroring_sunat(
        self, raw_unit: str, caplog
    ) -> None:
        """R2-W1: "UND"/"UNIDAD" is NOT a valid steel unit → line is DROPPED + WARNING.

        Mirrors the SUNAT side exactly (``_SUNAT_UNIT_MAP`` has no UND/UNIDAD key).
        """
        import logging  # noqa: PLC0415

        from reconciliation.adapters.vision.anthropic_vision import AnthropicVisionAdapter

        payload = json.dumps({
            "lines": [{"descripcion": "BARRA 1/2\" 9M", "cantidad": 4.124, "unidad": raw_unit}],
            "confidence": 0.9,
        })
        adapter = AnthropicVisionAdapter(client=_make_anthropic_fake_client(payload))
        with caplog.at_level(logging.WARNING):
            result = adapter.read_material_table(b"image")
        assert result == [], f"unit {raw_unit!r} should be dropped (mirrors SUNAT)"
        assert any(raw_unit in rec.message for rec in caplog.records)

    @pytest.mark.parametrize(
        ("raw_unit", "expected"),
        [
            ("TONELADAS", "TN"),
            ("KILOGRAMOS", "KG"),
            ("VARILLA", "RD"),
        ],
    )
    def test_openai_retains_longform_unit_line(
        self, raw_unit: str, expected: str
    ) -> None:
        """OpenAI-compatible adapter retains valid long-form unit lines too (parity)."""
        from reconciliation.adapters.vision.openai_compatible import (
            OpenAICompatibleVisionAdapter,
        )

        payload = json.dumps({
            "lines": [{"descripcion": "BARRA 3/8\" 9M", "cantidad": 2.5, "unidad": raw_unit}],
            "confidence": 0.9,
        })
        adapter = OpenAICompatibleVisionAdapter(client=_make_openai_fake_client(payload))
        result = adapter.read_material_table(b"image")
        assert len(result) == 1, f"line with unit {raw_unit!r} was silently dropped"
        assert result[0].unidad == expected

    @pytest.mark.parametrize("raw_unit", ["UND", "UNIDAD"])
    def test_openai_drops_und_unidad_mirroring_sunat(self, raw_unit: str) -> None:
        """R2-W1: OpenAI adapter drops UND/UNIDAD too (SUNAT parity)."""
        from reconciliation.adapters.vision.openai_compatible import (
            OpenAICompatibleVisionAdapter,
        )

        payload = json.dumps({
            "lines": [{"descripcion": "BARRA 3/8\" 9M", "cantidad": 2.5, "unidad": raw_unit}],
            "confidence": 0.9,
        })
        adapter = OpenAICompatibleVisionAdapter(client=_make_openai_fake_client(payload))
        result = adapter.read_material_table(b"image")
        assert result == [], f"unit {raw_unit!r} should be dropped (mirrors SUNAT)"

    def test_vision_unit_map_mirrors_sunat_map_exactly(self) -> None:
        """PARITY GUARD (R2-W1): the vision UNIT_LABEL_MAP MUST equal the
        authoritative SUNAT recovery map so the two can never drift again."""
        from reconciliation.application.reprocess_service import _SUNAT_UNIT_MAP
        from reconciliation.domain.units import UNIT_LABEL_MAP

        assert UNIT_LABEL_MAP == _SUNAT_UNIT_MAP

    def test_unmappable_unit_still_dropped_and_warns(self, caplog) -> None:
        """A genuinely unmappable unit is still skipped but logged at WARNING."""
        import logging  # noqa: PLC0415

        from reconciliation.adapters.vision.anthropic_vision import AnthropicVisionAdapter

        payload = json.dumps({
            "lines": [{"descripcion": "MYSTERY", "cantidad": 1, "unidad": "PARSECS"}],
            "confidence": 0.9,
        })
        adapter = AnthropicVisionAdapter(client=_make_anthropic_fake_client(payload))
        with caplog.at_level(logging.WARNING):
            result = adapter.read_material_table(b"image")
        assert result == []
        assert any("PARSECS" in rec.message for rec in caplog.records)
