/**
 * TDD RED — PendientesPorProcesarTab (F1 / REV-R21 / D3, D7).
 *
 * The Pendientes tab hosts the errored-guías list grouped by Registro and a
 * per-Registro "Procesar todos con IA" bulk button. Flow (REV-R21):
 *   1. Confirm dialog shows the call count ("N guías con IA = N llamadas").
 *   2. On confirm → reprocessRegistroBatch(runId, registro) (202).
 *   3. Button disabled while in-flight (REV-R21-S04).
 *   4. Poll: emit 'refetch' so the parent re-feeds erroredGuias; recovered guías
 *      leave the list incrementally (REV-R21-S02).
 *   5. Completion summary "N recuperadas / M fallaron" (REV-R21-S03), N+M = total.
 *
 * The 202 ReprocessBatchResponse does NOT carry recovered/failed — those are
 * DERIVED frontend-side from the errored-list delta after polling (D3).
 *
 * Fails before PendientesPorProcesarTab.vue exists.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'
import type { ErroredGuiaResponse } from '@/api/types'

const { reprocessBatchMock } = vi.hoisted(() => ({
  reprocessBatchMock: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  reprocessRegistroBatch: reprocessBatchMock,
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
  })
}

/** Find the bulk "Procesar todos con IA" buttons (idle or in-flight label). */
function bulkButtons(wrapper: ReturnType<typeof mountTab>) {
  return wrapper.findAll('button').filter((b) => {
    const t = b.text().toLowerCase()
    return t.includes('procesar todos') || t.includes('procesando')
  })
}

describe('PendientesPorProcesarTab — bulk per-Registro reprocess (F1)', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    reprocessBatchMock.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders one "Procesar todos con IA" button per Registro group', () => {
    const wrapper = mountTab([
      makeErrored({ registro: '232', guia_id: 'g1' }),
      makeErrored({ registro: '232', guia_id: 'g2' }),
      makeErrored({ registro: '230', guia_id: 'g3' }),
    ])
    // Two distinct registros → two bulk buttons.
    expect(bulkButtons(wrapper)).toHaveLength(2)
  })

  it('shows a confirm dialog with the call count and does NOT fire until confirmed', async () => {
    const wrapper = mountTab([
      makeErrored({ registro: '232', guia_id: 'g1' }),
      makeErrored({ registro: '232', guia_id: 'g2' }),
      makeErrored({ registro: '232', guia_id: 'g3' }),
      makeErrored({ registro: '232', guia_id: 'g4' }),
    ])

    await bulkButtons(wrapper)[0].trigger('click')
    await nextTick()

    // Confirm dialog visible with N = 4 (4 guías = 4 llamadas).
    const dialog = wrapper.find('[role="dialog"]')
    expect(dialog.exists()).toBe(true)
    expect(dialog.text()).toContain('4')
    // Request NOT sent yet.
    expect(reprocessBatchMock).not.toHaveBeenCalled()
  })

  it('fires reprocessRegistroBatch on confirm and disables the button while in-flight', async () => {
    reprocessBatchMock.mockReturnValue(new Promise(() => {})) // never resolves → stays in-flight
    const wrapper = mountTab([
      makeErrored({ registro: '232', guia_id: 'g1' }),
      makeErrored({ registro: '232', guia_id: 'g2' }),
    ])

    await bulkButtons(wrapper)[0].trigger('click')
    await nextTick()

    // Confirm.
    const confirmBtn = wrapper
      .findAll('button')
      .find((b) => /confirmar|procesar 2|sí|si\b/i.test(b.text()))
    expect(confirmBtn).toBeDefined()
    await confirmBtn!.trigger('click')
    await nextTick()

    expect(reprocessBatchMock).toHaveBeenCalledWith('run-123', '232')

    // The bulk button is now disabled + shows in-flight label.
    const inflight = bulkButtons(wrapper)[0]
    expect(inflight.attributes('disabled')).toBeDefined()
    expect(inflight.text().toLowerCase()).toContain('procesando')
  })

  it('emits refetch on the poll interval after a 202 response', async () => {
    reprocessBatchMock.mockResolvedValue({
      run_id: 'run-123',
      registro: '232',
      count: 2,
      task: 'started',
    })
    const wrapper = mountTab([
      makeErrored({ registro: '232', guia_id: 'g1' }),
      makeErrored({ registro: '232', guia_id: 'g2' }),
    ])

    await bulkButtons(wrapper)[0].trigger('click')
    await nextTick()
    const confirmBtn = wrapper
      .findAll('button')
      .find((b) => /confirmar|procesar 2|sí|si\b/i.test(b.text()))!
    await confirmBtn.trigger('click')
    await flushPromises()

    // Advance the poll timer — the component must emit 'refetch' to refresh table.
    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()

    expect(wrapper.emitted('refetch')).toBeTruthy()
  })

  it('shows "N recuperadas / M fallaron" once the batch settles (derived from list delta)', async () => {
    reprocessBatchMock.mockResolvedValue({
      run_id: 'run-123',
      registro: '232',
      count: 3,
      task: 'started',
    })
    // Start: 3 errored guías for registro 232.
    const initial = [
      makeErrored({ registro: '232', guia_id: 'g1' }),
      makeErrored({ registro: '232', guia_id: 'g2' }),
      makeErrored({ registro: '232', guia_id: 'g3' }),
    ]
    const wrapper = mountTab(initial)

    await bulkButtons(wrapper)[0].trigger('click')
    await nextTick()
    const confirmBtn = wrapper
      .findAll('button')
      .find((b) => /confirmar|procesar 3|sí|si\b/i.test(b.text()))!
    await confirmBtn.trigger('click')
    await flushPromises()

    // Parent re-feeds the prop after polling: 2 recovered, 1 still errored.
    await wrapper.setProps({
      erroredGuias: [makeErrored({ registro: '232', guia_id: 'g3' })],
      runId: 'run-123',
      rows: [],
    })
    // Let the poll loop observe a stable read (no further shrink) and finalize.
    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('2')
    expect(text.toLowerCase()).toContain('recuperada')
    expect(text).toContain('1')
    expect(text.toLowerCase()).toContain('fallaron')
  })
})
