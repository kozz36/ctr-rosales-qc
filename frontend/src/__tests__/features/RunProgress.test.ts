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
 *
 * TanStack Query useRunStatus is mocked to return controlled data.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { ref, type Ref } from 'vue'
import type { RunStatus } from '@/api/types'

// ---------------------------------------------------------------------------
// Mock TanStack Query composable
// ---------------------------------------------------------------------------

const mockStatus = ref<RunStatus>('pending')
const mockData = ref<{
  status: RunStatus
  vision_calls_made: number
  warnings: string[]
  error: string | null
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

function setMockStatus(status: RunStatus, opts: { warnings?: string[]; error?: string } = {}) {
  mockStatus.value = status
  mockData.value = {
    status,
    vision_calls_made: 0,
    warnings: opts.warnings ?? [],
    error: opts.error ?? null,
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
  })

  function buildWrapper(runId = 'run-abc') {
    return mount(RunProgress, {
      props: { runId },
      global: { plugins: [createPinia()] },
    })
  }

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
    // Progress bar present for active states
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
})
