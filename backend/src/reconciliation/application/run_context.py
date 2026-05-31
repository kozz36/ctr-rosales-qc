"""RunContext — per-run isolation container.

Each pipeline invocation gets its own RunContext, which owns:
  - A unique run_id (UUID4)
  - A run-specific output directory under AppConfig.output_dir
  - An immutable extraction cache path (extraction_cache.json)
  - A mutable review sidecar path (review.json)

The input PDF is treated as read-only; RunContext never writes to it.

Atomic sidecar writes use a temp-file-then-rename strategy to prevent
partial writes from corrupting the review state.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any


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
    ) -> None:
        """Create a RunContext, creating the run directory if needed.

        Args:
            pdf_path:    Absolute path to the source PDF (never written).
            output_base: Root output directory (from AppConfig.output_dir).
            run_id:      Optional explicit run ID (used for restart/resume).
                         Generates a new UUID4 if not provided.
        """
        self.run_id: str = run_id or str(uuid.uuid4())
        self.pdf_path: Path = Path(pdf_path)
        self.run_dir: Path = Path(output_base) / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)

        self.extraction_cache: Path = self.run_dir / "extraction_cache.json"
        self.review_sidecar: Path = self.run_dir / "review.json"

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
