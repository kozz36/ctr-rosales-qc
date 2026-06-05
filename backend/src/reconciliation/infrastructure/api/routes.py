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

from reconciliation.domain.models import ReconciliationRow
from reconciliation.infrastructure.api.schemas import (
    AuditEventResponse,
    AuditTrailResponse,
    ErroredGuiaResponse,
    ErrorResponse,  # noqa: F401 — imported for openapi docs
    ExportRequest,
    GuiaContributionResponse,
    GuiaLineEditRequest,
    ProgressResponse,
    ReassignRequest,
    ReassignResponse,
    ReconciliationRowResponse,
    ReconciliationTableResponse,
    RetryBatchResponse,
    RetryGuiaResponse,
    RowEditRequest,
    RowEditResponse,
    RunCreateResponse,
    RunStatusResponse,
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


RunRegistry = Annotated[dict[str, Any], Depends(_get_registry)]
AppConfigDep = Annotated[Any, Depends(_get_config)]


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
            raise HTTPException(
                status_code=422,
                detail=f"Run '{run_id}' ended in error: {entry.get('error', 'unknown')}",
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


# ---------------------------------------------------------------------------
# Background pipeline runner
# ---------------------------------------------------------------------------


def _run_pipeline_background(
    run_id: str,
    pdf_path: Path,
    config: Any,
    registry: dict[str, Any],
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
        "reprocess_service": None,
        "ctx": None,
        "result": None,
        "vision_calls_made": 0,
        "warnings": [],
        "errored_guias": [],
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

    return ReconciliationTableResponse(
        run_id=run_id,
        rows=rows,
        unresolved_guias=unresolved_guias,
        errored_guias=errored_guias,
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
                reprocess_service.apply_retry(
                    guia_id=eg.guia_id,
                    source_pages=list(eg.source_pages),
                )
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
