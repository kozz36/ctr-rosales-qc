"""Unit tests for manifest hooks in _run_pipeline_background.

Spec: RH-001-S01 (success), RH-001-S02 (non-fatal), RH-001-S03 (failure).
TDD Phase: RED — all tests FAIL before manifest hooks are wired in routes.py.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest


def _fresh_run_id() -> str:
    return str(uuid.uuid4())


def _make_real_registro(numero: str) -> Any:
    """Build a REAL domain Registro so manifest field access mirrors production.

    The model field is ``numero`` (NOT ``registro``); a MagicMock with a
    ``.registro`` attr masked the bug where ``_build_run_manifest`` read the
    wrong attribute and always produced ``registro_min/max = None``.
    """
    from reconciliation.domain.models import Registro  # noqa: PLC0415

    return Registro(numero=numero, fecha_declarada=date(2026, 5, 28), declared_lines=[])


def _make_fake_result(row_count: int = 5) -> MagicMock:
    """Build a fake PipelineResult that the wrapper can extract manifest fields from."""
    result = MagicMock()
    result.rows = [MagicMock() for _ in range(row_count)]
    result.warnings = ["warn1", "warn2"]
    result.vision_calls_made = 2
    result.errored_guias = []
    # declared holds REAL Registro objects (field is `numero`, not `registro`)
    result.declared = [
        _make_real_registro("220"),
        _make_real_registro("232"),
        _make_real_registro("245"),
    ]
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
        #
        # D1 (F5): the run-history adapter is now INJECTED into the wrapper (the
        # single app.state.run_history instance), not constructed inline — so we
        # pass the mock directly as the run_history argument.
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
        ):
            _run_pipeline_background(run_id, pdf_path, config, registry, _adapter)

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


# ---------------------------------------------------------------------------
# A1 — _build_run_manifest reads the REAL Registro.numero field
# ---------------------------------------------------------------------------


class TestBuildRunManifestRegistroRange:
    """_build_run_manifest must derive registro_min/max from Registro.numero."""

    def test_registro_min_max_from_real_registro_numero(self) -> None:
        """A single REAL Registro(numero='227') → registro_min == registro_max == '227'.

        Regression: the wrapper read ``item.registro`` (nonexistent) so every
        manifest carried registro_min/max = None.  The model field is ``numero``.
        """
        from reconciliation.infrastructure.api.routes import _build_run_manifest  # noqa: PLC0415

        result = MagicMock()
        result.rows = []
        result.warnings = []
        result.vision_calls_made = 0
        result.errored_guias = []
        result.declared = [_make_real_registro("227")]

        entry: dict[str, Any] = {"vision_calls_made": 0}
        manifest = _build_run_manifest(
            result, entry, "2026-06-10T00:00:00+00:00", _fresh_run_id()
        )

        assert manifest.registro_min == "227"
        assert manifest.registro_max == "227"

    def test_registro_min_max_int_sorted_range(self) -> None:
        """Multiple REAL Registros → int-sorted min/max (220..245)."""
        from reconciliation.infrastructure.api.routes import _build_run_manifest  # noqa: PLC0415

        result = _make_fake_result()
        manifest = _build_run_manifest(
            result, {"vision_calls_made": 2}, "2026-06-10T00:00:00+00:00", _fresh_run_id()
        )

        assert manifest.registro_min == "220"
        assert manifest.registro_max == "245"


# ---------------------------------------------------------------------------
# A2 — successful background run merges manifest-derived fields into registry
# ---------------------------------------------------------------------------


class TestBackgroundWrapperRegistryMerge:
    """After a successful write_manifest, the registry entry must carry the
    display fields (seq / registro_min / registro_max / completed_at) so a
    same-session GET /runs shows #N and the registro range without a restart."""

    def test_registry_entry_carries_manifest_fields_after_success(
        self, tmp_path: Path
    ) -> None:
        from reconciliation.infrastructure.api.routes import (  # noqa: PLC0415
            _run_pipeline_background,
        )

        run_id = _fresh_run_id()
        registry: dict[str, Any] = {run_id: {"status": "pending"}}

        pdf_path = tmp_path / f"{run_id}.pdf"
        pdf_path.write_bytes(b"%PDF-1.4")
        (tmp_path / run_id).mkdir(exist_ok=True)

        config = MagicMock()
        config.output_dir = tmp_path

        fake_result = _make_fake_result()
        ctx = MagicMock()
        ctx.run_dir = tmp_path / run_id
        pipeline_mock = MagicMock()
        pipeline_mock.run.return_value = fake_result

        mock_adapter = MagicMock()
        # write_manifest returns the ALLOCATED per-day seq (additive contract).
        mock_adapter.write_manifest.return_value = 3

        with patch(
            "reconciliation.infrastructure.container.build_pipeline",
            return_value=(pipeline_mock, ctx, {}),
        ), patch(
            "reconciliation.infrastructure.container.build_review_service",
            return_value=MagicMock(),
        ), patch(
            "reconciliation.infrastructure.container.build_reprocess_service",
            return_value=MagicMock(),
        ):
            _run_pipeline_background(run_id, pdf_path, config, registry, mock_adapter)

        entry = registry[run_id]
        assert entry["seq"] == 3, "registry must carry the allocated seq after success"
        assert entry["registro_min"] == "220"
        assert entry["registro_max"] == "245"
        assert entry.get("completed_at"), "registry must carry completed_at"
