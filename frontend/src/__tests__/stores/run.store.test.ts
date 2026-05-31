/**
 * Run store — unit tests
 *
 * Tests cover:
 *  - Initial state
 *  - upload() success → runId + status set
 *  - upload() rejects non-PDF (client validation)
 *  - upload() rejects oversized files (client validation)
 *  - upload() network error → error captured in store
 *  - setStatus() mirrors status
 *  - reset() clears all state
 *
 * API client is mocked via vi.mock so no real HTTP is issued.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useRunStore } from '@/stores/run'

// ---------------------------------------------------------------------------
// Mock the API client module
// ---------------------------------------------------------------------------

vi.mock('@/api/client', () => ({
  createRun: vi.fn(),
}))

import { createRun } from '@/api/client'
const mockCreateRun = vi.mocked(createRun)

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeFile(name: string, type: string, size = 1024): File {
  const blob = new Blob([new Uint8Array(size)], { type })
  return new File([blob], name, { type })
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

describe('useRunStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockCreateRun.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('starts with null state', () => {
    const store = useRunStore()

    expect(store.runId).toBeNull()
    expect(store.status).toBeNull()
    expect(store.uploading).toBe(false)
    expect(store.uploadProgress).toBe(0)
    expect(store.error).toBeNull()
    expect(store.isActive).toBe(false)
    expect(store.isReady).toBe(false)
    expect(store.isFailed).toBe(false)
  })

  it('sets runId + status on successful upload', async () => {
    const store = useRunStore()
    mockCreateRun.mockResolvedValueOnce({ run_id: 'abc-123', status: 'pending' })

    const file = makeFile('plan.pdf', 'application/pdf')
    const id = await store.upload(file)

    expect(id).toBe('abc-123')
    expect(store.runId).toBe('abc-123')
    expect(store.status).toBe('pending')
    expect(store.uploading).toBe(false)
    expect(store.error).toBeNull()
  })

  it('rejects a non-PDF file without calling the API', async () => {
    const store = useRunStore()
    const file = makeFile('data.xlsx', 'application/vnd.ms-excel')

    await expect(store.upload(file)).rejects.toThrow('El archivo debe ser un PDF.')
    expect(mockCreateRun).not.toHaveBeenCalled()
    expect(store.error).toBe('El archivo debe ser un PDF.')
  })

  it('rejects a .pdf extension with wrong MIME type only when no pdf in name', async () => {
    // A file with .pdf extension IS accepted (some OS send application/octet-stream)
    const store = useRunStore()
    mockCreateRun.mockResolvedValueOnce({ run_id: 'xyz-456', status: 'pending' })

    const file = makeFile('report.pdf', 'application/octet-stream')
    const id = await store.upload(file)
    expect(id).toBe('xyz-456')
  })

  it('rejects a file exceeding 100 MB', async () => {
    const store = useRunStore()
    // Build a File with a size property > 100 MB by overriding size
    const bigFile = Object.defineProperty(
      makeFile('huge.pdf', 'application/pdf', 100),
      'size',
      { value: 100 * 1024 * 1024 + 1 },
    ) as File

    await expect(store.upload(bigFile)).rejects.toThrow('El archivo excede el límite de 100 MB.')
    expect(mockCreateRun).not.toHaveBeenCalled()
  })

  it('captures API network errors in store.error', async () => {
    const store = useRunStore()
    mockCreateRun.mockRejectedValueOnce(new Error('Network Error'))

    const file = makeFile('plan.pdf', 'application/pdf')
    await expect(store.upload(file)).rejects.toThrow('Network Error')
    expect(store.error).toBe('Network Error')
    expect(store.runId).toBeNull()
  })

  it('captures API 4xx detail from AxiosError-shaped error', async () => {
    const store = useRunStore()
    const axiosErr = Object.assign(new Error('Unsupported Media Type'), {
      response: { data: { detail: 'Only PDF uploads are accepted.' } },
    })
    mockCreateRun.mockRejectedValueOnce(axiosErr)

    const file = makeFile('plan.pdf', 'application/pdf')
    await expect(store.upload(file)).rejects.toThrow()
    expect(store.error).toBe('Only PDF uploads are accepted.')
  })

  it('setStatus() updates status and sets error on error state', () => {
    const store = useRunStore()
    store.setStatus('processing')
    expect(store.status).toBe('processing')

    store.setStatus('error', 'Pipeline crashed')
    expect(store.status).toBe('error')
    expect(store.error).toBe('Pipeline crashed')
    expect(store.isFailed).toBe(true)
  })

  it('setStatus() to review sets isReady', () => {
    const store = useRunStore()
    store.setStatus('review')
    expect(store.isReady).toBe(true)
  })

  it('reset() clears all state', async () => {
    const store = useRunStore()
    mockCreateRun.mockResolvedValueOnce({ run_id: 'r1', status: 'pending' })
    await store.upload(makeFile('a.pdf', 'application/pdf'))

    store.reset()

    expect(store.runId).toBeNull()
    expect(store.status).toBeNull()
    expect(store.uploading).toBe(false)
    expect(store.uploadProgress).toBe(0)
    expect(store.error).toBeNull()
    expect(store.isActive).toBe(false)
  })

  it('uploading flag is true during upload and false after', async () => {
    const store = useRunStore()
    let flagDuringUpload = false

    mockCreateRun.mockImplementationOnce(async () => {
      flagDuringUpload = store.uploading
      return { run_id: 'r1', status: 'pending' as const }
    })

    await store.upload(makeFile('a.pdf', 'application/pdf'))
    expect(flagDuringUpload).toBe(true)
    expect(store.uploading).toBe(false)
  })
})
