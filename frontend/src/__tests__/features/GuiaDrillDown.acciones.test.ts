/**
 * F4 — [Acciones] menu + Corregir-manual dialog (REV-R24 / REV-R25, D8 / D9).
 *
 * Covers:
 * - The single [Reasignar] button is replaced by an [Acciones] disclosure menu.
 * - The menu (role=menu) opens with three items: Reasignar, Reprocesar, Corregir manual.
 * - Reasignar emits `reassign` (existing flow, unchanged).
 * - Reprocesar calls the existing per-guía `reprocessGuia` client.
 * - Corregir manual opens a dialog whose material dropdown lists ONLY that registro's
 *   declared materials (sourced from tableRows.filter(r => r.registro === guia.registro)).
 * - Submitting the dialog calls editGuiaLine (useGuiaLineEdit) with
 *   assign_material_canonical + cantidad.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import type { GuiaContributionResponse, ReconciliationRowResponse } from '@/api/types'

const mockMutate = vi.fn()
const mockIsPending = { value: false }
const mockVariables = { value: null as null | { guiaId: string } }

vi.mock('@/composables/useReconciliationApi', () => ({
  useGuiaLineEdit: () => ({
    mutate: mockMutate,
    isPending: mockIsPending,
    variables: mockVariables,
  }),
}))

const mockReprocessGuia = vi.fn().mockResolvedValue({ recovered: true })
vi.mock('@/api/client', () => ({
  reprocessGuia: (...args: unknown[]) => mockReprocessGuia(...args),
}))

vi.mock('@/features/review/ConfidenceBadge.vue', () => ({
  default: {
    name: 'ConfidenceBadge',
    template: '<span class="stub-confidence-badge" :data-value="value" />',
    props: ['value', 'compact'],
  },
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

function makeRow(overrides: Partial<ReconciliationRowResponse> = {}): ReconciliationRowResponse {
  return {
    row_id: 'r1',
    registro: '232',
    fecha: '2026-05-28',
    material_canonical: 'BARRA CORRUGADA 1/2',
    unidad: 'KG',
    declared_qty: '500',
    summed_qty: '500',
    delta: '0',
    status: 'MATCH',
    source_pages: [45],
    min_confidence: 0.9,
    requires_review: false,
    guias: [],
    any_year_inferred: false,
    has_fecha_divergence: false,
    ...overrides,
  } as ReconciliationRowResponse
}

// Two registros' rows — the dialog must show only registro 232's materials.
const TABLE_ROWS: ReconciliationRowResponse[] = [
  makeRow({ row_id: 'r1', registro: '232', material_canonical: 'BARRA CORRUGADA 1/2', unidad: 'KG' }),
  makeRow({ row_id: 'r2', registro: '232', material_canonical: 'BARRA CORRUGADA 3/4', unidad: 'KG' }),
  makeRow({ row_id: 'r3', registro: '230', material_canonical: 'ALAMBRE MQ #16', unidad: 'KG' }),
]

const BASE_PROPS = {
  guias: [makeGuia()],
  runId: 'run-abc',
  materialCanonical: 'BARRA CORRUGADA 1/2',
  registro: '232',
  tableRows: TABLE_ROWS,
}

describe('GuiaDrillDown — F4 Acciones menu (REV-R24)', () => {
  beforeEach(() => {
    mockMutate.mockReset()
    mockReprocessGuia.mockClear()
    mockIsPending.value = false
    mockVariables.value = null
  })

  it('renders an [Acciones] trigger button (no bare [Reasignar] button)', () => {
    const wrapper = mount(GuiaDrillDown, { props: BASE_PROPS })
    const trigger = wrapper.find('.guia-drill-down__acciones-trigger')
    expect(trigger.exists()).toBe(true)
    expect(trigger.attributes('aria-haspopup')).toBe('menu')
  })

  it('opening [Acciones] shows a role=menu with three items', async () => {
    const wrapper = mount(GuiaDrillDown, { props: BASE_PROPS })
    await wrapper.find('.guia-drill-down__acciones-trigger').trigger('click')
    const menu = wrapper.find('[role="menu"]')
    expect(menu.exists()).toBe(true)
    const items = wrapper.findAll('[role="menuitem"]')
    expect(items.length).toBe(3)
    const labels = items.map((i) => i.text())
    expect(labels.some((l) => l.includes('Reasignar'))).toBe(true)
    expect(labels.some((l) => l.includes('Reprocesar'))).toBe(true)
    expect(labels.some((l) => l.includes('Corregir manual'))).toBe(true)
  })

  it('Reasignar menu item emits reassign(guia_id) (existing flow)', async () => {
    const wrapper = mount(GuiaDrillDown, { props: BASE_PROPS })
    await wrapper.find('.guia-drill-down__acciones-trigger').trigger('click')
    const reasignar = wrapper.findAll('[role="menuitem"]').find((i) => i.text().includes('Reasignar'))!
    await reasignar.trigger('click')
    expect(wrapper.emitted('reassign')).toBeTruthy()
    expect(wrapper.emitted('reassign')![0]).toEqual(['T009-0741770'])
  })

  it('Reprocesar menu item calls reprocessGuia(runId, guia_id)', async () => {
    const wrapper = mount(GuiaDrillDown, { props: BASE_PROPS })
    await wrapper.find('.guia-drill-down__acciones-trigger').trigger('click')
    const reprocesar = wrapper.findAll('[role="menuitem"]').find((i) => i.text().includes('Reprocesar'))!
    await reprocesar.trigger('click')
    expect(mockReprocessGuia).toHaveBeenCalledOnce()
    expect(mockReprocessGuia).toHaveBeenCalledWith('run-abc', 'T009-0741770')
  })

  it('the [Acciones] menu is present on every guía row', async () => {
    const guias = [makeGuia({ guia_id: 'T009-A' }), makeGuia({ guia_id: 'T073-B' })]
    const wrapper = mount(GuiaDrillDown, { props: { ...BASE_PROPS, guias } })
    expect(wrapper.findAll('.guia-drill-down__acciones-trigger').length).toBe(2)
  })
})

describe('GuiaDrillDown — F4 Corregir manual dialog (REV-R25)', () => {
  beforeEach(() => {
    mockMutate.mockReset()
    mockReprocessGuia.mockClear()
  })

  async function openCorregir(wrapper: ReturnType<typeof mount>) {
    await wrapper.find('.guia-drill-down__acciones-trigger').trigger('click')
    const corregir = wrapper.findAll('[role="menuitem"]').find((i) => i.text().includes('Corregir manual'))!
    await corregir.trigger('click')
  }

  // The dialog is rendered via <Teleport to="body">, so it lives in document.body
  // (not inside the wrapper subtree). Query the document for teleported content.
  function setNativeValue(selector: string, value: string): void {
    const el = document.querySelector(selector) as HTMLInputElement | HTMLSelectElement | null
    if (!el) throw new Error(`element not found: ${selector}`)
    el.value = value
    el.dispatchEvent(new Event('input', { bubbles: true }))
    el.dispatchEvent(new Event('change', { bubbles: true }))
  }

  it('opens a dialog whose dropdown lists ONLY that registro declared materials (REV-R25-S01)', async () => {
    const wrapper = mount(GuiaDrillDown, { props: BASE_PROPS, attachTo: document.body })
    await openCorregir(wrapper)
    const dialog = document.querySelector('[role="dialog"]')
    expect(dialog).not.toBeNull()
    const options = Array.from(document.querySelectorAll('.guia-correct__material-option'))
    const texts = options.map((o) => o.textContent ?? '')
    expect(texts.some((t) => t.includes('BARRA CORRUGADA 1/2'))).toBe(true)
    expect(texts.some((t) => t.includes('BARRA CORRUGADA 3/4'))).toBe(true)
    // registro 230's material MUST NOT appear
    expect(texts.some((t) => t.includes('ALAMBRE MQ #16'))).toBe(false)
    wrapper.unmount()
  })

  it('submitting calls editGuiaLine with assign_material_canonical + cantidad (REV-R25-S02)', async () => {
    const wrapper = mount(GuiaDrillDown, { props: BASE_PROPS, attachTo: document.body })
    await openCorregir(wrapper)
    setNativeValue('.guia-correct__material-select', 'BARRA CORRUGADA 3/4|KG')
    setNativeValue('.guia-correct__cantidad-input', '500')
    await wrapper.vm.$nextTick()
    ;(document.querySelector('.guia-correct__submit') as HTMLButtonElement).click()
    await wrapper.vm.$nextTick()

    expect(mockMutate).toHaveBeenCalledOnce()
    const arg = mockMutate.mock.calls[0][0] as {
      guiaId: string
      body: { assign_material_canonical: string; cantidad: number; material_canonical: string | null }
    }
    expect(arg.guiaId).toBe('T009-0741770')
    expect(arg.body.assign_material_canonical).toBe('BARRA CORRUGADA 3/4')
    expect(arg.body.cantidad).toBe(500)
    wrapper.unmount()
  })

  it('does not submit when no material is chosen (guard)', async () => {
    const wrapper = mount(GuiaDrillDown, { props: BASE_PROPS, attachTo: document.body })
    await openCorregir(wrapper)
    setNativeValue('.guia-correct__cantidad-input', '500')
    await wrapper.vm.$nextTick()
    ;(document.querySelector('.guia-correct__submit') as HTMLButtonElement).click()
    await wrapper.vm.$nextTick()
    expect(mockMutate).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
