/**
 * TDD RED — PendientesPorProcesarTab robust settlement + warnings (fix-forward).
 *
 * Fixes a fresh-context ctr-review CRITICAL: premature batch-settlement. The
 * old logic finalized on the FIRST poll tick when `remaining >= lastRemaining`
 * (a plateau), so a still-running backend batch (vision 6-14s/guía under
 * Semaphore(3)) was reported as "0 recuperadas / N fallaron" even though no
 * guía had recovered yet.
 *
 * Robust contract (poll-based, frontend-only — no backend task-status endpoint):
 *   - Settlement requires remaining stable across ticks AND either (a) we have
 *     OBSERVED ≥1 shrink since firing, OR (b) an elapsed floor proportional to N
 *     (Semaphore(3) bounded) has passed. A first-tick plateau must NOT finalize.
 *   - remaining === 0 finalizes immediately.
 *   - Hard cap on total poll duration; on cap, finalize with observed delta.
 *   - The poll samples AFTER the async refetch resolves (await refetch).
 *
 * Warnings also covered here:
 *   - W3: the null/'—' bucket must NOT render a bulk button (dead 404 action).
 *
 * These tests exercise the REAL settlement seam (fake timers across a plateau,
 * no pre-staged shrink) and fail against the current premature-settlement code.
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

function bulkButtons(wrapper: ReturnType<typeof mountTab>) {
  return wrapper.findAll('button').filter((b) => {
    const t = b.text().toLowerCase()
    return t.includes('procesar todos') || t.includes('procesando')
  })
}

async function confirmBatch(wrapper: ReturnType<typeof mountTab>) {
  await bulkButtons(wrapper)[0].trigger('click')
  await nextTick()
  const confirmBtn = wrapper
    .findAll('button')
    .find((b) => /confirmar/i.test(b.text()))!
  await confirmBtn.trigger('click')
  await flushPromises()
}

describe('PendientesPorProcesarTab — robust settlement (CRITICAL fix-forward)', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    reprocessBatchMock.mockReset()
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

  it('does NOT finalize on a first-tick plateau while remaining is still full (keeps polling)', async () => {
    // 3 errored guías; backend batch is still running — remaining stays full
    // across the first several ticks (no recovery yet). The OLD code finalized
    // here with recovered=0 because remaining >= lastRemaining on tick 1.
    const wrapper = mountTab([
      makeErrored({ registro: '232', guia_id: 'g1' }),
      makeErrored({ registro: '232', guia_id: 'g2' }),
      makeErrored({ registro: '232', guia_id: 'g3' }),
    ])

    await confirmBatch(wrapper)

    // Advance several poll ticks WITHOUT any prop shrink (plateau, batch running).
    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()

    // Must NOT have finalized a wrong "0 recuperadas / 3 fallaron" summary.
    const text = wrapper.text().toLowerCase()
    expect(text).not.toContain('recuperada')
    expect(text).not.toContain('fallaron')

    // Still in-flight (button disabled, "Procesando…").
    const btn = bulkButtons(wrapper)[0]
    expect(btn.attributes('disabled')).toBeDefined()
    expect(btn.text().toLowerCase()).toContain('procesando')
  })

  it('finalizes correctly once a shrink is OBSERVED then remaining stabilizes', async () => {
    const wrapper = mountTab([
      makeErrored({ registro: '232', guia_id: 'g1' }),
      makeErrored({ registro: '232', guia_id: 'g2' }),
      makeErrored({ registro: '232', guia_id: 'g3' }),
    ])

    await confirmBatch(wrapper)

    // Tick 1: plateau (batch starting, nothing recovered) — must keep polling.
    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()

    // Backend recovers 2 → parent re-feeds the prop (only g3 remains errored).
    await wrapper.setProps({
      erroredGuias: [makeErrored({ registro: '232', guia_id: 'g3' })],
      runId: 'run-123',
      rows: [],
    })

    // Tick 2: observes the shrink (3 → 1).
    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()
    // Tick 3+: remaining stable at 1 → finalize with the OBSERVED shrink.
    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()

    const text = wrapper.text().toLowerCase()
    expect(text).toContain('recuperada')
    expect(text).toContain('fallaron')
    expect(wrapper.text()).toContain('2') // recovered
    expect(wrapper.text()).toContain('1') // failed
  })

  it('finalizes immediately when remaining reaches 0 (all recovered)', async () => {
    const wrapper = mountTab([
      makeErrored({ registro: '232', guia_id: 'g1' }),
      makeErrored({ registro: '232', guia_id: 'g2' }),
      makeErrored({ registro: '232', guia_id: 'g3' }),
    ])

    await confirmBatch(wrapper)

    // All 3 recovered → parent re-feeds an empty list.
    await wrapper.setProps({ erroredGuias: [], runId: 'run-123', rows: [] })
    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()

    const text = wrapper.text().toLowerCase()
    expect(wrapper.text()).toContain('3') // recovered
    expect(text).toContain('recuperada')
  })

  it('finalizes via the hard cap if the batch never shrinks (avoids infinite poll)', async () => {
    const wrapper = mountTab([
      makeErrored({ registro: '232', guia_id: 'g1' }),
      makeErrored({ registro: '232', guia_id: 'g2' }),
      makeErrored({ registro: '232', guia_id: 'g3' }),
    ])

    await confirmBatch(wrapper)

    // Drive well past the hard cap (N * 20s = 60s for N=3) with no shrink.
    await vi.advanceTimersByTimeAsync(90_000)
    await flushPromises()

    // Capped → finalized with the observed delta (0 recovered, all failed).
    const text = wrapper.text().toLowerCase()
    expect(text).toContain('fallaron')
    expect(wrapper.text()).toContain('3') // all failed at cap
    // No longer in-flight.
    expect(bulkButtons(wrapper)[0].attributes('disabled')).toBeUndefined()
  })
})

describe('PendientesPorProcesarTab — W3: null/"—" bucket has no bulk button', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    reprocessBatchMock.mockReset()
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

    // The "—" group renders, but only the real registro (232) gets a bulk button.
    expect(wrapper.text()).toContain('—')
    expect(bulkButtons(wrapper)).toHaveLength(1)
  })
})
