/**
 * API client for the reconciliation backend.
 *
 * Uses Axios (already in package.json) with a typed wrapper per endpoint.
 * Base URL is read from VITE_API_BASE_URL; falls back to http://localhost:8000
 * for local development. The Vite dev-server proxy is NOT relied upon here —
 * the client always uses the full base URL so it works equally in prod.
 *
 * All methods throw AxiosError on non-2xx. Callers (TanStack Query) handle
 * retries and error state.
 */

import axios, { type AxiosInstance } from 'axios'
import type {
  AuditTrailResponse,
  DiscardedBatchResponse,
  DiscardedRecoverStatusResponse,
  ExportFormat,
  GuiaLineEditRequest,
  GuiaLineEditResponse,
  ReassignRequest,
  ReassignResponse,
  ReconciliationTableResponse,
  RecoverPageResponse,
  ReprocessBatchResponse,
  ReprocessBatchStatusResponse,
  ReprocessGuiaResponse,
  RetryBatchResponse,
  RetryGuiaResponse,
  RowEditRequest,
  RowEditResponse,
  RunCreateResponse,
  RunStatusResponse,
} from './types'

// ---------------------------------------------------------------------------
// Axios instance
// ---------------------------------------------------------------------------

const BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000'

const API_PREFIX = '/api/v1'

const http: AxiosInstance = axios.create({
  baseURL: BASE_URL + API_PREFIX,
  headers: { Accept: 'application/json' },
  // Timeout: 30 s for regular calls; export uses a separate instance (streaming)
  timeout: 30_000,
})

// ---------------------------------------------------------------------------
// POST /runs  — multipart PDF upload → returns run_id immediately (202)
// ---------------------------------------------------------------------------

export async function createRun(file: File): Promise<RunCreateResponse> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await http.post<RunCreateResponse>('/runs', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    // Upload can be large (up to 100 MB); give it 5 minutes
    timeout: 300_000,
  })
  return data
}

// ---------------------------------------------------------------------------
// GET /runs/{run_id}  — poll status
// ---------------------------------------------------------------------------

export async function getRunStatus(runId: string): Promise<RunStatusResponse> {
  const { data } = await http.get<RunStatusResponse>(`/runs/${runId}`)
  return data
}

// ---------------------------------------------------------------------------
// GET /runs/{run_id}/table  — fetch reconciliation rows
// ---------------------------------------------------------------------------

export async function getTable(runId: string): Promise<ReconciliationTableResponse> {
  const { data } = await http.get<ReconciliationTableResponse>(`/runs/${runId}/table`)
  return data
}

// ---------------------------------------------------------------------------
// PATCH /runs/{run_id}/rows/{row_id}  — edit a guía field
// ---------------------------------------------------------------------------

export async function editRow(
  runId: string,
  rowId: string,
  body: RowEditRequest,
): Promise<RowEditResponse> {
  const { data } = await http.patch<RowEditResponse>(`/runs/${runId}/rows/${rowId}`, body)
  return data
}

// ---------------------------------------------------------------------------
// PATCH /runs/{run_id}/guias/{guia_id}/lines  — edit a guía line cantidad (rev-2)
// ---------------------------------------------------------------------------

export async function editGuiaLine(
  runId: string,
  guiaId: string,
  body: GuiaLineEditRequest,
): Promise<GuiaLineEditResponse> {
  const { data } = await http.patch<GuiaLineEditResponse>(
    `/runs/${runId}/guias/${encodeURIComponent(guiaId)}/lines`,
    body,
  )
  return data
}

// ---------------------------------------------------------------------------
// POST /runs/{run_id}/reassign  — move guía to different registro/fecha
// ---------------------------------------------------------------------------

export async function reassignGuia(
  runId: string,
  body: ReassignRequest,
): Promise<ReassignResponse> {
  const { data } = await http.post<ReassignResponse>(`/runs/${runId}/reassign`, body)
  return data
}

// ---------------------------------------------------------------------------
// POST /runs/{run_id}/export  — trigger file download (blob response)
// ---------------------------------------------------------------------------

export async function exportRun(runId: string, fmt: ExportFormat): Promise<Blob> {
  const { data } = await http.post<Blob>(
    `/runs/${runId}/export`,
    { fmt },
    {
      responseType: 'blob',
      // Allow more time for xlsx generation on large runs
      timeout: 60_000,
    },
  )
  return data
}

// ---------------------------------------------------------------------------
// GET /runs/{run_id}/audit  — audit trail
// ---------------------------------------------------------------------------

export async function getAuditTrail(runId: string): Promise<AuditTrailResponse> {
  const { data } = await http.get<AuditTrailResponse>(`/runs/${runId}/audit`)
  return data
}

// ---------------------------------------------------------------------------
// POST /runs/{run_id}/errored-guias/{guia_id}/retry  — REINTENTAR single guía (T-8)
// ---------------------------------------------------------------------------

export async function retryGuia(
  runId: string,
  guiaId: string,
): Promise<RetryGuiaResponse> {
  const { data } = await http.post<RetryGuiaResponse>(
    `/runs/${runId}/errored-guias/${encodeURIComponent(guiaId)}/retry`,
  )
  return data
}

// ---------------------------------------------------------------------------
// POST /runs/{run_id}/registros/{registro}/retry  — REINTENTAR per-Registro batch (T-8)
// ---------------------------------------------------------------------------

export async function retryRegistro(
  runId: string,
  registro: string,
): Promise<RetryBatchResponse> {
  const { data } = await http.post<RetryBatchResponse>(
    `/runs/${runId}/registros/${encodeURIComponent(registro)}/retry`,
  )
  return data
}

// ---------------------------------------------------------------------------
// POST /runs/{run_id}/errored-guias/{guia_id}/reprocess — Reprocesar con IA (T7/PR#3)
// ---------------------------------------------------------------------------

export async function reprocessGuia(
  runId: string,
  guiaId: string,
): Promise<ReprocessGuiaResponse> {
  const { data } = await http.post<ReprocessGuiaResponse>(
    `/runs/${runId}/errored-guias/${encodeURIComponent(guiaId)}/reprocess`,
  )
  return data
}

// ---------------------------------------------------------------------------
// POST /runs/{run_id}/registros/{registro}/reprocess — bulk per-Registro AI
// reprocess (F1 / REV-R20). 202: background batch started; client polls
// GET /table for the recovered/failed delta (D3).
// ---------------------------------------------------------------------------

export async function reprocessRegistroBatch(
  runId: string,
  registro: string,
): Promise<ReprocessBatchResponse> {
  const { data } = await http.post<ReprocessBatchResponse>(
    `/runs/${runId}/registros/${encodeURIComponent(registro)}/reprocess`,
  )
  return data
}

// ---------------------------------------------------------------------------
// GET /runs/{run_id}/registros/{registro}/reprocess-status — REAL completion
// signal for the bulk AI reprocess batch (SA-5 fix). The frontend polls this
// until `done` is true and drives the N/M summary from the real counts,
// replacing the fragile time-heuristic that settled prematurely.
// ---------------------------------------------------------------------------

export async function getReprocessBatchStatus(
  runId: string,
  registro: string,
): Promise<ReprocessBatchStatusResponse> {
  const { data } = await http.get<ReprocessBatchStatusResponse>(
    `/runs/${runId}/registros/${encodeURIComponent(registro)}/reprocess-status`,
  )
  return data
}

// ---------------------------------------------------------------------------
// POST /runs/{run_id}/discarded-pages/{page}/recover — single-page recovery
// (SDD#2 PR-3a / REV-R31). OCR-first: cached lines → OCR re-run → vision.
// 200 with recovered=true/false (+ reason); 404 page not discarded; 409 not ready.
// ---------------------------------------------------------------------------

export async function recoverDiscardedPage(
  runId: string,
  page: number,
): Promise<RecoverPageResponse> {
  const { data } = await http.post<RecoverPageResponse>(
    `/runs/${runId}/discarded-pages/${page}/recover`,
    undefined,
    {
      // Tier-2 OCR (~10 s) or Tier-3 vision can exceed the default 30 s budget.
      timeout: 120_000,
    },
  )
  return data
}

// ---------------------------------------------------------------------------
// POST /runs/{run_id}/discarded-pages/recover-batch — operator-selected subset
// (SDD#2 / REV-R30). 202: background batch started; client polls recover-status.
// ---------------------------------------------------------------------------

export async function recoverDiscardedBatch(
  runId: string,
  pages: number[],
): Promise<DiscardedBatchResponse> {
  const { data } = await http.post<DiscardedBatchResponse>(
    `/runs/${runId}/discarded-pages/recover-batch`,
    { pages },
  )
  return data
}

// ---------------------------------------------------------------------------
// GET /runs/{run_id}/discarded-pages/recover-status — REAL completion signal
// (SA-5 shape). Terminal shape {total:0, recovered:0, failed:0, done:true}
// when no batch fired — the client never hangs.
// ---------------------------------------------------------------------------

export async function getDiscardedRecoverStatus(
  runId: string,
): Promise<DiscardedRecoverStatusResponse> {
  const { data } = await http.get<DiscardedRecoverStatusResponse>(
    `/runs/${runId}/discarded-pages/recover-status`,
  )
  return data
}

// Re-export the Axios instance for tests that need to mock it
export { http }
