"""JsonManifestRunHistoryAdapter — filesystem-backed run history.

Infrastructure layer.  Implements RunHistoryPort via JSON manifests
written atomically to {output_dir}/{run_id}/run_manifest.json.

Design:
- D2: schema_version=1; atomic overwrite via _atomic_json_write.
- D3: write-time seq allocation under a process-wide threading.Lock
      (single uvicorn process, BackgroundTasks share the threadpool).
- D4: scan strategy: manifest → full; corrupted → skip; cache → degraded;
      pdf-only → degraded error; non-UUID dirs → skip.
- D5: sweep_failed touches ONLY status="error" entries; completed runs safe.

Architecture:
- Lazy-imports nothing heavy (no paddleocr / pyzbar / etc.).
- _atomic_json_write is imported from application/run_context.py — that
  module owns the canonical atomic-write helper; infrastructure-to-application
  import is acceptable for a pure-stdlib utility function with no IO deps.
  The function itself is listed in application/run_context.py alongside the
  RunContext class.  If it ever needs to move, update this import only.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# UUID4 pattern — 8-4-4-4-12 lowercase hex
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

# Process-wide lock for per-day seq allocation (D3).
# Single uvicorn process; BackgroundTasks run in a threadpool — this is sufficient.
_SEQ_LOCK = threading.Lock()


def _atomic_json_write(path: Path, data: dict[str, Any]) -> None:
    """Delegate to application/run_context._atomic_json_write (stdlib only, no IO deps)."""
    from reconciliation.application.run_context import _atomic_json_write as _write  # noqa: PLC0415

    _write(path, data)


def _is_valid_uuid(value: str) -> bool:
    return bool(_UUID_RE.match(value.lower()))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date_prefix(iso_str: str) -> str:
    """Extract YYYY-MM-DD from an ISO-8601 UTC string."""
    return iso_str[:10]


class JsonManifestRunHistoryAdapter:
    """Filesystem-backed RunHistoryPort implementation.

    All public methods mirror the RunHistoryPort Protocol.  The adapter does NOT
    subclass the Protocol (structural subtyping — duck typing is sufficient and
    avoids circular imports between application and infrastructure layers).
    """

    # ------------------------------------------------------------------
    # Seq allocation (D3)
    # ------------------------------------------------------------------

    def _scan_max_seq(self, date_prefix: str, output_dir: Path) -> int:
        """Return the current max seq for date_prefix (lock-free scan helper).

        Callers MUST hold _SEQ_LOCK across this scan AND the subsequent
        manifest write — otherwise two concurrent same-day completions can
        read the same max and duplicate the seq (TOCTOU; violates RH-004/D3).

        Args:
            date_prefix: "YYYY-MM-DD" string.
            output_dir:  Root output directory.

        Returns:
            Highest seq found for the given date, or 0 if none.
        """
        max_seq = 0
        if output_dir.is_dir():
            for entry in output_dir.iterdir():
                if not entry.is_dir() or not _is_valid_uuid(entry.name):
                    continue
                manifest_path = entry / "run_manifest.json"
                if not manifest_path.exists():
                    continue
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if data.get("started_at", "")[:10] == date_prefix:
                        seq = data.get("seq", 0)
                        if isinstance(seq, int) and seq > max_seq:
                            max_seq = seq
                except Exception:  # noqa: BLE001
                    continue
        return max_seq

    # ------------------------------------------------------------------
    # write_manifest (D2, RH-001-S01)
    # ------------------------------------------------------------------

    def write_manifest(
        self,
        manifest: "RunManifest",  # type: ignore[name-defined]
        output_dir: Path,
        force_seq: int | None = None,
    ) -> None:
        """Persist a completed run manifest with write-time seq allocation.

        Atomic overwrite — NOT write-once (retry semantics, D2/D5).
        Non-fatal: IOError/OSError is caught and logged; run continues.

        Args:
            manifest:   RunManifest to persist (seq field overwritten here).
            output_dir: Root output directory.
            force_seq:  L-3: when set (a same-day retry completion), reuse the
                        run's ORIGINAL per-day seq instead of allocating a new
                        one — the display identity (#N) must be stable per D3.
                        Allocation is skipped entirely in this case.
        """
        from reconciliation.application.run_history import RunManifest  # noqa: PLC0415

        try:
            # D3/RH-004: hold _SEQ_LOCK across scan AND write so the allocated
            # seq is published to disk before the next allocator scans (closes
            # the TOCTOU window; writes are ~ms, contention trivial single-process).
            prefix = _date_prefix(manifest.started_at)
            with _SEQ_LOCK:
                if force_seq is not None:
                    seq = force_seq
                else:
                    seq = self._scan_max_seq(prefix, output_dir) + 1

                # Build updated manifest data (overwrite seq with allocated value)
                data = manifest.model_dump()
                data["seq"] = seq

                manifest_path = output_dir / manifest.run_id / "run_manifest.json"
                _atomic_json_write(manifest_path, data)

            logger.debug(
                "run_history: manifest written run_id=%s seq=%d status=%s",
                manifest.run_id, seq, manifest.status,
            )
        except OSError as exc:
            logger.warning(
                "run_history: manifest write failed for run_id=%s (non-fatal): %s",
                manifest.run_id, exc,
            )

    # ------------------------------------------------------------------
    # write_failure_manifest (RH-001-S03)
    # ------------------------------------------------------------------

    def write_failure_manifest(
        self,
        run_id: str,
        started_at: str,
        error_str: str,
        output_dir: Path,
    ) -> None:
        """Write a failure manifest (status='error', counts 0) to disk.

        Non-fatal: OSError caught and logged.

        Args:
            run_id:     Pipeline run UUID.
            started_at: ISO-8601 UTC start timestamp.
            error_str:  str(exc) from the except branch.
            output_dir: Root output directory.
        """
        from reconciliation.application.run_history import RunManifest  # noqa: PLC0415

        try:
            prefix = _date_prefix(started_at)
            # D3/RH-004: hold _SEQ_LOCK across scan AND write (see write_manifest).
            with _SEQ_LOCK:
                seq = self._scan_max_seq(prefix, output_dir) + 1

                manifest = RunManifest(
                    schema_version=1,
                    run_id=run_id,
                    status="error",
                    started_at=started_at,
                    completed_at=_utc_now_iso(),
                    seq=seq,
                    registro_min=None,
                    registro_max=None,
                    row_count=0,
                    match_count=0,
                    mismatch_count=0,
                    warnings=[],
                    vision_calls_made=0,
                    error=error_str,
                )
                data = manifest.model_dump()
                manifest_path = output_dir / run_id / "run_manifest.json"
                _atomic_json_write(manifest_path, data)

            logger.debug(
                "run_history: failure manifest written run_id=%s seq=%d",
                run_id, seq,
            )
        except OSError as exc:
            logger.warning(
                "run_history: failure manifest write failed for run_id=%s (non-fatal): %s",
                run_id, exc,
            )

    # ------------------------------------------------------------------
    # read_seq / mark_pending (retry support — L-3 seq stability, H-1 belt)
    # ------------------------------------------------------------------

    def read_seq(self, run_id: str, output_dir: Path) -> int | None:
        """Return the per-day seq stored in the run's manifest, or None.

        L-3: a same-day retry must PRESERVE its original display seq (#N). The
        prior manifest survives the retry dir reset (only cache/review/pages are
        deleted), so its seq is read here and threaded back into the completion
        manifest write.
        """
        manifest_path = output_dir / run_id / "run_manifest.json"
        if not manifest_path.exists():
            return None
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            seq = data.get("seq")
            return seq if isinstance(seq, int) else None
        except (OSError, json.JSONDecodeError):
            return None

    def mark_pending(self, run_id: str, output_dir: Path) -> None:
        """Rewrite the run's manifest status to 'pending' (truthful disk state).

        H-1 belt: when a retry resets a failed run, the stale on-disk manifest
        still reads status='error'. A concurrent sweep that consults only the
        disk would delete the in-flight dir. Rewriting status to 'pending' makes
        the disk truthful so the sweep's error-only guard skips it (the suspenders
        side is the registry skip-set passed to sweep_failed). Identity fields
        (run_id, started_at, seq) are preserved; error is cleared. Non-fatal.
        """
        manifest_path = output_dir / run_id / "run_manifest.json"
        if not manifest_path.exists():
            return
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "run_history: mark_pending could not read manifest for %s (non-fatal): %s",
                run_id, exc,
            )
            return
        data["status"] = "pending"
        data["error"] = None
        try:
            _atomic_json_write(manifest_path, data)
        except OSError as exc:
            logger.warning(
                "run_history: mark_pending write failed for %s (non-fatal): %s",
                run_id, exc,
            )

    # ------------------------------------------------------------------
    # scan (D4, RH-002)
    # ------------------------------------------------------------------

    def scan(self, output_dir: Path) -> list[dict[str, Any]]:
        """Scan output_dir and return registry-ready entry dicts (D4, RH-002).

        Strategy per UUID-named subdir:
        1. run_manifest.json valid → full entry, degraded=False.
        2. run_manifest.json corrupted → skip + log.
        3. extraction_cache.json present (no manifest) → degraded "review".
        4. {run_id}.pdf present (no cache, no manifest) → degraded "error".
        5. Empty dir → skip.
        Non-UUID dirs → ignored.

        Per-dir try/except — NEVER crashes startup.

        Returns:
            List of registry-compatible dicts (hydrated=False on all entries).
        """
        results: list[dict[str, Any]] = []

        if not output_dir.is_dir():
            return results

        for entry in output_dir.iterdir():
            if not entry.is_dir():
                continue
            if not _is_valid_uuid(entry.name):
                continue

            run_id = entry.name
            try:
                registry_entry = self._derive_entry(run_id, entry)
                if registry_entry is not None:
                    results.append(registry_entry)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "run_history: scan error on dir %s (skipping): %s", run_id, exc
                )

        return results

    def _derive_entry(self, run_id: str, run_dir: Path) -> dict[str, Any] | None:
        """Derive a single registry entry from a run directory.

        Returns None if the dir is empty or unrecognised.
        """
        manifest_path = run_dir / "run_manifest.json"
        cache_path = run_dir / "extraction_cache.json"
        pdf_path = run_dir / f"{run_id}.pdf"

        # Strategy 1: valid manifest
        if manifest_path.exists():
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                return {
                    # F7: key by the UUID-validated dir name, NOT the manifest's
                    # self-reported run_id (the dir name is the trusted identity;
                    # a corrupted/mismatched run_id field cannot collide the registry).
                    "run_id": run_id,
                    "status": data.get("status", "review"),
                    "started_at": data.get("started_at"),
                    "completed_at": data.get("completed_at"),
                    "seq": data.get("seq"),
                    "registro_min": data.get("registro_min"),
                    "registro_max": data.get("registro_max"),
                    "row_count": data.get("row_count", 0),
                    "match_count": data.get("match_count", 0),
                    "mismatch_count": data.get("mismatch_count", 0),
                    "warnings": data.get("warnings", []),
                    "vision_calls_made": data.get("vision_calls_made", 0),
                    "error": data.get("error"),
                    "degraded": False,
                    "hydrated": False,
                    # Manifest key for downstream (lazy hydration uses ctx from run)
                    "_manifest": True,
                }
            except (json.JSONDecodeError, KeyError) as exc:
                # Strategy 2: corrupted → skip
                logger.warning(
                    "run_history: corrupted manifest in %s (skipping): %s", run_id, exc
                )
                return None

        # Strategy 3: extraction_cache only → legacy "review" degraded
        if cache_path.exists():
            mtime = cache_path.stat().st_mtime
            return _degraded_entry(run_id, status="review", mtime=mtime)

        # Strategy 4: PDF only → legacy "error" degraded (interrupted run)
        if pdf_path.exists():
            mtime = pdf_path.stat().st_mtime
            return _degraded_entry(run_id, status="error", mtime=mtime)

        # Strategy 5: empty dir → skip
        return None

    # ------------------------------------------------------------------
    # sweep_failed (D5, RH-008)
    # ------------------------------------------------------------------

    def sweep_failed(
        self,
        output_dir: Path,
        cutoff: datetime,
        skip_run_ids: set[str] | None = None,
    ) -> list[str]:
        """Delete error-status runs older than cutoff from disk.

        ONLY touches runs with status="error" — completed runs NEVER deleted.
        Identifies eligible dirs by reading their manifests.
        Per-dir try/except — never crashes.

        Args:
            output_dir:   Root output directory.
            cutoff:       Timezone-aware datetime; dirs whose completed_at (or
                          mtime fallback) is before this are deleted.
            skip_run_ids: Run IDs that are currently in-flight (pending/processing)
                          per the in-memory registry. H-1: NEVER sweep these — a
                          retry may have just reset the run while the on-disk
                          manifest still reads stale status=error, and rmtree
                          would delete the PDF out from under the running pipeline.

        Returns:
            List of run_ids whose directories were deleted.
        """
        deleted: list[str] = []
        skip = skip_run_ids or set()

        if not output_dir.is_dir():
            return deleted

        for entry in output_dir.iterdir():
            if not entry.is_dir() or not _is_valid_uuid(entry.name):
                continue
            run_id = entry.name
            if run_id in skip:
                # H-1: in-flight run — never delete (belt: see also pending manifest).
                continue
            try:
                deleted_id = self._try_sweep_dir(run_id, entry, cutoff)
                if deleted_id:
                    deleted.append(deleted_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "run_history: sweep error on dir %s (skipping): %s", run_id, exc
                )

        return deleted

    def _try_sweep_dir(
        self, run_id: str, run_dir: Path, cutoff: datetime
    ) -> str | None:
        """Return run_id and delete dir if eligible; else return None."""
        manifest_path = run_dir / "run_manifest.json"

        if not manifest_path.exists():
            return None

        data = json.loads(manifest_path.read_text(encoding="utf-8"))

        # Only sweep error-status runs
        if data.get("status") != "error":
            return None

        # Determine the age timestamp: prefer completed_at, fallback mtime.
        # F11/deliberate deviation: spec RH-008 phrases the 48h window against
        # started_at; we use completed_at (run end) which is strictly >= started_at,
        # so a failed run is swept slightly LATER, never earlier — conservative,
        # never deletes a run sooner than the spec's floor.
        ts_str: str | None = data.get("completed_at")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except ValueError:
                ts = None
        else:
            ts = None

        if ts is None:
            # mtime fallback
            mtime = manifest_path.stat().st_mtime
            ts = datetime.fromtimestamp(mtime, tz=timezone.utc)

        if ts < cutoff:
            shutil.rmtree(run_dir, ignore_errors=True)
            logger.info("run_history: swept failed run %s (older than %s)", run_id, cutoff)
            return run_id

        return None

    # ------------------------------------------------------------------
    # delete_run (D5, RH-009)
    # ------------------------------------------------------------------

    def delete_run(self, run_id: str, output_dir: Path) -> None:
        """Remove the run directory for run_id.

        Caller MUST UUID-validate run_id before calling this method.
        rmtree is scoped strictly to output_dir / run_id.

        Args:
            run_id:     Validated UUID string.
            output_dir: Root output directory (never deleted itself).
        """
        target = output_dir / run_id
        if target.exists():
            shutil.rmtree(target)
            logger.info("run_history: deleted run dir %s", run_id)
        else:
            logger.warning("run_history: delete_run called on missing dir %s", run_id)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _degraded_entry(
    run_id: str,
    status: str,
    mtime: float | None = None,
) -> dict[str, Any]:
    """Build a degraded registry entry (no manifest; derive from disk)."""
    started_at = None
    if mtime is not None:
        started_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    return {
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "completed_at": None,
        "seq": None,
        "registro_min": None,
        "registro_max": None,
        "row_count": 0,
        "match_count": 0,
        "mismatch_count": 0,
        "warnings": [],
        "vision_calls_made": 0,
        "error": None,
        "degraded": True,
        "hydrated": False,
    }
