/**
 * TDD RED — reprocessRegistroBatch client method (F1 / REV-R21 / D3).
 *
 * The bulk per-Registro AI reprocess endpoint:
 *   POST /runs/{runId}/registros/{registro}/reprocess → 202 ReprocessBatchResponse
 *
 * Authoritative merged backend contract (verified in
 * backend/.../api/schemas.py::ReprocessBatchResponse and
 * routes.py::reprocess_registro): the 202 body is
 *   { run_id, registro, count, task="started" }
 * NOT { registro, total, recovered, failed } — recovered/failed are derived
 * frontend-side by polling GET /table and comparing the errored list delta (D3).
 *
 * These tests fail before client.ts gains `reprocessRegistroBatch` and
 * types.ts gains `ReprocessBatchResponse`.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

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

import { reprocessRegistroBatch } from '@/api/client'

describe('API client — reprocessRegistroBatch (F1 / REV-R21)', () => {
  beforeEach(() => {
    mockGet.mockReset()
    mockPost.mockReset()
    mockPatch.mockReset()
  })

  it('is exported from client.ts', () => {
    expect(typeof reprocessRegistroBatch).toBe('function')
  })

  it('POSTs /runs/{runId}/registros/{registro}/reprocess and returns the 202 body', async () => {
    mockPost.mockResolvedValueOnce({
      data: { run_id: 'r1', registro: '232', count: 4, task: 'started' },
    })

    const result = await reprocessRegistroBatch('r1', '232')

    expect(mockPost).toHaveBeenCalledWith('/runs/r1/registros/232/reprocess')
    expect(result.run_id).toBe('r1')
    expect(result.registro).toBe('232')
    expect(result.count).toBe(4)
    expect(result.task).toBe('started')
  })

  it('URL-encodes the registro path segment', async () => {
    mockPost.mockResolvedValueOnce({
      data: { run_id: 'r1', registro: '23/2', count: 1, task: 'started' },
    })

    await reprocessRegistroBatch('r1', '23/2')

    expect(mockPost).toHaveBeenCalledWith('/runs/r1/registros/23%2F2/reprocess')
  })
})
