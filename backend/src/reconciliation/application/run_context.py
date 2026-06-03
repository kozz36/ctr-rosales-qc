"""RunContext — per-run isolation container.

Each pipeline invocation gets its own RunContext, which owns:
  - A unique run_id (UUID4)
  - A run-specific output directory under AppConfig.output_dir
  - An immutable extraction cache path (extraction_cache.json)
  - A mutable review sidecar path (review.json)
  - An optional progress_cb (Callable[[ProgressEvent], None]) for live
    progress reporting to infrastructure (determinate progress bar).

The input PDF is treated as read-only; RunContext never writes to it.

Atomic sidecar writes use a temp-file-then-rename strategy to prevent
partial writes from corrupting the review state.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ProgressEvent — pure stdlib frozen dataclass; no adapter/framework imports
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProgressEvent:
    """Emitted by the pipeline on each item within a slow stage.

    Invariants:
      - stage_index is 1-based (1..stage_total).
      - item_done is 1-based (1..item_total).
      - item_total always comes from REAL counts (never hardcoded).

    Fields:
        stage_label:  Human-readable Spanish label for the current stage.
        stage_index:  1-based index of the current stage (1..stage_total).
        stage_total:  Total number of instrumented stages (always 5).
        item_done:    Number of items completed so far in this stage (1-based).
        item_total:   Total number of items in this stage (from real counts).
    """

    stage_label: str
    stage_index: int
    stage_total: int
    item_done: int
    item_total: int


class RunContext:
    """Holds all per-run I/O paths and provides sidecar persistence helpers.

    Attributes:
        run_id:           UUID4 string uniquely identifying this run.
        run_dir:          Directory dedicated to this run's outputs.
        pdf_path:         Source PDF (read-only reference).
        extraction_cache: Path to the immutable extraction cache JSON.
        review_sidecar:   Path to the mutable review.json sidecar.
    """

    def __init__(
        self,
        pdf_path: Path,
        output_base: Path,
        run_id: str | None = None,
        progress_cb: Callable[[ProgressEvent], None] | None = None,
    ) -> None:
        """Create a RunContext, creating the run directory if needed.

        Args:
            pdf_path:    Absolute path to the source PDF (never written).
            output_base: Root output directory (from AppConfig.output_dir).
            run_id:      Optional explicit run ID (used for restart/resume).
                         Generates a new UUID4 if not provided.
            progress_cb: Optional callable that receives ProgressEvent objects
                         during pipeline execution.  Infrastructure-injected;
                         application/ must never import a concrete type here.
                         Exceptions from the callback are swallowed so they
                         never interrupt a run.
        """
        self.run_id: str = run_id or str(uuid.uuid4())
        self.pdf_path: Path = Path(pdf_path)
        self.run_dir: Path = Path(output_base) / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)

        self.extraction_cache: Path = self.run_dir / "extraction_cache.json"
        self.review_sidecar: Path = self.run_dir / "review.json"
        self._progress_cb: Callable[[ProgressEvent], None] | None = progress_cb

    # ------------------------------------------------------------------
    # Progress reporting (observational-only; never alters run results)
    # ------------------------------------------------------------------

    def report_progress(
        self,
        stage_label: str,
        stage_index: int,
        stage_total: int,
        item_done: int,
        item_total: int,
    ) -> None:
        """Emit a ProgressEvent to the injected callback (if set).

        This method is intentionally observational: it MUST NOT alter any
        reconciliation result, grouping key, or quantity.  It is a pure
        side-channel.

        Any exception raised by ``_progress_cb`` is caught and logged at
        DEBUG level — progress reporting must never break a run.

        Args:
            stage_label:  Human-readable Spanish label for the current stage.
            stage_index:  1-based stage number (1..stage_total).
            stage_total:  Total number of instrumented stages (5).
            item_done:    Items completed so far in this stage (1-based).
            item_total:   Total items in this stage (from real counts).
        """
        if self._progress_cb is None:
            return
        event = ProgressEvent(
            stage_label=stage_label,
            stage_index=stage_index,
            stage_total=stage_total,
            item_done=item_done,
            item_total=item_total,
        )
        try:
            self._progress_cb(event)
        except Exception:  # noqa: BLE001
            _logger.debug(
                "report_progress: progress_cb raised (swallowed); stage=%r item=%d/%d",
                stage_label,
                item_done,
                item_total,
            )

    # ------------------------------------------------------------------
    # Extraction cache (write-once; immutable after first write)
    # ------------------------------------------------------------------

    def has_extraction_cache(self) -> bool:
        """Return True if the extraction cache already exists on disk."""
        return self.extraction_cache.exists()

    def write_extraction_cache(self, data: dict[str, Any]) -> None:
        """Persist extraction results atomically.

        This is a write-once operation — calling this when the cache already
        exists is a programming error and raises RuntimeError.

        Args:
            data: Serialisable dict produced by the extraction stage.
        """
        if self.has_extraction_cache():
            raise RuntimeError(
                f"Extraction cache already exists: {self.extraction_cache}. "
                "It is immutable after first write."
            )
        _atomic_json_write(self.extraction_cache, data)

    def read_extraction_cache(self) -> dict[str, Any]:
        """Load and return the extraction cache.

        Raises:
            FileNotFoundError: if the cache has not been written yet.
        """
        if not self.has_extraction_cache():
            raise FileNotFoundError(
                f"Extraction cache not found: {self.extraction_cache}"
            )
        with self.extraction_cache.open("r", encoding="utf-8") as fh:
            return json.load(fh)  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Review sidecar (mutable; atomic overwrite on each save)
    # ------------------------------------------------------------------

    def has_review_sidecar(self) -> bool:
        """Return True if a review sidecar exists on disk."""
        return self.review_sidecar.exists()

    def write_review_sidecar(self, data: dict[str, Any]) -> None:
        """Persist review state atomically (temp-file + rename).

        Overwrites any existing sidecar.  Safe to call multiple times.

        Args:
            data: Serialisable dict containing edits and audit trail.
        """
        _atomic_json_write(self.review_sidecar, data)

    def read_review_sidecar(self) -> dict[str, Any]:
        """Load and return the review sidecar, or an empty dict if none exists.

        Returns:
            Parsed sidecar dict, or ``{}`` when no sidecar exists yet.
        """
        if not self.has_review_sidecar():
            return {}
        with self.review_sidecar.open("r", encoding="utf-8") as fh:
            return json.load(fh)  # type: ignore[no-any-return]

    def append_vision_audit(self, record: dict[str, Any]) -> None:
        """Append a vision-stage audit record to the review sidecar.

        Creates the sidecar if it does not yet exist.  Atomically overwrites
        on each call (temp-file + rename strategy).

        The sidecar ``vision_audit`` key holds a list of audit records:
          {stage: "vision", calls_made: int, cap_reached: bool}
        """
        sidecar = self.read_review_sidecar()
        sidecar.setdefault("vision_audit", [])
        sidecar["vision_audit"].append(record)
        _atomic_json_write(self.review_sidecar, sidecar)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _atomic_json_write(path: Path, data: dict[str, Any]) -> None:
    """Write *data* to *path* atomically via a sibling temp file + rename.

    Uses the same directory as *path* to guarantee the rename is on the same
    filesystem (avoids cross-device link errors on Windows).
    """
    dir_ = path.parent
    # NamedTemporaryFile with delete=False keeps the file after close on Windows.
    fd, tmp_path_str = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
        # os.replace is atomic on POSIX and Windows (NTFS).
        os.replace(tmp_path_str, path)
    except Exception:
        # Clean up temp file on failure; ignore cleanup errors.
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise
