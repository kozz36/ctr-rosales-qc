/**
 * ReviewPage cold-load 404 empty-state (ctr-review W1).
 *
 * Bug: a stale localStorage `run_id` (the run was swept/deleted) drives the
 * status query to a perpetual 404; with no error handling ReviewPage stayed on
 * the "Esperando que el pipeline complete..." spinner forever (TanStack retries
 * the 404 indefinitely). The fix renders a graceful es-PE empty-state, clears
 * the stale localStorage run_id (runStore.reset), and stops retrying on 4xx.
 *
 * Harness mirrors ReviewPage.coldload.test.ts: composables + child components
 * stubbed; the Pinia store is real; useQuery is mocked to surface a 404 error.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useRunStore } from '@/stores/run'
import { installLocalStorageStub } from '../test-utils/local-storage-stub'
import type { ReconciliationTableResponse } from '@/api/types'

const { MOCK_TABLE, NOT_FOUND_ERROR, capturedRetry, capturedRefetchInterval } = vi.hoisted(() => {
  const MOCK_TABLE: ReconciliationTableResponse = {
    run_id: 'stale-run',
    rows: [],
    unresolved_guias: [],
    errored_guias: [],
  }
  // Axios-shaped 404 (mirrors `(err as { response?: { status? } }).response.status`).
  const NOT_FOUND_ERROR = { response: { status: 404 } }
  const capturedRetry: { fn: ((failureCount: number, error: unknown) => boolean) | null } = {
    fn: null,
  }
  const capturedRefetchInterval: { fn: ((query: unknown) => number | false) | null } = {
    fn: null,
  }
  return { MOCK_TABLE, NOT_FOUND_ERROR, capturedRetry, capturedRefetchInterval }
})

vi.mock('@/composables/useReconciliationApi', () => ({
  useTable: () => ({
    data: { value: MOCK_TABLE },
    isFetching: { value: false },
    error: { value: null },
    refetch: vi.fn(),
  }),
  useReassignGuia: () => ({ mutateAsync: vi.fn(), isPending: { value: false } }),
  useExportRun: () => ({ mutateAsync: vi.fn(), isPending: { value: false } }),
  useRunsList: () => ({
    data: { value: [] },
    isFetching: { value: false },
    error: { value: null },
    refetch: vi.fn(),
  }),
  queryKeys: {
    runStatus: (id: string) => ['run', id, 'status'],
    table: (id: string) => ['run', id, 'table'],
    runs: () => ['runs'],
  },
}))

vi.mock('@/api/client', () => ({
  createRun: vi.fn(),
  getRunStatus: vi.fn().mockRejectedValue(NOT_FOUND_ERROR),
  reassignGuia: vi.fn(),
  exportRun: vi.fn(),
}))

// useQuery for the status query surfaces a 404 error (data undefined). We also
// capture the `retry` option so the test can assert 4xx is NOT retried.
vi.mock('@tanstack/vue-query', () => ({
  useQuery: vi.fn().mockImplementation((opts: { retry?: unknown; refetchInterval?: unknown }) => {
    if (typeof opts.retry === 'function') {
      capturedRetry.fn = opts.retry as (failureCount: number, error: unknown) => boolean
    }
    if (typeof opts.refetchInterval === 'function') {
      capturedRefetchInterval.fn = opts.refetchInterval as (query: unknown) => number | false
    }
    return {
      data: { value: undefined },
      isFetching: { value: false },
      error: { value: NOT_FOUND_ERROR },
      isError: { value: true },
      refetch: vi.fn(),
    }
  }),
  useQueryClient: vi.fn().mockReturnValue({ invalidateQueries: vi.fn() }),
  useMutation: vi.fn().mockImplementation(() => ({
    mutateAsync: vi.fn(),
    isPending: { value: false },
  })),
}))

vi.mock('@/features/review/ReviewGrid.vue', () => ({
  default: { name: 'ReviewGrid', template: '<div class="stub-review-grid" />', props: ['rows', 'runId', 'isLoading', 'error', 'pendingEdits', 'activeFilter'] },
}))
vi.mock('@/features/review/GuiaReassignDialog.vue', () => ({
  default: { name: 'GuiaReassignDialog', template: '<div />', props: ['modelValue', 'guiaId', 'row', 'isPending', 'apiError'] },
}))
vi.mock('@/features/review/ExportButton.vue', () => ({
  default: { name: 'ExportButton', template: '<button />', props: ['disabled', 'isPending', 'error'] },
}))
vi.mock('@/features/review/UnresolvedGuiasPanel.vue', () => ({
  default: { name: 'UnresolvedGuiasPanel', template: '<div />', props: ['unresolvedGuias'] },
}))
vi.mock('@/features/review/PendientesPorProcesarTab.vue', () => ({
  default: { name: 'PendientesPorProcesarTab', template: '<div />', props: ['erroredGuias', 'runId', 'rows'] },
}))
vi.mock('@/features/review/DescartadasTab.vue', () => ({
  default: { name: 'DescartadasTab', template: '<div />', props: ['discardedPages', 'runId'] },
}))
vi.mock('@/features/review/PageSheetViewer.vue', () => ({
  default: { name: 'PageSheetViewer', template: '<div />', props: ['modelValue', 'runId', 'page', 'rowPages'] },
}))

import ReviewPage from '@/features/review/ReviewPage.vue'

function mountReviewPage(pinia = createPinia()) {
  return mount(ReviewPage, {
    props: { id: 'stale-run' },
    global: {
      plugins: [pinia],
      stubs: {
        Teleport: { template: '<slot />' },
        RouterLink: { template: '<a><slot /></a>', props: ['to'] },
      },
    },
  })
}

describe('ReviewPage cold-load 404 empty-state (W1)', () => {
  beforeEach(() => {
    installLocalStorageStub()
    setActivePinia(createPinia())
  })

  it('renders a graceful empty-state instead of an infinite spinner on 404', () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mountReviewPage(pinia)

    // No infinite "Esperando..." spinner.
    expect(wrapper.find('.review-page__waiting').exists()).toBe(false)
    // Graceful es-PE not-found message.
    expect(wrapper.text()).toContain('Ejecución no encontrada')
  })

  it('clears the stale localStorage run_id on 404 (runStore reset)', () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useRunStore()
    store.runId = 'stale-run'
    expect(localStorage.getItem('run_id')).toBe('stale-run')

    mountReviewPage(pinia)

    expect(store.runId).toBeNull()
    expect(localStorage.getItem('run_id')).toBeNull()
  })

  it('does NOT retry the status query on a 4xx (404) error', () => {
    mountReviewPage()

    expect(capturedRetry.fn).not.toBeNull()
    // 404 → never retry.
    expect(capturedRetry.fn!(0, NOT_FOUND_ERROR)).toBe(false)
    // A 5xx / network error → still retried (bounded).
    expect(capturedRetry.fn!(0, { response: { status: 503 } })).toBe(true)
  })

  it('stops the refetchInterval polling once the status query is in a terminal error state', () => {
    // SA-5-caught: `retry: false` does NOT govern refetchInterval (independent
    // scheduling path) — after a 404 the page kept polling every 2s forever.
    mountReviewPage()

    expect(capturedRefetchInterval.fn).not.toBeNull()
    // Terminal error (404, retries exhausted) → polling must STOP.
    expect(
      capturedRefetchInterval.fn!({ state: { data: undefined, error: NOT_FOUND_ERROR } }),
    ).toBe(false)
    // Healthy in-flight run (no data yet, no error) → keep polling.
    expect(
      capturedRefetchInterval.fn!({ state: { data: undefined, error: null } }),
    ).toBe(2000)
    // Completed run → polling stops (pre-existing behavior preserved).
    expect(
      capturedRefetchInterval.fn!({ state: { data: { status: 'review' }, error: null } }),
    ).toBe(false)
  })
})
