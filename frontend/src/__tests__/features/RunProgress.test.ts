/**
 * RunProgress component — unit tests
 *
 * Covers:
 *  - Renders "En cola" badge for pending status
 *  - Renders "Procesando" badge for processing status
 *  - Renders "Completado" badge for review status
 *  - Renders "Error" badge for error status
 *  - Shows warnings list when present
 *  - Shows error detail for error status
 *  - Emits 'completed' event when status transitions to review
 *  - Emits 'failed' event when status transitions to error
 *  -- Determinate progress bar (new) --
 *  - Determinate bar: aria-valuenow + fill width when progress present
 *  - Indeterminate fallback: no aria-valuenow when progress is null
 *  - Stage label and item counts render from progress
 *  - Elapsed time formatted from started_at (fake timers)
 *  - ETA appears only when percent >= 5 and is labeled "estimado"
 *  - ETA hidden when percent is 0 or below the 5% floor (inflated-estimate guard)
 *  - Status transitions (review/error) still emit completed/failed
 *
 * TanStack Query useRunStatus is mocked to return controlled data.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { ref, type Ref } from 'vue'
import type { RunStatus, RunProgressInfo } from '@/api/types'

// ---------------------------------------------------------------------------
// Mock TanStack Query composable
// ---------------------------------------------------------------------------

const mockData = ref<{
  status: RunStatus
  vision_calls_made: number
  warnings: string[]
  error: string | null
  started_at: string | null
  progress: RunProgressInfo | null
} | undefined>(undefined)

vi.mock('@/composables/useReconciliationApi', () => ({
  useRunStatus: (_runId: Ref<string | null>) => ({
    data: mockData,
    isError: ref(false),
  }),
}))

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function setMockStatus(
  status: RunStatus,
  opts: {
    warnings?: string[]
    error?: string
    started_at?: string | null
    progress?: RunProgressInfo | null
  } = {},
) {
  mockData.value = {
    status,
    vision_calls_made: 0,
    warnings: opts.warnings ?? [],
    error: opts.error ?? null,
    started_at: opts.started_at ?? null,
    progress: opts.progress ?? null,
  }
}

import RunProgress from '@/features/run/RunProgress.vue'

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

describe('RunProgress', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    setMockStatus('pending')
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  function buildWrapper(runId = 'run-abc') {
    return mount(RunProgress, {
      props: { runId },
      global: { plugins: [createPinia()] },
    })
  }

  // -------------------------------------------------------------------------
  // Existing status badge tests
  // -------------------------------------------------------------------------

  it('shows "En cola" badge for pending status', async () => {
    setMockStatus('pending')
    const wrapper = buildWrapper()
    await flushPromises()
    expect(wrapper.find('.run-progress__status-label').text()).toBe('En cola')
    expect(wrapper.find('.run-progress__status-badge').classes()).toContain(
      'run-progress__status-badge--pending',
    )
  })

  it('shows "Procesando" badge for processing status', async () => {
    setMockStatus('processing')
    const wrapper = buildWrapper()
    await flushPromises()
    expect(wrapper.find('.run-progress__status-label').text()).toBe('Procesando')
    expect(wrapper.find('.run-progress__status-badge').classes()).toContain(
      'run-progress__status-badge--processing',
    )
    // Progress bar track present for active states
    expect(wrapper.find('.run-progress__bar-track').exists()).toBe(true)
  })

  it('shows "Completado" badge for review status', async () => {
    setMockStatus('review')
    const wrapper = buildWrapper()
    await flushPromises()
    expect(wrapper.find('.run-progress__status-label').text()).toBe('Completado')
    expect(wrapper.find('.run-progress__status-badge').classes()).toContain(
      'run-progress__status-badge--review',
    )
    // No progress bar for terminal state
    expect(wrapper.find('.run-progress__bar-track').exists()).toBe(false)
  })

  it('shows "Error" badge and error detail for error status', async () => {
    setMockStatus('error', { error: 'Pipeline crashed: PDF corrupt' })
    const wrapper = buildWrapper()
    await flushPromises()
    expect(wrapper.find('.run-progress__status-label').text()).toBe('Error')
    expect(wrapper.find('.run-progress__error-detail').text()).toContain(
      'Pipeline crashed: PDF corrupt',
    )
  })

  it('renders warnings list', async () => {
    setMockStatus('review', { warnings: ['Page 4 unclassified', 'Low confidence on page 7'] })
    const wrapper = buildWrapper()
    await flushPromises()
    const warnings = wrapper.findAll('.run-progress__warning-item')
    expect(warnings).toHaveLength(2)
    expect(warnings[0].text()).toContain('Page 4 unclassified')
  })

  it('emits completed when status becomes review', async () => {
    setMockStatus('pending')
    const wrapper = buildWrapper()
    await flushPromises()

    setMockStatus('review')
    await flushPromises()

    expect(wrapper.emitted('completed')).toBeTruthy()
  })

  it('emits failed with error message when status becomes error', async () => {
    setMockStatus('pending')
    const wrapper = buildWrapper()
    await flushPromises()

    setMockStatus('error', { error: 'Timeout' })
    await flushPromises()

    const failed = wrapper.emitted('failed')
    expect(failed).toBeTruthy()
    expect(failed?.[0]).toEqual(['Timeout'])
  })

  // -------------------------------------------------------------------------
  // Determinate progress bar (new)
  // -------------------------------------------------------------------------

  it('determinate: aria-valuenow equals rounded percent and fill width matches', async () => {
    setMockStatus('processing', {
      started_at: new Date(Date.now() - 30_000).toISOString(),
      progress: {
        stage_label: 'Lectura de visión',
        stage_index: 2,
        stage_total: 5,
        item_done: 7,
        item_total: 18,
        percent: 70,
      },
    })
    const wrapper = buildWrapper()
    await flushPromises()

    const track = wrapper.find('.run-progress__bar-track')
    expect(track.exists()).toBe(true)
    expect(track.attributes('aria-valuenow')).toBe('70')
    expect(track.attributes('aria-valuemin')).toBe('0')
    expect(track.attributes('aria-valuemax')).toBe('100')

    const fill = wrapper.find('.run-progress__bar-fill')
    expect(fill.attributes('style')).toContain('width: 70%')
  })

  it('determinate: aria-valuetext contains stage label and percent', async () => {
    setMockStatus('processing', {
      started_at: new Date(Date.now() - 10_000).toISOString(),
      progress: {
        stage_label: 'Clasificación de páginas',
        stage_index: 1,
        stage_total: 5,
        item_done: 3,
        item_total: 10,
        percent: 30,
      },
    })
    const wrapper = buildWrapper()
    await flushPromises()

    const track = wrapper.find('.run-progress__bar-track')
    const ariaValueText = track.attributes('aria-valuetext') ?? ''
    expect(ariaValueText).toContain('Clasificación de páginas')
    expect(ariaValueText).toContain('30%')
  })

  it('fallback: indeterminate bar when progress is null and isActive', async () => {
    setMockStatus('processing', { started_at: null, progress: null })
    const wrapper = buildWrapper()
    await flushPromises()

    const track = wrapper.find('.run-progress__bar-track')
    expect(track.exists()).toBe(true)
    // No aria-valuenow on indeterminate
    expect(track.attributes('aria-valuenow')).toBeUndefined()

    // Fill element has slide animation class (indeterminate)
    const fill = wrapper.find('.run-progress__bar-fill')
    expect(fill.exists()).toBe(true)
    // In indeterminate mode, no inline width style is bound
    const style = fill.attributes('style') ?? ''
    expect(style).not.toContain('width:')
    expect(style).not.toMatch(/width\s*:/)
  })

  it('stage label and item counts render from progress', async () => {
    setMockStatus('processing', {
      started_at: new Date(Date.now() - 20_000).toISOString(),
      progress: {
        stage_label: 'Extracción de guías',
        stage_index: 3,
        stage_total: 5,
        item_done: 5,
        item_total: 12,
        percent: 50,
      },
    })
    const wrapper = buildWrapper()
    await flushPromises()

    const detail = wrapper.find('.run-progress__stage-detail')
    expect(detail.exists()).toBe(true)
    expect(detail.text()).toContain('Extracción de guías')
    expect(detail.text()).toContain('5')
    expect(detail.text()).toContain('12')
  })

  it('elapsed time ticks via fake timer and displays "transcurrido"', async () => {
    // started_at 90 seconds ago
    const startedAt = new Date(Date.now() - 90_000).toISOString()
    setMockStatus('processing', {
      started_at: startedAt,
      progress: {
        stage_label: 'Lectura de visión',
        stage_index: 2,
        stage_total: 5,
        item_done: 4,
        item_total: 10,
        percent: 40,
      },
    })
    const wrapper = buildWrapper()
    await flushPromises()

    // Advance 1 second to trigger the first tick
    vi.advanceTimersByTime(1000)
    await flushPromises()

    const elapsed = wrapper.find('.run-progress__elapsed')
    expect(elapsed.exists()).toBe(true)
    // Should show something like "1m 31s transcurrido" or "1m 30s transcurrido"
    expect(elapsed.text()).toMatch(/\d+m \d+s transcurrido/)
  })

  it('ETA is hidden below the 5% floor (inflated-estimate guard)', async () => {
    const startedAt = new Date(Date.now() - 5_000).toISOString()
    setMockStatus('processing', {
      started_at: startedAt,
      progress: {
        stage_label: 'Decodificando identidades',
        stage_index: 1,
        stage_total: 5,
        item_done: 1,
        item_total: 50,
        percent: 2,
      },
    })
    const wrapper = buildWrapper()
    await flushPromises()

    vi.advanceTimersByTime(1000)
    await flushPromises()

    expect(wrapper.find('.run-progress__eta').exists()).toBe(false)
  })

  it('ETA appears when percent >= 5 and is labeled "estimado"', async () => {
    const startedAt = new Date(Date.now() - 60_000).toISOString()
    setMockStatus('processing', {
      started_at: startedAt,
      progress: {
        stage_label: 'Lectura de visión',
        stage_index: 2,
        stage_total: 5,
        item_done: 6,
        item_total: 10,
        percent: 60,
      },
    })
    const wrapper = buildWrapper()
    await flushPromises()

    vi.advanceTimersByTime(1000)
    await flushPromises()

    const eta = wrapper.find('.run-progress__eta')
    expect(eta.exists()).toBe(true)
    expect(eta.text()).toContain('estimado')
    expect(eta.text()).toMatch(/~\d+m \d+s estimado/)
  })

  it('ETA is hidden when percent is 0', async () => {
    const startedAt = new Date(Date.now() - 5_000).toISOString()
    setMockStatus('processing', {
      started_at: startedAt,
      progress: {
        stage_label: 'Lectura de visión',
        stage_index: 1,
        stage_total: 5,
        item_done: 0,
        item_total: 10,
        percent: 0,
      },
    })
    const wrapper = buildWrapper()
    await flushPromises()

    vi.advanceTimersByTime(1000)
    await flushPromises()

    expect(wrapper.find('.run-progress__eta').exists()).toBe(false)
  })

  it('no elapsed / eta shown when started_at is null', async () => {
    setMockStatus('processing', { started_at: null, progress: null })
    const wrapper = buildWrapper()
    await flushPromises()

    vi.advanceTimersByTime(1000)
    await flushPromises()

    expect(wrapper.find('.run-progress__elapsed').exists()).toBe(false)
    expect(wrapper.find('.run-progress__eta').exists()).toBe(false)
  })

  it('does not regress: completed still emitted after progress transitions to review', async () => {
    setMockStatus('processing', {
      started_at: new Date(Date.now() - 30_000).toISOString(),
      progress: {
        stage_label: 'Reconciliación',
        stage_index: 5,
        stage_total: 5,
        item_done: 10,
        item_total: 10,
        percent: 100,
      },
    })
    const wrapper = buildWrapper()
    await flushPromises()

    setMockStatus('review')
    await flushPromises()

    expect(wrapper.emitted('completed')).toBeTruthy()
  })

  it('does not regress: failed still emitted after progress transitions to error', async () => {
    setMockStatus('processing', {
      started_at: new Date(Date.now() - 15_000).toISOString(),
      progress: {
        stage_label: 'Extracción de guías',
        stage_index: 3,
        stage_total: 5,
        item_done: 2,
        item_total: 8,
        percent: 25,
      },
    })
    const wrapper = buildWrapper()
    await flushPromises()

    setMockStatus('error', { error: 'Vision API timeout' })
    await flushPromises()

    const failed = wrapper.emitted('failed')
    expect(failed).toBeTruthy()
    expect(failed?.[0]).toEqual(['Vision API timeout'])
  })
})
