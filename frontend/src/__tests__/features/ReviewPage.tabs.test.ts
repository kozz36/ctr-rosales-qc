/**
 * TDD RED — ReviewPage tabs: Reconciliación | Pendientes por procesar (F3 / REV-R23 / D7).
 *
 * Contract:
 *   - ReviewPage renders a tablist with two tabs: "Reconciliación" and
 *     "Pendientes por procesar".
 *   - Default active tab is "Reconciliación" (reconciliation grid visible,
 *     PendientesPorProcesarTab NOT mounted).
 *   - The "Pendientes" tab shows a count badge = erroredGuias.length.
 *   - Clicking "Pendientes" activates it and mounts PendientesPorProcesarTab
 *     (errored panel + bulk button); the grid is no longer in this tab.
 *   - Tabs use proper ARIA (role=tablist/tab/tabpanel, aria-selected).
 *
 * Fails before ReviewPage gains the tab bar + activeTab ref and the
 * PendientesPorProcesarTab component exists.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import type {
  ReconciliationTableResponse,
  RunStatusResponse,
} from '@/api/types'

const { MOCK_TABLE, MOCK_STATUS, refetchSpy } = vi.hoisted(() => {
  const refetchSpy = vi.fn()

  const MOCK_TABLE: ReconciliationTableResponse = {
    run_id: 'rp-tabs-run',
    rows: [],
    unresolved_guias: [],
    errored_guias: [
      { registro: '232', guia_id: 'T009-A', source_pages: [5], retry_attempted: true },
      { registro: '232', guia_id: 'T009-B', source_pages: [6], retry_attempted: true },
      { registro: '230', guia_id: 'T009-C', source_pages: [7], retry_attempted: true },
    ],
  }

  const MOCK_STATUS: RunStatusResponse = {
    run_id: 'rp-tabs-run',
    status: 'review',
    vision_calls_made: 0,
    warnings: [],
    error: null,
    started_at: null,
    progress: null,
  }

  return { MOCK_TABLE, MOCK_STATUS, refetchSpy }
})

vi.mock('@/composables/useReconciliationApi', () => ({
  useTable: () => ({
    data: { value: MOCK_TABLE },
    isFetching: { value: false },
    error: { value: null },
    refetch: refetchSpy,
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

// Stub heavy children — we only assert tab structure + which child mounts.
vi.mock('@/features/review/ReviewGrid.vue', () => ({
  default: {
    name: 'ReviewGrid',
    template: '<div class="stub-review-grid" />',
    props: ['rows', 'runId', 'isLoading', 'error', 'pendingEdits', 'activeFilter'],
    emits: ['open-reassign', 'page-click', 'filter-change', 'retry'],
  },
}))

vi.mock('@/features/review/GuiaReassignDialog.vue', () => ({
  default: {
    name: 'GuiaReassignDialog',
    template: '<div class="stub-reassign" />',
    props: ['modelValue', 'guiaId', 'row', 'isPending', 'apiError'],
    emits: ['update:modelValue', 'submit'],
  },
}))

vi.mock('@/features/review/ExportButton.vue', () => ({
  default: {
    name: 'ExportButton',
    template: '<button class="stub-export" />',
    props: ['disabled', 'isPending', 'error'],
    emits: ['export'],
  },
}))

vi.mock('@/features/review/UnresolvedGuiasPanel.vue', () => ({
  default: {
    name: 'UnresolvedGuiasPanel',
    template: '<div class="stub-unresolved" />',
    props: ['unresolvedGuias'],
    emits: ['assign-guia'],
  },
}))

vi.mock('@/features/review/PageSheetViewer.vue', () => ({
  default: {
    name: 'PageSheetViewer',
    template: '<div class="stub-viewer" />',
    props: ['modelValue', 'runId', 'page', 'rowPages'],
    emits: ['update:modelValue'],
  },
}))

vi.mock('@/features/review/PendientesPorProcesarTab.vue', () => ({
  default: {
    name: 'PendientesPorProcesarTab',
    template: '<div class="stub-pendientes-tab" />',
    props: ['erroredGuias', 'runId', 'rows'],
    emits: ['refetch'],
  },
}))

import ReviewPage from '@/features/review/ReviewPage.vue'

function mountReviewPage() {
  return mount(ReviewPage, {
    props: { id: 'rp-tabs-run' },
    global: {
      plugins: [createPinia()],
      stubs: { Teleport: { template: '<slot />' } },
    },
  })
}

describe('ReviewPage — tabs (F3 / REV-R23)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('renders a tablist with Reconciliación and Pendientes tabs', () => {
    const wrapper = mountReviewPage()
    const tablist = wrapper.find('[role="tablist"]')
    expect(tablist.exists()).toBe(true)

    const tabs = wrapper.findAll('[role="tab"]')
    expect(tabs).toHaveLength(2)
    const labels = tabs.map((t) => t.text())
    expect(labels.some((l) => l.includes('Reconciliación'))).toBe(true)
    expect(labels.some((l) => l.includes('Pendientes'))).toBe(true)
  })

  it('defaults to the Reconciliación tab (grid visible, Pendientes tab not mounted)', () => {
    const wrapper = mountReviewPage()
    expect(wrapper.find('.stub-review-grid').exists()).toBe(true)
    expect(wrapper.find('.stub-pendientes-tab').exists()).toBe(false)

    // Reconciliación tabpanel is visible (not display:none); Pendientes is absent.
    const reconPanel = wrapper.find('#tabpanel-reconciliacion')
    expect(reconPanel.exists()).toBe(true)
    expect(reconPanel.attributes('style') ?? '').not.toContain('display: none')

    const tabs = wrapper.findAll('[role="tab"]')
    const reconTab = tabs.find((t) => t.text().includes('Reconciliación'))
    expect(reconTab?.attributes('aria-selected')).toBe('true')
  })

  it('shows the errored count badge on the Pendientes tab', () => {
    const wrapper = mountReviewPage()
    const tabs = wrapper.findAll('[role="tab"]')
    const pendientesTab = tabs.find((t) => t.text().includes('Pendientes'))
    expect(pendientesTab).toBeDefined()
    // 3 errored guías in the mock table → badge "3".
    expect(pendientesTab!.text()).toContain('3')
  })

  it('activates Pendientes tab on click: mounts PendientesPorProcesarTab, hides the grid', async () => {
    const wrapper = mountReviewPage()
    const tabs = wrapper.findAll('[role="tab"]')
    const pendientesTab = tabs.find((t) => t.text().includes('Pendientes'))!

    await pendientesTab.trigger('click')

    // Pendientes panel now mounted + visible; Reconciliación panel hidden via v-show.
    expect(wrapper.find('.stub-pendientes-tab').exists()).toBe(true)
    const reconPanel = wrapper.find('#tabpanel-reconciliacion')
    expect(reconPanel.attributes('style') ?? '').toContain('display: none')
    expect(pendientesTab.attributes('aria-selected')).toBe('true')
  })
})
