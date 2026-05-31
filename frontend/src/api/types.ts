/**
 * API types — anti-corruption layer mirroring backend/src/reconciliation/infrastructure/api/schemas.py
 *
 * Naming mirrors backend Pydantic models exactly so the mapping is obvious
 * during schema evolution. Decimal fields become string to avoid JS float
 * precision loss (backend serialises Decimal as string in JSON).
 */

// ---------------------------------------------------------------------------
// Shared enumerations
// ---------------------------------------------------------------------------

export type RunStatus = 'pending' | 'processing' | 'review' | 'error'

export type RowStatus =
  | 'MATCH'
  | 'MISMATCH'
  | 'DECLARED_MISSING'
  | 'GUIA_MISSING'
  | 'UNCLASSIFIED'

export type EditableField = 'fecha' | 'registro'

export type ExportFormat = 'xlsx' | 'csv'

// ---------------------------------------------------------------------------
// Run lifecycle
// ---------------------------------------------------------------------------

/** POST /runs → 202 */
export interface RunCreateResponse {
  run_id: string
  status: RunStatus
}

/** GET /runs/{run_id} */
export interface RunStatusResponse {
  run_id: string
  status: RunStatus
  vision_calls_made: number
  warnings: string[]
  error: string | null
}

// ---------------------------------------------------------------------------
// Reconciliation table
// ---------------------------------------------------------------------------

/**
 * One row of the reconciliation table.
 *
 * Columns (as shown in the UI):
 *   Registro | Fecha | Material | Unidad | Declarado | Sumado(guías) | Delta | Estado | Confianza mín | Páginas origen
 *
 * declared_qty / summed_qty / delta are serialised as strings by the backend
 * (Pydantic Decimal → JSON string via model_config json_encoders) to preserve
 * exact decimal precision.
 */
export interface ReconciliationRowResponse {
  row_id: string // "{registro}|{fecha}|{material_canonical}|{unidad}"
  registro: string
  fecha: string | null // ISO-8601 "YYYY-MM-DD" or null
  material_canonical: string
  unidad: string
  declared_qty: string // Decimal serialised as string
  summed_qty: string
  delta: string
  status: RowStatus
  source_pages: number[]
  min_confidence: number | null
}

/** GET /runs/{run_id}/table */
export interface ReconciliationTableResponse {
  run_id: string
  rows: ReconciliationRowResponse[]
}

// ---------------------------------------------------------------------------
// Edit (PATCH /runs/{run_id}/rows/{row_id})
// ---------------------------------------------------------------------------

export interface RowEditRequest {
  guia_id: string
  field: EditableField
  value: string | null
}

export interface RowEditResponse {
  run_id: string
  rows: ReconciliationRowResponse[]
}

// ---------------------------------------------------------------------------
// Reassign (POST /runs/{run_id}/reassign)
// ---------------------------------------------------------------------------

export interface ReassignRequest {
  guia_id: string
  new_registro: string
  new_fecha: string | null // ISO-8601 or null
}

export interface ReassignResponse {
  run_id: string
  rows: ReconciliationRowResponse[]
}

// ---------------------------------------------------------------------------
// Export (POST /runs/{run_id}/export)
// ---------------------------------------------------------------------------

export interface ExportRequest {
  fmt: ExportFormat
}

// The export endpoint returns a FileResponse (binary download), not JSON.
// The client uses a blob URL approach — no typed response object needed here.

// ---------------------------------------------------------------------------
// Audit trail (GET /runs/{run_id}/audit)
// ---------------------------------------------------------------------------

export interface AuditEventResponse {
  timestamp: string
  kind: string
  target: Record<string, unknown>
  field: string | null
  old_value: unknown
  new_value: unknown
}

export interface AuditTrailResponse {
  run_id: string
  events: AuditEventResponse[]
}

// ---------------------------------------------------------------------------
// API error envelope
// ---------------------------------------------------------------------------

export interface ApiErrorDetail {
  detail: string
}
