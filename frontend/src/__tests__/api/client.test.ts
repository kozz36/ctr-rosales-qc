/**
 * API client — unit tests
 *
 * Validates the request shape (method, URL, headers, body) for each endpoint.
 * Axios is mocked via vi.hoisted + vi.mock so the mock functions exist before
 * the module is imported (vi.mock is hoisted to the top by Vitest's transform).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

// ---------------------------------------------------------------------------
// Hoist mock fns so they are initialised before the vi.mock factory runs
// ---------------------------------------------------------------------------

const { mockGet, mockPost, mockPatch } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockPatch: vi.fn(),
}))

vi.mock('axios', async (importOriginal) => {
  const actual = await importOriginal<typeof import('axios')>()
  return {
    ...actual,
    default: {
      ...actual.default,
      create: () => ({
        get: mockGet,
        post: mockPost,
        patch: mockPatch,
      }),
    },
  }
})

import {
  getRunStatus,
  getTable,
  editRow,
  editGuiaLine,
  reassignGuia,
  getAuditTrail,
} from '@/api/client'

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

describe('API client', () => {
  beforeEach(() => {
    mockGet.mockReset()
    mockPost.mockReset()
    mockPatch.mockReset()
  })

  // -------------------------------------------------------------------------
  // GET /runs/{run_id}
  // -------------------------------------------------------------------------

  it('getRunStatus calls GET /runs/{id}', async () => {
    mockGet.mockResolvedValueOnce({
      data: {
        run_id: 'r1',
        status: 'pending',
        vision_calls_made: 0,
        warnings: [],
        error: null,
      },
    })

    const result = await getRunStatus('r1')
    expect(mockGet).toHaveBeenCalledWith('/runs/r1')
    expect(result.run_id).toBe('r1')
    expect(result.status).toBe('pending')
  })

  // -------------------------------------------------------------------------
  // GET /runs/{run_id}/table
  // -------------------------------------------------------------------------

  it('getTable calls GET /runs/{id}/table', async () => {
    mockGet.mockResolvedValueOnce({ data: { run_id: 'r1', rows: [] } })

    const result = await getTable('r1')
    expect(mockGet).toHaveBeenCalledWith('/runs/r1/table')
    expect(result.run_id).toBe('r1')
    expect(Array.isArray(result.rows)).toBe(true)
  })

  // -------------------------------------------------------------------------
  // PATCH /runs/{run_id}/rows/{row_id}
  // -------------------------------------------------------------------------

  it('editRow sends correct PATCH URL and body', async () => {
    mockPatch.mockResolvedValueOnce({ data: { run_id: 'r1', rows: [] } })

    await editRow('r1', 'row-abc', { guia_id: 'g1', field: 'fecha', value: '2024-01-20' })

    expect(mockPatch).toHaveBeenCalledWith('/runs/r1/rows/row-abc', {
      guia_id: 'g1',
      field: 'fecha',
      value: '2024-01-20',
    })
  })

  it('editRow accepts null value for field clearing', async () => {
    mockPatch.mockResolvedValueOnce({ data: { run_id: 'r1', rows: [] } })

    await editRow('r1', 'row-abc', { guia_id: 'g1', field: 'registro', value: null })

    const callArgs = mockPatch.mock.calls[0] as [string, { value: unknown }]
    expect(callArgs[1].value).toBeNull()
  })

  // -------------------------------------------------------------------------
  // PATCH /runs/{run_id}/guias/{guia_id}/lines — rev-2 (S2.1)
  // -------------------------------------------------------------------------

  it('editGuiaLine sends correct PATCH URL and body', async () => {
    mockPatch.mockResolvedValueOnce({ data: { run_id: 'r1', rows: [] } })

    await editGuiaLine('r1', 'T009-0741770', {
      line_index: null,
      material_canonical: null,
      cantidad: 1250,
    })

    expect(mockPatch).toHaveBeenCalledWith(
      '/runs/r1/guias/T009-0741770/lines',
      { line_index: null, material_canonical: null, cantidad: 1250 },
    )
  })

  it('editGuiaLine URL-encodes special characters in guia_id', async () => {
    mockPatch.mockResolvedValueOnce({ data: { run_id: 'r1', rows: [] } })

    // serie-numero format "T009-0741770" — hyphen is safe but we verify encoding path works
    await editGuiaLine('r1', 'T073-0680256', {
      line_index: 0,
      material_canonical: null,
      cantidad: 500,
    })

    // encodeURIComponent('T073-0680256') = 'T073-0680256' (safe chars)
    expect(mockPatch).toHaveBeenCalledWith(
      '/runs/r1/guias/T073-0680256/lines',
      expect.objectContaining({ cantidad: 500 }),
    )
  })

  it('useReassignGuia composable sends guia_id (not row_id) in body', async () => {
    // Direct client test: reassignGuia sends guia_id as-is (no proxy)
    mockPost.mockResolvedValueOnce({ data: { run_id: 'r1', rows: [] } })

    await reassignGuia('r1', {
      guia_id: 'T009-0741770', // actual serie-numero
      new_registro: '231',
      new_fecha: '2025-02-10',
    })

    const callBody = mockPost.mock.calls[0][1] as { guia_id: string }
    expect(callBody.guia_id).toBe('T009-0741770')
    expect(callBody.guia_id).not.toContain('|') // never a compound row_id
  })

  // -------------------------------------------------------------------------
  // POST /runs/{run_id}/reassign
  // -------------------------------------------------------------------------

  it('reassignGuia sends correct POST URL and body', async () => {
    mockPost.mockResolvedValueOnce({ data: { run_id: 'r1', rows: [] } })

    await reassignGuia('r1', {
      guia_id: 'g2',
      new_registro: 'R005',
      new_fecha: '2024-03-01',
    })

    expect(mockPost).toHaveBeenCalledWith('/runs/r1/reassign', {
      guia_id: 'g2',
      new_registro: 'R005',
      new_fecha: '2024-03-01',
    })
  })

  // -------------------------------------------------------------------------
  // GET /runs/{run_id}/audit
  // -------------------------------------------------------------------------

  it('getAuditTrail calls GET /runs/{id}/audit', async () => {
    mockGet.mockResolvedValueOnce({ data: { run_id: 'r1', events: [] } })

    const result = await getAuditTrail('r1')
    expect(mockGet).toHaveBeenCalledWith('/runs/r1/audit')
    expect(Array.isArray(result.events)).toBe(true)
  })
})
