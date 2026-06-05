"""T2 / REV-R11, REV-R15 — VisionConfig reprocess fields.

Strict-TDD: tests written FIRST (RED) before implementation.
"""

from __future__ import annotations

import os

import pytest

from reconciliation.application.config import VisionConfig

try:
    from pydantic import ValidationError
except ImportError:
    from pydantic.v1 import ValidationError  # type: ignore[no-redef]


class TestVisionConfigReprocessFields:
    def test_reprocess_max_concurrency_default_is_3(self) -> None:
        """reprocess_max_concurrency defaults to 3."""
        c = VisionConfig()
        assert c.reprocess_max_concurrency == 3

    def test_reprocess_downscale_max_edge_default_is_2000(self) -> None:
        """reprocess_downscale_max_edge defaults to 2000."""
        c = VisionConfig()
        assert c.reprocess_downscale_max_edge == 2000

    def test_reprocess_max_concurrency_env_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """RECONCILIATION__VISION__REPROCESS_MAX_CONCURRENCY env override (via AppConfig)."""
        from reconciliation.application.config import AppConfig

        monkeypatch.setenv(
            "RECONCILIATION__VISION__REPROCESS_MAX_CONCURRENCY", "5"
        )
        cfg = AppConfig()
        assert cfg.vision.reprocess_max_concurrency == 5

    def test_reprocess_downscale_max_edge_env_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """RECONCILIATION__VISION__REPROCESS_DOWNSCALE_MAX_EDGE env override (via AppConfig)."""
        from reconciliation.application.config import AppConfig

        monkeypatch.setenv(
            "RECONCILIATION__VISION__REPROCESS_DOWNSCALE_MAX_EDGE", "1500"
        )
        cfg = AppConfig()
        assert cfg.vision.reprocess_downscale_max_edge == 1500

    def test_reprocess_max_concurrency_rejects_zero(self) -> None:
        """reprocess_max_concurrency must be > 0 (Field(gt=0))."""
        with pytest.raises((ValidationError, ValueError)):
            VisionConfig(reprocess_max_concurrency=0)

    def test_reprocess_downscale_max_edge_rejects_zero(self) -> None:
        """reprocess_downscale_max_edge must be > 0 (Field(gt=0))."""
        with pytest.raises((ValidationError, ValueError)):
            VisionConfig(reprocess_downscale_max_edge=0)

    def test_custom_values_accepted(self) -> None:
        """Both fields accept valid positive integer values."""
        c = VisionConfig(reprocess_max_concurrency=10, reprocess_downscale_max_edge=3000)
        assert c.reprocess_max_concurrency == 10
        assert c.reprocess_downscale_max_edge == 3000
