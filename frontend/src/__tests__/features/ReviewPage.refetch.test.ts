/**
 * TDD RED — ReviewPage ErroredGuiasPanel wiring contract.
 *
 * Bug 1: @reprocess and @reprocess-success are NOT wired in ReviewPage.vue,
 *        so tableQuery.refetch() is never called after a reprocess settles.
 *
 * Bug 2: @retry fires BEFORE the backend call resolves (optimistic emit at the
 *        START of handleRetry), and @retry-success fires ONLY when recovered===true.
 *        On a failed retry (recovered=false) there is NO post-settle refetch,
 *        so the retry_attempted-gated "Reprocesar con IA" button never appears
 *        without a manual reload.
 *
 * Required behavior after fix:
 *   - @reprocess-success → tableQuery.refetch()
 *   - @reprocess-settled → tableQuery.refetch()   (new settle event from finally block)
 *   - @retry-settled     → tableQuery.refetch()   (new settle event from finally block)
 *
 * The existing @retry and @retry-success wiring must NOT be broken.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import type {
  ReconciliationTableResponse,
  RunStatusResponse,
} from '@/api/types'

// ---------------------------------------------------------------------------
// Shared mock fixtures (hoisted for vi.mock closure access)
// ---------------------------------------------------------------------------

const { MOCK_TABLE_ERRORED, MOCK_STATUS, refetchSpy } = vi.hoisted(() => {
  const refetchSpy = vi.fn()

  const MOCK_TABLE_ERRORED: ReconciliationTableResponse = {
    run_id: 'rp-refetch-run',
    rows: [],
    unresolved_guias: [],
    errored_guias: [
      {
        registro: 'R001',
        guia_id: 'T009-AABB',
        source_pages: [5, 6],
        retry_attempted: true, // already retried → Reprocesar button is shown
      },
    ],
  }

  const MOCK_STATUS: RunStatusResponse = {
    run_id: 'rp-refetch-run',
    status: 'review',
    vision_calls_made: 0,
    warnings: [],
    error: null,
    started_at: null,
    progress: null,
  }

  return { MOCK_TABLE_ERRORED, MOCK_STATUS, refetchSpy }
})

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

vi.mock('@/composables/useReconciliationApi', () => ({
  useTable: () => ({
    data: { value: MOCK_TABLE_ERRORED },
    isFetching: { value: false },
    error: { value: null },
    refetch: refetchSpy,
  }),
  useReassignGuia: () => ({
    mutateAsync: vi.fn(),
    isPending: { value: false },
  }),
  useExportRun: () => ({
    mutateAsync: vi.fn(),
    isPending: { value: false },
  }),
  queryKeys: {
    runStatus: (id: string) => ['run', id, 'status'],
    table: (id: string) => ['run', id, 'table'],
  },
}))

vi.mock('@/api/client', () => ({
  getRunStatus: vi.fn().mockResolvedValue(MOCK_STATUS),
  retryGuia: vi.fn(),
  reprocessGuia: vi.fn(),
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
  useQueryClient: vi.fn().mockReturnValue({
    invalidateQueries: vi.fn(),
  }),
  useMutation: vi.fn().mockImplementation(() => ({
    mutateAsync: vi.fn(),
    isPending: { value: false },
  })),
  QueryClient: vi.fn().mockImplementation(() => ({
    invalidateQueries: vi.fn(),
  })),
}))

// Stub heavy components that are NOT under test here
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

// ErroredGuiasPanel is stubbed with a passthrough that exposes the event bus.
// We emit events programmatically from test bodies.
vi.mock('@/features/review/ErroredGuiasPanel.vue', () => ({
  default: {
    name: 'ErroredGuiasPanel',
    template: '<div class="stub-errored-panel" />',
    props: ['erroredGuias', 'runId'],
    emits: ['retry', 'retry-success', 'retry-settled', 'reprocess', 'reprocess-success', 'reprocess-settled'],
  },
}))

import ReviewPage from '@/features/review/ReviewPage.vue'

// ---------------------------------------------------------------------------
// Mount helper
// ---------------------------------------------------------------------------

function mountReviewPage() {
  return mount(ReviewPage, {
    props: { id: 'rp-refetch-run' },
    global: {
      plugins: [createPinia()],
      stubs: { Teleport: { template: '<slot />' } },
    },
  })
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

describe('ReviewPage — ErroredGuiasPanel wiring (refetch contract)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('renders ErroredGuiasPanel when errored_guias is non-empty', () => {
    const wrapper = mountReviewPage()
    expect(wrapper.find('.stub-errored-panel').exists()).toBe(true)
  })

  // -------------------------------------------------------------------------
  // Bug 1: @reprocess-success NOT wired
  // -------------------------------------------------------------------------

  it('calls tableQuery.refetch() when ErroredGuiasPanel emits reprocess-success', async () => {
    const wrapper = mountReviewPage()
    const panel = wrapper.findComponent({ name: 'ErroredGuiasPanel' })
    expect(panel.exists()).toBe(true)

    // Emit reprocess-success from the panel — ReviewPage MUST call refetch.
    // This FAILS before the fix because @reprocess-success is not wired.
    await panel.vm.$emit('reprocess-success', { guiaId: 'T009-AABB', erroredGuias: [] })
    await flushPromises()

    expect(refetchSpy).toHaveBeenCalled()
  })

  // -------------------------------------------------------------------------
  // Bug 2a: @retry-settled NOT wired → failed retry never triggers refetch
  // -------------------------------------------------------------------------

  it('calls tableQuery.refetch() when ErroredGuiasPanel emits retry-settled', async () => {
    const wrapper = mountReviewPage()
    const panel = wrapper.findComponent({ name: 'ErroredGuiasPanel' })

    // retry-settled is a new settle event emitted in the finally block of handleRetry.
    // ReviewPage must wire it so a failed retry (recovered=false) still triggers refetch.
    // This FAILS before the fix because @retry-settled is not wired.
    await panel.vm.$emit('retry-settled', 'T009-AABB')
    await flushPromises()

    expect(refetchSpy).toHaveBeenCalled()
  })

  // -------------------------------------------------------------------------
  // Bug 2b: @reprocess-settled NOT wired → failed reprocess never triggers refetch
  // -------------------------------------------------------------------------

  it('calls tableQuery.refetch() when ErroredGuiasPanel emits reprocess-settled', async () => {
    const wrapper = mountReviewPage()
    const panel = wrapper.findComponent({ name: 'ErroredGuiasPanel' })

    // reprocess-settled is emitted in the finally block of handleReprocess.
    // This FAILS before the fix because @reprocess-settled is not wired.
    await panel.vm.$emit('reprocess-settled', 'T009-AABB')
    await flushPromises()

    expect(refetchSpy).toHaveBeenCalled()
  })

  // -------------------------------------------------------------------------
  // Regression guard: existing @retry and @retry-success wiring must still work
  // -------------------------------------------------------------------------

  it('still calls tableQuery.refetch() when ErroredGuiasPanel emits retry (existing wiring)', async () => {
    const wrapper = mountReviewPage()
    const panel = wrapper.findComponent({ name: 'ErroredGuiasPanel' })
    await panel.vm.$emit('retry', 'T009-AABB')
    await flushPromises()
    expect(refetchSpy).toHaveBeenCalled()
  })

  it('still calls tableQuery.refetch() when ErroredGuiasPanel emits retry-success (existing wiring)', async () => {
    const wrapper = mountReviewPage()
    const panel = wrapper.findComponent({ name: 'ErroredGuiasPanel' })
    await panel.vm.$emit('retry-success', { guiaId: 'T009-AABB', erroredGuias: [] })
    await flushPromises()
    expect(refetchSpy).toHaveBeenCalled()
  })
})
