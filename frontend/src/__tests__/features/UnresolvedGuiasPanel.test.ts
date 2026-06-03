/**
 * Tests for UnresolvedGuiasPanel.vue (S2.6 — REV-C04)
 *
 * Covers:
 * - 2 unresolved guías → both visible in panel
 * - Panel hidden when unresolvedGuias is empty
 * - "Assign to registro" button emits assignGuia with correct guia_id
 * - Panel is collapsible (header toggle)
 * - identity_source label: 'qr' → 'QR', 'ocr_fallback' → 'OCR fallback'
 * - source_pages displayed
 */

import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import UnresolvedGuiasPanel from '@/features/review/UnresolvedGuiasPanel.vue'
import type { UnresolvedGuiaResponse } from '@/api/types'

function makeUnresolved(overrides: Partial<UnresolvedGuiaResponse> = {}): UnresolvedGuiaResponse {
  return {
    guia_id: 'T009-0741770',
    identity_source: 'qr',
    source_pages: [4, 5],
    first_page: 4,
    ...overrides,
  }
}

describe('UnresolvedGuiasPanel', () => {
  it('renders nothing when unresolvedGuias is empty', () => {
    const wrapper = mount(UnresolvedGuiasPanel, {
      props: { unresolvedGuias: [] },
    })
    expect(wrapper.find('.unresolved-panel').exists()).toBe(false)
  })

  it('renders the panel when unresolvedGuias has entries', () => {
    const wrapper = mount(UnresolvedGuiasPanel, {
      props: { unresolvedGuias: [makeUnresolved()] },
    })
    expect(wrapper.find('.unresolved-panel').exists()).toBe(true)
  })

  it('renders all 2 unresolved guías (REV-C04 scenario)', () => {
    const guias = [
      makeUnresolved({ guia_id: 'T009-AAAA', source_pages: [2, 3] }),
      makeUnresolved({ guia_id: 'T009-BBBB', source_pages: [8] }),
    ]
    const wrapper = mount(UnresolvedGuiasPanel, {
      props: { unresolvedGuias: guias },
    })
    const items = wrapper.findAll('.unresolved-panel__item')
    expect(items).toHaveLength(2)
    expect(items[0].text()).toContain('T009-AAAA')
    expect(items[1].text()).toContain('T009-BBBB')
  })

  it('"Assign to registro" button emits assignGuia with correct guia_id', async () => {
    const guia = makeUnresolved({ guia_id: 'T009-TEST' })
    const wrapper = mount(UnresolvedGuiasPanel, {
      props: { unresolvedGuias: [guia] },
    })
    await wrapper.find('.unresolved-panel__assign-btn').trigger('click')
    expect(wrapper.emitted('assignGuia')).toBeTruthy()
    expect(wrapper.emitted('assignGuia')![0]).toEqual(['T009-TEST'])
  })

  it('emits correct guia_id for each entry when multiple guías present', async () => {
    const guias = [
      makeUnresolved({ guia_id: 'T009-FIRST' }),
      makeUnresolved({ guia_id: 'T009-SECOND' }),
    ]
    const wrapper = mount(UnresolvedGuiasPanel, {
      props: { unresolvedGuias: guias },
    })
    const buttons = wrapper.findAll('.unresolved-panel__assign-btn')
    await buttons[1].trigger('click')
    expect(wrapper.emitted('assignGuia')![0]).toEqual(['T009-SECOND'])
  })

  it('panel is open by default (collapsible starts expanded)', () => {
    const wrapper = mount(UnresolvedGuiasPanel, {
      props: { unresolvedGuias: [makeUnresolved()] },
    })
    const header = wrapper.find('.unresolved-panel__header')
    expect(header.attributes('aria-expanded')).toBe('true')
    expect(wrapper.find('.unresolved-panel__body').isVisible()).toBe(true)
  })

  it('clicking header collapses the panel', async () => {
    const wrapper = mount(UnresolvedGuiasPanel, {
      props: { unresolvedGuias: [makeUnresolved()] },
    })
    await wrapper.find('.unresolved-panel__header').trigger('click')
    expect(wrapper.find('.unresolved-panel__header').attributes('aria-expanded')).toBe('false')
  })

  it('shows QR as identity_source label for qr guías', () => {
    const wrapper = mount(UnresolvedGuiasPanel, {
      props: { unresolvedGuias: [makeUnresolved({ identity_source: 'qr' })] },
    })
    expect(wrapper.find('.unresolved-panel__item-source').text()).toBe('QR')
  })

  it('shows OCR fallback as identity_source label for ocr_fallback guías', () => {
    const wrapper = mount(UnresolvedGuiasPanel, {
      props: { unresolvedGuias: [makeUnresolved({ identity_source: 'ocr_fallback' })] },
    })
    expect(wrapper.find('.unresolved-panel__item-source').text()).toBe('OCR fallback')
  })

  it('displays first_page when set (REV-C06: first_page is authoritative)', () => {
    const wrapper = mount(UnresolvedGuiasPanel, {
      props: { unresolvedGuias: [makeUnresolved({ first_page: 7, source_pages: [7, 8] })] },
    })
    expect(wrapper.find('.unresolved-panel__item-pages').text()).toBe('Pág. 7')
  })

  it('displays source_pages when first_page is null (REV-C06: null fallback)', () => {
    const wrapper = mount(UnresolvedGuiasPanel, {
      props: { unresolvedGuias: [makeUnresolved({ first_page: null, source_pages: [12, 13, 14] })] },
    })
    expect(wrapper.find('.unresolved-panel__item-pages').text()).toContain('12')
    expect(wrapper.find('.unresolved-panel__item-pages').text()).toContain('13')
  })

  it('shows — when first_page is null and source_pages is empty (REV-C06: empty fallback)', () => {
    const wrapper = mount(UnresolvedGuiasPanel, {
      props: { unresolvedGuias: [makeUnresolved({ first_page: null, source_pages: [] })] },
    })
    expect(wrapper.find('.unresolved-panel__item-pages').text()).toBe('—')
  })

  it('displays first_page=0 as valid page (not treated as absent)', () => {
    const wrapper = mount(UnresolvedGuiasPanel, {
      props: { unresolvedGuias: [makeUnresolved({ first_page: 0, source_pages: [0, 1] })] },
    })
    expect(wrapper.find('.unresolved-panel__item-pages').text()).toBe('Pág. 0')
  })

  it('shows page range as fallback when guia_id is empty string', () => {
    const wrapper = mount(UnresolvedGuiasPanel, {
      props: { unresolvedGuias: [makeUnresolved({ guia_id: '', source_pages: [5, 6] })] },
    })
    expect(wrapper.find('.unresolved-panel__item-id').text()).toContain('5')
  })

  it('count badge shows the number of unresolved guías', () => {
    const guias = [makeUnresolved(), makeUnresolved({ guia_id: 'T009-B' })]
    const wrapper = mount(UnresolvedGuiasPanel, {
      props: { unresolvedGuias: guias },
    })
    expect(wrapper.find('.unresolved-panel__count').text()).toBe('2')
  })
})
