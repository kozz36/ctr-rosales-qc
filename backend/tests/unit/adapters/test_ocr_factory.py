"""Unit tests for the OCR adapter factory (EXT-027).

Verifies that ``build_ocr_extractor`` correctly selects the concrete adapter
based on ``OcrConfig.engine`` and ``OcrConfig.enabled``.

No real OCR SDK installed required:
- ``rapidocr`` path is tested by type-name (class.__name__) because the
  actual module is never imported at test time.
- ``paddle`` path is tested similarly (PrintedTableAdapter).
- ``enabled=False`` path uses NullOcrExtractor (stdlib-only, always importable).

S027a: engine="rapidocr"  → RapidOCRAdapter instance
S027b: engine="paddle"    → PrintedTableAdapter instance
S027c: enabled=False      → NullOcrExtractor instance (regardless of engine)
S027d: factory module itself must not import any heavy SDK at module load
S027e: unknown engine     → ValueError
"""

from __future__ import annotations

import sys


# ---------------------------------------------------------------------------
# S027d — no-SDK-at-import gate
# ---------------------------------------------------------------------------


class TestFactoryNoSdkAtImport:
    def test_factory_module_imports_no_sdk(self) -> None:
        """S027d: importing factory.py must NOT pull in rapidocr, onnxruntime, paddleocr."""
        heavy = {"rapidocr", "onnxruntime", "paddleocr"}
        before = set(sys.modules.keys())

        from reconciliation.adapters.ocr import factory  # noqa: PLC0415, F401

        after = set(sys.modules.keys())
        newly_imported = {k.split(".")[0] for k in (after - before)}
        leaked = heavy & newly_imported
        assert not leaked, (
            f"Factory module imported heavy SDK(s) at module load time: {leaked}. "
            "All concrete adapters must be lazy-imported inside build_ocr_extractor."
        )


# ---------------------------------------------------------------------------
# S027a-c, S027e — selection logic
# ---------------------------------------------------------------------------


class TestBuildOcrExtractorSelection:
    """build_ocr_extractor selects the correct adapter from OcrConfig."""

    def test_rapidocr_engine_returns_rapidocr_adapter(self) -> None:
        """S027a: engine='rapidocr' → RapidOCRAdapter."""
        from reconciliation.adapters.ocr.factory import build_ocr_extractor
        from reconciliation.application.config import OcrConfig

        cfg = OcrConfig(enabled=True, engine="rapidocr")
        adapter = build_ocr_extractor(cfg)
        assert type(adapter).__name__ == "RapidOCRAdapter", (
            f"Expected RapidOCRAdapter, got {type(adapter).__name__}"
        )

    def test_paddle_engine_returns_printed_table_adapter(self) -> None:
        """S027b: engine='paddle' → PrintedTableAdapter."""
        from reconciliation.adapters.ocr.factory import build_ocr_extractor
        from reconciliation.application.config import OcrConfig

        cfg = OcrConfig(enabled=True, engine="paddle")
        adapter = build_ocr_extractor(cfg)
        assert type(adapter).__name__ == "PrintedTableAdapter", (
            f"Expected PrintedTableAdapter, got {type(adapter).__name__}"
        )

    def test_enabled_false_returns_null_extractor(self) -> None:
        """S027c: enabled=False → NullOcrExtractor regardless of engine value."""
        from reconciliation.adapters.ocr.factory import build_ocr_extractor
        from reconciliation.application.config import OcrConfig

        cfg = OcrConfig(enabled=False, engine="rapidocr")
        adapter = build_ocr_extractor(cfg)
        # NullOcrExtractor is always importable (stdlib-only).
        from reconciliation.adapters.ocr.null_extractor import NullOcrExtractor  # noqa: PLC0415

        assert isinstance(adapter, NullOcrExtractor), (
            f"expected NullOcrExtractor for enabled=False, got {type(adapter).__name__}"
        )

    def test_unknown_engine_raises_value_error(self) -> None:
        """S027e: an unrecognised engine string raises ValueError."""
        from reconciliation.adapters.ocr.factory import build_ocr_extractor
        from reconciliation.application.config import OcrConfig

        # extra="allow" on OcrConfig means an unknown engine string can be set;
        # the factory must still reject it.
        cfg = OcrConfig(enabled=True)
        # Force the engine field to an unknown value bypassing Literal validation.
        object.__setattr__(cfg, "engine", "trocr")  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="trocr"):
            build_ocr_extractor(cfg)

    def test_factory_returns_extraction_port_protocol(self) -> None:
        """build_ocr_extractor must return an ExtractionPort-compatible object."""
        from reconciliation.adapters.ocr.factory import build_ocr_extractor
        from reconciliation.application.config import OcrConfig
        from reconciliation.domain.ports import ExtractionPort

        # Use enabled=False — NullOcrExtractor satisfies the protocol without any SDK.
        cfg = OcrConfig(enabled=False)
        adapter = build_ocr_extractor(cfg)
        assert isinstance(adapter, ExtractionPort), (
            f"{type(adapter).__name__} does not satisfy ExtractionPort"
        )


import pytest  # noqa: E402 — placed after class definitions intentionally
