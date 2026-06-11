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
    RunStatusResponse,
    RunSummaryResponse,
    UnresolvedGuiaResponse,
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


RunRegistry = Annotated[dict[str, Any], Depends(_get_registry)]
AppConfigDep = Annotated[Any, Depends(_get_config)]
RunHistoryDep = Annotated[Any, Depends(_get_run_history)]


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
    """Return the ReviewService from a run entry, or raise 409 if not ready."""
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
        # An entry hydrated from a prior session's manifest scan has status
        # "review" but no live review_service yet (lazy hydration is PR-2).
        # Distinguish it from a run that is genuinely mid-pipeline so the
        # operator/UI knows a reload is pending rather than a transient state.
        if entry.get("hydrated") is False and status == "review":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Run '{run_id}' is from a previous session; "
                    "reload is pending (PR-2)."
                ),
            )
        raise HTTPException(
            status_code=409,
            detail=f"Run '{run_id}' is not yet in review state (current: {status}).",
        )
    return review_service


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
    registros = [getattr(item, "registro", None) for item in declared_items]
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
            }
        )
        logger.info("pipeline run %s completed; %d rows", run_id, len(result.rows))

        # --- Run history manifest (D1: non-fatal side-channel in routes.py) ---
        # Uses the single app.state.run_history adapter passed in from create_run.
        try:
            manifest = _build_run_manifest(result, registry[run_id], started_at, run_id)
            run_history.write_manifest(manifest, config.output_dir)
        except Exception as _mex:  # noqa: BLE001
            logger.warning("run_history: manifest write error for %s (non-fatal): %s", run_id, _mex)

    except Exception as exc:  # noqa: BLE001
        logger.exception("pipeline run %s failed", run_id)
        registry[run_id].update({"status": "error", "error": str(exc)})

        # --- Failure manifest (D1: non-fatal; always try after registry update) ---
        # Uses the single app.state.run_history adapter passed in from create_run.
        try:
            run_history.write_failure_manifest(
                run_id=run_id,
                started_at=started_at,
                error_str=str(exc),
                output_dir=config.output_dir,
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
        deleted_ids = run_history.sweep_failed(config.output_dir, cutoff)
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
def get_table(run_id: str, registry: RunRegistry) -> ReconciliationTableResponse:
    """Return reconciliation rows and unresolved guías for the run.

    Spec: REC-C05 / REV-C04 — guías whose ``registro`` is ``None`` surface in
    ``unresolved_guias`` and are NEVER included in ``rows``.
    """
    entry = _require_run(registry, run_id)
    review_service = _require_review_service(entry, run_id)
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
) -> RowEditResponse:
    """Update a single field on a GuiaDeRemision and return updated rows.

    ``row_id`` is accepted in the URL for RESTful resource addressing but the
    actual mutation target is identified by ``body.guia_id``.

    Prohibited fields:
        ``summed_qty`` — computed property; returns 422 (REC-C04).
    """
    entry = _require_run(registry, run_id)
    review_service = _require_review_service(entry, run_id)

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
) -> RowEditResponse:
    """Update the cantidad of a specific material line on a GuiaDeRemision.

    Spec: REC-C04 / REV-C02 / S1.7.

    Validation:
        - ``cantidad < 0`` → 422 (enforced by Pydantic schema ``ge=0``).
        - Unknown ``guia_id`` → 404.
        - Idempotent: sending the same request twice returns the same result.
    """
    entry = _require_run(registry, run_id)
    review_service = _require_review_service(entry, run_id)

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
) -> ReassignResponse:
    """Move a guía to a new registro+fecha and return the updated table."""
    entry = _require_run(registry, run_id)
    review_service = _require_review_service(entry, run_id)

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
    review_service = _require_review_service(entry, run_id)
    ctx = entry["ctx"]

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
    ctx = entry.get("ctx")
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
    ctx = entry.get("ctx")
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
    reprocess_service = _require_reprocess_service(entry, run_id)
    # REINTENTAR requires SUNAT; raise 503 if the service was built for vision-only.
    _require_sunat_on_service(reprocess_service, run_id)
    review_service = _require_review_service(entry, run_id)

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
    reprocess_service = _require_reprocess_service(entry, run_id)
    # Batch REINTENTAR also requires SUNAT.
    _require_sunat_on_service(reprocess_service, run_id)
    review_service = _require_review_service(entry, run_id)

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
    reprocess_service = _require_reprocess_service(entry, run_id)
    # Reprocesar con IA requires a real vision adapter (NOT NullVisionAdapter).
    _require_vision_on_service(reprocess_service, run_id)
    review_service = _require_review_service(entry, run_id)

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
    reprocess_service = _require_reprocess_service(entry, run_id)
    # Bulk AI reprocess requires a real vision adapter (NOT NullVisionAdapter).
    _require_vision_on_service(reprocess_service, run_id)
    review_service = _require_review_service(entry, run_id)

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
def get_audit_trail(run_id: str, registry: RunRegistry) -> AuditTrailResponse:
    """Return the ordered list of review edits and reassignments."""
    entry = _require_run(registry, run_id)
    review_service = _require_review_service(entry, run_id)

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
    review_service = _require_review_service(entry, run_id)
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
    _require_review_service(entry, run_id)
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
