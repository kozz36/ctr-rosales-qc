/**
 * API client — vision-key + capabilities endpoints (CAP-001, VKS-001).
 *
 * Validates request shape (method, URL, body) for the three PR-2 client
 * methods that consume the PR-1 backend contract:
 *   GET    /capabilities          → { vision_enabled, sunat_enabled }
 *   POST   /settings/vision-key    body { key } → { restart_required }
 *   DELETE /settings/vision-key    → { restart_required }
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

const { mockGet, mockPost, mockDelete } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockDelete: vi.fn(),
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
        delete: mockDelete,
      }),
    },
  }
})

import { getCapabilities, saveVisionKey, deleteVisionKey } from '@/api/client'

describe('API client — capabilities + vision-key', () => {
  beforeEach(() => {
    mockGet.mockReset()
    mockPost.mockReset()
    mockDelete.mockReset()
  })

  it('getCapabilities calls GET /capabilities and returns the body (CAP-001)', async () => {
    mockGet.mockResolvedValue({ data: { vision_enabled: false, sunat_enabled: true } })
    const result = await getCapabilities()
    expect(mockGet).toHaveBeenCalledWith('/capabilities')
    expect(result).toEqual({ vision_enabled: false, sunat_enabled: true })
  })

  it('saveVisionKey POSTs /settings/vision-key with the key body (VKS-001)', async () => {
    mockPost.mockResolvedValue({ data: { restart_required: true } })
    const result = await saveVisionKey('sk-test-123')
    expect(mockPost).toHaveBeenCalledWith('/settings/vision-key', { key: 'sk-test-123' })
    expect(result).toEqual({ restart_required: true })
  })

  it('deleteVisionKey DELETEs /settings/vision-key (VKS off-ramp)', async () => {
    mockDelete.mockResolvedValue({ data: { restart_required: true } })
    const result = await deleteVisionKey()
    expect(mockDelete).toHaveBeenCalledWith('/settings/vision-key')
    expect(result).toEqual({ restart_required: true })
  })
})
