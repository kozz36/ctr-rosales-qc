/**
 * GuiaDrillDown — vision-key gating on the [Acciones] > Reprocesar item
 * (REV-R34 / REV-R35).
 *
 * The Reprocesar menu item MUST be visible-but-disabled (NOT hidden) with an
 * explanatory tooltip when capabilities.visionEnabled === false, and enabled
 * when true. Gating is store-driven (reactive), never hardcoded.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import { useCapabilitiesStore } from '@/stores/capabilities'
import type { GuiaContributionResponse, ReconciliationRowResponse } from '@/api/types'

// GuiaDrillDown's inline-edit composable uses TanStack Query (useQueryClient);
// mock it so the component mounts without a QueryClient provider, matching the
// existing GuiaDrillDown.acciones.test.ts setup.
vi.mock('@/composables/useReconciliationApi', () => ({
  useGuiaLineEdit: () => ({
    mutate: vi.fn(),
    isPending: { value: false },
    variables: { value: null },
  }),
}))

vi.mock('@/api/client', () => ({
  reprocessGuia: vi.fn().mockResolvedValue({ recovered: true }),
}))

import GuiaDrillDown from '@/features/review/GuiaDrillDown.vue'

function makeGuia(overrides: Partial<GuiaContributionResponse> = {}): GuiaContributionResponse {
  return {
    guia_id: 'T009-0741770',
    source_pages: [45, 46],
    cantidad: '1250.5',
    unidad: 'KG',
    confidence: 0.92,
    identity_source: 'qr',
    year_inferred: false,
    fecha: '2026-05-28',
    fecha_divergence: false,
    divergence_reason: null,
    ...overrides,
  }
}

const TABLE_ROWS: ReconciliationRowResponse[] = []

const BASE_PROPS = {
  guias: [makeGuia()],
  runId: 'run-abc',
  materialCanonical: 'BARRA CORRUGADA 1/2',
  registro: '232',
  tableRows: TABLE_ROWS,
}

function reprocesarItem(wrapper: ReturnType<typeof mount>) {
  return wrapper.findAll('[role="menuitem"]').find((i) => i.text().includes('Reprocesar'))!
}

describe('GuiaDrillDown — vision gating on Reprocesar (REV-R34/R35)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('vision OFF → Reprocesar item is disabled but present in the DOM (REV-R34-S03)', async () => {
    const store = useCapabilitiesStore()
    store.visionEnabled = false
    const wrapper = mount(GuiaDrillDown, { props: BASE_PROPS })
    await wrapper.find('.guia-drill-down__acciones-trigger').trigger('click')
    const item = reprocesarItem(wrapper)
    expect(item.exists()).toBe(true)
    expect(item.attributes('disabled')).toBeDefined()
    // discoverable: a tooltip explains why it is disabled
    expect(item.attributes('title')).toMatch(/Ajustes/i)
  })

  it('vision ON → Reprocesar item is enabled, no vision tooltip (REV-R34-S02)', async () => {
    const store = useCapabilitiesStore()
    store.visionEnabled = true
    const wrapper = mount(GuiaDrillDown, { props: BASE_PROPS })
    await wrapper.find('.guia-drill-down__acciones-trigger').trigger('click')
    const item = reprocesarItem(wrapper)
    expect(item.attributes('disabled')).toBeUndefined()
    expect(item.attributes('title') ?? '').not.toMatch(/Ajustes/i)
  })

  it('store flip is reactive — disabled state follows visionEnabled (REV-R35-S02)', async () => {
    const store = useCapabilitiesStore()
    store.visionEnabled = false
    const wrapper = mount(GuiaDrillDown, { props: BASE_PROPS })
    await wrapper.find('.guia-drill-down__acciones-trigger').trigger('click')
    expect(reprocesarItem(wrapper).attributes('disabled')).toBeDefined()

    store.visionEnabled = true
    await wrapper.vm.$nextTick()
    expect(reprocesarItem(wrapper).attributes('disabled')).toBeUndefined()
  })
})
