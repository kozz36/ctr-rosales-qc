/**
 * TDD RED — ReviewPage tabs keyboard navigation (W1 / WAI-ARIA tabs pattern).
 *
 * ctr-review W1: the tablist has a roving tabindex (one tab focusable) but NO
 * arrow-key navigation. Per the WAI-ARIA Authoring Practices "tabs" pattern,
 * ArrowRight/ArrowLeft move focus+activation between tabs, Home/End jump to the
 * first/last tab. Without this, keyboard users are stuck on the focusable tab.
 *
 * Fails against the current code (no @keydown handler on the tabs).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import type {
  ReconciliationTableResponse,
  RunStatusResponse,
} from '@/api/types'

const { MOCK_TABLE, MOCK_STATUS } = vi.hoisted(() => {
  const MOCK_TABLE: ReconciliationTableResponse = {
    run_id: 'rp-kbd-run',
    rows: [],
    unresolved_guias: [],
    errored_guias: [
      { registro: '232', guia_id: 'T009-A', source_pages: [5], retry_attempted: true },
    ],
  }
  const MOCK_STATUS: RunStatusResponse = {
    run_id: 'rp-kbd-run',
    status: 'review',
    vision_calls_made: 0,
    warnings: [],
    error: null,
    started_at: null,
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
  queryKeys: {
    runStatus: (id: string) => ['run', id, 'status'],
    table: (id: string) => ['run', id, 'table'],
  },
}))

vi.mock('@/api/client', () => ({
  getRunStatus: vi.fn().mockResolvedValue(MOCK_STATUS),
  retryGuia: vi.fn(),
  reprocessGuia: vi.fn(),
  reprocessRegistroBatch: vi.fn(),
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
  QueryClient: vi.fn().mockImplementation(() => ({ invalidateQueries: vi.fn() })),
}))

vi.mock('@/features/review/ReviewGrid.vue', () => ({
  default: { name: 'ReviewGrid', template: '<div class="stub-review-grid" />', props: ['rows', 'runId', 'isLoading', 'error', 'pendingEdits', 'activeFilter'], emits: ['open-reassign', 'page-click', 'filter-change', 'retry'] },
}))
vi.mock('@/features/review/GuiaReassignDialog.vue', () => ({
  default: { name: 'GuiaReassignDialog', template: '<div class="stub-reassign" />', props: ['modelValue', 'guiaId', 'row', 'isPending', 'apiError'], emits: ['update:modelValue', 'submit'] },
}))
vi.mock('@/features/review/ExportButton.vue', () => ({
  default: { name: 'ExportButton', template: '<button class="stub-export" />', props: ['disabled', 'isPending', 'error'], emits: ['export'] },
}))
vi.mock('@/features/review/UnresolvedGuiasPanel.vue', () => ({
  default: { name: 'UnresolvedGuiasPanel', template: '<div class="stub-unresolved" />', props: ['unresolvedGuias'], emits: ['assign-guia'] },
}))
vi.mock('@/features/review/PageSheetViewer.vue', () => ({
  default: { name: 'PageSheetViewer', template: '<div class="stub-viewer" />', props: ['modelValue', 'runId', 'page', 'rowPages'], emits: ['update:modelValue'] },
}))
vi.mock('@/features/review/PendientesPorProcesarTab.vue', () => ({
  default: { name: 'PendientesPorProcesarTab', template: '<div class="stub-pendientes-tab" />', props: ['erroredGuias', 'runId', 'rows'], emits: ['refetch'] },
}))

import ReviewPage from '@/features/review/ReviewPage.vue'

function mountReviewPage() {
  return mount(ReviewPage, {
    props: { id: 'rp-kbd-run' },
    attachTo: document.body,
    global: { plugins: [createPinia()], stubs: { Teleport: { template: '<slot />' } } },
  })
}

describe('ReviewPage — tabs keyboard navigation (W1 / WAI-ARIA)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('ArrowRight from the first tab activates the next tab', async () => {
    const wrapper = mountReviewPage()
    const tabs = wrapper.findAll('[role="tab"]')
    const reconTab = tabs.find((t) => t.text().includes('Reconciliación'))!
    const pendientesTab = tabs.find((t) => t.text().includes('Pendientes'))!

    await reconTab.trigger('keydown', { key: 'ArrowRight' })

    expect(pendientesTab.attributes('aria-selected')).toBe('true')
    expect(reconTab.attributes('aria-selected')).toBe('false')
  })

  it('ArrowLeft from the last tab wraps to the first tab', async () => {
    const wrapper = mountReviewPage()
    const tabs = wrapper.findAll('[role="tab"]')
    const reconTab = tabs.find((t) => t.text().includes('Reconciliación'))!
    const pendientesTab = tabs.find((t) => t.text().includes('Pendientes'))!

    // Move to Pendientes first, then ArrowLeft wraps back to Reconciliación.
    await pendientesTab.trigger('click')
    await pendientesTab.trigger('keydown', { key: 'ArrowLeft' })

    expect(reconTab.attributes('aria-selected')).toBe('true')
  })

  it('Home activates the first tab, End the last tab', async () => {
    const wrapper = mountReviewPage()
    const tabs = wrapper.findAll('[role="tab"]')
    const reconTab = tabs.find((t) => t.text().includes('Reconciliación'))!
    const pendientesTab = tabs.find((t) => t.text().includes('Pendientes'))!

    await reconTab.trigger('keydown', { key: 'End' })
    expect(pendientesTab.attributes('aria-selected')).toBe('true')

    await pendientesTab.trigger('keydown', { key: 'Home' })
    expect(reconTab.attributes('aria-selected')).toBe('true')
  })
})
