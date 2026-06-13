/**
 * App.vue — fetches capabilities once on mount (CAP-002 / REV-R35-S01).
 *
 * The capabilities store fetch must fire at app startup so the vision-key gating
 * resolves before the operator reaches the review surfaces. RouterLink/View and
 * the hamburger are stubbed — this test only asserts the onMounted fetch.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import { useCapabilitiesStore } from '@/stores/capabilities'

vi.mock('@/api/client', () => ({
  createRun: vi.fn(),
  getCapabilities: vi.fn().mockResolvedValue({ vision_enabled: false, sunat_enabled: true }),
}))

import App from '@/app/App.vue'

describe('App.vue — capabilities bootstrap', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('calls capabilities.fetch() on mount (CAP-002)', async () => {
    const store = useCapabilitiesStore()
    const fetchSpy = vi.spyOn(store, 'fetch')

    mount(App, {
      global: {
        stubs: {
          RouterLink: true,
          RouterView: true,
          RunHistoryMenu: true,
        },
      },
    })
    await Promise.resolve()

    expect(fetchSpy).toHaveBeenCalledOnce()
  })
})
