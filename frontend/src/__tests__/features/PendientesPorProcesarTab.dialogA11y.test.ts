/**
 * TDD RED — PendientesPorProcesarTab confirm-dialog focus management (W2).
 *
 * ctr-review W2: the confirm dialog lacks a focus-trap (Tab can escape the
 * modal) and does NOT restore focus to the triggering bulk button on
 * cancel/confirm. Esc + initial focus already work.
 *
 * WAI-ARIA dialog (modal) pattern: focus is trapped within the dialog while
 * open, and on close focus returns to the element that opened it.
 *
 * Fails against the current code (no focus-trap, no restore).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import type { ErroredGuiaResponse } from '@/api/types'

const { reprocessBatchMock } = vi.hoisted(() => ({
  reprocessBatchMock: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  reprocessRegistroBatch: reprocessBatchMock,
  getReprocessBatchStatus: vi.fn().mockResolvedValue({
    registro: '232',
    total: 0,
    recovered: 0,
    failed: 0,
    done: true,
  }),
  retryGuia: vi.fn(),
  reprocessGuia: vi.fn(),
}))

import PendientesPorProcesarTab from '@/features/review/PendientesPorProcesarTab.vue'

function makeErrored(overrides: Partial<ErroredGuiaResponse> = {}): ErroredGuiaResponse {
  return {
    registro: '232',
    guia_id: 'T009-0001',
    source_pages: [5, 6],
    retry_attempted: true,
    ...overrides,
  }
}

function mountTab(erroredGuias: ErroredGuiaResponse[]) {
  return mount(PendientesPorProcesarTab, {
    props: { erroredGuias, runId: 'run-123', rows: [] },
    attachTo: document.body,
  })
}

function bulkButtons(wrapper: ReturnType<typeof mountTab>) {
  return wrapper.findAll('button').filter((b) => {
    const t = b.text().toLowerCase()
    return t.includes('procesar todos') || t.includes('procesando')
  })
}

describe('PendientesPorProcesarTab — confirm dialog focus management (W2)', () => {
  beforeEach(() => {
    reprocessBatchMock.mockReset()
  })
  afterEach(() => {
    document.body.replaceChildren()
  })

  it('restores focus to the triggering bulk button on Cancel', async () => {
    const wrapper = mountTab([
      makeErrored({ registro: '232', guia_id: 'g1' }),
      makeErrored({ registro: '232', guia_id: 'g2' }),
    ])

    const trigger = bulkButtons(wrapper)[0]
    const triggerEl = trigger.element as HTMLButtonElement
    triggerEl.focus()
    await trigger.trigger('click')
    await nextTick()

    // Dialog open; focus moved inside.
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)

    const cancelBtn = wrapper.findAll('button').find((b) => /cancelar/i.test(b.text()))!
    await cancelBtn.trigger('click')
    await nextTick()

    expect(document.activeElement).toBe(triggerEl)
  })

  it('traps Tab focus within the dialog (Tab on the last element cycles to the first)', async () => {
    const wrapper = mountTab([
      makeErrored({ registro: '232', guia_id: 'g1' }),
      makeErrored({ registro: '232', guia_id: 'g2' }),
    ])

    await bulkButtons(wrapper)[0].trigger('click')
    await nextTick()

    const dialog = wrapper.find('[role="dialog"]')
    const cancelBtn = wrapper.findAll('button').find((b) => /cancelar/i.test(b.text()))!
    const confirmBtn = wrapper.findAll('button').find((b) => /confirmar/i.test(b.text()))!

    // Focus the last focusable (confirm) then Tab → should cycle to first (cancel).
    ;(confirmBtn.element as HTMLButtonElement).focus()
    await dialog.trigger('keydown', { key: 'Tab' })
    await nextTick()

    expect(document.activeElement).toBe(cancelBtn.element)
  })

  it('Shift+Tab on the first element cycles to the last (confirm)', async () => {
    const wrapper = mountTab([makeErrored({ registro: '232', guia_id: 'g1' })])

    await bulkButtons(wrapper)[0].trigger('click')
    await nextTick()

    const dialog = wrapper.find('[role="dialog"]')
    const cancelBtn = wrapper.findAll('button').find((b) => /cancelar/i.test(b.text()))!
    const confirmBtn = wrapper.findAll('button').find((b) => /confirmar/i.test(b.text()))!

    ;(cancelBtn.element as HTMLButtonElement).focus()
    await dialog.trigger('keydown', { key: 'Tab', shiftKey: true })
    await nextTick()

    expect(document.activeElement).toBe(confirmBtn.element)
  })
})
