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

from pydantic import BaseModel, Field


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
    identity_source: Literal["qr", "ocr_fallback"] = Field(
        description="How the guía identity was determined."
    )


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------


class RunCreateResponse(BaseModel):
    """Returned immediately when POST /runs accepts a PDF upload."""

    run_id: str
    status: Literal["pending", "processing", "review", "error"]


class RunStatusResponse(BaseModel):
    """Returned by GET /runs/{run_id}."""

    run_id: str
    status: Literal["pending", "processing", "review", "error"]
    vision_calls_made: int = 0
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


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
    # Rev-2: inline guía contributions (REC-C02 / design §D)
    guias: list[GuiaContributionResponse] = Field(
        default_factory=list,
        description="Per-guía contributions to this reconciliation group.",
    )


class ReconciliationTableResponse(BaseModel):
    """Response for GET /runs/{run_id}/table."""

    run_id: str
    rows: list[ReconciliationRowResponse]


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
# Internal helpers
# ---------------------------------------------------------------------------


def _row_id(registro: str, fecha: date | None, material: str, unidad: str) -> str:
    """Stable composite key for a reconciliation row."""
    return f"{registro}|{str(fecha)}|{material}|{unidad}"
