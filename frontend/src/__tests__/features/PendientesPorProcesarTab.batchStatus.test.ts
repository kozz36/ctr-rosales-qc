/**
 * TDD RED — PendientesPorProcesarTab real backend-signal settlement (SA-5 fix).
 *
 * Replaces the fragile time-heuristic (elapsed-floor + observed-shrink + hard-cap
 * polling GET /table) with a REAL completion signal: the component polls
 * GET /runs/{id}/registros/{r}/reprocess-status until `done:true` and drives the
 * "N recuperadas / M fallaron" summary from the real `{recovered, failed}` counts.
 *
 * SA-5 bug reproduced (run c8a6f97d): the UI finalized at "2 recuperadas /
 * 22 fallaron" on a real-latency batch while the backend recovered 17/24. The
 * heuristic GUESSED completion via timing and plateaued early. The contract here:
 *   - The summary appears ONLY after the status endpoint returns `done:true`.
 *   - A mid-batch plateau (status running, recovered still climbing) does NOT
 *     finalize early — the button stays disabled while `!done`.
 *   - On `done`, the summary shows the REAL recovered/failed, a final table
 *     refetch is emitted, and the button re-enables.
 *
 * These tests exercise the real poll seam (fake timers + async resolution) and
 * fail against the current heuristic code (which polls /table, not /status).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'
import type { ErroredGuiaResponse, ReprocessBatchStatusResponse } from '@/api/types'

const { reprocessBatchMock, batchStatusMock } = vi.hoisted(() => ({
  reprocessBatchMock: vi.fn(),
  batchStatusMock: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  reprocessRegistroBatch: reprocessBatchMock,
  getReprocessBatchStatus: batchStatusMock,
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

function status(p: Partial<ReprocessBatchStatusResponse>): ReprocessBatchStatusResponse {
  return { registro: '232', total: 3, recovered: 0, failed: 0, done: false, ...p }
}

function mountTab(erroredGuias: ErroredGuiaResponse[]) {
  return mount(PendientesPorProcesarTab, {
    props: { erroredGuias, runId: 'run-123', rows: [] },
  })
}

function bulkButtons(wrapper: ReturnType<typeof mountTab>) {
  return wrapper.findAll('button').filter((b) => {
    const t = b.text().toLowerCase()
    return t.includes('procesar todos') || t.includes('procesando')
  })
}

async function confirmBatch(wrapper: ReturnType<typeof mountTab>) {
  await bulkButtons(wrapper)[0].trigger('click')
  await nextTick()
  const confirmBtn = wrapper.findAll('button').find((b) => /confirmar/i.test(b.text()))!
  await confirmBtn.trigger('click')
  await flushPromises()
}

describe('PendientesPorProcesarTab — real backend-signal settlement (SA-5 fix)', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    reprocessBatchMock.mockReset()
    batchStatusMock.mockReset()
    reprocessBatchMock.mockResolvedValue({
      run_id: 'run-123',
      registro: '232',
      count: 3,
      task: 'started',
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('does NOT finalize while the status endpoint reports done:false (mid-batch plateau)', async () => {
    // running → running → still running: backend recovered climbs but not done.
    batchStatusMock
      .mockResolvedValueOnce(status({ recovered: 0, failed: 0, done: false }))
      .mockResolvedValueOnce(status({ recovered: 1, failed: 0, done: false }))
      .mockResolvedValue(status({ recovered: 2, failed: 0, done: false }))

    const wrapper = mountTab([
      makeErrored({ guia_id: 'g1' }),
      makeErrored({ guia_id: 'g2' }),
      makeErrored({ guia_id: 'g3' }),
    ])
    await confirmBatch(wrapper)

    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()

    // No summary yet — done was never true.
    const text = wrapper.text().toLowerCase()
    expect(text).not.toContain('recuperada')
    expect(text).not.toContain('fallaron')

    // Still in-flight: button disabled + "Procesando…".
    const btn = bulkButtons(wrapper)[0]
    expect(btn.attributes('disabled')).toBeDefined()
    expect(btn.text().toLowerCase()).toContain('procesando')
  })

  it('finalizes with the REAL recovered/failed counts ONLY after done:true', async () => {
    // The SA-5 scenario in miniature: a long plateau then a done with REAL counts
    // that the time-heuristic would have under-reported.
    batchStatusMock
      .mockResolvedValueOnce(status({ recovered: 0, failed: 0, done: false }))
      .mockResolvedValueOnce(status({ recovered: 5, failed: 1, done: false }))
      .mockResolvedValue(
        status({ total: 24, recovered: 17, failed: 7, done: true }),
      )

    const errored = Array.from({ length: 24 }, (_, i) =>
      makeErrored({ guia_id: `g${i}` }),
    )
    const wrapper = mountTab(errored)
    await confirmBatch(wrapper)
    // confirmBatch fires the immediate kickoff poll (call 1 → running).

    // First interval tick (call 2 → running) → still no summary.
    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()
    expect(wrapper.text().toLowerCase()).not.toContain('recuperada')

    // Second interval tick (call 3 → done:true with real 17/7).
    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()

    const text = wrapper.text()
    expect(text.toLowerCase()).toContain('recuperada')
    expect(text.toLowerCase()).toContain('fallaron')
    expect(text).toContain('17') // real recovered
    expect(text).toContain('7') // real failed

    // Button re-enabled on done.
    const btn = bulkButtons(wrapper)[0]
    expect(btn.attributes('disabled')).toBeUndefined()
    expect(btn.text().toLowerCase()).toContain('procesar todos')
  })

  it('emits a final refetch on done so the table reflects the recovery', async () => {
    batchStatusMock
      .mockResolvedValueOnce(status({ recovered: 0, done: false }))
      .mockResolvedValue(status({ total: 3, recovered: 3, failed: 0, done: true }))

    const wrapper = mountTab([
      makeErrored({ guia_id: 'g1' }),
      makeErrored({ guia_id: 'g2' }),
      makeErrored({ guia_id: 'g3' }),
    ])
    await confirmBatch(wrapper)

    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()

    // A refetch must have been emitted (final table refresh on done).
    expect(wrapper.emitted('refetch')).toBeTruthy()
    expect(wrapper.text()).toContain('3')
    expect(wrapper.text().toLowerCase()).toContain('recuperada')
  })

  it('stops polling after done (no further status calls)', async () => {
    batchStatusMock
      .mockResolvedValueOnce(status({ recovered: 0, done: false }))
      .mockResolvedValue(status({ total: 3, recovered: 2, failed: 1, done: true }))

    const wrapper = mountTab([
      makeErrored({ guia_id: 'g1' }),
      makeErrored({ guia_id: 'g2' }),
      makeErrored({ guia_id: 'g3' }),
    ])
    await confirmBatch(wrapper)

    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()

    const callsAtDone = batchStatusMock.mock.calls.length
    expect(callsAtDone).toBeGreaterThanOrEqual(2)

    // Advance well past — no more polling once done.
    await vi.advanceTimersByTimeAsync(10_000)
    await flushPromises()
    expect(batchStatusMock.mock.calls.length).toBe(callsAtDone)
  })
})
