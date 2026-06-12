/**
 * RunHistoryPage — /historial run list (SDD#3 PR-3, RH-010/RH-007/RH-009).
 *
 * List comes from the useRunsList TanStack hook (mocked module-level, like the
 * ReviewPage suites mock useReconciliationApi). delete/retry call the API
 * client directly (DescartadasTab pattern) and then refetch the runs query —
 * the page-level "invalidation" observable asserted here is the refetch spy.
 *
 * Coverage:
 *  - renders rows with label + status badge from GET /runs data
 *  - clicking an entry navigates to /runs/{id}
 *  - [Eliminar] requires a confirm dialog before deleteRun fires (RH-009)
 *  - [Reintentar] only on error-status entries (RH-007-S01)
 *  - retry calls retryRun and refreshes the runs query (RH-007-S02)
 *  - degraded legacy entries render with "—" placeholders, no crash
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import type { RunSummaryResponse } from '@/api/types'
import { installLocalStorageStub } from '../test-utils/local-storage-stub'

const { pushSpy, refetchSpy, deleteRunMock, retryRunMock, MOCK_RUNS } = vi.hoisted(() => {
  const MOCK_RUNS: RunSummaryResponse[] = [
    {
      run_id: 'xyz',
      status: 'review',
      started_at: '2026-06-10T15:04:00+00:00',
      completed_at: '2026-06-10T15:24:00+00:00',
      seq: 2,
      registro_min: '230',
      registro_max: '245',
      row_count: 12,
      match_count: 10,
      mismatch_count: 2,
      warnings_count: 1,
      vision_calls_made: 0,
      degraded: false,
      error: null,
    },
    {
      run_id: 'err-1',
      status: 'error',
      started_at: '2026-06-09T10:00:00+00:00',
      completed_at: '2026-06-09T10:01:00+00:00',
      seq: 1,
      registro_min: null,
      registro_max: null,
      row_count: 0,
      match_count: 0,
      mismatch_count: 0,
      warnings_count: 0,
      vision_calls_made: 0,
      degraded: false,
      error: 'pipeline exploded',
    },
    {
      // Degraded legacy entry — no manifest, nullable fields (RH-003-S03)
      run_id: 'legacy-1',
      status: 'review',
      started_at: null,
      completed_at: null,
      seq: null,
      registro_min: null,
      registro_max: null,
      row_count: 0,
      match_count: 0,
      mismatch_count: 0,
      warnings_count: 0,
      vision_calls_made: 0,
      degraded: true,
      error: null,
    },
    {
      // S3: near-UTC-midnight entry. 02:00 UTC on 2026-06-10 is 21:00 on
      // 2026-06-09 in America/Lima (UTC-5). The label MUST read the UTC day
      // (10-06-2026) so it agrees with the backend's UTC per-day #seq; the
      // prior local-getter formatter rendered the previous day (09-06-2026).
      // Appended LAST so existing index-based assertions ([1], [2]) are stable.
      run_id: 'tz-edge',
      status: 'review',
      started_at: '2026-06-10T02:00:00+00:00',
      completed_at: '2026-06-10T02:05:00+00:00',
      seq: 1,
      registro_min: '300',
      registro_max: '301',
      row_count: 1,
      match_count: 1,
      mismatch_count: 0,
      warnings_count: 0,
      vision_calls_made: 0,
      degraded: false,
      error: null,
    },
  ]
  return {
    pushSpy: vi.fn(),
    refetchSpy: vi.fn(),
    deleteRunMock: vi.fn(),
    retryRunMock: vi.fn(),
    MOCK_RUNS,
  }
})

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: pushSpy }),
}))

vi.mock('@/composables/useReconciliationApi', () => ({
  useRunsList: () => ({
    data: { value: MOCK_RUNS },
    isFetching: { value: false },
    error: { value: null },
    refetch: refetchSpy,
  }),
}))

vi.mock('@/api/client', () => ({
  createRun: vi.fn(),
  deleteRun: deleteRunMock,
  retryRun: retryRunMock,
}))

import RunHistoryPage from '@/features/run/RunHistoryPage.vue'

function mountPage() {
  return mount(RunHistoryPage, {
    global: {
      plugins: [createPinia()],
      stubs: { Teleport: { template: '<slot />' } },
    },
  })
}

describe('RunHistoryPage (RH-010 / RH-007 / RH-009)', () => {
  beforeEach(() => {
    installLocalStorageStub()
    setActivePinia(createPinia())
    pushSpy.mockReset()
    refetchSpy.mockReset()
    deleteRunMock.mockReset().mockResolvedValue(undefined)
    retryRunMock.mockReset().mockResolvedValue({ run_id: 'err-1', status: 'processing' })
  })

  it('renders run list from GET /runs response with label + status badge (RH-010-S02)', () => {
    const wrapper = mountPage()
    const rows = wrapper.findAll('.run-history-page__row')
    expect(rows).toHaveLength(4)

    // Manifest-backed entry: fecha + registro range + #seq label
    const first = rows[0].text()
    expect(first).toContain('10-06-2026')
    expect(first).toContain('Registros 230–245')
    expect(first).toContain('#2')

    const badges = wrapper.findAll('.run-history-page__badge')
    expect(badges).toHaveLength(4)
    expect(badges[0].attributes('data-status')).toBe('review')
    expect(badges[1].attributes('data-status')).toBe('error')
  })

  it('formatFecha uses the UTC day so the label agrees with the UTC per-day #seq (S3)', () => {
    const wrapper = mountPage()
    // tz-edge entry is appended last (index 3); started_at 02:00 UTC on the
    // 10th is the 9th in America/Lima — the label must still read the UTC day.
    const tzEdge = wrapper.findAll('.run-history-page__row')[3].text()
    expect(tzEdge).toContain('10-06-2026')
    expect(tzEdge).not.toContain('09-06-2026')
  })

  it('degraded legacy entry renders placeholders, never "undefined" (RH-003-S03)', () => {
    const wrapper = mountPage()
    const legacy = wrapper.findAll('.run-history-page__row')[2].text()
    expect(legacy).toContain('—')
    expect(legacy).not.toContain('undefined')
    expect(legacy).not.toContain('null')
  })

  it('clicking a history entry navigates to the run (RH-010-S03)', async () => {
    const wrapper = mountPage()
    await wrapper.findAll('.run-history-page__entry')[0].trigger('click')
    expect(pushSpy).toHaveBeenCalledWith('/runs/xyz')
  })

  it('delete button shows confirm dialog before deletion (RH-009)', async () => {
    const wrapper = mountPage()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)

    await wrapper.findAll('.run-history-page__delete-btn')[0].trigger('click')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(deleteRunMock).not.toHaveBeenCalled()

    await wrapper.find('.run-history-page__dialog-confirm').trigger('click')
    await flushPromises()
    expect(deleteRunMock).toHaveBeenCalledWith('xyz')
    expect(refetchSpy).toHaveBeenCalled()
  })

  it('cancel in the confirm dialog never calls deleteRun', async () => {
    const wrapper = mountPage()
    await wrapper.findAll('.run-history-page__delete-btn')[0].trigger('click')
    await wrapper.find('.run-history-page__dialog-cancel').trigger('click')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(deleteRunMock).not.toHaveBeenCalled()
  })

  it('retry button is only shown for error-status runs (RH-007-S01)', () => {
    const wrapper = mountPage()
    const retryBtns = wrapper.findAll('.run-history-page__retry-btn')
    expect(retryBtns).toHaveLength(1)
    // It belongs to the error entry, which also shows the error reason honestly
    const errorRow = wrapper.findAll('.run-history-page__row')[1]
    expect(errorRow.find('.run-history-page__retry-btn').exists()).toBe(true)
    expect(errorRow.text()).toContain('pipeline exploded')
  })

  it('retry calls retryRun and refreshes the runs query (RH-007-S02)', async () => {
    const wrapper = mountPage()
    await wrapper.find('.run-history-page__retry-btn').trigger('click')
    await flushPromises()
    expect(retryRunMock).toHaveBeenCalledWith('err-1')
    expect(refetchSpy).toHaveBeenCalled()
  })

  it('deleting the currently-open run clears runStore and navigates home (RH-009-S04)', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const { useRunStore } = await import('@/stores/run')
    const store = useRunStore()
    store.runId = 'xyz'

    const wrapper = mount(RunHistoryPage, {
      global: { plugins: [pinia], stubs: { Teleport: { template: '<slot />' } } },
    })
    await wrapper.findAll('.run-history-page__delete-btn')[0].trigger('click')
    await wrapper.find('.run-history-page__dialog-confirm').trigger('click')
    await flushPromises()

    expect(deleteRunMock).toHaveBeenCalledWith('xyz')
    expect(store.runId).toBeNull()
    expect(pushSpy).toHaveBeenCalledWith('/')
  })

  it('retry 409 (another run processing) is surfaced honestly, never silent', async () => {
    retryRunMock.mockRejectedValueOnce(
      Object.assign(new Error('Conflict'), {
        response: { status: 409, data: { detail: 'another run is processing' } },
      }),
    )
    const wrapper = mountPage()
    await wrapper.find('.run-history-page__retry-btn').trigger('click')
    await flushPromises()
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
  })
})
