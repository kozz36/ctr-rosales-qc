"""FastAPI route definitions for the reconciliation API.

All endpoints are local-first (localhost binding; no auth by design for MVP).
Security enforced at the upload boundary:
  - Only application/pdf content-type accepted.
  - File size capped at MAX_UPLOAD_BYTES (100 MB default; configurable via env).
  - Client filename NEVER used for disk storage (run_id is the on-disk name).
  - No path traversal: PDF is stored under the run's output directory only.

Background task strategy:
  POST /runs starts the pipeline in a FastAPI BackgroundTask.  The run_id is
  returned immediately with status="processing".  GET /runs/{run_id} polls
  the in-memory run registry for completion.  This is the simplest correct
  approach for a local MVP — no external queue or worker process required.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from reconciliation.domain.models import ReconciliationRow
from reconciliation.infrastructure.api.schemas import (
    AuditEventResponse,
    AuditTrailResponse,
    CapabilitiesResponse,
    DiscardedBatchResponse,
    DiscardedBatchStatusResponse,
    DiscardedPageResponse,
    ErroredGuiaResponse,
    ErrorResponse,  # noqa: F401 — imported for openapi docs
    ExportRequest,
    GuiaContributionResponse,
    GuiaLineEditRequest,
    ProgressResponse,
    ReassignRequest,
    ReassignResponse,
    RecoverPageResponse,
    ReconciliationRowResponse,
    ReconciliationTableResponse,
    ReprocessBatchResponse,
    ReprocessBatchStatusResponse,
    ReprocessGuiaResponse,
    RetryBatchResponse,
    RetryGuiaResponse,
    RowEditRequest,
    RowEditResponse,
    RunCreateResponse,
    RunRetryResponse,
    RunStatusResponse,
    RunSummaryResponse,
    UnresolvedGuiaResponse,
    VisionKeyDeleteResponse,
    VisionKeySaveRequest,
    VisionKeySaveResponse,
    _row_id,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Upload validation constants (security-ops: no client filename, size cap)
# ---------------------------------------------------------------------------

MAX_UPLOAD_BYTES: int = int(os.environ.get("MAX_UPLOAD_BYTES", str(100 * 1024 * 1024)))  # 100 MB
ALLOWED_CONTENT_TYPES = frozenset({"application/pdf"})

router = APIRouter()


# ---------------------------------------------------------------------------
# Dependency: get run registry / config from app state
# ---------------------------------------------------------------------------


def _get_registry(request: Request) -> dict[str, Any]:
    """Extract the run registry dict from FastAPI app state."""
    return request.app.state.run_registry  # type: ignore[no-any-return]


def _get_config(request: Request) -> Any:
    """Extract the AppConfig from FastAPI app state."""
    return request.app.state.config  # noqa: ANN401


def _get_run_history(request: Request) -> Any:
    """Extract the RunHistoryPort adapter from FastAPI app state (D1).

    The single adapter instance is constructed once in the lifespan startup
    (main.py) and stored on app.state.run_history. Resolving it here keeps ONE
    instance shared across the lifespan scan/sweep and the request handlers,
    rather than constructing a fresh JsonManifestRunHistoryAdapter inline.
    """
    return request.app.state.run_history  # noqa: ANN401


def _get_key_store(request: Request) -> Any:
    """Extract the VisionKeyStorePort adapter from FastAPI app state (D2).

    Constructed once in lifespan (main.py); stored on app.state.key_store.
    Mirrors the _get_run_history pattern.
    """
    return request.app.state.key_store  # noqa: ANN401


def _get_key_probe(request: Request) -> Any:
    """Extract the VisionKeyProbePort adapter from FastAPI app state (D3).

    Constructed once in lifespan (main.py); stored on app.state.key_probe.
    """
    return request.app.state.key_probe  # noqa: ANN401


RunRegistry = Annotated[dict[str, Any], Depends(_get_registry)]
AppConfigDep = Annotated[Any, Depends(_get_config)]
RunHistoryDep = Annotated[Any, Depends(_get_run_history)]
KeyStoreDep = Annotated[Any, Depends(_get_key_store)]
KeyProbeDep = Annotated[Any, Depends(_get_key_probe)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_response(row: ReconciliationRow) -> ReconciliationRowResponse:
    """Convert a domain ReconciliationRow to the API DTO (rev-2: includes guias[]).

    Rev-3 D5 (REC-C07): maps year_inferred + any_year_inferred provenance fields.
    """
    guia_responses = [
        GuiaContributionResponse(
            guia_id=g.guia_id,
            source_pages=g.source_pages,
            cantidad=g.cantidad,
            unidad=g.unidad,
            confidence=g.confidence,
            identity_source=g.identity_source,
            year_inferred=g.year_inferred,
            # R9.6 (FDR-008): fecha-divergence fields.
            fecha=g.fecha,
            fecha_divergence=g.fecha_divergence,
            divergence_reason=g.divergence_reason,
            # R9b: delivery-floor side-channel.
            delivery_floor_applied=g.delivery_floor_applied,
            # Reception-ceiling side-channel.
            reception_ceiling_applied=g.reception_ceiling_applied,
            # Crossed-bounds anomaly side-channel.
            delivery_after_protocolo=g.delivery_after_protocolo,
        )
        for g in row.guias
    ]
    return ReconciliationRowResponse(
        row_id=_row_id(row.registro, row.fecha, row.material_canonical, row.unidad),
        registro=row.registro,
        fecha=row.fecha,
        material_canonical=row.material_canonical,
        unidad=row.unidad,
        declared_qty=row.declared_qty,
        summed_qty=row.summed_qty,
        delta=row.delta,
        status=row.status,
        source_pages=row.source_pages,
        min_confidence=row.min_confidence,
        requires_review=row.requires_review,
        guias=guia_responses,
        any_year_inferred=row.any_year_inferred,
        match_method=row.match_method,  # R8.12 (MAT-008)
        has_fecha_divergence=row.has_fecha_divergence,  # R9.6 (FDR-008)
        has_delivery_floor=row.has_delivery_floor,  # R9b
        has_reception_ceiling=row.has_reception_ceiling,  # reception-ceiling
        has_delivery_after_protocolo=row.has_delivery_after_protocolo,  # crossed-bounds
    )


def _require_run(registry: dict[str, Any], run_id: str) -> Any:
    """Look up a run entry or raise 404."""
    entry = registry.get(run_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    return entry


def _require_review_service(entry: Any, run_id: str) -> Any:
    """Return the ReviewService from a run entry, or raise 409 if not ready.

    Does NOT trigger lazy hydration — use _get_hydrated_review_service for
    endpoints that need the ReviewService and may receive a cold-started entry.
    """
    review_service = entry.get("review_service")
    if review_service is None:
        status = entry.get("status", "unknown")
        if status == "error":
            # entry["error"] may exist as None — coerce to a readable string.
            err = entry.get("error") or "unknown"
            raise HTTPException(
                status_code=422,
                detail=f"Run '{run_id}' ended in error: {err}",
            )
        raise HTTPException(
            status_code=409,
            detail=f"Run '{run_id}' is not yet in review state (current: {status}).",
        )
    return review_service


# Module-level lock serialising the check-build-cache of _ensure_hydrated (M-2).
# Sync endpoints run in Starlette's threadpool, so concurrent first-access
# requests for the same cold entry could double-build the services. A thread
# lock (not asyncio) is correct here. Local single-user → contention is trivial.
# Tradeoff (M-2, accepted): the few async endpoints (reprocess_guia/registro)
# call _get_hydrated_review_service synchronously, so a cold first-access build
# briefly blocks the event loop; acceptable for the local single-user MVP — no
# offload to a threadpool executor is added.
_HYDRATION_LOCK = __import__("threading").Lock()

# Module-level lock guarding the retry check-and-set (M-1 double-fire TOCTOU).
# Two concurrent POST /retry both pass the status==error check then both flip
# to pending and both add a background task. Mirrors the batch check-and-set
# discipline. Sync endpoint → threadpool → thread lock is correct.
_RETRY_LOCK = __import__("threading").Lock()


def _build_cold_ctx(entry: dict[str, Any], run_id: str, config: Any) -> Any:
    """Construct a RunContext attached to an EXISTING cold-scanned run dir.

    C1 / D4: scan entries carry no ctx (only manifest fields). The run dir at
    ``config.output_dir / run_id`` already exists on disk; RunContext attaches
    to it (mkdir exist_ok=True — never a NEW dir). The PDF reference points at
    the stored ``{run_id}.pdf`` convention (or entry['pdf_path'] if present).

    Returns the RunContext, or None when the run directory does not exist
    (genuinely-deleted dir → caller raises the truthful 409).
    """
    from pathlib import Path as _Path  # noqa: PLC0415

    from reconciliation.application.run_context import RunContext  # noqa: PLC0415

    output_base: _Path = config.output_dir
    run_dir = output_base / run_id
    if not run_dir.is_dir():
        return None

    pdf_path_str: str | None = entry.get("pdf_path")
    pdf_path = _Path(pdf_path_str) if pdf_path_str else run_dir / f"{run_id}.pdf"

    # run_id + output_base → run_dir is the existing dir (exist_ok=True attach).
    return RunContext(pdf_path=pdf_path, output_base=output_base, run_id=run_id)


def _ensure_hydrated(entry: dict[str, Any], run_id: str, config: Any) -> None:
    """Lazily build ReviewService + ReprocessService for a cold-started run entry.

    Called on the first review-service request for an entry that was scanned at
    startup (hydrated=False) but has never served a live request.  Builds the
    services from the on-disk extraction cache and caches them back into the
    entry dict so subsequent requests skip re-building (Virtual Proxy pattern).

    Design: D4 (lazy hydration on first review-endpoint access).
    Spec: RH-005, RH-011-S01.

    Args:
        entry:   Registry entry dict (mutated in place on success).
        run_id:  Run UUID string (for error messages only).
        config:  AppConfig used to build the reprocess service.

    Raises:
        HTTPException 409: if the entry is not in 'review' status or lacks
                           a RunContext (ctx) needed for hydration.
        HTTPException 500: if build_review_service fails (disk read error).
    """
    if entry.get("hydrated") is not False:
        return  # Already hydrated (True) or a fresh in-process run (None).

    status = entry.get("status", "unknown")
    if status != "review":
        # Error or pending entries are never hydrated here — callers handle those.
        return

    # M-2: serialise the check-build-cache so two concurrent first-access threads
    # (sync endpoints run in the threadpool) cannot double-build the services.
    with _HYDRATION_LOCK:
        # Re-check under the lock: a racing thread may have hydrated already.
        if entry.get("hydrated") is not False:
            return

        ctx = entry.get("ctx")
        if ctx is None:
            # C1: scanned (cold) entries NEVER carry a ctx — _derive_entry only
            # reads the manifest. D4 mandates lazy hydration CONSTRUCTS the
            # RunContext on first access. Attach to the EXISTING run dir (run_id +
            # output_base); RunContext.__init__ uses mkdir(exist_ok=True) so this
            # never creates a new tree. A genuinely-missing extraction cache later
            # surfaces as a 500 via build_review_service; only a missing dir is a
            # true 409.
            ctx = _build_cold_ctx(entry, run_id, config)
            if ctx is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Run '{run_id}' cannot be hydrated: run directory not found. "
                        "The run directory may have been deleted."
                    ),
                )
            entry["ctx"] = ctx

        from reconciliation.infrastructure.container import (  # noqa: PLC0415
            build_review_service,
            build_reprocess_service,
        )

        try:
            review_service = build_review_service(ctx)
        except Exception as exc:  # noqa: BLE001
            # Honest 500; entry untouched (hydrated stays False) → retryable.
            raise HTTPException(
                status_code=500,
                detail=f"Run '{run_id}' cold-load hydration failed: {exc}",
            ) from exc

        try:
            reprocess_service = build_reprocess_service(
                config=config,
                ctx=ctx,
                review_service=review_service,
            )
        except Exception as exc:  # noqa: BLE001
            # M-2: mirror build_review_service — honest 500, entry untouched
            # (hydrated stays False, review_service NOT cached) → retryable.
            raise HTTPException(
                status_code=500,
                detail=f"Run '{run_id}' cold-load reprocess-service build failed: {exc}",
            ) from exc

        entry["review_service"] = review_service
        entry["reprocess_service"] = reprocess_service
        entry["hydrated"] = True
        logger.info("run_history: lazily hydrated run %s from disk cache", run_id)


def _get_hydrated_ctx(entry: dict[str, Any], run_id: str, config: Any) -> Any:
    """Ensure a RunContext exists for an entry then return it (C1 blast radius).

    Page-viewer endpoints (thumbnail/image) need only the RunContext, not the
    ReviewService. For a cold-scanned 'review' entry (hydrated=False, ctx absent)
    this triggers full hydration so the ctx is constructed and cached. For
    fresh in-process runs the ctx is already present. Returns None only when the
    run has no ctx and cannot be hydrated (pending/error/processing) — callers
    map that to the existing 409.
    """
    if entry.get("status") == "review" and entry.get("hydrated") is False:
        _ensure_hydrated(entry, run_id, config)
    return entry.get("ctx")


def _get_hydrated_review_service(entry: dict[str, Any], run_id: str, config: Any) -> Any:
    """Ensure the entry is hydrated then return the ReviewService.

    Replaces the pair (_require_run + _require_review_service) on all
    review-service endpoints so that cold-started entries (hydrated=False)
    are transparently built on the first access instead of returning 409.

    Args:
        entry:   Registry entry returned by _require_run.
        run_id:  Run UUID (for error messages).
        config:  AppConfig from app.state.

    Returns:
        The ReviewService for the run.

    Raises:
        HTTPException 409/422/500 via _ensure_hydrated or _require_review_service.
    """
    _ensure_hydrated(entry, run_id, config)
    return _require_review_service(entry, run_id)


def _require_reprocess_service(entry: Any, run_id: str) -> Any:
    """Return the ReprocessService from a run entry, or raise 503 if unavailable.

    PR#3 (T5): service is now built when vision OR SUNAT is enabled.
    This guard only raises 503 when BOTH are disabled (service is None).
    For REINTENTAR-specific SUNAT check, use _require_sunat_on_service.
    """
    reprocess_service = entry.get("reprocess_service")
    if reprocess_service is None:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Recovery endpoints not available for run '{run_id}': "
                "both vision and SUNAT fetch are disabled. "
                "Enable vision or SUNAT in config to use REINTENTAR / Reprocesar con IA."
            ),
        )
    return reprocess_service


def _require_sunat_on_service(reprocess_service: Any, run_id: str) -> None:
    """Raise 503 if the ReprocessService has no SUNAT adapter (REINTENTAR-specific guard).

    REINTENTAR (apply_retry) requires a SUNAT adapter.  Reprocesar con IA (apply_reprocess)
    does not.  After PR#3 T5, the service may be built for vision-only runs where _sunat
    is None — this guard preserves the REINTENTAR 503 behaviour in that scenario.
    """
    if getattr(reprocess_service, "_sunat", None) is None:
        raise HTTPException(
            status_code=503,
            detail=(
                f"REINTENTAR not available for run '{run_id}': "
                "SUNAT fetch is disabled (sunat.enabled=False). "
                "Enable SUNAT in config to use REINTENTAR."
            ),
        )


def _require_vision_on_service(reprocess_service: Any, run_id: str) -> None:
    """Raise 503 vision_disabled if the ReprocessService has no usable vision port.

    Reprocesar con IA (apply_reprocess) requires a real vision adapter.  When
    vision.enabled=False the container injects a NullVisionAdapter, which returns
    [] from read_material_table — without this guard apply_reprocess would resolve
    to 200 recovered=False reason="vision_empty", masking the disabled state.

    REV-R16-S03 / REV-R17-S03: vision DISABLED → 503 "vision_disabled".  This is
    distinct from vision ENABLED but returning no lines (REV-R16-S02), which stays
    200 "vision_empty" — a real empty read, not a disabled service.  Mirrors the
    _require_sunat_on_service pattern used by REINTENTAR.
    """
    from reconciliation.adapters.vision.null_vision import (  # noqa: PLC0415
        NullVisionAdapter,
    )

    vision = getattr(reprocess_service, "_vision", None)
    if vision is None or isinstance(vision, NullVisionAdapter):
        raise HTTPException(
            status_code=503,
            detail=(
                f"Reprocesar con IA not available for run '{run_id}': "
                "vision is disabled (vision.enabled=False). reason=vision_disabled. "
                "Enable vision in config to use Reprocesar con IA."
            ),
        )


# ---------------------------------------------------------------------------
# Run history helpers
# ---------------------------------------------------------------------------


def _build_run_manifest(
    result: Any,
    entry: dict[str, Any],
    started_at: str,
    run_id: str,
) -> "RunManifest":  # type: ignore[name-defined]
    """Build a RunManifest from a successful PipelineResult.

    Derives registro_min/max by int-sorting the registro numbers from
    result.declared; falls back to lexicographic sort when not all numeric.

    Args:
        result:     PipelineResult returned by pipeline.run().
        entry:      Current registry entry (for vision_calls_made, warnings).
        started_at: ISO-8601 UTC start timestamp.
        run_id:     UUID string of the run.

    Returns:
        RunManifest ready for write_manifest().
    """
    import datetime  # noqa: PLC0415

    from reconciliation.application.run_history import RunManifest  # noqa: PLC0415

    declared_items = getattr(result, "declared", []) or []
    # A1: the domain model field is `numero` (Registro.numero), NOT `registro`.
    # Direct attribute access — the outer non-fatal try/except in the wrapper
    # already guards prod; a defensive getattr default here silently masked the
    # bug that made registro_min/max None in every manifest.
    registros = [item.numero for item in declared_items]
    registros = [r for r in registros if r is not None]

    registro_min: str | None = None
    registro_max: str | None = None
    if registros:
        try:
            sorted_nums = sorted(registros, key=lambda r: int(r))
        except (ValueError, TypeError):
            sorted_nums = sorted(registros)
        registro_min = sorted_nums[0]
        registro_max = sorted_nums[-1]

    rows = getattr(result, "rows", []) or []
    warnings = getattr(result, "warnings", []) or []
    vision_calls = entry.get("vision_calls_made", 0)

    match_count = sum(
        1 for r in rows if getattr(r, "status", None) == "MATCH"
    )
    mismatch_count = sum(
        1 for r in rows if getattr(r, "status", None) == "MISMATCH"
    )

    return RunManifest(
        schema_version=1,
        run_id=run_id,
        status="review",
        started_at=started_at,
        completed_at=datetime.datetime.now(datetime.UTC).isoformat(),
        seq=1,  # placeholder — write_manifest overwrites with allocated seq
        registro_min=registro_min,
        registro_max=registro_max,
        row_count=len(rows),
        match_count=match_count,
        mismatch_count=mismatch_count,
        warnings=list(warnings),
        vision_calls_made=vision_calls,
        error=None,
    )


# ---------------------------------------------------------------------------
# Background pipeline runner
# ---------------------------------------------------------------------------


def _run_pipeline_background(
    run_id: str,
    pdf_path: Path,
    config: Any,
    registry: dict[str, Any],
    run_history: Any,
) -> None:
    """Execute the pipeline synchronously inside a background task.

    Updates the registry entry with status transitions:
      processing → review  (success)
      processing → error   (exception)

    Progress wiring:
      - Sets registry[run_id]["started_at"] (ISO-8601 UTC) at run start.
      - Builds a progress_cb closure that writes registry[run_id]["progress"]
        on each ProgressEvent so GET /runs/{id} can return live progress.
      - Passes progress_cb to RunContext so the pipeline can emit events.
    """
    import datetime  # noqa: PLC0415

    from reconciliation.application.run_context import ProgressEvent  # noqa: PLC0415
    from reconciliation.infrastructure.container import (  # noqa: PLC0415
        build_pipeline,
        build_reprocess_service,
        build_review_service,
    )

    def _progress_cb(event: ProgressEvent) -> None:
        registry[run_id]["progress"] = {
            "stage_label": event.stage_label,
            "stage_index": event.stage_index,
            "stage_total": event.stage_total,
            "item_done": event.item_done,
            "item_total": event.item_total,
        }

    try:
        started_at = datetime.datetime.now(datetime.UTC).isoformat()
        registry[run_id]["status"] = "processing"
        registry[run_id]["started_at"] = started_at
        pipeline, ctx, page_to_registro = build_pipeline(
            pdf_path, config, run_id=run_id, progress_cb=_progress_cb
        )
        result = pipeline.run(ctx)
        review_service = build_review_service(ctx)
        reprocess_service = build_reprocess_service(
            config=config, ctx=ctx, review_service=review_service
        )

        registry[run_id].update(
            {
                "status": "review",
                "ctx": ctx,
                "result": result,
                "review_service": review_service,
                "reprocess_service": reprocess_service,
                "page_to_registro": page_to_registro,
                "vision_calls_made": result.vision_calls_made,
                "warnings": result.warnings,
                "errored_guias": result.errored_guias,
                # L-1: a fresh in-process run is fully built — mark hydrated so the
                # lazy-hydration guard never re-builds services for it.
                "hydrated": True,
            }
        )
        logger.info("pipeline run %s completed; %d rows", run_id, len(result.rows))

        # --- Run history manifest (D1: non-fatal side-channel in routes.py) ---
        # Uses the single app.state.run_history adapter passed in from create_run.
        try:
            manifest = _build_run_manifest(result, registry[run_id], started_at, run_id)
            # L-3: a same-day retry carries its original seq forward (stable #N).
            preserved_seq = registry[run_id].get("preserved_seq")
            allocated_seq = run_history.write_manifest(
                manifest, config.output_dir, force_seq=preserved_seq
            )
            # A2: merge the manifest-derived display fields into the in-memory
            # registry so a same-session GET /runs shows #N and the registro
            # range without waiting for a restart-time disk scan. The allocated
            # seq comes from write_manifest (None only on a non-fatal write fail).
            if allocated_seq is not None:
                registry[run_id]["seq"] = allocated_seq
            registry[run_id].update(
                {
                    "registro_min": manifest.registro_min,
                    "registro_max": manifest.registro_max,
                    "completed_at": manifest.completed_at,
                }
            )
        except Exception as _mex:  # noqa: BLE001
            logger.warning("run_history: manifest write error for %s (non-fatal): %s", run_id, _mex)

    except Exception as exc:  # noqa: BLE001
        logger.exception("pipeline run %s failed", run_id)
        registry[run_id].update({"status": "error", "error": str(exc)})

        # --- Failure manifest (D1: non-fatal; always try after registry update) ---
        # Uses the single app.state.run_history adapter passed in from create_run.
        try:
            # F-4: a same-day retry that fails AGAIN keeps its #N — thread the
            # preserved seq through the failure path exactly as the success path
            # does (symmetry; None on a first run or a cross-day retry).
            preserved_seq = registry[run_id].get("preserved_seq")
            run_history.write_failure_manifest(
                run_id=run_id,
                started_at=started_at,
                error_str=str(exc),
                output_dir=config.output_dir,
                force_seq=preserved_seq,
            )
        except Exception as _mex:  # noqa: BLE001
            logger.warning(
                "run_history: failure manifest write error for %s (non-fatal): %s", run_id, _mex
            )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/runs",
    response_model=RunCreateResponse,
    status_code=202,
    summary="Upload a PDF and start a reconciliation run.",
)
async def create_run(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    registry: RunRegistry,
    config: AppConfigDep,
    run_history: RunHistoryDep,
) -> RunCreateResponse:
    """Accept a PDF upload and start the reconciliation pipeline asynchronously.

    Security validation (security-ops):
    - Rejects non-PDF content-type (content-type header check).
    - Rejects files exceeding MAX_UPLOAD_BYTES (100 MB default).
    - Stores PDF using run_id as filename — client filename is never used on
      disk to prevent path traversal (CWE-22).
    """
    # --- Security: content-type validation ---
    ct = (file.content_type or "").lower().split(";")[0].strip()
    if ct not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Only PDF uploads are accepted. Got content-type: {file.content_type!r}.",
        )

    # --- Security: size cap (read in chunks, abort on oversize) ---
    import uuid  # noqa: PLC0415

    run_id = str(uuid.uuid4())
    output_dir: Path = config.output_dir / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # Use run_id as the filename — never the client-supplied filename
    pdf_path = output_dir / f"{run_id}.pdf"
    chunk_size = 1024 * 256  # 256 KB chunks
    total_bytes = 0

    try:
        with pdf_path.open("wb") as fh:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_BYTES:
                    fh.close()
                    pdf_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB size limit."
                        ),
                    )
                fh.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        pdf_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Failed to store uploaded file.") from exc

    # --- Register the run (pending → processing transition in background) ---
    registry[run_id] = {
        # Judge-A L3: list_runs reads e["run_id"]; a same-session entry created
        # here MUST carry the run_id key or GET /runs raises a latent KeyError.
        "run_id": run_id,
        "status": "pending",
        "pdf_path": str(pdf_path),
        "review_service": None,
        "reprocess_service": None,
        "ctx": None,
        "result": None,
        "vision_calls_made": 0,
        "warnings": [],
        "errored_guias": [],
        "error": None,
    }

    background_tasks.add_task(
        _run_pipeline_background, run_id, pdf_path, config, registry, run_history
    )

    logger.info("accepted run %s (%d bytes)", run_id, total_bytes)
    return RunCreateResponse(run_id=run_id, status="pending")


@router.get(
    "/runs",
    response_model=list[RunSummaryResponse],
    summary="List all known runs, sorted newest-first (RH-003).",
)
def list_runs(
    registry: RunRegistry,
    config: AppConfigDep,
    run_history: RunHistoryDep,
) -> list[RunSummaryResponse]:
    """Return summary of all known runs, sorted by started_at descending.

    Runs without a started_at (legacy degraded entries) appear last.
    Triggers a lazy 48 h sweep of error-status runs before assembling the list.
    Spec: RH-003, D5.
    """
    import datetime  # noqa: PLC0415

    # Lazy 48 h sweep: delete old error-status runs from disk + remove from registry.
    # D1: reuse the single app.state.run_history adapter, not a fresh inline one.
    try:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=48)
        # H-1: never sweep a run that the registry shows in-flight (pending/
        # processing) — a retry may have reset it while the on-disk manifest
        # still reads stale status=error. Skipping protects the live pipeline's
        # PDF from rmtree. (Belt: retry also rewrites the manifest to pending.)
        in_flight = {
            rid
            for rid, e in registry.items()
            if e.get("status") in {"pending", "processing"}
        }
        deleted_ids = run_history.sweep_failed(config.output_dir, cutoff, skip_run_ids=in_flight)
        for rid in deleted_ids:
            registry.pop(rid, None)
            logger.info("run_history: swept run %s from registry", rid)
    except Exception as _sweep_exc:  # noqa: BLE001
        logger.warning("run_history: sweep error (non-fatal): %s", _sweep_exc)

    # Desc by started_at, legacy entries (no started_at) last (RH-003-S03).
    entries = list(registry.values())
    has_ts = [e for e in entries if e.get("started_at")]
    no_ts = [e for e in entries if not e.get("started_at")]
    has_ts_desc = sorted(has_ts, key=lambda e: e.get("started_at", ""), reverse=True)
    ordered = has_ts_desc + no_ts

    return [
        RunSummaryResponse(
            run_id=e["run_id"],
            status=e.get("status", "review"),
            started_at=e.get("started_at"),
            completed_at=e.get("completed_at"),
            seq=e.get("seq"),
            registro_min=e.get("registro_min"),
            registro_max=e.get("registro_max"),
            row_count=e.get("row_count", 0),
            match_count=e.get("match_count", 0),
            mismatch_count=e.get("mismatch_count", 0),
            warnings_count=len(e.get("warnings", [])),
            vision_calls_made=e.get("vision_calls_made", 0),
            degraded=e.get("degraded", False),
            error=e.get("error"),
        )
        for e in ordered
    ]


@router.delete(
    "/runs/{run_id}",
    status_code=204,
    summary="Delete a completed or failed run and its on-disk directory (RH-009).",
)
def delete_run(
    run_id: str,
    registry: RunRegistry,
    config: AppConfigDep,
    run_history: RunHistoryDep,
) -> None:
    """Remove a run's directory from disk and purge it from the in-memory registry.

    Security:
    - UUID-validates run_id before any filesystem operation (CWE-22, D5).
    - rmtree is scoped strictly to config.output_dir / run_id (own-dir-only invariant).

    Spec: RH-009, D5.
    Returns 204 on success (no body).

    Errors:
        400 — run_id is not a valid UUID4 string.
        404 — run_id not found in registry.
        409 — run is pending or processing (cannot delete an in-flight run).
    """
    from reconciliation.infrastructure.run_history_store import _is_valid_uuid  # noqa: PLC0415

    if not _is_valid_uuid(run_id):
        raise HTTPException(
            status_code=400,
            detail=f"run_id must be a valid UUID4; got {run_id!r}.",
        )

    entry = registry.get(run_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    status = entry.get("status", "unknown")
    if status in {"pending", "processing"}:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Run '{run_id}' is currently {status!r}. "
                "Cannot delete an in-flight run."
            ),
        )

    # rmtree own-dir-only (never uses client input directly as path).
    run_history.delete_run(run_id, config.output_dir)
    registry.pop(run_id, None)
    logger.info("run_history: deleted run %s (status was %s)", run_id, status)


@router.post(
    "/runs/{run_id}/retry",
    response_model=RunRetryResponse,
    status_code=202,
    summary="Retry a failed run with the same run_id (RH-007-S02, D5).",
)
def retry_run(
    run_id: str,
    background_tasks: BackgroundTasks,
    registry: RunRegistry,
    config: AppConfigDep,
    run_history: RunHistoryDep,
) -> RunRetryResponse:
    """Reset a failed run's working directory and re-fire the pipeline.

    Same run_id semantics: the PDF and sunat/ cache are preserved; only
    extraction_cache.json, review.json, and pages/ are deleted before re-firing.
    The pipeline background task will write a new manifest on completion.

    Spec: RH-007-S02, D5.
    Returns 202 immediately (background task started).

    Errors:
        400 — run_id is not a valid UUID4.
        404 — run_id not found in registry.
        409 — run is not in error status (retry only valid on failed runs).
    """
    import shutil  # noqa: PLC0415

    from reconciliation.infrastructure.run_history_store import _is_valid_uuid  # noqa: PLC0415

    if not _is_valid_uuid(run_id):
        raise HTTPException(
            status_code=400,
            detail=f"run_id must be a valid UUID4; got {run_id!r}.",
        )

    if registry.get(run_id) is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    # M-1: serialise the whole check-and-set so two concurrent POST /retry (sync
    # endpoints → threadpool) cannot both pass the guards and both fire. Mirrors
    # the batch check-and-set discipline. The status flip happens inside the lock.
    with _RETRY_LOCK:
        # Re-fetch under the lock: the loser of a concurrent double-fire must see
        # the winner's status flip. Reading the pre-lock entry reference would let
        # both threads observe the original 'error' status (TOCTOU).
        entry = registry.get(run_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
        status = entry.get("status", "unknown")
        if status != "error":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Run '{run_id}' cannot be retried: status is {status!r}. "
                    "Retry is only valid for failed (error) runs."
                ),
            )

        # RH-007-S04: single-pipeline rule. Reject if ANY OTHER run is currently
        # pending/processing — never silently drop or queue concurrently.
        busy = next(
            (
                rid
                for rid, e in registry.items()
                if rid != run_id and e.get("status") in {"pending", "processing"}
            ),
            None,
        )
        if busy is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot retry run '{run_id}': another run ({busy}) is currently "
                    "in progress. Wait for it to finish (single-pipeline rule)."
                ),
            )

        run_dir = config.output_dir / run_id

        # L-3/F-3: capture the original per-day seq BEFORE the reset so the
        # SAME-DAY retry keeps its #N display identity (the prior manifest
        # survives the reset; only cache/review/pages are deleted). seq is a
        # PER-DAY identity: a run that failed on day X and is retried on day Y
        # belongs to day Y's sequence — preserving X's seq would duplicate day
        # Y's existing #N. So only preserve when the original started_at day ==
        # today (UTC); otherwise let write_manifest allocate a fresh seq.
        import datetime  # noqa: PLC0415

        preserved_seq = None
        _orig_started = run_history.read_started_at(run_id, config.output_dir)
        _today = datetime.datetime.now(datetime.UTC).date().isoformat()
        if _orig_started is not None and _orig_started[:10] == _today:
            preserved_seq = run_history.read_seq(run_id, config.output_dir)

        # Reset the run dir: delete cache/review/pages; keep pdf + sunat/.
        for name in ("extraction_cache.json", "review.json"):
            target = run_dir / name
            if target.exists():
                target.unlink()

        pages_dir = run_dir / "pages"
        if pages_dir.exists():
            shutil.rmtree(pages_dir, ignore_errors=True)

        # H-1 belt: make the on-disk manifest truthful (status='pending') so a
        # concurrent sweep's error-only guard skips the in-flight dir even if the
        # registry skip-set were somehow stale. Non-fatal side-channel.
        try:
            run_history.mark_pending(run_id, config.output_dir)
        except Exception as _mex:  # noqa: BLE001
            logger.warning(
                "run_history: mark_pending failed for %s (non-fatal): %s", run_id, _mex
            )

        # Resolve the PDF path — prefer the stored pdf_path, fall back to convention.
        pdf_path_str: str | None = entry.get("pdf_path")
        if pdf_path_str:
            from pathlib import Path as _Path  # noqa: PLC0415
            pdf_path = _Path(pdf_path_str)
        else:
            pdf_path = run_dir / f"{run_id}.pdf"

        # Reset registry to in-flight. L-1: drop stale degraded/completed_at/
        # progress keys carried from the prior failed entry so the re-run starts
        # clean. L-2: status is 'processing' to match the response value below.
        # L-3: thread the preserved seq through so the completion manifest keeps it.
        new_entry = {
            **entry,
            "status": "processing",
            "review_service": None,
            "reprocess_service": None,
            "ctx": None,
            "result": None,
            "vision_calls_made": 0,
            "warnings": [],
            "errored_guias": [],
            "error": None,
            "hydrated": False,
            "preserved_seq": preserved_seq,
        }
        for stale in ("degraded", "completed_at", "progress"):
            new_entry.pop(stale, None)
        registry[run_id] = new_entry

    background_tasks.add_task(
        _run_pipeline_background, run_id, pdf_path, config, registry, run_history
    )

    logger.info("run_history: retry fired for run %s", run_id)
    # L-2: response matches the registry status just set (truthful).
    return RunRetryResponse(run_id=run_id, status="processing")


@router.get(
    "/runs/{run_id}",
    response_model=RunStatusResponse,
    summary="Poll a run's status.",
)
def get_run_status(run_id: str, registry: RunRegistry) -> RunStatusResponse:
    """Return the current status of a reconciliation run."""
    entry = _require_run(registry, run_id)

    # Map raw progress dict (written by _progress_cb) to ProgressResponse.
    progress_resp: ProgressResponse | None = None
    raw_progress = entry.get("progress")
    if raw_progress is not None:
        progress_resp = ProgressResponse(
            stage_label=raw_progress["stage_label"],
            stage_index=raw_progress["stage_index"],
            stage_total=raw_progress["stage_total"],
            item_done=raw_progress["item_done"],
            item_total=raw_progress["item_total"],
        )

    # REC-EG-001: map the errored_guias side-channel (domain ErroredGuia objects)
    # to the API DTO so an API consumer can see the 0-line guías.
    errored_guias = [
        ErroredGuiaResponse(
            registro=eg.registro,
            guia_id=eg.guia_id,
            source_pages=list(eg.source_pages),
            retry_attempted=eg.retry_attempted,
        )
        for eg in entry.get("errored_guias", [])
    ]

    return RunStatusResponse(
        run_id=run_id,
        status=entry["status"],
        vision_calls_made=entry.get("vision_calls_made", 0),
        warnings=entry.get("warnings", []),
        errored_guias=errored_guias,
        error=entry.get("error"),
        progress=progress_resp,
        started_at=entry.get("started_at"),
    )


@router.get(
    "/runs/{run_id}/table",
    response_model=ReconciliationTableResponse,
    summary="Fetch the reconciliation table for a completed run.",
)
def get_table(run_id: str, registry: RunRegistry, config: AppConfigDep) -> ReconciliationTableResponse:
    """Return reconciliation rows and unresolved guías for the run.

    Spec: REC-C05 / REV-C04 — guías whose ``registro`` is ``None`` surface in
    ``unresolved_guias`` and are NEVER included in ``rows``.
    """
    entry = _require_run(registry, run_id)
    review_service = _get_hydrated_review_service(entry, run_id, config)
    rows = [_row_to_response(r) for r in review_service.rows]

    # Populate unresolved_guias: any guía in the service whose registro is None
    # was excluded from reconciliation rows (domain invariant REC-C05).
    unresolved_guias = [
        UnresolvedGuiaResponse(
            guia_id=g.guia_id,
            identity_source=g.identity_source,
            source_pages=g.source_pages,
            first_page=(
                g.first_page
                if g.first_page is not None
                else (g.source_pages[0] if g.source_pages else None)
            ),
        )
        for g in review_service.guias
        if g.registro is None
    ]

    # Populate errored_guias: guías that resolved to 0 material lines (REV-E04).
    # Additive side-channel — never appears in rows, never affects reconciliation.
    errored_guias = [
        ErroredGuiaResponse(
            registro=eg.registro,
            guia_id=eg.guia_id,
            source_pages=eg.source_pages,
            retry_attempted=eg.retry_attempted,
        )
        for eg in review_service.errored_guias
    ]

    # Populate discarded_pages: GUIA pages dropped by the rev-6 QR-evidence gate (EXT-034).
    # Additive side-channel — never appears in rows, never affects MATCH/MISMATCH logic.
    discarded_pages_response = [
        DiscardedPageResponse(
            page=d.page,
            registro=d.registro,
            has_cached_lines=bool(d.lines),
        )
        for d in review_service.discarded_pages
    ]

    return ReconciliationTableResponse(
        run_id=run_id,
        rows=rows,
        unresolved_guias=unresolved_guias,
        errored_guias=errored_guias,
        discarded_pages=discarded_pages_response,
    )


@router.patch(
    "/runs/{run_id}/rows/{row_id}",
    response_model=RowEditResponse,
    summary="Apply a value edit to a guía field and recompute rows.",
)
def edit_row(
    run_id: str,
    row_id: str,  # noqa: ARG001 — present for URL routing; edit targets guia_id
    body: RowEditRequest,
    registry: RunRegistry,
    config: AppConfigDep,
) -> RowEditResponse:
    """Update a single field on a GuiaDeRemision and return updated rows.

    ``row_id`` is accepted in the URL for RESTful resource addressing but the
    actual mutation target is identified by ``body.guia_id``.

    Prohibited fields:
        ``summed_qty`` — computed property; returns 422 (REC-C04).
    """
    entry = _require_run(registry, run_id)
    review_service = _get_hydrated_review_service(entry, run_id, config)

    # Note: field='summed_qty' is rejected by Pydantic before reaching here
    # (RowEditRequest.field is Literal["fecha", "registro"]).  The ReviewService
    # also guards it explicitly (REC-C04) in case the schema is relaxed later.

    try:
        updated_rows = review_service.apply_edit(
            guia_id=body.guia_id,
            field=body.field,
            new_value=body.value,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return RowEditResponse(
        run_id=run_id,
        rows=[_row_to_response(r) for r in updated_rows],
    )


@router.patch(
    "/runs/{run_id}/guias/{guia_id}/lines",
    response_model=RowEditResponse,
    summary="Update a guía line quantity and recompute affected reconciliation rows.",
)
def edit_guia_line(
    run_id: str,
    guia_id: str,
    body: GuiaLineEditRequest,
    registry: RunRegistry,
    config: AppConfigDep,
) -> RowEditResponse:
    """Update the cantidad of a specific material line on a GuiaDeRemision.

    Spec: REC-C04 / REV-C02 / S1.7.

    Validation:
        - ``cantidad < 0`` → 422 (enforced by Pydantic schema ``ge=0``).
        - Unknown ``guia_id`` → 404.
        - Idempotent: sending the same request twice returns the same result.
    """
    entry = _require_run(registry, run_id)
    review_service = _get_hydrated_review_service(entry, run_id, config)

    from decimal import Decimal, InvalidOperation  # noqa: PLC0415

    try:
        new_cantidad = Decimal(str(body.cantidad))
    except InvalidOperation:
        raise HTTPException(status_code=422, detail=f"Invalid cantidad value: {body.cantidad!r}")

    try:
        updated_rows = review_service.apply_guia_line_edit(
            guia_id=guia_id,
            line_index=body.line_index,
            material_canonical=body.material_canonical,
            new_cantidad=new_cantidad,
            assign_material_canonical=body.assign_material_canonical,
        )
    except ValueError as exc:
        detail = str(exc)
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=422, detail=detail) from exc

    return RowEditResponse(
        run_id=run_id,
        rows=[_row_to_response(r) for r in updated_rows],
    )


@router.post(
    "/runs/{run_id}/reassign",
    response_model=ReassignResponse,
    summary="Reassign a guía to a different registro/fecha and recompute.",
)
def reassign_guia(
    run_id: str,
    body: ReassignRequest,
    registry: RunRegistry,
    config: AppConfigDep,
) -> ReassignResponse:
    """Move a guía to a new registro+fecha and return the updated table."""
    entry = _require_run(registry, run_id)
    review_service = _get_hydrated_review_service(entry, run_id, config)

    try:
        updated_rows = review_service.apply_reassignment(
            guia_id=body.guia_id,
            new_registro=body.new_registro,
            new_fecha=body.new_fecha,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return ReassignResponse(
        run_id=run_id,
        rows=[_row_to_response(r) for r in updated_rows],
    )


@router.post(
    "/runs/{run_id}/export",
    summary="Export the reconciliation report as xlsx or csv.",
)
def export_run(
    run_id: str,
    body: ExportRequest,
    registry: RunRegistry,
    config: AppConfigDep,
) -> FileResponse:
    """Generate and stream the export file.

    Returns the file as a ``FileResponse`` (direct download).  The file is
    written to the run's output directory under a stable name so repeated
    exports overwrite the same file rather than accumulating copies.
    """
    entry = _require_run(registry, run_id)
    review_service = _get_hydrated_review_service(entry, run_id, config)
    # C1 blast radius: hydration above set entry['ctx'] for cold entries; use
    # .get to avoid a KeyError on scan entries that never carried the 'ctx' key.
    ctx = entry.get("ctx")
    if ctx is None:
        raise HTTPException(
            status_code=409,
            detail=f"Run '{run_id}' has no processed context yet (status: {entry.get('status')}).",
        )

    from reconciliation.adapters.report.xlsx_report import ExcelReportAdapter  # noqa: PLC0415

    exporter = ExcelReportAdapter()
    dst = ctx.run_dir / f"export.{body.fmt}"

    try:
        out_path = exporter.export(
            rows=review_service.rows,
            audit_trail=review_service.get_audit_trail(),
            dst=dst,
            fmt=body.fmt,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Export failed: {exc}") from exc

    media_type = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if body.fmt == "xlsx"
        else "text/csv"
    )
    filename = f"reconciliation_{run_id[:8]}.{body.fmt}"

    return FileResponse(
        path=str(out_path),
        media_type=media_type,
        filename=filename,
    )


@router.get(
    "/runs/{run_id}/pages/{page}/thumbnail",
    summary="Fetch a page thumbnail, falling back to on-demand PDF render.",
)
def get_page_thumbnail(
    run_id: str,
    page: int,
    registry: RunRegistry,
    config: AppConfigDep,
) -> FileResponse:
    """Return a PNG thumbnail for page *page* (0-based) of run *run_id*.

    Spec: REV-005 / S1.8.

    Fallback chain (fix for issue #17 — OCR-off / vision-off modes):

    1. A PNG already present at ``run_dir/pages/{page:04d}.png`` — served directly.
       (This is the on-demand render cache written by step 2 on a prior request.)
    2. On-demand fitz render from the run's source PDF (``ctx.pdf_path``) — covers
       OCR-off, vision-off, and air-gap modes where no page render was produced
       during processing.  The rendered PNG is cached at the same
       ``pages/{page:04d}.png`` path so subsequent requests do not re-render.

    Returns 404 only when:
    - The run has no context yet (→ 409).
    - The page index is out of range (>= page_count).

    Fitz is lazy-imported inside this function (never at module top) to keep the
    import side-effect isolated to infrastructure and allow tests to run without
    a GPU runtime.  Input PDF is opened read-only; no mutation.
    """
    entry = _require_run(registry, run_id)
    # C1 blast radius: re-activate a cold-scanned run so the page viewer serves it.
    ctx = _get_hydrated_ctx(entry, run_id, config)
    if ctx is None:
        raise HTTPException(
            status_code=409,
            detail=f"Run '{run_id}' has no processed context yet (status: {entry.get('status')}).",
        )

    pages_dir: Path = ctx.run_dir / "pages"
    page_file = pages_dir / f"{page:04d}.png"

    # (1) Fast path: deskewed PNG already on disk (OCR-on mode).
    if page_file.exists():
        return FileResponse(
            path=str(page_file),
            media_type="image/png",
            filename=f"{run_id[:8]}_page_{page:04d}.png",
        )

    # (2) Fallback: render from source PDF via fitz (PyMuPDF) — lazy import.
    pdf_path: Path = ctx.pdf_path
    if not pdf_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Source PDF for run '{run_id}' not found on disk.",
        )

    try:
        import fitz  # noqa: PLC0415  — lazy import; never at module top

        doc = fitz.open(str(pdf_path))
        try:
            page_count = doc.page_count
            if page < 0 or page >= page_count:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Page {page} is out of range for run '{run_id}' "
                        f"(PDF has {page_count} pages, 0-based)."
                    ),
                )
            # Render at ~120 DPI — enough for a thumbnail; fast and small.
            fitz_page = doc.load_page(page)
            mat = fitz.Matrix(120 / 72, 120 / 72)  # 120 DPI (72 pt/in baseline)
            pix = fitz_page.get_pixmap(matrix=mat, alpha=False)
            png_bytes: bytes = pix.tobytes("png")
        finally:
            doc.close()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "get_page_thumbnail: fitz render failed for page %d of run %s: %s",
            page,
            run_id,
            exc,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to render page {page} from source PDF: {exc}",
        ) from exc

    # Cache the rendered PNG under pages/ so subsequent requests skip re-rendering.
    pages_dir.mkdir(parents=True, exist_ok=True)
    try:
        page_file.write_bytes(png_bytes)
    except OSError as exc:
        # Non-fatal: cache write failure is logged but the response still succeeds.
        logger.warning(
            "get_page_thumbnail: could not cache rendered PNG at %s: %s", page_file, exc
        )

    from fastapi.responses import Response  # noqa: PLC0415

    return Response(  # type: ignore[return-value]
        content=png_bytes,
        media_type="image/png",
        headers={
            "Content-Disposition": f'inline; filename="{run_id[:8]}_page_{page:04d}.png"',
        },
    )


@router.get(
    "/runs/{run_id}/pages/{page}/image",
    summary="Fetch a full-resolution page image, falling back to on-demand PDF render.",
)
def get_page_image(
    run_id: str,
    page: int,
    registry: RunRegistry,
    config: AppConfigDep,
) -> FileResponse:
    """Return a high-resolution PNG for page *page* (0-based) of run *run_id*.

    Issue #27 — page-sheet viewer. Sibling of ``get_page_thumbnail`` but renders
    at ~200 DPI (vs 120) so a full scanned sheet is legible in a lightbox. The
    render is cached to a DISTINCT path (``run_dir/pages/full/{page:04d}.png``) so
    it NEVER clobbers the 120-DPI thumbnail cache (``run_dir/pages/{page:04d}.png``).

    Fallback chain:

    1. A PNG already present at ``run_dir/pages/full/{page:04d}.png`` — served directly
       (the on-demand render cache written by step 2 on a prior request).
    2. On-demand fitz render from the run's source PDF (``ctx.pdf_path``) at 200 DPI —
       covers OCR-off / vision-off / air-gap modes. The rendered PNG is cached at the
       full-res path so subsequent requests skip re-rendering.

    Returns:
    - 409 when the run has no context yet.
    - 404 when the page index is out of range, the run is unknown, or the source PDF
      is missing.

    Fitz is lazy-imported inside this function (never at module top) to keep the heavy
    import isolated to infrastructure and let the suite run without a render runtime.
    Input PDF is opened read-only; renders write only under the run's own output dir.
    """
    entry = _require_run(registry, run_id)
    # C1 blast radius: re-activate a cold-scanned run so the page viewer serves it.
    ctx = _get_hydrated_ctx(entry, run_id, config)
    if ctx is None:
        raise HTTPException(
            status_code=409,
            detail=f"Run '{run_id}' has no processed context yet (status: {entry.get('status')}).",
        )

    # Distinct full-res cache dir — never the 120-DPI thumbnail path (pages/{page:04d}.png).
    full_dir: Path = ctx.run_dir / "pages" / "full"
    page_file = full_dir / f"{page:04d}.png"

    # (1) Fast path: full-res PNG already cached.
    if page_file.exists():
        return FileResponse(
            path=str(page_file),
            media_type="image/png",
            filename=f"{run_id[:8]}_page_{page:04d}_full.png",
        )

    # (2) Fallback: render from source PDF via fitz (PyMuPDF) — lazy import.
    pdf_path: Path = ctx.pdf_path
    if not pdf_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Source PDF for run '{run_id}' not found on disk.",
        )

    try:
        import fitz  # noqa: PLC0415  — lazy import; never at module top

        doc = fitz.open(str(pdf_path))
        try:
            page_count = doc.page_count
            if page < 0 or page >= page_count:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Page {page} is out of range for run '{run_id}' "
                        f"(PDF has {page_count} pages, 0-based)."
                    ),
                )
            # Render at ~200 DPI — full-res, legible scanned sheet for the lightbox.
            fitz_page = doc.load_page(page)
            mat = fitz.Matrix(200 / 72, 200 / 72)  # 200 DPI (72 pt/in baseline)
            pix = fitz_page.get_pixmap(matrix=mat, alpha=False)
            png_bytes: bytes = pix.tobytes("png")
        finally:
            doc.close()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "get_page_image: fitz render failed for page %d of run %s: %s",
            page,
            run_id,
            exc,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to render page {page} from source PDF: {exc}",
        ) from exc

    # Cache the rendered PNG under pages/full/ so subsequent requests skip re-rendering.
    full_dir.mkdir(parents=True, exist_ok=True)
    try:
        page_file.write_bytes(png_bytes)
    except OSError as exc:
        # Non-fatal: cache write failure is logged but the response still succeeds.
        logger.warning("get_page_image: could not cache rendered PNG at %s: %s", page_file, exc)

    from fastapi.responses import Response  # noqa: PLC0415

    return Response(  # type: ignore[return-value]
        content=png_bytes,
        media_type="image/png",
        headers={
            "Content-Disposition": f'inline; filename="{run_id[:8]}_page_{page:04d}_full.png"',
        },
    )


@router.post(
    "/runs/{run_id}/errored-guias/{guia_id}/retry",
    response_model=RetryGuiaResponse,
    status_code=200,
    summary="Retry a single errored guía via REINTENTAR deterministic recovery.",
)
def retry_errored_guia(
    run_id: str,
    guia_id: str,
    registry: RunRegistry,
    config: AppConfigDep,
) -> RetryGuiaResponse:
    """Attempt to recover a single errored guía using SUNAT descargaqr (REINTENTAR).

    Spec: REV-R08.

    Flow: render → decode_hashqr_url → SUNAT fetch → normalize → re-reconcile.
    No vision; guía fecha = SUNAT fecha_entrega (R9b floor, deterministic).

    Returns:
        200 RetryGuiaResponse with recovered=True on success, or recovered=False + reason on failure.

    Errors:
        404 — run_id unknown, or guia_id not in errored_guias (already recovered or never existed).
        503 — SUNAT fetch is disabled (sunat.enabled=False); REINTENTAR requires SUNAT.
    """
    entry = _require_run(registry, run_id)
    review_service = _get_hydrated_review_service(entry, run_id, config)
    reprocess_service = _require_reprocess_service(entry, run_id)
    # REINTENTAR requires SUNAT; raise 503 if the service was built for vision-only.
    _require_sunat_on_service(reprocess_service, run_id)

    # Verify guia_id is in the errored_guias list.
    errored_list = review_service.errored_guias if hasattr(review_service, "errored_guias") else []
    errored_entry = next((e for e in errored_list if e.guia_id == guia_id), None)
    if errored_entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"Guía '{guia_id}' not found in errored_guias for run '{run_id}'.",
        )

    result = reprocess_service.apply_retry(
        guia_id=guia_id,
        source_pages=list(errored_entry.source_pages),
    )

    # FIX 1: on a FAILED retry the guía stays errored — durably mark it so the
    # frontend gates the REINTENTAR button + shows the "SUNAT no disponible" hint.
    # On SUCCESS the guía leaves errored entirely (no flag needed).
    if result.recovered is False:
        review_service.mark_retry_attempted(guia_id)

    # Map updated rows to response DTOs.
    updated_rows = [_row_to_response(r) for r in result.rows] if result.rows else []

    # Map remaining errored guías.
    remaining_errored = [
        ErroredGuiaResponse(
            registro=eg.registro,
            guia_id=eg.guia_id,
            source_pages=list(eg.source_pages),
            retry_attempted=eg.retry_attempted,
        )
        for eg in review_service.errored_guias
    ]

    return RetryGuiaResponse(
        run_id=run_id,
        guia_id=guia_id,
        recovered=result.recovered,
        reason=result.reason,
        rows=updated_rows,
        errored_guias=remaining_errored,
    )


@router.post(
    "/runs/{run_id}/registros/{registro}/retry",
    response_model=RetryBatchResponse,
    status_code=202,
    summary="Retry all errored guías for a registro as a background task.",
)
def retry_registro(
    run_id: str,
    registro: str,
    background_tasks: BackgroundTasks,
    registry: RunRegistry,
    config: AppConfigDep,
) -> RetryBatchResponse:
    """Start a background batch retry for all errored guías in a registro.

    Spec: REV-R07.

    Returns 202 immediately; client re-polls GET /table for updated rows.
    Individual guía failures do NOT abort the remaining guías in the batch.

    Returns:
        202 RetryBatchResponse with the count of guías queued.

    Errors:
        404 — run_id unknown or no errored guías for the registro.
        503 — SUNAT fetch is disabled.
    """
    entry = _require_run(registry, run_id)
    review_service = _get_hydrated_review_service(entry, run_id, config)
    reprocess_service = _require_reprocess_service(entry, run_id)
    # Batch REINTENTAR also requires SUNAT.
    _require_sunat_on_service(reprocess_service, run_id)

    errored_list = review_service.errored_guias if hasattr(review_service, "errored_guias") else []
    target_guias = [e for e in errored_list if e.registro == registro]

    if not target_guias:
        raise HTTPException(
            status_code=404,
            detail=f"No errored guías found for registro='{registro}' in run '{run_id}'.",
        )

    def _retry_batch() -> None:
        for eg in target_guias:
            try:
                result = reprocess_service.apply_retry(
                    guia_id=eg.guia_id,
                    source_pages=list(eg.source_pages),
                )
                # Fix #42 (REV-R26): mirror the per-guía retry path — mark the guía
                # when apply_retry fails so the frontend gates REINTENTAR correctly.
                if result.recovered is False:
                    review_service.mark_retry_attempted(eg.guia_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "retry_registro: apply_retry failed for %r in run %r: %s",
                    eg.guia_id,
                    run_id,
                    exc,
                )

    background_tasks.add_task(_retry_batch)

    return RetryBatchResponse(
        run_id=run_id,
        registro=registro,
        count=len(target_guias),
    )


# ---------------------------------------------------------------------------
# Reprocesar con IA — vision recovery (PR#3)
# ---------------------------------------------------------------------------


@router.post(
    "/runs/{run_id}/errored-guias/{guia_id}/reprocess",
    response_model=ReprocessGuiaResponse,
    status_code=200,
    summary="Attempt to recover an errored guía using vision (Reprocesar con IA).",
)
async def reprocess_guia(
    run_id: str,
    guia_id: str,
    registry: RunRegistry,
    config: AppConfigDep,
) -> ReprocessGuiaResponse:
    """Attempt to recover a single errored guía via vision (Reprocesar con IA, PR#3).

    Spec: REV-R16.

    Flow: render page → downscale → VisionLLMPort.read_material_table → normalize
    → re-reconcile.  Vision; guía fecha = ErroredGuia.fecha_entrega (R9b floor) when
    available, else None.

    Returns:
        200 ReprocessGuiaResponse with recovered=True on success, or
        recovered=False + reason on failure.

    Errors:
        404 — run_id unknown, or guia_id not in errored_guias.
        503 — both vision and SUNAT disabled (reprocess_service is None), OR
              vision disabled (NullVisionAdapter) → reason=vision_disabled
              (REV-R16-S03; distinct from 200 vision_empty when vision is on).
    """
    entry = _require_run(registry, run_id)
    review_service = _get_hydrated_review_service(entry, run_id, config)
    reprocess_service = _require_reprocess_service(entry, run_id)
    # Reprocesar con IA requires a real vision adapter (NOT NullVisionAdapter).
    _require_vision_on_service(reprocess_service, run_id)

    # Verify guia_id is in errored_guias.
    errored_list = review_service.errored_guias if hasattr(review_service, "errored_guias") else []
    errored_entry = next((e for e in errored_list if e.guia_id == guia_id), None)
    if errored_entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"Guía '{guia_id}' not found in errored_guias for run '{run_id}'.",
        )

    result = await reprocess_service.apply_reprocess(
        guia_id=guia_id,
        source_pages=list(errored_entry.source_pages),
    )

    updated_rows = [_row_to_response(r) for r in result.rows] if result.rows else []

    remaining_errored = [
        ErroredGuiaResponse(
            registro=eg.registro,
            guia_id=eg.guia_id,
            source_pages=list(eg.source_pages),
            retry_attempted=eg.retry_attempted,
        )
        for eg in review_service.errored_guias
    ]

    return ReprocessGuiaResponse(
        run_id=run_id,
        guia_id=guia_id,
        recovered=result.recovered,
        reason=result.reason,
        rows=updated_rows,
        errored_guias=remaining_errored,
    )


async def _run_reprocess_batch(
    reprocess_service: Any,
    target_guias: list[Any],
    status: dict[str, Any],
    *,
    run_id: str,
) -> None:
    """Run all per-guía apply_reprocess calls concurrently, maintaining a live
    {total, recovered, failed, done} status record (SA-5 fix).

    Race-free by construction: asyncio is single-threaded; control only yields at
    ``await`` points.  Each guía runs inside ``_one`` which awaits apply_reprocess
    and then increments the shared ``status`` dict in a SYNCHRONOUS block (no
    ``await`` between read and write), so concurrent coroutines can never interleave
    a partial update.  ``done`` flips True only after ``gather`` resolves every guía.

    The existing Semaphore(3) inside ReprocessService.apply_reprocess remains the
    SOLE concurrency limiter (D1 / KI-2) — no second semaphore here, no asyncio.run.
    mark_retry_attempted is NEVER called here (D5 / REV-R26): AI reprocess is
    stateless-retryable; that flag is SUNAT-REINTENTAR-only.
    """
    status["total"] = len(target_guias)
    status["recovered"] = 0
    status["failed"] = 0
    status["done"] = False

    async def _one(eg: Any) -> None:
        try:
            result = await reprocess_service.apply_reprocess(
                guia_id=eg.guia_id,
                source_pages=list(eg.source_pages),
            )
            recovered = bool(getattr(result, "recovered", False))
        except Exception as exc:  # noqa: BLE001 — per-guía isolation (REV-R20-S03)
            logger.warning(
                "reprocess_registro: apply_reprocess raised for %r in run %r: %s",
                eg.guia_id,
                run_id,
                exc,
            )
            recovered = False
        # SYNCHRONOUS counter update — no await between read and write → race-free.
        if recovered:
            status["recovered"] = status["recovered"] + 1
        else:
            status["failed"] = status["failed"] + 1

    await asyncio.gather(*[_one(eg) for eg in target_guias])
    status["done"] = True


@router.post(
    "/runs/{run_id}/registros/{registro}/reprocess",
    response_model=ReprocessBatchResponse,
    status_code=202,
    summary="Bulk AI reprocess of all errored guías for a registro (REV-R20).",
)
async def reprocess_registro(
    run_id: str,
    registro: str,
    background_tasks: BackgroundTasks,
    registry: RunRegistry,
    config: AppConfigDep,
) -> ReprocessBatchResponse:
    """Start a background async batch reprocess for all errored guías in a registro.

    Spec: REV-R20.

    Reuses the bounded apply_reprocess coroutine (existing Semaphore(3) concurrency cap)
    via asyncio.gather(..., return_exceptions=True) so individual failures do not abort
    the remaining guías.  No nested event loop (D1 / D2); no mark_retry_attempted (D5 —
    that flag is SUNAT-only, not AI-reprocess).

    Returns 202 immediately; client re-polls GET /table for updated rows.

    Returns:
        202 ReprocessBatchResponse with the count of guías queued.

    Errors:
        404 — run_id unknown or no errored guías for the registro.
        503 — vision disabled (NullVisionAdapter) or reprocess_service is None.
    """
    entry = _require_run(registry, run_id)
    review_service = _get_hydrated_review_service(entry, run_id, config)
    reprocess_service = _require_reprocess_service(entry, run_id)
    # Bulk AI reprocess requires a real vision adapter (NOT NullVisionAdapter).
    _require_vision_on_service(reprocess_service, run_id)

    errored_list = review_service.errored_guias if hasattr(review_service, "errored_guias") else []
    target_guias = [e for e in errored_list if e.registro == registro]

    if not target_guias:
        raise HTTPException(
            status_code=404,
            detail=f"No errored guías found for registro='{registro}' in run '{run_id}'.",
        )

    # SA-5 fix: per-batch status record in the run-registry entry (API-layer
    # bookkeeping, NOT domain/application state — consistent with how the registry
    # already holds run state).  Initialized BEFORE the task fires so an immediate
    # GET .../reprocess-status sees done=False while the batch is still running.
    batches: dict[str, Any] = entry.setdefault("reprocess_batches", {})
    status: dict[str, Any] = {
        "total": len(target_guias),
        "recovered": 0,
        "failed": 0,
        "done": False,
    }
    batches[registro] = status

    async def _reprocess_batch() -> None:
        await _run_reprocess_batch(
            reprocess_service, target_guias, status, run_id=run_id
        )

    background_tasks.add_task(_reprocess_batch)

    return ReprocessBatchResponse(
        run_id=run_id,
        registro=registro,
        count=len(target_guias),
    )


@router.get(
    "/runs/{run_id}/registros/{registro}/reprocess-status",
    response_model=ReprocessBatchStatusResponse,
    summary="Live status of a bulk AI reprocess batch (SA-5 completion signal).",
)
def get_reprocess_status(
    run_id: str,
    registro: str,
    registry: RunRegistry,
) -> ReprocessBatchStatusResponse:
    """Return the live {total, recovered, failed, done} record for a registro's batch.

    SA-5 fix (REV-R20): the frontend polls this until ``done`` is True and drives the
    "N recuperadas / M fallaron" summary from the REAL counts, replacing the fragile
    PR-B time-heuristic that settled prematurely on real latency.

    When no batch has been fired for the registro, returns a terminal shape
    (``total=0``, ``done=True``) so the client never hangs.

    Errors:
        404 — run_id unknown.
    """
    entry = _require_run(registry, run_id)
    batches: dict[str, Any] = entry.get("reprocess_batches") or {}
    status = batches.get(registro)
    if status is None:
        return ReprocessBatchStatusResponse(
            registro=registro, total=0, recovered=0, failed=0, done=True
        )
    return ReprocessBatchStatusResponse(
        registro=registro,
        total=int(status.get("total", 0)),
        recovered=int(status.get("recovered", 0)),
        failed=int(status.get("failed", 0)),
        done=bool(status.get("done", False)),
    )


@router.get(
    "/runs/{run_id}/audit",
    response_model=AuditTrailResponse,
    summary="Retrieve the audit trail for a run.",
)
def get_audit_trail(run_id: str, registry: RunRegistry, config: AppConfigDep) -> AuditTrailResponse:
    """Return the ordered list of review edits and reassignments."""
    entry = _require_run(registry, run_id)
    review_service = _get_hydrated_review_service(entry, run_id, config)

    raw_events = review_service.get_audit_trail()
    events = [
        AuditEventResponse(
            timestamp=e["timestamp"],
            kind=e["kind"],
            target=e["target"],
            field=e.get("field"),
            old_value=e.get("old_value"),
            new_value=e.get("new_value"),
        )
        for e in raw_events
    ]
    return AuditTrailResponse(run_id=run_id, events=events)


# ---------------------------------------------------------------------------
# PR-2 — Discarded-page recovery endpoints (EXT-036/037, REV-R31)
# ---------------------------------------------------------------------------


async def _run_discarded_recovery_batch(
    reprocess_service: Any,
    pages: list[int],
    status: dict[str, Any],
    *,
    run_id: str,
) -> None:
    """Run per-page apply_page_recovery calls concurrently.

    Race-free by construction (mirrors _run_reprocess_batch): asyncio is single-threaded;
    counter updates are synchronous (no await between read and write).
    ``done`` flips True only after ``gather`` resolves ALL pages — never settle prematurely
    (SA-5 / PR-#49 lesson ×3: STRICT, STRICT, STRICT).
    """
    status["total"] = len(pages)
    status["recovered"] = 0
    status["failed"] = 0
    status["done"] = False

    # MEDIUM (JD / REV-R30 item 3 + S08) — bound recovery concurrency to max 3
    # simultaneous calls. apply_page_recovery only caps Tier-3 vision internally;
    # the Tier-2 OCR path (300-DPI render + OCR) is uncapped, so an A4 no-cap
    # selection (up to 343 pages) would otherwise spawn dozens of parallel renders.
    # Mirror ReprocessService's configured cap when available; default 3.
    max_concurrency = int(getattr(reprocess_service, "_max_concurrency", 3) or 3)
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _one(page: int) -> None:
        async with semaphore:
            try:
                result = await reprocess_service.apply_page_recovery(page=page)
                recovered = bool(getattr(result, "recovered", False))
            except Exception as exc:  # noqa: BLE001 — per-page isolation
                logger.warning(
                    "_run_discarded_recovery_batch: apply_page_recovery raised for page=%d "
                    "in run %r: %s",
                    page,
                    run_id,
                    exc,
                )
                recovered = False
            # SYNCHRONOUS counter update — no await between read and write → race-free.
            if recovered:
                status["recovered"] = status["recovered"] + 1
            else:
                status["failed"] = status["failed"] + 1

    # LOW (JD) — status["done"]=True under try/finally so a CancelledError (deadline
    # guard / client disconnect) can never leave the batch permanently in-flight
    # (a stuck done=False blocks every future batch with a 409).
    try:
        await asyncio.gather(*[_one(p) for p in pages])
    finally:
        status["done"] = True


@router.post(
    "/runs/{run_id}/discarded-pages/{page}/recover",
    response_model=RecoverPageResponse,
    summary="Recover a single discarded GUIA page via OCR-first chain (PR-2 / REV-R31).",
)
async def recover_discarded_page(
    run_id: str,
    page: int,
    registry: RunRegistry,
    config: AppConfigDep,
) -> RecoverPageResponse:
    """Recover a single discarded page via Tier-1 cached → Tier-2 OCR → Tier-3 vision.

    Spec: REV-R31. Design §3.

    Returns 200 RecoverPageResponse with recovered=True on success.
    Returns 200 RecoverPageResponse with recovered=False + reason on failure
      (reason: "empty" | "not_found").

    Errors:
        404 — run_id unknown or page not in discarded list.
        409 — run not in review state.
    """
    entry = _require_run(registry, run_id)
    review_service = _get_hydrated_review_service(entry, run_id, config)
    reprocess_service = _require_reprocess_service(entry, run_id)

    # Verify the page exists in the discarded list BEFORE calling apply_page_recovery.
    discarded_pages = getattr(review_service, "discarded_pages", [])
    if not any(dp.page == page for dp in discarded_pages):
        raise HTTPException(
            status_code=404,
            detail=f"Page {page} not found in discarded list for run '{run_id}'.",
        )

    result = await reprocess_service.apply_page_recovery(page=page)

    updated_rows = [_row_to_response(r) for r in result.rows] if result.rows else []
    remaining_discarded = [
        DiscardedPageResponse(
            page=d.page,
            registro=d.registro,
            has_cached_lines=bool(d.lines),
        )
        for d in review_service.discarded_pages
    ]

    return RecoverPageResponse(
        recovered=result.recovered,
        page=result.page,
        guia_id=result.guia_id,
        reason=result.reason,
        rows=updated_rows,
        discarded_pages=remaining_discarded,
    )


@router.post(
    "/runs/{run_id}/discarded-pages/recover-batch",
    response_model=DiscardedBatchResponse,
    status_code=202,
    summary="Bulk OCR-first recovery of operator-selected discarded pages (PR-2 / REV-R30).",
)
async def recover_discarded_batch(
    run_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    registry: RunRegistry,
    config: AppConfigDep,
) -> DiscardedBatchResponse:
    """Start a background batch recovery for operator-selected discarded pages.

    Body: { "pages": [int, ...] }  — operator-selected subset (not all 343 pages blindly).

    One active batch per run (409 if a batch is already in-flight).  Mirrors the
    _run_reprocess_batch / reprocess_registro convention (routes.py:1123-1243).

    Returns 202 immediately; client polls GET .../recover-status until done=True.

    Spec: REV-R30. Design §3 (SA-5 settle-only-on-done contract).

    Errors:
        404 — run_id unknown.
        409 — run not in review state, OR batch already in-flight.
    """
    entry = _require_run(registry, run_id)
    _get_hydrated_review_service(entry, run_id, config)
    reprocess_service = _require_reprocess_service(entry, run_id)

    body = await request.json()
    raw_pages: list[int] = [int(p) for p in body.get("pages", [])]

    # JD CRITICAL — de-duplicate pages (order-preserving) BEFORE scheduling.
    # A duplicated page (e.g. {"pages":[88,88]}) is a deterministic double-count
    # path with zero concurrency: it would invoke apply_page_recovery(88) twice.
    pages: list[int] = list(dict.fromkeys(raw_pages))

    if not pages:
        raise HTTPException(status_code=400, detail="'pages' must be a non-empty list.")

    # One active batch per run (409 on concurrent).
    batches: dict[str, Any] = entry.setdefault("discarded_batches", {})
    existing_status = batches.get("discarded")
    if existing_status is not None and not existing_status.get("done", True):
        raise HTTPException(
            status_code=409,
            detail=(
                f"A discarded-page recovery batch is already in-flight for run '{run_id}'. "
                "Wait for it to finish (poll recover-status until done=True) before starting another."
            ),
        )

    status: dict[str, Any] = {
        "total": len(pages),
        "recovered": 0,
        "failed": 0,
        "done": False,
    }
    batches["discarded"] = status

    async def _batch() -> None:
        await _run_discarded_recovery_batch(
            reprocess_service, pages, status, run_id=run_id
        )

    background_tasks.add_task(_batch)

    return DiscardedBatchResponse(run_id=run_id, count=len(pages))


@router.get(
    "/runs/{run_id}/discarded-pages/recover-status",
    response_model=DiscardedBatchStatusResponse,
    summary="Live status of a bulk discarded-page recovery batch (SA-5 completion signal).",
)
def get_discarded_recover_status(
    run_id: str,
    registry: RunRegistry,
) -> DiscardedBatchStatusResponse:
    """Return the live {total, recovered, failed, done} record for the discarded-page batch.

    SA-5 contract: the frontend polls this until done=True.
    Terminal shape {total=0, done=True} when no batch has been fired — client NEVER hangs.
    This terminal shape is LOCKED by test 2.1.15 (PR-3b re-attach on mount depends on it).

    Spec: REV-R30 (progress lifecycle). Design §3.

    Errors:
        404 — run_id unknown.
    """
    entry = _require_run(registry, run_id)
    batches: dict[str, Any] = entry.get("discarded_batches") or {}
    status = batches.get("discarded")
    if status is None:
        # Terminal shape — no batch fired.
        return DiscardedBatchStatusResponse(total=0, recovered=0, failed=0, done=True)
    return DiscardedBatchStatusResponse(
        total=int(status.get("total", 0)),
        recovered=int(status.get("recovered", 0)),
        failed=int(status.get("failed", 0)),
        done=bool(status.get("done", False)),
    )


# ---------------------------------------------------------------------------
# Capabilities endpoint (CAP-001)
# ---------------------------------------------------------------------------


@router.get(
    "/capabilities",
    response_model=CapabilitiesResponse,
    summary="Return global capability flags (vision and SUNAT enabled state).",
)
def get_capabilities(config: AppConfigDep) -> CapabilitiesResponse:
    """Return whether vision LLM and SUNAT fetch are currently enabled (CAP-001).

    Run-independent — always available, even with no active runs.
    NEVER includes secrets, paths, model names, or provider details (CAP-001-S03).

    Uses AppConfigDep backed by app.state.config (read-once at lifespan startup).
    """
    return CapabilitiesResponse(
        vision_enabled=config.vision.enabled,
        sunat_enabled=config.sunat.enabled,
    )


# ---------------------------------------------------------------------------
# Vision key settings endpoint (VKS-001/004)
# ---------------------------------------------------------------------------


@router.post(
    "/settings/vision-key",
    response_model=VisionKeySaveResponse,
    summary="Validate and persist a vision API key (VKS-001).",
)
def save_vision_key(
    body: VisionKeySaveRequest,
    key_store: KeyStoreDep,
    key_probe: KeyProbeDep,
) -> VisionKeySaveResponse:
    """Validate a candidate vision API key and persist it on success (VKS-001).

    Flow:
      1. Probe the candidate key against Ollama Cloud.
      2. valid   → store.write(key) + 200 {restart_required: true}.
      3. unauthorized → 400 (nothing persisted).
      4. unreachable | error → 503 (nothing persisted).

    Security invariants:
      - Key NEVER echoed in the response body.
      - Key NEVER logged at any log level.
      - Nothing is persisted unless the probe confirms valid.
    """
    # Probe first — persist only on success.
    result = key_probe.probe(body.key)

    if result.ok:
        key_store.write(body.key)
        logger.info("vision key: validated and persisted (restart_required=True)")
        return VisionKeySaveResponse(restart_required=True)

    if result.reason == "unauthorized":
        raise HTTPException(
            status_code=400,
            detail=result.message or "API key rejected (HTTP 401). Check the key and try again.",
        )

    # unreachable or error → 503
    # LOW-9: do NOT echo probe message (may contain provider base_url).
    # Use a generic, provider-agnostic message.
    raise HTTPException(
        status_code=503,
        detail="Vision API service is unreachable or returned an unexpected error. "
               "Check network connectivity and try again.",
    )


# ---------------------------------------------------------------------------
# Vision key DELETE (off-ramp / kill-switch restore — VKS-006)
# ---------------------------------------------------------------------------


@router.delete(
    "/settings/vision-key",
    response_model=VisionKeyDeleteResponse,
    summary="Remove the stored vision API key (VKS-006).",
)
def delete_vision_key(
    key_store: KeyStoreDep,
) -> VisionKeyDeleteResponse:
    """Clear the stored vision API key, restoring the kill-switch (VKS-006).

    Idempotent — returns 200 even if no key is currently stored.
    Vision stays off only AFTER the backend is restarted (restart_required=true).

    Security invariants:
      - Key value NEVER logged.
      - Nothing is echoed in the response body.
      - Operation is idempotent (no 404 on absent key).
    """
    key_store.clear()
    logger.info("vision key: cleared (restart_required=True)")
    return VisionKeyDeleteResponse(restart_required=True)
