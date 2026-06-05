"""T5 / REV-R17 — build_reprocess_service decoupled from SUNAT gate.

Strict-TDD: ALL tests written FIRST (RED).

Covers:
- build_reprocess_service returns ReprocessService when vision.enabled=True (SUNAT off).
- build_reprocess_service returns ReprocessService when sunat.enabled=True (vision off).
- build_reprocess_service returns ReprocessService when both enabled.
- Vision adapter is injected into the returned service.
- sunat=None on the service when SUNAT disabled (apply_retry still callable).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from reconciliation.application.config import AppConfig
from reconciliation.application.reprocess_service import ReprocessService


def _make_config(
    sunat_enabled: bool = False,
    vision_enabled: bool = True,
    **kwargs: Any,
) -> AppConfig:
    return AppConfig(
        pdf_path=Path("/tmp/fake.pdf"),
        output_dir=Path("/tmp/out"),
        sunat={"enabled": sunat_enabled},
        vision={"enabled": vision_enabled},
        **kwargs,
    )


def _make_ctx(tmp_path: Path) -> MagicMock:
    ctx = MagicMock()
    ctx.pdf_path = tmp_path / "fake.pdf"
    ctx.run_dir = tmp_path / "run"
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "fake.pdf").touch()
    return ctx


@pytest.fixture()
def tmp_ctx(tmp_path: Path) -> MagicMock:
    return _make_ctx(tmp_path)


class TestBuildReprocessServiceDecoupled:
    """build_reprocess_service should build when vision OR SUNAT is available."""

    def test_returns_service_when_vision_enabled_sunat_disabled(
        self, tmp_ctx: MagicMock
    ) -> None:
        """Vision-only config → service built (not None)."""
        from reconciliation.infrastructure.container import (  # noqa: PLC0415
            build_reprocess_service,
        )

        config = _make_config(sunat_enabled=False, vision_enabled=True)
        review_service = MagicMock()

        with (
            patch("reconciliation.adapters.pdf.pymupdf_source.PdfStructureAdapter"),
            patch("reconciliation.adapters.identity.qr_barcode.QrBarcodeExtractionAdapter"),
            patch("reconciliation.adapters.vision.factory.build_vision_adapter", return_value=MagicMock()),
            patch("reconciliation.adapters.inference.factory.build_inference_adapter", return_value=None),
        ):
            service = build_reprocess_service(config, tmp_ctx, review_service)

        assert service is not None
        assert isinstance(service, ReprocessService)

    def test_returns_service_when_sunat_enabled_vision_disabled(
        self, tmp_ctx: MagicMock
    ) -> None:
        """SUNAT-only config → service built (not None)."""
        from reconciliation.infrastructure.container import (  # noqa: PLC0415
            build_reprocess_service,
        )

        config = _make_config(sunat_enabled=True, vision_enabled=False)
        review_service = MagicMock()

        with (
            patch("reconciliation.adapters.pdf.pymupdf_source.PdfStructureAdapter"),
            patch("reconciliation.adapters.identity.qr_barcode.QrBarcodeExtractionAdapter"),
            patch("reconciliation.adapters.sunat.descargaqr.SunatDescargaqrAdapter"),
            patch("reconciliation.adapters.vision.null_vision.NullVisionAdapter", return_value=MagicMock()),
            patch("reconciliation.adapters.inference.factory.build_inference_adapter", return_value=None),
        ):
            service = build_reprocess_service(config, tmp_ctx, review_service)

        assert service is not None
        assert isinstance(service, ReprocessService)

    def test_returns_service_when_both_enabled(self, tmp_ctx: MagicMock) -> None:
        """Vision+SUNAT both enabled → service built."""
        from reconciliation.infrastructure.container import (  # noqa: PLC0415
            build_reprocess_service,
        )

        config = _make_config(sunat_enabled=True, vision_enabled=True)
        review_service = MagicMock()

        with (
            patch("reconciliation.adapters.pdf.pymupdf_source.PdfStructureAdapter"),
            patch("reconciliation.adapters.identity.qr_barcode.QrBarcodeExtractionAdapter"),
            patch("reconciliation.adapters.sunat.descargaqr.SunatDescargaqrAdapter"),
            patch("reconciliation.adapters.vision.factory.build_vision_adapter", return_value=MagicMock()),
            patch("reconciliation.adapters.inference.factory.build_inference_adapter", return_value=None),
        ):
            service = build_reprocess_service(config, tmp_ctx, review_service)

        assert service is not None
        assert isinstance(service, ReprocessService)

    def test_vision_adapter_injected_when_vision_enabled(self, tmp_ctx: MagicMock) -> None:
        """The vision adapter is injected as service._vision (not None) when vision enabled."""
        from reconciliation.infrastructure.container import (  # noqa: PLC0415
            build_reprocess_service,
        )

        config = _make_config(sunat_enabled=False, vision_enabled=True)
        review_service = MagicMock()
        fake_vision = MagicMock()

        with (
            patch("reconciliation.adapters.pdf.pymupdf_source.PdfStructureAdapter"),
            patch("reconciliation.adapters.identity.qr_barcode.QrBarcodeExtractionAdapter"),
            patch("reconciliation.adapters.vision.factory.build_vision_adapter", return_value=fake_vision),
            patch("reconciliation.adapters.inference.factory.build_inference_adapter", return_value=None),
        ):
            service = build_reprocess_service(config, tmp_ctx, review_service)

        assert service is not None
        assert service._vision is fake_vision  # type: ignore[attr-defined]

    def test_sunat_is_none_when_sunat_disabled(self, tmp_ctx: MagicMock) -> None:
        """service._sunat is None when SUNAT disabled (vision-only path)."""
        from reconciliation.infrastructure.container import (  # noqa: PLC0415
            build_reprocess_service,
        )

        config = _make_config(sunat_enabled=False, vision_enabled=True)
        review_service = MagicMock()

        with (
            patch("reconciliation.adapters.pdf.pymupdf_source.PdfStructureAdapter"),
            patch("reconciliation.adapters.identity.qr_barcode.QrBarcodeExtractionAdapter"),
            patch("reconciliation.adapters.vision.factory.build_vision_adapter", return_value=MagicMock()),
            patch("reconciliation.adapters.inference.factory.build_inference_adapter", return_value=None),
        ):
            service = build_reprocess_service(config, tmp_ctx, review_service)

        assert service is not None
        assert service._sunat is None  # type: ignore[attr-defined]

    def test_config_max_concurrency_passed_to_service(self, tmp_ctx: MagicMock) -> None:
        """max_concurrency from config is wired to service._max_concurrency."""
        from reconciliation.infrastructure.container import (  # noqa: PLC0415
            build_reprocess_service,
        )

        config = AppConfig(
            pdf_path=Path("/tmp/fake.pdf"),
            output_dir=Path("/tmp/out"),
            sunat={"enabled": False},
            vision={"enabled": True, "reprocess_max_concurrency": 5},
        )
        review_service = MagicMock()

        with (
            patch("reconciliation.adapters.pdf.pymupdf_source.PdfStructureAdapter"),
            patch("reconciliation.adapters.identity.qr_barcode.QrBarcodeExtractionAdapter"),
            patch("reconciliation.adapters.vision.factory.build_vision_adapter", return_value=MagicMock()),
            patch("reconciliation.adapters.inference.factory.build_inference_adapter", return_value=None),
        ):
            service = build_reprocess_service(config, tmp_ctx, review_service)

        assert service is not None
        assert service._max_concurrency == 5  # type: ignore[attr-defined]
