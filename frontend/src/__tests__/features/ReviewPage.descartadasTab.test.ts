/**
 * TDD RED — ReviewPage third tab: [Descartadas para revisión] (SDD#2 PR-3a).
 *
 * Contract (REV-R27):
 *   - ReviewPage renders THREE tabs in TAB_ORDER:
 *     Reconciliación (0, default) | Pendientes por procesar (1) | Descartadas para revisión (2).
 *   - The Descartadas tab shows a count badge = discarded_pages.length;
 *     badge hidden (or "0") when there are no discarded entries — tab stays present.
 *   - Existing Reconciliación/Pendientes behavior is unchanged (REV-R27-S03).
 *
 * Fails before ReviewPage gains the third tab (TabKey/TAB_ORDER extension).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import type {
  DiscardedPageResponse,
  ReconciliationTableResponse,
  RunStatusResponse,
} from '@/api/types'

const { state, MOCK_STATUS, refetchSpy } = vi.hoisted(() => {
  const refetchSpy = vi.fn()

  // Mutable per-test table state — useTable mock reads it at mount time.
  const state: { table: ReconciliationTableResponse } = {
    table: {
      run_id: 'rp-desc-run',
      rows: [],
      unresolved_guias: [],
      errored_guias: [],
      discarded_pages: [],
    },
  }

  const MOCK_STATUS: RunStatusResponse = {
    run_id: 'rp-desc-run',
    status: 'review',
    vision_calls_made: 0,
    warnings: [],
    error: null,
    started_at: null,
    progress: null,
  }

  return { state, MOCK_STATUS, refetchSpy }
})

vi.mock('@/composables/useReconciliationApi', () => ({
  useTable: () => ({
    data: { value: state.table },
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
  getReprocessBatchStatus: vi.fn(),
  reassignGuia: vi.fn(),
  exportRun: vi.fn(),
  recoverDiscardedPage: vi.fn(),
  recoverDiscardedBatch: vi.fn(),
  getDiscardedRecoverStatus: vi.fn(),
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

function makeDiscarded(
  overrides: Partial<DiscardedPageResponse> = {},
): DiscardedPageResponse {
  return { page: 152, registro: '232', has_cached_lines: true, ...overrides }
}

function setTable(overrides: Partial<ReconciliationTableResponse> = {}): void {
  state.table = {
    run_id: 'rp-desc-run',
    rows: [],
    unresolved_guias: [],
    errored_guias: [],
    discarded_pages: [],
    ...overrides,
  }
}

function mountReviewPage() {
  return mount(ReviewPage, {
    props: { id: 'rp-desc-run' },
    global: {
      plugins: [createPinia()],
      stubs: { Teleport: { template: '<slot />' } },
    },
  })
}

describe('ReviewPage — Descartadas para revisión tab (REV-R27 / PR-3a)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    setTable()
  })

  // 3a.1.1 — three tabs rendered, TAB_ORDER preserved, default Reconciliación.
  it('renders three tabs in TAB_ORDER with Descartadas appended; default is Reconciliación', () => {
    setTable({
      discarded_pages: [makeDiscarded({ page: 152, registro: '232' })],
    })
    const wrapper = mountReviewPage()

    const tabs = wrapper.findAll('[role="tab"]')
    expect(tabs).toHaveLength(3)

    // Runtime TAB_ORDER check: DOM order mirrors
    // ['reconciliacion', 'pendientes', 'descartadas'].
    expect(tabs[0].text()).toContain('Reconciliación')
    expect(tabs[1].text()).toContain('Pendientes por procesar')
    expect(tabs[2].text()).toContain('Descartadas para revisión')

    // Default active tab is Reconciliación (index 0).
    expect(tabs[0].attributes('aria-selected')).toBe('true')
    expect(tabs[2].attributes('aria-selected')).toBe('false')
    expect(wrapper.find('.stub-review-grid').exists()).toBe(true)
  })

  // 3a.1.2 — count badge shows the discarded total.
  it('shows the discarded count badge on the Descartadas tab', () => {
    setTable({
      discarded_pages: [
        makeDiscarded({ page: 152, registro: '232' }),
        makeDiscarded({ page: 175, registro: '230', has_cached_lines: false }),
      ],
    })
    const wrapper = mountReviewPage()

    const tabs = wrapper.findAll('[role="tab"]')
    const descartadasTab = tabs.find((t) => t.text().includes('Descartadas'))
    expect(descartadasTab).toBeDefined()
    expect(descartadasTab!.text()).toContain('2')
  })

  // 3a.1.3 — zero discarded: tab present, badge hidden (or "0").
  it('keeps the Descartadas tab present with zero entries; badge is hidden or "0"', () => {
    setTable({ discarded_pages: [] })
    const wrapper = mountReviewPage()

    const tabs = wrapper.findAll('[role="tab"]')
    expect(tabs).toHaveLength(3)
    const descartadasTab = tabs.find((t) => t.text().includes('Descartadas'))!
    expect(descartadasTab.exists()).toBe(true)

    // Badge hidden (mirrors the Pendientes v-if pattern) or showing "0".
    const badge = descartadasTab.find('.review-page__tab-badge')
    if (badge.exists()) {
      expect(badge.text()).toBe('0')
    } else {
      expect(descartadasTab.text().trim()).toBe('Descartadas para revisión')
    }
  })

  // 3a.1.4 — existing tabs unaffected (REV-R27-S03).
  it('preserves existing tab indices and Pendientes behavior', async () => {
    setTable({
      errored_guias: [
        { registro: '232', guia_id: 'T009-A', source_pages: [5], retry_attempted: true },
      ],
      discarded_pages: [makeDiscarded()],
    })
    const wrapper = mountReviewPage()

    const tabs = wrapper.findAll('[role="tab"]')
    // Indices preserved: Reconciliación=0, Pendientes=1.
    expect(tabs[0].text()).toContain('Reconciliación')
    expect(tabs[1].text()).toContain('Pendientes')

    // Pendientes badge still driven by errored_guias.
    expect(tabs[1].text()).toContain('1')

    // Clicking Pendientes still mounts PendientesPorProcesarTab and hides the grid.
    await tabs[1].trigger('click')
    expect(wrapper.find('.stub-pendientes-tab').exists()).toBe(true)
    expect(tabs[1].attributes('aria-selected')).toBe('true')
    const reconPanel = wrapper.find('#tabpanel-reconciliacion')
    expect(reconPanel.attributes('style') ?? '').toContain('display: none')
  })
})
