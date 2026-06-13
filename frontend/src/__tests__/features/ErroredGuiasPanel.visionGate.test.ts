/**
 * ErroredGuiasPanel — vision-key gating on the per-guía "Reprocesar con IA"
 * button (REV-R34 / REV-R35).
 *
 * The reprocess button (shown when retry_attempted=true) MUST be
 * visible-but-disabled with an explanatory tooltip when
 * capabilities.visionEnabled === false. Its existing in-flight disabling
 * (reprocessingIds) MUST still compose with the vision gate.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import { useCapabilitiesStore } from '@/stores/capabilities'
import type { ErroredGuiaResponse } from '@/api/types'

vi.mock('@/api/client', () => ({
  retryGuia: vi.fn(),
  reprocessGuia: vi.fn().mockResolvedValue({ recovered: true, errored_guias: [] }),
}))

import ErroredGuiasPanel from '@/features/review/ErroredGuiasPanel.vue'

function makeErrored(overrides: Partial<ErroredGuiaResponse> = {}): ErroredGuiaResponse {
  return {
    registro: '232',
    guia_id: 'T009-0741770',
    source_pages: [45],
    retry_attempted: true,
    ...overrides,
  }
}

const BASE_PROPS = {
  erroredGuias: [makeErrored()],
  runId: 'run-abc',
}

function reprocessBtn(wrapper: ReturnType<typeof mount>) {
  return wrapper.find('.errored-panel__reprocess-btn')
}

describe('ErroredGuiasPanel — vision gating on Reprocesar con IA (REV-R34/R35)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('vision OFF → reprocess button present but disabled with tooltip (REV-R34-S01/S03)', () => {
    const store = useCapabilitiesStore()
    store.visionEnabled = false
    const wrapper = mount(ErroredGuiasPanel, { props: BASE_PROPS })
    const btn = reprocessBtn(wrapper)
    expect(btn.exists()).toBe(true)
    expect(btn.attributes('disabled')).toBeDefined()
    expect(btn.attributes('title')).toMatch(/Ajustes/i)
  })

  it('vision ON → reprocess button enabled (REV-R34-S02)', () => {
    const store = useCapabilitiesStore()
    store.visionEnabled = true
    const wrapper = mount(ErroredGuiasPanel, { props: BASE_PROPS })
    expect(reprocessBtn(wrapper).attributes('disabled')).toBeUndefined()
  })

  it('store flip reactively enables/disables the button (REV-R35-S02)', async () => {
    const store = useCapabilitiesStore()
    store.visionEnabled = false
    const wrapper = mount(ErroredGuiasPanel, { props: BASE_PROPS })
    expect(reprocessBtn(wrapper).attributes('disabled')).toBeDefined()
    store.visionEnabled = true
    await wrapper.vm.$nextTick()
    expect(reprocessBtn(wrapper).attributes('disabled')).toBeUndefined()
  })
})
