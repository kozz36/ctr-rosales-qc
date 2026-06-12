/**
 * ReviewPage cold-load + runStore persistence (SDD#3 PR-3, RH-011).
 *
 * Cold-load gap: navigating directly to /runs/{id} (server restart, history
 * click, browser refresh) mounted ReviewPage with runStore.runId still null —
 * the header nav never appeared and [Batch actual] had no target. The fix is
 * the D6 one-line mount hook: ReviewPage setup adopts the route param into
 * the store, and the store persists runId to localStorage ("run_id" key —
 * pre-work 3.0.3 confirmed no existing consumer of that key).
 *
 * Mock harness mirrors ReviewPage.refetch.test.ts (composables + vue-query +
 * child components stubbed; the Pinia stores are real).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useRunStore } from '@/stores/run'
import { installLocalStorageStub } from '../test-utils/local-storage-stub'
import type {
  ReconciliationTableResponse,
  RunStatusResponse,
} from '@/api/types'

const { MOCK_TABLE, MOCK_STATUS } = vi.hoisted(() => {
  const MOCK_TABLE: ReconciliationTableResponse = {
    run_id: 'abc123',
    rows: [],
    unresolved_guias: [],
    errored_guias: [],
  }
  const MOCK_STATUS: RunStatusResponse = {
    run_id: 'abc123',
    status: 'review',
    vision_calls_made: 0,
    warnings: [],
    error: null,
    started_at: '2026-06-11T12:00:00+00:00',
    progress: null,
  }
  return { MOCK_TABLE, MOCK_STATUS }
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
  getRunStatus: vi.fn().mockResolvedValue(MOCK_STATUS),
  reassignGuia: vi.fn(),
  exportRun: vi.fn(),
}))

vi.mock('@tanstack/vue-query', () => ({
  useQuery: vi.fn().mockImplementation(() => ({
    data: { value: MOCK_STATUS },
    isFetching: { value: false },
    error: { value: null },
    refetch: vi.fn(),
  })),
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
import App from '@/app/App.vue'

function mountReviewPage(pinia = createPinia()) {
  return mount(ReviewPage, {
    props: { id: 'abc123' },
    global: {
      plugins: [pinia],
      stubs: { Teleport: { template: '<slot />' } },
    },
  })
}

describe('ReviewPage cold-load (RH-011)', () => {
  beforeEach(() => {
    installLocalStorageStub()
    setActivePinia(createPinia())
  })

  it('sets runStore.runId from route param on mount (RH-011-S01)', () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useRunStore()
    expect(store.runId).toBeNull()

    mountReviewPage(pinia)

    expect(store.runId).toBe('abc123')
  })

  it('adopts the route param when it differs from a stale runId (history nav)', () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useRunStore()
    store.runId = 'old-run'

    mountReviewPage(pinia)

    expect(store.runId).toBe('abc123')
  })

  it('Revisión nav link appears after cold-load sets runStore state (RH-011-S03)', () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useRunStore()
    expect(store.isReady).toBe(false)

    // Cold-load: ReviewPage adopts the route param AND mirrors the polled
    // status (review) into the store...
    mountReviewPage(pinia)
    expect(store.runId).toBe('abc123')
    expect(store.isReady).toBe(true)

    // ...which is exactly the gate App.vue uses for the "Revisión" nav link.
    const app = mount(App, {
      global: {
        plugins: [pinia],
        stubs: {
          RouterLink: { template: '<a><slot /></a>', props: ['to'] },
          RouterView: { template: '<div />' },
          RunHistoryMenu: true,
        },
      },
    })
    expect(app.text()).toContain('Revisión')
  })

  it('runStore.runId persists in localStorage after assignment (RH-011-S02)', () => {
    const store = useRunStore()
    store.runId = 'abc123'
    expect(localStorage.getItem('run_id')).toBe('abc123')
  })

  it('runStore initializes runId from localStorage (browser refresh)', () => {
    localStorage.setItem('run_id', 'persisted-run')
    setActivePinia(createPinia()) // fresh pinia = fresh store instance
    const store = useRunStore()
    expect(store.runId).toBe('persisted-run')
  })

  it('reset() clears the localStorage key (no stale run after [Nuevo batch])', () => {
    const store = useRunStore()
    store.runId = 'abc123'
    expect(localStorage.getItem('run_id')).toBe('abc123')
    store.reset()
    expect(localStorage.getItem('run_id')).toBeNull()
  })
})
