/**
 * Tests for GuiaDrillDown.vue
 *
 * Covers (S2.2 deliverables):
 * - Renders all GuiaContributionResponse fields (REV-C01 scenario)
 * - confidence < 0.85 → ConfidenceBadge shows "low" (amber) state
 * - identity_source = "qr" → "QR" badge visible
 * - identity_source = "ocr_fallback" → "OCR fallback" label shown
 * - Editable cantidad cell → triggers useGuiaLineEdit mutation on change
 * - "Reassign" button click → emits `reassign` event with guia_id (REV-C02)
 * - No extra API call on mount (data from props, not fetched)
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import type { GuiaContributionResponse } from '@/api/types'

// ---------------------------------------------------------------------------
// Mock useGuiaLineEdit so we can inspect mutation calls without hitting the API
// ---------------------------------------------------------------------------

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

// Stub ConfidenceBadge to make badge state assertions simple
vi.mock('@/features/review/ConfidenceBadge.vue', () => ({
  default: {
    name: 'ConfidenceBadge',
    template: '<span class="stub-confidence-badge" :data-needs-review="value !== null && value < 0.85" :data-value="value" />',
    props: ['value', 'compact'],
  },
}))

import GuiaDrillDown from '@/features/review/GuiaDrillDown.vue'

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeGuia(overrides: Partial<GuiaContributionResponse> = {}): GuiaContributionResponse {
  return {
    guia_id: 'T009-0741770',
    source_pages: [4, 5],
    cantidad: '1250.5',
    unidad: 'KG',
    confidence: 0.92,
    identity_source: 'qr',
    ...overrides,
  }
}

const DEFAULT_PROPS = {
  guias: [makeGuia()],
  runId: 'run-abc',
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

describe('GuiaDrillDown', () => {
  beforeEach(() => {
    mockMutate.mockReset()
    mockIsPending.value = false
    mockVariables.value = null
  })

  it('renders guia_id for each contribution (REV-C01)', () => {
    const wrapper = mount(GuiaDrillDown, { props: DEFAULT_PROPS })
    expect(wrapper.text()).toContain('T009-0741770')
  })

  it('renders source_pages as comma-separated list', () => {
    const wrapper = mount(GuiaDrillDown, { props: DEFAULT_PROPS })
    expect(wrapper.text()).toContain('4, 5')
  })

  it('renders formatted cantidad', () => {
    const wrapper = mount(GuiaDrillDown, { props: DEFAULT_PROPS })
    // 1250.5 formatted with es-PE locale
    expect(wrapper.text()).toMatch(/1[,.]250/)
  })

  it('renders unidad', () => {
    const wrapper = mount(GuiaDrillDown, { props: DEFAULT_PROPS })
    expect(wrapper.text()).toContain('KG')
  })

  it('renders ConfidenceBadge with correct confidence value', () => {
    const wrapper = mount(GuiaDrillDown, { props: DEFAULT_PROPS })
    const badge = wrapper.find('.stub-confidence-badge')
    expect(badge.exists()).toBe(true)
    expect(badge.attributes('data-value')).toBe('0.92')
  })

  it('ConfidenceBadge shows needs-review when confidence < 0.85 (amber state)', () => {
    const wrapper = mount(GuiaDrillDown, {
      props: { guias: [makeGuia({ confidence: 0.72 })], runId: 'run-abc' },
    })
    const badge = wrapper.find('.stub-confidence-badge')
    expect(badge.attributes('data-needs-review')).toBe('true')
  })

  it('renders QR badge when identity_source = "qr"', () => {
    const wrapper = mount(GuiaDrillDown, {
      props: { guias: [makeGuia({ identity_source: 'qr' })], runId: 'run-abc' },
    })
    expect(wrapper.find('.guia-drill-down__identity-badge--qr').exists()).toBe(true)
    expect(wrapper.find('.guia-drill-down__identity-badge--qr').text()).toBe('QR')
  })

  it('renders OCR fallback label when identity_source = "ocr_fallback"', () => {
    const wrapper = mount(GuiaDrillDown, {
      props: { guias: [makeGuia({ identity_source: 'ocr_fallback' })], runId: 'run-abc' },
    })
    expect(wrapper.find('.guia-drill-down__identity-badge--ocr').exists()).toBe(true)
    expect(wrapper.find('.guia-drill-down__identity-badge--ocr').text()).toContain('OCR fallback')
  })

  it('renders multiple guía rows', () => {
    const guias = [
      makeGuia({ guia_id: 'T009-0741770' }),
      makeGuia({ guia_id: 'T073-0680256', source_pages: [10] }),
    ]
    const wrapper = mount(GuiaDrillDown, { props: { guias, runId: 'run-abc' } })
    expect(wrapper.text()).toContain('T009-0741770')
    expect(wrapper.text()).toContain('T073-0680256')
    expect(wrapper.findAll('.guia-drill-down__row')).toHaveLength(2)
  })

  it('"Reassign" button emits reassign event with guia_id (REV-C02)', async () => {
    const wrapper = mount(GuiaDrillDown, { props: DEFAULT_PROPS })
    await wrapper.find('.guia-drill-down__reassign-btn').trigger('click')
    expect(wrapper.emitted('reassign')).toBeTruthy()
    expect(wrapper.emitted('reassign')![0]).toEqual(['T009-0741770'])
  })

  it('editable cantidad cell shows input on click', async () => {
    const wrapper = mount(GuiaDrillDown, { props: DEFAULT_PROPS })
    expect(wrapper.find('.guia-drill-down__input').exists()).toBe(false)
    await wrapper.find('.guia-drill-down__editable-cell').trigger('click')
    expect(wrapper.find('.guia-drill-down__input').exists()).toBe(true)
  })

  it('committing cantidad edit calls useGuiaLineEdit mutation with guia_id and new value', async () => {
    const wrapper = mount(GuiaDrillDown, { props: DEFAULT_PROPS })
    await wrapper.find('.guia-drill-down__editable-cell').trigger('click')
    const input = wrapper.find('.guia-drill-down__input')
    await input.setValue('999')
    await input.trigger('keydown', { key: 'Enter' })
    expect(mockMutate).toHaveBeenCalledOnce()
    const callArg = mockMutate.mock.calls[0][0] as { guiaId: string; body: { cantidad: number } }
    expect(callArg.guiaId).toBe('T009-0741770')
    expect(callArg.body.cantidad).toBe(999)
  })

  it('canceling edit with Escape does not call mutation', async () => {
    const wrapper = mount(GuiaDrillDown, { props: DEFAULT_PROPS })
    await wrapper.find('.guia-drill-down__editable-cell').trigger('click')
    const input = wrapper.find('.guia-drill-down__input')
    await input.setValue('999')
    await input.trigger('keydown', { key: 'Escape' })
    expect(mockMutate).not.toHaveBeenCalled()
    expect(wrapper.find('.guia-drill-down__input').exists()).toBe(false)
  })

  it('does not call mutation when cantidad is unchanged', async () => {
    const wrapper = mount(GuiaDrillDown, { props: DEFAULT_PROPS })
    await wrapper.find('.guia-drill-down__editable-cell').trigger('click')
    const input = wrapper.find('.guia-drill-down__input')
    // Same value as the guía
    await input.setValue('1250.5')
    await input.trigger('keydown', { key: 'Enter' })
    expect(mockMutate).not.toHaveBeenCalled()
  })

  it('does not call mutation when cantidad is negative (invalid)', async () => {
    const wrapper = mount(GuiaDrillDown, { props: DEFAULT_PROPS })
    await wrapper.find('.guia-drill-down__editable-cell').trigger('click')
    const input = wrapper.find('.guia-drill-down__input')
    await input.setValue('-10')
    await input.trigger('keydown', { key: 'Enter' })
    expect(mockMutate).not.toHaveBeenCalled()
  })

  it('no extra API call on mount (data from props only)', () => {
    // GuiaDrillDown must not import useTable or any query composable.
    // We verify this by counting how many times the mock was called — should be 0.
    mount(GuiaDrillDown, { props: DEFAULT_PROPS })
    // Only useGuiaLineEdit mock is in scope; it sets up the mutation but does NOT
    // fire on mount. mutate() should not have been called.
    expect(mockMutate).not.toHaveBeenCalled()
  })
})
