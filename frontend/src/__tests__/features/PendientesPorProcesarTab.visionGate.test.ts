/**
 * PendientesPorProcesarTab — vision-key gating on the bulk "Procesar todos
 * con IA" button (REV-R34 / REV-R35).
 *
 * The per-Registro bulk button MUST be visible-but-disabled with an
 * explanatory tooltip when capabilities.visionEnabled === false, composing
 * with the existing in-flight disabling.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import { useCapabilitiesStore } from '@/stores/capabilities'
import type { ErroredGuiaResponse } from '@/api/types'

vi.mock('@/api/client', () => ({
  reprocessRegistroBatch: vi.fn(),
  getReprocessBatchStatus: vi.fn(),
  retryGuia: vi.fn(),
  reprocessGuia: vi.fn(),
}))

import PendientesPorProcesarTab from '@/features/review/PendientesPorProcesarTab.vue'

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

function bulkBtn(wrapper: ReturnType<typeof mount>) {
  return wrapper.find('.pendientes-tab__bulk-btn')
}

describe('PendientesPorProcesarTab — vision gating on bulk button (REV-R34/R35)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('vision OFF → bulk button present but disabled with tooltip (REV-R34-S01/S03)', () => {
    const store = useCapabilitiesStore()
    store.visionEnabled = false
    const wrapper = mount(PendientesPorProcesarTab, { props: BASE_PROPS })
    const btn = bulkBtn(wrapper)
    expect(btn.exists()).toBe(true)
    expect(btn.attributes('disabled')).toBeDefined()
    expect(btn.attributes('title')).toMatch(/Ajustes/i)
  })

  it('vision ON → bulk button enabled (REV-R34-S02)', () => {
    const store = useCapabilitiesStore()
    store.visionEnabled = true
    const wrapper = mount(PendientesPorProcesarTab, { props: BASE_PROPS })
    expect(bulkBtn(wrapper).attributes('disabled')).toBeUndefined()
  })

  it('store flip reactively toggles the bulk button (REV-R35-S02)', async () => {
    const store = useCapabilitiesStore()
    store.visionEnabled = false
    const wrapper = mount(PendientesPorProcesarTab, { props: BASE_PROPS })
    expect(bulkBtn(wrapper).attributes('disabled')).toBeDefined()
    store.visionEnabled = true
    await wrapper.vm.$nextTick()
    expect(bulkBtn(wrapper).attributes('disabled')).toBeUndefined()
  })
})
