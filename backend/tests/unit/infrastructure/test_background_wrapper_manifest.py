"""Unit tests for manifest hooks in _run_pipeline_background.

Spec: RH-001-S01 (success), RH-001-S02 (non-fatal), RH-001-S03 (failure).
TDD Phase: RED — all tests FAIL before manifest hooks are wired in routes.py.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest


def _fresh_run_id() -> str:
    return str(uuid.uuid4())


def _make_fake_result(row_count: int = 5) -> MagicMock:
    """Build a fake PipelineResult that the wrapper can extract manifest fields from."""
    result = MagicMock()
    result.rows = [MagicMock() for _ in range(row_count)]
    result.warnings = ["warn1", "warn2"]
    result.vision_calls_made = 2
    result.errored_guias = []
    # declared has registro numbers for min/max derivation
    declared_items = [MagicMock() for _ in range(3)]
    declared_items[0].registro = "220"
    declared_items[1].registro = "232"
    declared_items[2].registro = "245"
    result.declared = declared_items
    return result


# ---------------------------------------------------------------------------
# 1.1.21 — manifest written on success
# ---------------------------------------------------------------------------


class TestBackgroundWrapperManifestHooks:
    """Manifest hooks in _run_pipeline_background (D1 invariant: routes.py only)."""

    def _run_wrapper(
        self,
        run_id: str,
        tmp_path: Path,
        registry: dict[str, Any],
        pipeline_side_effect: Any = None,
        mock_adapter: MagicMock | None = None,
    ) -> None:
        """Call _run_pipeline_background in the same thread (no BackgroundTask)."""
        from reconciliation.infrastructure.api.routes import _run_pipeline_background  # noqa: PLC0415

        # Fake pdf path (doesn't need to exist — build_pipeline is mocked)
        pdf_path = tmp_path / f"{run_id}.pdf"
        pdf_path.write_bytes(b"%PDF-1.4")
        (tmp_path / run_id).mkdir(exist_ok=True)

        config = MagicMock()
        config.output_dir = tmp_path

        fake_result = _make_fake_result()

        ctx = MagicMock()
        ctx.run_dir = tmp_path / run_id

        if pipeline_side_effect is not None:
            pipeline_mock = MagicMock()
            pipeline_mock.run.side_effect = pipeline_side_effect
        else:
            pipeline_mock = MagicMock()
            pipeline_mock.run.return_value = fake_result

        # All heavy deps are lazy-imported inside _run_pipeline_background.
        # Patch at the module where they are defined (not on routes which never has them
        # as module-level attrs).
        _adapter = mock_adapter if mock_adapter is not None else MagicMock()

        with patch(
            "reconciliation.infrastructure.container.build_pipeline",
            return_value=(pipeline_mock, ctx, {}),
        ), patch(
            "reconciliation.infrastructure.container.build_review_service",
            return_value=MagicMock(),
        ), patch(
            "reconciliation.infrastructure.container.build_reprocess_service",
            return_value=MagicMock(),
        ), patch(
            "reconciliation.infrastructure.run_history_store.JsonManifestRunHistoryAdapter",
            return_value=_adapter,
        ):
            _run_pipeline_background(run_id, pdf_path, config, registry)

    def test_manifest_written_on_success(self, tmp_path: Path) -> None:
        """On success, adapter.write_manifest is called once (RH-001-S01)."""
        run_id = _fresh_run_id()
        registry: dict[str, Any] = {run_id: {"status": "pending"}}

        mock_adapter = MagicMock()
        self._run_wrapper(run_id, tmp_path, registry, mock_adapter=mock_adapter)

        assert mock_adapter.write_manifest.call_count == 1, (
            "write_manifest must be called exactly once on success"
        )

    def test_manifest_written_on_pipeline_exception(self, tmp_path: Path) -> None:
        """On pipeline exception, write_failure_manifest is called once (RH-001-S03)."""
        run_id = _fresh_run_id()
        registry: dict[str, Any] = {run_id: {"status": "pending"}}

        mock_adapter = MagicMock()
        self._run_wrapper(
            run_id, tmp_path, registry,
            pipeline_side_effect=RuntimeError("boom"),
            mock_adapter=mock_adapter,
        )

        assert mock_adapter.write_failure_manifest.call_count == 1, (
            "write_failure_manifest must be called once on exception"
        )

    def test_manifest_ioerror_does_not_fail_run(self, tmp_path: Path) -> None:
        """IOError in write_manifest MUST NOT change run status to error (RH-001-S02, D1)."""
        run_id = _fresh_run_id()
        registry: dict[str, Any] = {run_id: {"status": "pending"}}

        mock_adapter = MagicMock()
        mock_adapter.write_manifest.side_effect = OSError("disk full")

        self._run_wrapper(run_id, tmp_path, registry, mock_adapter=mock_adapter)

        # Run must still show review status — manifest failure is non-fatal
        assert registry[run_id]["status"] == "review", (
            "manifest IOError must not change run status; run must complete as 'review'"
        )
