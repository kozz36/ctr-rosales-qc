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

/**
 * Progress snapshot emitted by the pipeline during a processing run.
 * Mirrors backend RunProgressInfo (schemas.py).
 */
export interface RunProgressInfo {
  /** Spanish label for the current stage, e.g. "Lectura de visión". */
  stage_label: string
  /** 1-based index of the current stage (1..stage_total). */
  stage_index: number
  /** Total number of pipeline stages (currently 5). */
  stage_total: number
  /** 1-based count of items completed within the current stage. */
  item_done: number
  /** Total items expected in the current stage (real count). */
  item_total: number
  /** Overall completion percentage, 0..100, computed server-side. */
  percent: number
}

/** GET /runs/{run_id} */
export interface RunStatusResponse {
  run_id: string
  status: RunStatus
  vision_calls_made: number
  warnings: string[]
  error: string | null
  /** ISO-8601 UTC timestamp set when the run starts processing; null while pending. */
  started_at: string | null
  /** Progress snapshot; null until the first pipeline stage emits. */
  progress: RunProgressInfo | null
}

/**
 * GET /runs — one entry of the run-history listing (SDD#3, RH-003).
 *
 * Mirrors backend RunSummaryResponse (schemas.py). Fields come from the
 * per-run manifest; `degraded=true` marks legacy entries derived from disk
 * (no manifest) whose nullable fields render as "—" in the UI.
 */
export interface RunSummaryResponse {
  run_id: string
  /** "review" | "error" (also "processing"/"pending" for in-flight entries). */
  status: RunStatus
  /** ISO-8601 UTC; null for legacy degraded entries. */
  started_at: string | null
  completed_at: string | null
  /** Per-day sequence (#N); null for degraded legacy entries. */
  seq: number | null
  registro_min: string | null
  registro_max: string | null
  row_count: number
  match_count: number
  mismatch_count: number
  /** len(warnings) — the listing never carries the full warnings list. */
  warnings_count: number
  vision_calls_made: number
  /** True when the entry was derived from disk (no manifest). */
  degraded: boolean
  error: string | null
}

/**
 * POST /runs/{run_id}/retry → 202 (SDD#3, RH-007-S02).
 *
 * Mirrors backend RunRetryResponse: SAME run_id semantics; status is always
 * "processing" immediately after the retry fires.
 */
export interface RunRetryResponse {
  run_id: string
  status: RunStatus
}

// ---------------------------------------------------------------------------
// Guía contribution (rev-2: inline in each ReconciliationRowResponse)
// ---------------------------------------------------------------------------

/**
 * One contributing guía within a reconciliation row.
 *
 * Mirrors backend GuiaContributionResponse (schemas.py).
 * cantidad is serialised as a Decimal string by the backend.
 */
export interface GuiaContributionResponse {
  guia_id: string // serie-numero, e.g. "T009-0741770"
  source_pages: number[]
  cantidad: string // Decimal serialised as string
  unidad: string
  confidence: number
  identity_source: 'qr' | 'ocr_fallback' | 'vision' | 'operator'
  /**
   * Rev-3 D5 (REC-C07): true when the year component of this guía's reception
   * date was reconstructed via bounded inference (EXT-021), not read directly
   * from vision output. Advisory flag — does NOT affect MATCH/MISMATCH logic.
   */
  year_inferred: boolean
  /**
   * R9 (FDR-008): guía handwritten reception date (ISO-8601 string or null).
   */
  fecha: string | null
  /**
   * R9 (FDR-008): true when this guía's handwritten date diverges (day-month
   * mismatch) from the registro's authoritative declared date. RED indicator —
   * a misfiled-guía signal; does NOT affect MATCH/MISMATCH logic.
   */
  fecha_divergence: boolean
  /** R9 (FDR-008): divergence reason code, or null when not divergent. */
  divergence_reason: 'fecha_divergence' | null
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
 *
 * guias[] is inline from the backend (rev-2) — no additional API call needed
 * to expand the drill-down view (REV-C01).
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
  /** True when OCR confidence < 0.85 or vision returned null date (task 7.3 / REV-004). */
  requires_review: boolean // server always sends this; default false
  guias: GuiaContributionResponse[] // rev-2: inline contributions
  /**
   * Rev-3 D5 (REC-C07): true when at least one contributing guía's reception-date
   * year was reconstructed via bounded inference (EXT-021). Advisory only —
   * yellow badge in UI; does NOT affect MATCH/MISMATCH logic.
   */
  any_year_inferred: boolean
  /**
   * R9 (FDR-008): true when at least one contributing guía has a fecha divergence
   * (group-level roll-up of guias[*].fecha_divergence). RED group indicator;
   * advisory only — does NOT affect MATCH/MISMATCH logic.
   */
  has_fecha_divergence: boolean
}

// ---------------------------------------------------------------------------
// Unresolved guía (REV-C04 — guías whose registro could not be determined)
// ---------------------------------------------------------------------------

/**
 * An unresolved GuiaDeRemision from the ``unresolved_guias`` bucket.
 *
 * These guías could not be matched to a Registro N° during the pipeline run.
 * They MUST appear only in the UnresolvedGuiasPanel, never in the main grid.
 *
 * Mirrors backend UnresolvedGuiaResponse (schemas.py).
 */
export interface UnresolvedGuiaResponse {
  guia_id: string // serie-numero, e.g. "T009-0741770"
  identity_source: 'qr' | 'ocr_fallback' | 'vision' | 'operator'
  source_pages: number[]
  first_page: number | null
}

/**
 * A guía that resolved to 0 material lines during extraction (REV-E04).
 *
 * Additive read-only side-channel — MUST NOT appear in the main reconciliation
 * grid and NEVER affects MATCH/MISMATCH logic.  Rendered in ErroredGuiasPanel.
 *
 * Mirrors backend ErroredGuiaResponse (schemas.py).
 *
 * T-8 / REV-R09: retry_attempted is set when a REINTENTAR attempt was made
 * (regardless of success/failure).  When true, the REINTENTAR button is disabled
 * and "SUNAT no disponible" is shown (gates PR#3 Reprocesar button).
 * Default false for backward compatibility with cached/older runs.
 */
export interface ErroredGuiaResponse {
  registro: string | null
  guia_id: string
  source_pages: number[]
  /** T-8/REV-R09: true when a REINTENTAR attempt has been made for this guía. */
  retry_attempted?: boolean
}

// ---------------------------------------------------------------------------
// REINTENTAR (T-7 / REV-R08)
// ---------------------------------------------------------------------------

/** POST /runs/{run_id}/errored-guias/{guia_id}/retry → 200 */
export interface RetryGuiaResponse {
  run_id: string
  guia_id: string
  recovered: boolean
  reason: string | null
  rows: ReconciliationRowResponse[]
  errored_guias: ErroredGuiaResponse[]
}

/** POST /runs/{run_id}/registros/{registro}/retry → 202 */
export interface RetryBatchResponse {
  run_id: string
  registro: string
  count: number
  task: string
}

/**
 * POST /runs/{run_id}/registros/{registro}/reprocess → 202 (F1 / REV-R20).
 *
 * Bulk per-Registro AI reprocess. The 202 body reports the COUNT of guías
 * queued for background vision reprocess — it does NOT carry recovered/failed.
 * The "N recuperadas / M fallaron" summary is DERIVED frontend-side by polling
 * GET /table and comparing the errored-list delta (design D3).
 *
 * Mirrors backend ReprocessBatchResponse (schemas.py).
 */
export interface ReprocessBatchResponse {
  run_id: string
  registro: string
  /** Number of errored guías queued for reprocess. */
  count: number
  /** Task handle — always "started". */
  task: string
}

/**
 * GET /runs/{run_id}/registros/{registro}/reprocess-status (SA-5 fix).
 *
 * REAL backend completion signal for the bulk AI reprocess batch. The frontend
 * polls this until `done` is true and drives the "N recuperadas / M fallaron"
 * summary from the real `recovered` / `failed` counts — replacing the fragile
 * time-heuristic that settled prematurely on real latency.
 *
 * Mirrors backend ReprocessBatchStatusResponse (schemas.py).
 */
export interface ReprocessBatchStatusResponse {
  registro: string
  /** Number of guías queued for the batch (0 when no batch fired). */
  total: number
  /** Guías recovered so far. */
  recovered: number
  /** Guías that failed so far. */
  failed: number
  /** True once the backend batch coroutine has finished. */
  done: boolean
}

/** POST /runs/{run_id}/errored-guias/{guia_id}/reprocess → 200 (PR#3) */
export interface ReprocessGuiaResponse {
  run_id: string
  guia_id: string
  recovered: boolean
  reason: string | null
  rows: ReconciliationRowResponse[]
  errored_guias: ErroredGuiaResponse[]
}

/** GET /runs/{run_id}/table */
/**
 * A GUIA-classified page dropped by the rev-6 QR-evidence gate (EXT-034 / SDD#2).
 *
 * Additive read-only side-channel.  `has_cached_lines` indicates whether Tier-1
 * (near-instant) recovery is available.  Raw MaterialLine data is NOT exposed.
 */
export interface DiscardedPageResponse {
  page: number
  registro: string | null
  has_cached_lines: boolean
}

/**
 * Response for POST /runs/{run_id}/discarded-pages/{page}/recover.
 *
 * `recovered=true` → page recovered, entry removed from discarded list.
 * `recovered=false` → see `reason` ("empty" | "not_found").
 * `rows` → updated reconciliation rows (empty on failure).
 * `discarded_pages` → remaining discarded entries after recovery.
 */
export interface RecoverPageResponse {
  recovered: boolean
  page: number
  guia_id: string | null
  reason: string | null
  rows: ReconciliationRowResponse[]
  discarded_pages: DiscardedPageResponse[]
}

/** Response for POST /runs/{run_id}/discarded-pages/recover-batch → 202. */
export interface DiscardedBatchResponse {
  run_id: string
  count: number
}

/**
 * Response for GET /runs/{run_id}/discarded-pages/recover-status.
 *
 * SA-5 shape — mirrors ReprocessBatchStatusResponse.
 * Terminal shape: `{ total: 0, recovered: 0, failed: 0, done: true }` when no batch fired.
 */
export interface DiscardedRecoverStatusResponse {
  total: number
  recovered: number
  failed: number
  done: boolean
}

export interface ReconciliationTableResponse {
  run_id: string
  rows: ReconciliationRowResponse[]
  /** Rev-2: guías whose registro could not be determined (REV-C04 / REC-C05). */
  unresolved_guias: UnresolvedGuiaResponse[]
  /** Rev-3 (REV-E04): guías that resolved to 0 material lines — read-only surface. */
  errored_guias: ErroredGuiaResponse[]
  /** SDD#2 (EXT-034): GUIA pages dropped by rev-6 QR-evidence gate — read-only surface. */
  discarded_pages?: DiscardedPageResponse[]
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
// Guía line edit (PATCH /runs/{run_id}/guias/{guia_id}/lines) — rev-2
// ---------------------------------------------------------------------------

/**
 * Mirrors backend GuiaLineEditRequest (schemas.py).
 * cantidad must be >= 0 (backend enforces ge=0; 422 otherwise).
 */
export interface GuiaLineEditRequest {
  line_index: number | null
  material_canonical: string | null
  cantidad: number // numeric (not string) — backend expects float ge=0
  /**
   * F4 / REV-R25: operator-assigned canonical correction. When present the
   * backend reassigns the guía line to this declared material, sets
   * match_method="operator" and requires_review=True. snake_case to mirror the
   * backend GuiaLineEditRequest body posted verbatim (consumed in PR-C).
   */
  assign_material_canonical?: string | null
}

/** The updated rows are returned, same shape as the table endpoint. */
export interface GuiaLineEditResponse {
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
