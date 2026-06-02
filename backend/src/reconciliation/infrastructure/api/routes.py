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

import logging
import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from reconciliation.infrastructure.api.schemas import (
    AuditEventResponse,
    AuditTrailResponse,
    ErrorResponse,  # noqa: F401 — imported for openapi docs
    ExportRequest,
    ExportResponse,
    GuiaContributionResponse,
    GuiaLineEditRequest,
    ReassignRequest,
    ReassignResponse,
    ReconciliationRowResponse,
    ReconciliationTableResponse,
    RowEditRequest,
    RowEditResponse,
    RunCreateResponse,
    RunStatusResponse,
    UnresolvedGuiaResponse,
    _row_id,
)
from reconciliation.domain.models import ReconciliationRow

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


def _get_registry(request: Request) -> "dict[str, Any]":
    """Extract the run registry dict from FastAPI app state."""
    return request.app.state.run_registry  # type: ignore[no-any-return]


def _get_config(request: Request) -> Any:
    """Extract the AppConfig from FastAPI app state."""
    return request.app.state.config  # type: ignore[no-any-return]


RunRegistry = Annotated[dict, Depends(_get_registry)]
AppConfigDep = Annotated[Any, Depends(_get_config)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_response(row: ReconciliationRow) -> ReconciliationRowResponse:
    """Convert a domain ReconciliationRow to the API DTO (rev-2: includes guias[])."""
    guia_responses = [
        GuiaContributionResponse(
            guia_id=g.guia_id,
            source_pages=g.source_pages,
            cantidad=g.cantidad,
            unidad=g.unidad,
            confidence=g.confidence,
            identity_source=g.identity_source,
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
        guias=guia_responses,
    )


def _require_run(registry: dict, run_id: str) -> Any:
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
            raise HTTPException(
                status_code=422,
                detail=f"Run '{run_id}' ended in error: {entry.get('error', 'unknown')}",
            )
        raise HTTPException(
            status_code=409,
            detail=f"Run '{run_id}' is not yet in review state (current: {status}).",
        )
    return review_service


# ---------------------------------------------------------------------------
# Background pipeline runner
# ---------------------------------------------------------------------------


def _run_pipeline_background(
    run_id: str,
    pdf_path: Path,
    config: Any,
    registry: dict,
) -> None:
    """Execute the pipeline synchronously inside a background task.

    Updates the registry entry with status transitions:
      processing → review  (success)
      processing → error   (exception)
    """
    from reconciliation.infrastructure.container import (  # noqa: PLC0415
        build_pipeline,
        build_review_service,
    )

    try:
        registry[run_id]["status"] = "processing"
        pipeline, ctx, page_to_registro = build_pipeline(pdf_path, config, run_id=run_id)
        result = pipeline.run(ctx)
        review_service = build_review_service(ctx)

        registry[run_id].update(
            {
                "status": "review",
                "ctx": ctx,
                "result": result,
                "review_service": review_service,
                "page_to_registro": page_to_registro,
                "vision_calls_made": result.vision_calls_made,
                "warnings": result.warnings,
            }
        )
        logger.info("pipeline run %s completed; %d rows", run_id, len(result.rows))
    except Exception as exc:  # noqa: BLE001
        logger.exception("pipeline run %s failed", run_id)
        registry[run_id].update({"status": "error", "error": str(exc)})


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
        "ctx": None,
        "result": None,
        "vision_calls_made": 0,
        "warnings": [],
        "error": None,
    }

    background_tasks.add_task(
        _run_pipeline_background, run_id, pdf_path, config, registry
    )

    logger.info("accepted run %s (%d bytes)", run_id, total_bytes)
    return RunCreateResponse(run_id=run_id, status="pending")


@router.get(
    "/runs/{run_id}",
    response_model=RunStatusResponse,
    summary="Poll a run's status.",
)
def get_run_status(run_id: str, registry: RunRegistry) -> RunStatusResponse:
    """Return the current status of a reconciliation run."""
    entry = _require_run(registry, run_id)
    return RunStatusResponse(
        run_id=run_id,
        status=entry["status"],
        vision_calls_made=entry.get("vision_calls_made", 0),
        warnings=entry.get("warnings", []),
        error=entry.get("error"),
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
            first_page=g.first_page if g.first_page != 0 else (g.source_pages[0] if g.source_pages else None),
        )
        for g in review_service.guias
        if g.registro is None
    ]

    return ReconciliationTableResponse(run_id=run_id, rows=rows, unresolved_guias=unresolved_guias)


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
    summary="Fetch the deskewed page render as a PNG thumbnail.",
)
def get_page_thumbnail(
    run_id: str,
    page: int,
    registry: RunRegistry,
) -> FileResponse:
    """Return the deskewed PNG render for page *page* (0-based) of run *run_id*.

    Spec: REV-005 / S1.8.

    The file is expected at ``run_dir/pages/{page:04d}.png`` — written by the
    pipeline's DeskewAdapter during processing.  Returns 404 if the page file
    does not exist (run not yet processed or page index out of range).

    No new dependencies — uses the standard ``FileResponse`` from ``fastapi``.
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

    if not page_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Page {page} not found for run '{run_id}'. "
            "The page may not have been rendered or the index is out of range.",
        )

    return FileResponse(
        path=str(page_file),
        media_type="image/png",
        filename=f"{run_id[:8]}_page_{page:04d}.png",
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
