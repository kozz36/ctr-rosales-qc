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
  ExportFormat,
  GuiaLineEditRequest,
  GuiaLineEditResponse,
  ReassignRequest,
  ReassignResponse,
  ReconciliationTableResponse,
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

// Re-export the Axios instance for tests that need to mock it
export { http }
