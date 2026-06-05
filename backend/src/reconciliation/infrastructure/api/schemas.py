"""FastAPI request / response DTOs — anti-corruption layer.

Domain models (ReconciliationRow, GuiaDeRemision, etc.) are never exposed
directly through the API.  This module defines Pydantic v2 schemas that
translate between HTTP payloads and domain objects.

Naming convention:
  *Request  — inbound payload (POST/PATCH body)
  *Response — outbound payload

All date fields use ISO-8601 strings ("YYYY-MM-DD") for JSON interoperability.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, computed_field

# ---------------------------------------------------------------------------
# Guía contribution (inline per reconciliation row — rev-2, REC-C02)
# ---------------------------------------------------------------------------


class GuiaContributionResponse(BaseModel):
    """Per-guía contribution inline in a ReconciliationRowResponse (rev-2)."""

    guia_id: str = Field(description="Deterministic identifier: {serie}-{numero}.")
    source_pages: list[int] = Field(description="Physical page indices contributing to this guía.")
    cantidad: Decimal = Field(description="Total quantity contributed by this guía to the group.")
    unidad: str = Field(description="Unit of measure (must match the parent group's unidad).")
    confidence: float = Field(description="Identity confidence from QR decode or fallback.")
    identity_source: Literal["qr", "ocr_fallback", "vision"] = Field(
        description="How the guía identity was determined."
    )
    # Rev-3 D5 (REC-C07): year_inferred provenance flag.
    year_inferred: bool = Field(
        default=False,
        description=(
            "True when the year component of this guía's reception date was reconstructed "
            "via bounded inference (EXT-021), not read directly from vision output."
        ),
    )
    # R9.6 (FDR-008, ADR-5): fecha-divergence fields — additive, backward-compatible.
    fecha: date | None = Field(
        default=None,
        description="Guía handwritten reception date (ISO-8601 or null).",
    )
    fecha_divergence: bool = Field(
        default=False,
        description=(
            "True when this guía's handwritten date diverges (day-month mismatch) "
            "from the registro's authoritative declared date."
        ),
    )
    divergence_reason: Literal["fecha_divergence"] | None = Field(
        default=None,
        description="Divergence classification code, or null when not divergent.",
    )
    # R9b: delivery-floor side-channel — mirrors the fecha_divergence pattern.
    delivery_floor_applied: bool = Field(
        default=False,
        description=(
            "True when this guía's resolved reception date was floored to the "
            "SUNAT fecha_entrega lower bound (goods-before-delivery invariant, R9b). "
            "Advisory only; does not affect MATCH/MISMATCH logic."
        ),
    )
    # Reception-ceiling side-channel — mirrors delivery_floor_applied.
    reception_ceiling_applied: bool = Field(
        default=False,
        description=(
            "True when this guía's reception date was clamped to the Protocolo "
            "declared date upper ceiling (Registro.fecha_authoritative). "
            "The ceiling is the symmetric upper bound to the delivery-floor lower bound. "
            "Advisory only; does not affect MATCH/MISMATCH logic."
        ),
    )
    # Crossed-bounds anomaly side-channel — mirrors reception_ceiling_applied.
    delivery_after_protocolo: bool = Field(
        default=False,
        description=(
            "True when this guía's SUNAT delivery date (fecha_entrega) is LATER than "
            "the Protocolo declared ceiling — a physical impossibility (goods cannot "
            "be delivered after the declared reception; likely a human error in the "
            "Protocolo). The ceiling clamp is suppressed (the floored read date is "
            "kept above the delivery floor) and the guía is flagged for review. "
            "Advisory only; does not affect MATCH/MISMATCH logic."
        ),
    )


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------


class RunCreateResponse(BaseModel):
    """Returned immediately when POST /runs accepts a PDF upload."""

    run_id: str
    status: Literal["pending", "processing", "review", "error"]


class ProgressResponse(BaseModel):
    """Live progress snapshot for a running pipeline stage.

    Percent formula:
        percent = ((stage_index - 1) + (item_done / item_total if item_total else 1)) / stage_total
        clamped to [0, 100].

    The numerator gives fractional stage progress: completed stages contribute
    a full 1.0 unit each; the current stage contributes its item-fraction.
    """

    stage_label: str = Field(description="Human-readable Spanish label for the current stage.")
    stage_index: int = Field(description="1-based index of the current stage (1..stage_total).")
    stage_total: int = Field(description="Total number of instrumented stages.")
    item_done: int = Field(description="Items completed so far in this stage (1-based).")
    item_total: int = Field(description="Total items in this stage.")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def percent(self) -> float:
        """Completion percentage [0.0..100.0], clamped.

        Formula: ((stage_index - 1) + (item_done / item_total)) / stage_total * 100
        Treats item_total=0 as a completed stage (fraction=1.0) — no division by zero.
        """
        item_fraction = (self.item_done / self.item_total) if self.item_total > 0 else 1.0
        raw = ((self.stage_index - 1) + item_fraction) / self.stage_total
        return max(0.0, min(1.0, raw)) * 100.0


class ErroredGuiaResponse(BaseModel):
    """A guía block that resolved to 0 material lines (REC-EG-001).

    Additive side-channel surfaced on the run status so an API consumer can
    see the 0-line guías the pipeline collected; it NEVER appears as a
    reconciliation row and never affects MATCH/MISMATCH logic.
    """

    registro: str | None = Field(
        default=None,
        description="Section registro número, or null when unresolved.",
    )
    guia_id: str = Field(description="Deterministic identifier: {serie}-{numero}.")
    source_pages: list[int] = Field(
        description="Physical page indices contributing to this 0-line guía."
    )
    retry_attempted: bool = Field(
        default=False,
        description=(
            "True once a REINTENTAR (SUNAT recovery) was attempted and FAILED for "
            "this guía. Gates the frontend REINTENTAR button + 'SUNAT no disponible' "
            "hint. Additive UX flag — never alters qty/status/delta of any row."
        ),
    )


class RunStatusResponse(BaseModel):
    """Returned by GET /runs/{run_id}."""

    run_id: str
    status: Literal["pending", "processing", "review", "error"]
    vision_calls_made: int = 0
    warnings: list[str] = Field(default_factory=list)
    # REC-EG-001: 0-line guías collected by the pipeline (additive side-channel).
    errored_guias: list[ErroredGuiaResponse] = Field(
        default_factory=list,
        description="Guía blocks that resolved to 0 material lines (REC-EG-001).",
    )
    error: str | None = None
    # Determinate progress bar (backward-compatible: optional fields)
    progress: ProgressResponse | None = Field(
        default=None,
        description="Live progress snapshot; None when the run is not yet processing.",
    )
    started_at: str | None = Field(
        default=None,
        description="ISO-8601 UTC timestamp when the run entered 'processing' state.",
    )


# ---------------------------------------------------------------------------
# Unresolved guía (REV-C04 — surfaces guías that could not be assigned to a registro)
# ---------------------------------------------------------------------------


class UnresolvedGuiaResponse(BaseModel):
    """An unresolved GuiaDeRemision — one whose registro could not be determined.

    These appear in the ``unresolved_guias`` bucket of ``ReconciliationTableResponse``
    and MUST NOT appear as rows in the main reconciliation grid (REC-C05 / REV-C04).
    """

    guia_id: str = Field(description="Deterministic identifier: {serie}-{numero}.")
    identity_source: str = Field(
        description="How the guía identity was determined: 'qr' or 'ocr_fallback'."
    )
    source_pages: list[int] = Field(
        description="Physical page indices contributing to this guía."
    )
    first_page: int | None = Field(
        default=None,
        description="First page index of this guía block (0-based).",
    )


# ---------------------------------------------------------------------------
# Reconciliation table
# ---------------------------------------------------------------------------


class ReconciliationRowResponse(BaseModel):
    """A single row in the reconciliation table (rev-2: guias[] inline)."""

    row_id: str  # "{registro}|{fecha}|{material_canonical}|{unidad}"
    registro: str
    fecha: date | None
    material_canonical: str
    unidad: str
    declared_qty: Decimal
    summed_qty: Decimal
    delta: Decimal
    status: Literal["MATCH", "MISMATCH", "DECLARED_MISSING", "GUIA_MISSING", "UNCLASSIFIED"]
    source_pages: list[int]
    min_confidence: float | None = None
    # Flagging surface (task 7.3 / REV-004, EXT-S08, EXT-S08b)
    requires_review: bool = Field(
        default=False,
        description=(
            "True when any contributing line has low OCR confidence, "
            "or when the guía reception date could not be read by vision."
        ),
    )
    # Rev-2: inline guía contributions (REC-C02 / design §D)
    guias: list[GuiaContributionResponse] = Field(
        default_factory=list,
        description="Per-guía contributions to this reconciliation group.",
    )
    # Rev-3 D5 (REC-C07): advisory flag — true when any contributing guía used
    # year inference.  Does NOT affect MATCH/MISMATCH; transparency signal only.
    any_year_inferred: bool = Field(
        default=False,
        description=(
            "True when at least one contributing guía's reception-date year was "
            "reconstructed via bounded inference (EXT-021). Advisory only."
        ),
    )
    # R8.12 (MAT-008, ADR-5): read-only provenance field (no POST/PATCH accepts it).
    match_method: Literal["deterministic", "llm_inferred", "unresolved"] = Field(
        default="deterministic",
        description=(
            "How the canonical material key was derived: "
            "'deterministic' (regex), 'llm_inferred' (Ollama), or 'unresolved' (fallback)."
        ),
    )
    # R9.6 (FDR-008, ADR-5): group-level divergence indicator (derived from guías).
    has_fecha_divergence: bool = Field(
        default=False,
        description=(
            "True when at least one contributing guía has a fecha divergence "
            "(group-level roll-up of guias[*].fecha_divergence). Advisory only."
        ),
    )
    # R9b: group-level delivery-floor indicator (mirrors has_fecha_divergence).
    has_delivery_floor: bool = Field(
        default=False,
        description=(
            "True when at least one contributing guía had its reception date floored "
            "to the SUNAT fecha_entrega lower bound "
            "(group-level roll-up of guias[*].delivery_floor_applied). Advisory only."
        ),
    )
    # Reception-ceiling group-level indicator (mirrors has_delivery_floor).
    has_reception_ceiling: bool = Field(
        default=False,
        description=(
            "True when at least one contributing guía had its reception date clamped "
            "to the Protocolo declared date upper ceiling "
            "(group-level roll-up of guias[*].reception_ceiling_applied). Advisory only."
        ),
    )
    # Crossed-bounds anomaly group-level indicator (mirrors has_reception_ceiling).
    has_delivery_after_protocolo: bool = Field(
        default=False,
        description=(
            "True when at least one contributing guía hit the crossed-bounds anomaly "
            "(SUNAT fecha_entrega later than the Protocolo ceiling — impossible) "
            "(group-level roll-up of guias[*].delivery_after_protocolo). Advisory only."
        ),
    )


class ReconciliationTableResponse(BaseModel):
    """Response for GET /runs/{run_id}/table.

    Rev-2: ``unresolved_guias`` carries guías that could not be assigned to a
    registro (REC-C05 / REV-C04).  They are NEVER included in ``rows``.

    Rev-3 (REV-E04): ``errored_guias`` surfaces guías that resolved to 0
    material lines during extraction (additive side-channel — NEVER appears
    in ``rows``, NEVER affects reconciliation logic).
    """

    run_id: str
    rows: list[ReconciliationRowResponse]
    unresolved_guias: list[UnresolvedGuiaResponse] = Field(
        default_factory=list,
        description="Guías whose registro could not be determined (REV-C04).",
    )
    errored_guias: list[ErroredGuiaResponse] = Field(
        default_factory=list,
        description=(
            "Guías that resolved to 0 material lines during extraction (REV-E04). "
            "Additive read-only side-channel — never in rows, never affects MATCH/MISMATCH."
        ),
    )


# ---------------------------------------------------------------------------
# Edit (PATCH /runs/{run_id}/rows/{row_id})
# ---------------------------------------------------------------------------


class RowEditRequest(BaseModel):
    """Apply a value edit to a guía field.

    The ``row_id`` in the URL identifies the row group; ``guia_id`` and
    ``field`` identify the specific guía+field to update within that group.
    """

    guia_id: str = Field(description="Target GuiaDeRemision identifier.")
    field: Literal["fecha", "registro"] = Field(
        description="Field to update: 'fecha' (ISO-8601 string) or 'registro' (string)."
    )
    value: str | None = Field(
        description="New value. For 'fecha': ISO-8601 'YYYY-MM-DD' or null. "
        "For 'registro': string or null."
    )


class RowEditResponse(BaseModel):
    """Response after applying an edit — returns updated rows."""

    run_id: str
    rows: list[ReconciliationRowResponse]


# ---------------------------------------------------------------------------
# Guía line edit (PATCH /runs/{run_id}/guias/{guia_id}/lines — rev-2, S1.7)
# ---------------------------------------------------------------------------


class GuiaLineEditRequest(BaseModel):
    """Update a specific material line's quantity on a GuiaDeRemision.

    Identifies the target line by ``line_index`` (0-based, preferred) or
    by ``material_canonical`` when ``line_index`` is omitted.
    """

    line_index: int | None = Field(
        default=None,
        description="0-based index of the line to update within guia.lines.",
    )
    material_canonical: str | None = Field(
        default=None,
        description="Canonical material description for line lookup when line_index is None.",
    )
    cantidad: float = Field(
        description="New quantity value. Must be >= 0.",
        ge=0,
    )


# ---------------------------------------------------------------------------
# Reassign (POST /runs/{run_id}/reassign)
# ---------------------------------------------------------------------------


class ReassignRequest(BaseModel):
    """Reassign a guía to a different registro/fecha."""

    guia_id: str = Field(description="GuiaDeRemision to reassign.")
    new_registro: str = Field(description="Target registro número.")
    new_fecha: str | None = Field(
        default=None,
        description="New reception date in ISO-8601 'YYYY-MM-DD' format, or null.",
    )


class ReassignResponse(BaseModel):
    """Response after reassignment — returns updated rows."""

    run_id: str
    rows: list[ReconciliationRowResponse]


# ---------------------------------------------------------------------------
# Export (POST /runs/{run_id}/export)
# ---------------------------------------------------------------------------


class ExportRequest(BaseModel):
    """Trigger export of the reconciliation report."""

    fmt: Literal["xlsx", "csv"] = Field(
        default="xlsx",
        description="Output format: 'xlsx' or 'csv'.",
    )


class ExportResponse(BaseModel):
    """Response after export — the file is returned as a download."""

    run_id: str
    fmt: Literal["xlsx", "csv"]
    filename: str


# ---------------------------------------------------------------------------
# Audit trail (GET /runs/{run_id}/audit)
# ---------------------------------------------------------------------------


class AuditEventResponse(BaseModel):
    """A single audit trail entry."""

    timestamp: str
    kind: str
    target: dict  # type: ignore[type-arg]
    field: str | None
    old_value: object
    new_value: object


class AuditTrailResponse(BaseModel):
    """Response for GET /runs/{run_id}/audit."""

    run_id: str
    events: list[AuditEventResponse]


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    """Generic error envelope."""

    detail: str


# ---------------------------------------------------------------------------
# REINTENTAR (T-7 / REV-R08)
# ---------------------------------------------------------------------------


class RetryGuiaResponse(BaseModel):
    """Response for POST /runs/{run_id}/errored-guias/{guia_id}/retry.

    recovered=True when the guía was successfully recovered (SUNAT fetch + lines).
    recovered=False when recovery failed (no_hashqr_url | sunat_empty | sunat_none).
    rows is the updated reconciliation table after re-reconcile (empty on failure).
    errored_guias is the remaining errored guías list after the attempt.
    """

    run_id: str
    guia_id: str
    recovered: bool
    reason: str | None = Field(
        default=None,
        description=(
            "Failure reason when recovered=False: "
            "'no_hashqr_url' | 'sunat_empty' | 'sunat_none'. "
            "Null on success."
        ),
    )
    rows: list[ReconciliationRowResponse] = Field(
        default_factory=list,
        description="Updated reconciliation rows after recovery (empty on failure).",
    )
    errored_guias: list[ErroredGuiaResponse] = Field(
        default_factory=list,
        description="Remaining errored guías after the retry attempt.",
    )


class RetryBatchResponse(BaseModel):
    """Response for POST /runs/{run_id}/registros/{registro}/retry → 202 Accepted.

    Background task started; client re-polls GET /table for updates.
    """

    run_id: str
    registro: str
    count: int = Field(description="Number of errored guías queued for retry.")
    task: str = Field(default="started", description="Task handle (always 'started').")


class ReprocessGuiaResponse(BaseModel):
    """Response for POST /runs/{run_id}/errored-guias/{guia_id}/reprocess (PR#3).

    recovered=True when the guía was successfully recovered via vision.
    recovered=False when recovery failed (vision_empty | not_found).
    rows is the updated reconciliation table after re-reconcile (empty on failure).
    errored_guias is the remaining errored guías list after the attempt.
    """

    run_id: str
    guia_id: str
    recovered: bool
    reason: str | None = Field(
        default=None,
        description=(
            "Failure reason when recovered=False: "
            "'vision_empty' | 'not_found'. "
            "Null on success."
        ),
    )
    rows: list[ReconciliationRowResponse] = Field(
        default_factory=list,
        description="Updated reconciliation rows after recovery (empty on failure).",
    )
    errored_guias: list[ErroredGuiaResponse] = Field(
        default_factory=list,
        description="Remaining errored guías after the reprocess attempt.",
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _row_id(registro: str, fecha: date | None, material: str, unidad: str) -> str:
    """Stable composite key for a reconciliation row."""
    return f"{registro}|{str(fecha)}|{material}|{unidad}"
