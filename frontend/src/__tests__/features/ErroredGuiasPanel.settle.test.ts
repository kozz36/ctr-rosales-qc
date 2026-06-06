/**
 * TDD RED — ErroredGuiasPanel settle events.
 *
 * Bug: handleRetry only emits 'retry-success' when recovered===true.
 *      handleReprocess only emits 'reprocess-success' when recovered===true.
 *      Neither emits a settle signal on failed-result or thrown-error paths,
 *      so ReviewPage never refetches after a failed retry or a failed reprocess.
 *
 * Required behavior: after EVERY retry/reprocess operation settles (success,
 * recovered=false, or thrown error) a settle event is emitted so the parent
 * can call tableQuery.refetch().
 *
 * We add 'retry-settled' and 'reprocess-settled' events emitted in the
 * `finally` block of each handler.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import type { ErroredGuiaResponse } from '@/api/types'

function makeErrored(overrides: Partial<ErroredGuiaResponse> = {}): ErroredGuiaResponse {
  return {
    registro: 'R001',
    guia_id: 'T009-0001',
    source_pages: [5, 6],
    retry_attempted: false,
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// retry-settled — emitted in finally (all paths)
// ---------------------------------------------------------------------------

describe('ErroredGuiasPanel — retry-settled event (settle contract)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('emits retry-settled when retry SUCCEEDS (recovered=true)', async () => {
    vi.doMock('@/api/client', () => ({
      retryGuia: vi.fn().mockResolvedValue({ recovered: true, errored_guias: [] }),
      reprocessGuia: vi.fn(() => new Promise(() => {})),
    }))
    const { default: Panel } = await import('@/features/review/ErroredGuiasPanel.vue')
    const wrapper = mount(Panel, {
      props: {
        erroredGuias: [makeErrored({ guia_id: 'T009-0001', retry_attempted: false })],
        runId: 'run-settle-1',
      },
    })
    const btn = wrapper.findAll('button').find((b) =>
      b.text().toUpperCase().includes('REINTENTAR'),
    )
    expect(btn).toBeDefined()
    await btn!.trigger('click')
    await flushPromises()

    expect(wrapper.emitted('retry-settled')).toBeTruthy()
    vi.resetModules()
  })

  it('emits retry-settled when retry returns recovered=false (failed retry path)', async () => {
    vi.doMock('@/api/client', () => ({
      retryGuia: vi.fn().mockResolvedValue({ recovered: false, errored_guias: [], reason: 'no_hashqr_url' }),
      reprocessGuia: vi.fn(() => new Promise(() => {})),
    }))
    const { default: Panel } = await import('@/features/review/ErroredGuiasPanel.vue')
    const wrapper = mount(Panel, {
      props: {
        erroredGuias: [makeErrored({ guia_id: 'T009-0001', retry_attempted: false })],
        runId: 'run-settle-2',
      },
    })
    const btn = wrapper.findAll('button').find((b) =>
      b.text().toUpperCase().includes('REINTENTAR'),
    )
    await btn!.trigger('click')
    await flushPromises()

    // This MUST be emitted even when recovered===false — currently NOT emitted (RED)
    expect(wrapper.emitted('retry-settled')).toBeTruthy()
    vi.resetModules()
  })

  it('emits retry-settled when retry throws (network error path)', async () => {
    vi.doMock('@/api/client', () => ({
      retryGuia: vi.fn().mockRejectedValue(new Error('network error')),
      reprocessGuia: vi.fn(() => new Promise(() => {})),
    }))
    const { default: Panel } = await import('@/features/review/ErroredGuiasPanel.vue')
    const wrapper = mount(Panel, {
      props: {
        erroredGuias: [makeErrored({ guia_id: 'T009-0001', retry_attempted: false })],
        runId: 'run-settle-3',
      },
    })
    const btn = wrapper.findAll('button').find((b) =>
      b.text().toUpperCase().includes('REINTENTAR'),
    )
    await btn!.trigger('click')
    await flushPromises()

    // MUST emit in finally — currently catch swallows without emitting (RED)
    expect(wrapper.emitted('retry-settled')).toBeTruthy()
    vi.resetModules()
  })
})

// ---------------------------------------------------------------------------
// reprocess-settled — emitted in finally (all paths)
// ---------------------------------------------------------------------------

describe('ErroredGuiasPanel — reprocess-settled event (settle contract)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('emits reprocess-settled when reprocess SUCCEEDS (recovered=true)', async () => {
    vi.doMock('@/api/client', () => ({
      retryGuia: vi.fn(() => new Promise(() => {})),
      reprocessGuia: vi.fn().mockResolvedValue({ recovered: true, errored_guias: [] }),
    }))
    const { default: Panel } = await import('@/features/review/ErroredGuiasPanel.vue')
    const wrapper = mount(Panel, {
      props: {
        erroredGuias: [makeErrored({ guia_id: 'T009-0001', retry_attempted: true })],
        runId: 'run-settle-4',
      },
    })
    const btn = wrapper.findAll('button').find((b) =>
      b.text().toLowerCase().includes('reprocesar'),
    )
    expect(btn).toBeDefined()
    await btn!.trigger('click')
    await flushPromises()

    expect(wrapper.emitted('reprocess-settled')).toBeTruthy()
    vi.resetModules()
  })

  it('emits reprocess-settled when reprocess returns recovered=false', async () => {
    vi.doMock('@/api/client', () => ({
      retryGuia: vi.fn(() => new Promise(() => {})),
      reprocessGuia: vi.fn().mockResolvedValue({ recovered: false, errored_guias: [], reason: 'low_confidence' }),
    }))
    const { default: Panel } = await import('@/features/review/ErroredGuiasPanel.vue')
    const wrapper = mount(Panel, {
      props: {
        erroredGuias: [makeErrored({ guia_id: 'T009-0001', retry_attempted: true })],
        runId: 'run-settle-5',
      },
    })
    const btn = wrapper.findAll('button').find((b) =>
      b.text().toLowerCase().includes('reprocesar'),
    )
    await btn!.trigger('click')
    await flushPromises()

    // MUST emit even on recovered=false — currently NOT emitted (RED)
    expect(wrapper.emitted('reprocess-settled')).toBeTruthy()
    vi.resetModules()
  })

  it('emits reprocess-settled when reprocess throws (error path)', async () => {
    vi.doMock('@/api/client', () => ({
      retryGuia: vi.fn(() => new Promise(() => {})),
      reprocessGuia: vi.fn().mockRejectedValue(new Error('vision error')),
    }))
    const { default: Panel } = await import('@/features/review/ErroredGuiasPanel.vue')
    const wrapper = mount(Panel, {
      props: {
        erroredGuias: [makeErrored({ guia_id: 'T009-0001', retry_attempted: true })],
        runId: 'run-settle-6',
      },
    })
    const btn = wrapper.findAll('button').find((b) =>
      b.text().toLowerCase().includes('reprocesar'),
    )
    await btn!.trigger('click')
    await flushPromises()

    // MUST emit in finally — currently NOT emitted on throw (RED)
    expect(wrapper.emitted('reprocess-settled')).toBeTruthy()
    vi.resetModules()
  })
})
