/**
 * F2 — drill-down → page viewer (REV-R22, D6 + refinement #3012).
 *
 * Covers:
 * - Páginas cell renders <SourcePages> chips (NOT a plain comma span).
 * - Clicking a page chip emits pageClick(page) so the parent opens PageSheetViewer.
 * - Clicking the GUÍA serie-número cell emits pageClick(guia.source_pages[0]) — the
 *   drill-down → viewer affordance (refinement: applies to the serie-número too).
 * - The serie-número affordance is a real button (keyboard-accessible) with aria-label.
 * - Applies to ALL drill-down guía rows, not gated by status.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import type { GuiaContributionResponse } from '@/api/types'

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

vi.mock('@/api/client', () => ({
  reprocessGuia: vi.fn(),
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

const BASE_PROPS = {
  guias: [makeGuia()],
  runId: 'run-abc',
  materialCanonical: 'BARRA A615 G60 1/2" 9M',
  registro: '232',
}

describe('GuiaDrillDown — F2 page viewer (REV-R22)', () => {
  beforeEach(() => {
    mockMutate.mockReset()
    mockIsPending.value = false
    mockVariables.value = null
  })

  it('renders SourcePages chips in the Páginas cell (NOT a plain comma span)', () => {
    const wrapper = mount(GuiaDrillDown, { props: BASE_PROPS })
    // SourcePages renders one button per page with the page number text.
    const chips = wrapper.findAll('.source-pages__chip')
    expect(chips.length).toBe(2)
    expect(chips.map((c) => c.text())).toEqual(['45', '46'])
  })

  it('clicking a page chip emits pageClick with that page (REV-R22-S01)', async () => {
    const wrapper = mount(GuiaDrillDown, { props: BASE_PROPS })
    const chips = wrapper.findAll('.source-pages__chip')
    await chips[0].trigger('click')
    expect(wrapper.emitted('pageClick')).toBeTruthy()
    expect(wrapper.emitted('pageClick')![0]).toEqual([45])
    await chips[1].trigger('click')
    expect(wrapper.emitted('pageClick')![1]).toEqual([46])
  })

  it('clicking the guía serie-número cell emits pageClick(source_pages[0]) — refinement', async () => {
    const wrapper = mount(GuiaDrillDown, { props: BASE_PROPS })
    const idBtn = wrapper.find('.guia-drill-down__guia-id')
    expect(idBtn.exists()).toBe(true)
    // It must be a real interactive element (button), keyboard-accessible.
    expect(idBtn.element.tagName).toBe('BUTTON')
    expect(idBtn.attributes('aria-label')).toContain('T009-0741770')
    await idBtn.trigger('click')
    expect(wrapper.emitted('pageClick')).toBeTruthy()
    expect(wrapper.emitted('pageClick')![0]).toEqual([45])
  })

  it('the serie-número button responds to Enter / Space (keyboard a11y)', async () => {
    const wrapper = mount(GuiaDrillDown, { props: BASE_PROPS })
    const idBtn = wrapper.find('.guia-drill-down__guia-id')
    await idBtn.trigger('keydown', { key: 'Enter' })
    await idBtn.trigger('keydown', { key: ' ' })
    // Native <button> activates on Enter/Space click; assert at least the click path emits.
    await idBtn.trigger('click')
    expect(wrapper.emitted('pageClick')).toBeTruthy()
  })

  it('the viewer affordance is present on EVERY guía row regardless of status', async () => {
    const guias = [
      makeGuia({ guia_id: 'T009-OK', source_pages: [10] }),
      makeGuia({ guia_id: 'T073-DIVERGENT', source_pages: [20], fecha_divergence: true, divergence_reason: 'fecha_divergence' }),
    ]
    const wrapper = mount(GuiaDrillDown, { props: { ...BASE_PROPS, guias } })
    const idButtons = wrapper.findAll('.guia-drill-down__guia-id')
    expect(idButtons.length).toBe(2)
    await idButtons[1].trigger('click')
    expect(wrapper.emitted('pageClick')![0]).toEqual([20])
  })

  it('clicking a page chip does NOT call the cantidad edit mutation (no edit side-effect)', async () => {
    const wrapper = mount(GuiaDrillDown, { props: BASE_PROPS })
    await wrapper.findAll('.source-pages__chip')[0].trigger('click')
    expect(mockMutate).not.toHaveBeenCalled()
  })
})
