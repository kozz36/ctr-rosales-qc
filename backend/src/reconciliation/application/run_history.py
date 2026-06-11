"""RunHistoryPort Protocol and RunManifest schema.

Application layer — PURE: zero IO imports, zero SDK imports.
Only stdlib typing + pydantic (already a project-wide dep).

Architecture invariant: domain/ is never touched.
The manifest is a pipeline-result aggregate persisted by the infrastructure
adapter (JsonManifestRunHistoryAdapter in infrastructure/run_history_store.py).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel

if TYPE_CHECKING:
    pass  # no adapter imports — Protocol only


# ---------------------------------------------------------------------------
# RunManifest — persistent record per completed pipeline run (D2)
# ---------------------------------------------------------------------------


class RunManifest(BaseModel):
    """Persisted snapshot of a completed (success or failure) pipeline run.

    Written atomically to {output_dir}/{run_id}/run_manifest.json.

    Design decisions (D2):
    - schema_version=1 allows forward-compatible migration.
    - status: "review" (success) | "error" (pipeline exception).
    - No pdf_filename field — CWE-22: path inputs must not be stored as-is.
    - seq: write-time per-day sequence number (1-based, allocated under
      threading.Lock in the adapter).
    - warnings: full list stored at write-time (serves GET /runs/{id} cold
      without hydrating the ReviewService).
    - Atomic overwrite semantic — NOT write-once. Retry resets the dir and
      re-writes the manifest via the same path (D5).
    """

    schema_version: int = 1
    run_id: str
    status: Literal["review", "error"]
    started_at: str  # ISO-8601 UTC
    completed_at: str | None = None  # ISO-8601 UTC; null on failure manifests
    seq: int  # per-day 1-based sequence number
    registro_min: str | None = None  # lexicographic min registro number from result.declared
    registro_max: str | None = None  # lexicographic max registro number from result.declared
    row_count: int = 0
    match_count: int = 0
    mismatch_count: int = 0
    warnings: list[str] = []
    vision_calls_made: int = 0
    error: str | None = None  # str(exc) on failure; null on success


# ---------------------------------------------------------------------------
# RunHistoryPort — Protocol (typing-only; never imported by domain/)
# ---------------------------------------------------------------------------


class RunHistoryPort(Protocol):
    """Port for persisting and scanning run history manifests.

    Implemented by JsonManifestRunHistoryAdapter in infrastructure layer.
    All methods interact with the filesystem; the protocol itself is pure.
    """

    def write_manifest(
        self,
        manifest: RunManifest,
        output_dir: Path,
        force_seq: int | None = None,
    ) -> None:
        """Persist a completed run manifest atomically.

        On IOError / OSError: log and return without propagating — manifest
        failure MUST NOT fail the run (D1 invariant).

        Args:
            manifest:   Completed RunManifest (seq already allocated).
            output_dir: Root output directory (AppConfig.output_dir).
            force_seq:  L-3: when set (same-day retry completion), reuse the
                        run's ORIGINAL per-day seq instead of allocating a new
                        one, so the display identity (#N) stays stable (D3).
        """
        ...

    def write_failure_manifest(
        self,
        run_id: str,
        started_at: str,
        error_str: str,
        output_dir: Path,
    ) -> None:
        """Write a failure manifest for an exceptioned pipeline run.

        Sets status="error", error=error_str, all count fields to 0,
        completed_at=now, registro fields null.  Non-fatal on IOError.

        Args:
            run_id:     UUID string of the failed run.
            started_at: ISO-8601 UTC string when the run started.
            error_str:  str(exc) from the except branch.
            output_dir: Root output directory.
        """
        ...

    def scan(self, output_dir: Path) -> list[dict[str, Any]]:
        """Scan output_dir and return registry-ready entry dicts.

        Strategy per subdir (UUID-named only, D4):
        1. run_manifest.json present and valid → full entry, degraded=False.
        2. run_manifest.json corrupted (JSON decode error) → skip + log.
        3. extraction_cache.json present (no manifest) → degraded entry,
           status="review", timestamps null.
        4. {run_id}.pdf only → degraded entry, status="error".
        5. Empty dir → skip.

        Per-dir try/except — NEVER crashes startup.
        All entries have hydrated=False.

        Args:
            output_dir: Root output directory to scan.

        Returns:
            List of registry-compatible dicts (one per discovered run).
        """
        ...

    def sweep_failed(
        self,
        output_dir: Path,
        cutoff: datetime,
        skip_run_ids: set[str] | None = None,
    ) -> list[str]:
        """Delete error-status runs older than cutoff.

        ONLY deletes runs with status="error" — completed runs NEVER swept.
        Per-dir try/except — never crashes.

        Args:
            output_dir:   Root output directory.
            cutoff:       datetime; runs with completed_at (or mtime) before
                          this value are eligible for deletion.
            skip_run_ids: H-1: run IDs currently in-flight (pending/processing)
                          per the in-memory registry — NEVER swept, protecting a
                          mid-retry run's PDF from rmtree.

        Returns:
            List of run_ids deleted (for caller to remove from registry).
        """
        ...

    def read_seq(self, run_id: str, output_dir: Path) -> int | None:
        """Return the per-day seq stored in the run's manifest, or None.

        L-3: read before a retry dir reset so the original display seq (#N)
        can be threaded into the completion manifest write (force_seq).
        """
        ...

    def mark_pending(self, run_id: str, output_dir: Path) -> None:
        """Rewrite the run's manifest status to 'pending' (truthful disk state).

        H-1 belt: after a retry resets a failed run, the stale on-disk manifest
        still reads status='error'; rewriting to 'pending' makes a concurrent
        sweep's error-only guard skip the in-flight dir. Non-fatal on IOError.
        """
        ...

    def delete_run(self, run_id: str, output_dir: Path) -> None:
        """Remove a run's directory from disk.

        Caller is responsible for UUID-validating run_id before calling this.
        rmtree is scoped strictly to output_dir / run_id — never broader.

        Args:
            run_id:     UUID string (already validated by caller).
            output_dir: Root output directory.
        """
        ...
