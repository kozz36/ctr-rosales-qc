/**
 * Capabilities store — unit tests (CAP-002, REV-R35).
 *
 * The store fetches GET /capabilities ONCE at app mount and exposes
 * `visionEnabled` / `sunatEnabled` as reactive state. Fail-safe contract
 * (REV-R35-S01): defaults are FALSE while loading and STAY false on fetch
 * error — vision-gated controls must never become enabled by accident.
 *
 * The API client is mocked so no real HTTP is issued.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useCapabilitiesStore } from '@/stores/capabilities'

vi.mock('@/api/client', () => ({
  getCapabilities: vi.fn(),
}))

import { getCapabilities } from '@/api/client'
const mockGetCapabilities = vi.mocked(getCapabilities)

describe('useCapabilitiesStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGetCapabilities.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('defaults to disabled (fail-safe) before any fetch (REV-R35-S01)', () => {
    const store = useCapabilitiesStore()
    expect(store.visionEnabled).toBe(false)
    expect(store.sunatEnabled).toBe(false)
    expect(store.loaded).toBe(false)
  })

  it('fetch() with vision_enabled=true flips visionEnabled to true (CAP-002-S01)', async () => {
    mockGetCapabilities.mockResolvedValue({ vision_enabled: true, sunat_enabled: false })
    const store = useCapabilitiesStore()
    await store.fetch()
    expect(store.visionEnabled).toBe(true)
    expect(store.sunatEnabled).toBe(false)
    expect(store.loaded).toBe(true)
  })

  it('fetch() with vision_enabled=false keeps visionEnabled false (CAP-002-S01)', async () => {
    mockGetCapabilities.mockResolvedValue({ vision_enabled: false, sunat_enabled: true })
    const store = useCapabilitiesStore()
    await store.fetch()
    expect(store.visionEnabled).toBe(false)
    expect(store.sunatEnabled).toBe(true)
    expect(store.loaded).toBe(true)
  })

  it('fetch() failure keeps the safe disabled default (REV-R35-S01 fail-safe)', async () => {
    mockGetCapabilities.mockRejectedValue(new Error('network down'))
    const store = useCapabilitiesStore()
    await store.fetch()
    expect(store.visionEnabled).toBe(false)
    expect(store.sunatEnabled).toBe(false)
    // loaded stays false so a later retry is allowed.
    expect(store.loaded).toBe(false)
  })

  it('fetch() is issued only once even when called repeatedly (CAP-002-S02 single fetch)', async () => {
    mockGetCapabilities.mockResolvedValue({ vision_enabled: true, sunat_enabled: true })
    const store = useCapabilitiesStore()
    await store.fetch()
    await store.fetch()
    await store.fetch()
    expect(mockGetCapabilities).toHaveBeenCalledOnce()
  })

  it('a failed fetch does NOT block a later successful retry (single-fetch is loaded-gated)', async () => {
    mockGetCapabilities.mockRejectedValueOnce(new Error('boom'))
    const store = useCapabilitiesStore()
    await store.fetch()
    expect(store.visionEnabled).toBe(false)

    mockGetCapabilities.mockResolvedValueOnce({ vision_enabled: true, sunat_enabled: true })
    await store.fetch()
    expect(store.visionEnabled).toBe(true)
    expect(store.loaded).toBe(true)
  })
})
