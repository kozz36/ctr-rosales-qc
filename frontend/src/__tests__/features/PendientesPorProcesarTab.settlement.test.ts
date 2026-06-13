/**
 * PendientesPorProcesarTab settlement — REAL backend signal (SA-5 fix).
 *
 * SUPERSEDES the previous frontend-only time-heuristic (elapsed-floor +
 * observed-shrink + hard-cap polling GET /table). That heuristic GUESSED batch
 * completion via timing and finalized prematurely on real latency (SA-5 run
 * c8a6f97d: UI "2 recuperadas / 22 fallaron" vs backend truth 17/7). Settlement
 * is now driven by GET /reprocess-status `done:true` + real counts.
 *
 * Contract retained here:
 *   - A mid-batch `done:false` (even a long plateau) does NOT finalize early.
 *   - `done:true` finalizes with the REAL recovered/failed counts.
 *   - A generous failsafe hard cap still bounds a hung batch (backend never
 *     reports done) — finalizes with the last-known counts.
 *   - W3: the null/'—' bucket renders no bulk button (dead 404 action).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'
import { useCapabilitiesStore } from '@/stores/capabilities'

// SDD#4 REV-R34: the bulk button is vision-gated; enable vision so the batch can
// be confirmed. Gating lives in PendientesPorProcesarTab.visionGate.test.ts.
beforeEach(() => {
  useCapabilitiesStore().visionEnabled = true
})
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

describe('PendientesPorProcesarTab — backend-signal settlement (SA-5 fix)', () => {
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

  it('does NOT finalize while done:false, even across a long plateau', async () => {
    batchStatusMock.mockResolvedValue(status({ recovered: 0, failed: 0, done: false }))
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

    const text = wrapper.text().toLowerCase()
    expect(text).not.toContain('recuperada')
    expect(text).not.toContain('fallaron')

    const btn = bulkButtons(wrapper)[0]
    expect(btn.attributes('disabled')).toBeDefined()
    expect(btn.text().toLowerCase()).toContain('procesando')
  })

  it('finalizes with the real recovered/failed counts on done:true', async () => {
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

    const text = wrapper.text()
    expect(text.toLowerCase()).toContain('recuperada')
    expect(text.toLowerCase()).toContain('fallaron')
    expect(text).toContain('2') // recovered
    expect(text).toContain('1') // failed
  })

  it('finalizes immediately when the backend reports all recovered (done:true)', async () => {
    batchStatusMock.mockResolvedValue(status({ total: 3, recovered: 3, failed: 0, done: true }))
    const wrapper = mountTab([
      makeErrored({ guia_id: 'g1' }),
      makeErrored({ guia_id: 'g2' }),
      makeErrored({ guia_id: 'g3' }),
    ])
    await confirmBatch(wrapper)

    // Kickoff poll already returns done:true.
    await flushPromises()

    const text = wrapper.text().toLowerCase()
    expect(text).toContain('recuperada')
    expect(wrapper.text()).toContain('3') // recovered
  })

  it('finalizes via the failsafe cap if the backend never reports done', async () => {
    // Always running — backend hung; the failsafe cap (N * 30s = 90s for N=3)
    // must finalize the poll with the last-known counts.
    batchStatusMock.mockResolvedValue(status({ recovered: 1, failed: 0, done: false }))
    const wrapper = mountTab([
      makeErrored({ guia_id: 'g1' }),
      makeErrored({ guia_id: 'g2' }),
      makeErrored({ guia_id: 'g3' }),
    ])
    await confirmBatch(wrapper)

    await vi.advanceTimersByTimeAsync(95_000)
    await flushPromises()

    // Capped → finalized; button re-enabled.
    expect(bulkButtons(wrapper)[0].attributes('disabled')).toBeUndefined()
    expect(wrapper.text().toLowerCase()).toMatch(/recuperada|fallaron/)
  })
})

describe('PendientesPorProcesarTab — W3: null/"—" bucket has no bulk button', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    reprocessBatchMock.mockReset()
    batchStatusMock.mockReset()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('does NOT render a bulk button for the null-registro ("—") bucket (dead 404 action)', () => {
    const wrapper = mountTab([
      makeErrored({ registro: '232', guia_id: 'g1' }),
      makeErrored({ registro: null as unknown as string, guia_id: 'gX' }),
      makeErrored({ registro: null as unknown as string, guia_id: 'gY' }),
    ])

    expect(wrapper.text()).toContain('—')
    expect(bulkButtons(wrapper)).toHaveLength(1)
  })
})
