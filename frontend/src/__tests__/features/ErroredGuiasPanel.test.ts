/**
 * Tests for ErroredGuiasPanel.vue (REV-E05)
 *
 * TDD RED phase — tests fail until ErroredGuiasPanel.vue is created.
 *
 * Covers:
 * - Empty state → panel hidden (v-if length > 0)
 * - 2 entries → panel visible and entries rendered
 * - Per-Registro "Error en páginas X" text shown
 * - NO REINTENTAR / Reprocesar buttons (read-only slice)
 * - Collapsible header toggle
 * - source_pages displayed
 */

import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ErroredGuiasPanel from '@/features/review/ErroredGuiasPanel.vue'
import type { ErroredGuiaResponse } from '@/api/types'

function makeErrored(overrides: Partial<ErroredGuiaResponse> = {}): ErroredGuiaResponse {
  return {
    registro: 'R001',
    guia_id: 'T009-0001',
    source_pages: [5, 6],
    ...overrides,
  }
}

describe('ErroredGuiasPanel', () => {
  it('renders nothing when erroredGuias is empty', () => {
    const wrapper = mount(ErroredGuiasPanel, {
      props: { erroredGuias: [] },
    })
    expect(wrapper.find('.errored-panel').exists()).toBe(false)
  })

  it('renders the panel when erroredGuias has entries', () => {
    const wrapper = mount(ErroredGuiasPanel, {
      props: { erroredGuias: [makeErrored()] },
    })
    expect(wrapper.find('.errored-panel').exists()).toBe(true)
  })

  it('renders all 2 errored guías', () => {
    const guias = [
      makeErrored({ guia_id: 'T009-0001', source_pages: [5, 6] }),
      makeErrored({ guia_id: 'T009-0002', source_pages: [11], registro: 'R002' }),
    ]
    const wrapper = mount(ErroredGuiasPanel, {
      props: { erroredGuias: guias },
    })
    const items = wrapper.findAll('.errored-panel__item')
    expect(items).toHaveLength(2)
  })

  it('displays guia_id for each entry', () => {
    const wrapper = mount(ErroredGuiasPanel, {
      props: { erroredGuias: [makeErrored({ guia_id: 'T009-AABB' })] },
    })
    expect(wrapper.text()).toContain('T009-AABB')
  })

  it('displays source_pages for each entry', () => {
    const wrapper = mount(ErroredGuiasPanel, {
      props: { erroredGuias: [makeErrored({ source_pages: [7, 8] })] },
    })
    // Should show page numbers in some form
    expect(wrapper.text()).toContain('7')
    expect(wrapper.text()).toContain('8')
  })

  it('does NOT render Reprocesar button when retry_attempted=false (PR#3 gate)', () => {
    // Reprocesar con IA is gated on retry_attempted=true (REINTENTAR must have been tried first).
    const wrapper = mount(ErroredGuiasPanel, {
      props: { erroredGuias: [makeErrored()], runId: 'run-123' },
    })
    const buttons = wrapper.findAll('button').filter(
      (b) => b.text().toLowerCase().includes('reprocesar'),
    )
    expect(buttons).toHaveLength(0)
  })

  it('renders REINTENTAR button per guía item (PR#2 scope)', () => {
    // PR#1 was read-only; PR#2 adds REINTENTAR button.
    const wrapper = mount(ErroredGuiasPanel, {
      props: { erroredGuias: [makeErrored(), makeErrored({ guia_id: 'T009-0002' })], runId: 'run-123' },
    })
    const reintentarButtons = wrapper.findAll('button').filter(
      (b) => b.text().toUpperCase().includes('REINTENTAR'),
    )
    expect(reintentarButtons).toHaveLength(2)
  })

  it('has collapsible header button plus one REINTENTAR per item', () => {
    const wrapper = mount(ErroredGuiasPanel, {
      props: { erroredGuias: [makeErrored(), makeErrored({ guia_id: 'T009-0002' })], runId: 'run-123' },
    })
    // 1 header + 2 REINTENTAR = 3 total
    const buttons = wrapper.findAll('button')
    expect(buttons.length).toBeGreaterThanOrEqual(3)
    expect(buttons[0].classes()).toContain('errored-panel__header')
  })

  it('panel is open by default (collapsible starts expanded)', () => {
    const wrapper = mount(ErroredGuiasPanel, {
      props: { erroredGuias: [makeErrored()] },
    })
    const header = wrapper.find('.errored-panel__header')
    expect(header.attributes('aria-expanded')).toBe('true')
    expect(wrapper.find('.errored-panel__body').isVisible()).toBe(true)
  })

  it('clicking header collapses the panel', async () => {
    const wrapper = mount(ErroredGuiasPanel, {
      props: { erroredGuias: [makeErrored()] },
    })
    await wrapper.find('.errored-panel__header').trigger('click')
    expect(wrapper.find('.errored-panel__header').attributes('aria-expanded')).toBe('false')
  })

  it('count badge shows the number of errored guías', () => {
    const guias = [makeErrored(), makeErrored({ guia_id: 'T009-0002' })]
    const wrapper = mount(ErroredGuiasPanel, {
      props: { erroredGuias: guias },
    })
    expect(wrapper.find('.errored-panel__count').text()).toBe('2')
  })

  it('displays registro when not null', () => {
    const wrapper = mount(ErroredGuiasPanel, {
      props: { erroredGuias: [makeErrored({ registro: 'R999' })] },
    })
    expect(wrapper.text()).toContain('R999')
  })

  it('handles null registro gracefully', () => {
    const wrapper = mount(ErroredGuiasPanel, {
      props: { erroredGuias: [makeErrored({ registro: null })] },
    })
    // Should not throw; panel still renders
    expect(wrapper.find('.errored-panel').exists()).toBe(true)
  })
})
