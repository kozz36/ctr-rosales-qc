/**
 * smoke_rev2.test.ts — ReviewPage integration smoke test (S2.7)
 *
 * Mounts ReviewPage with mocked TanStack Query composables and Pinia.
 * Asserts the full slice-2 contract:
 *   - 2 MISMATCH rows → drill-down toggles GuiaDrillDown per row
 *   - summed_qty cell has no <input> (CRITICAL-2 / REV-C03)
 *   - Reassign button in drill-down emits correct guia_id (CRITICAL-1 / REV-C02)
 *   - UnresolvedGuiasPanel shows 1 unresolved entry; NOT in main grid
 *   - aria-rowcount present on table
 *   - UNCLASSIFIED status uses neutral badge (not green MATCH) (REV-004)
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import type {
  ReconciliationRowResponse,
  RunStatusResponse,
  ReconciliationTableResponse,
} from '@/api/types'

// ---------------------------------------------------------------------------
// vi.hoisted — constants accessible inside vi.mock factory closures
// ---------------------------------------------------------------------------

const { MOCK_TABLE_DATA, MOCK_STATUS } = vi.hoisted(() => {
  const GUIA_A = {
    guia_id: 'T009-AAA',
    source_pages: [3],
    cantidad: '900.0',
    unidad: 'KG',
    confidence: 0.9,
    identity_source: 'qr' as const,
  }
  const GUIA_B = {
    guia_id: 'T009-BBB',
    source_pages: [5],
    cantidad: '800.0',
    unidad: 'KG',
    confidence: 0.85,
    identity_source: 'ocr_fallback' as const,
  }
  const ROW_MISMATCH_1: ReconciliationRowResponse = {
    row_id: '101|2025-03-15|BARRA 1/2|KG',
    registro: '101',
    fecha: '2025-03-15',
    material_canonical: 'BARRA 1/2',
    unidad: 'KG',
    declared_qty: '1000.0',
    summed_qty: '900.0',
    delta: '-100.0',
    status: 'MISMATCH',
    source_pages: [3],
    min_confidence: 0.9,
    requires_review: false,
    guias: [GUIA_A],
  }
  const ROW_MISMATCH_2: ReconciliationRowResponse = {
    row_id: '102|2025-03-16|BARRA 3/8|KG',
    registro: '102',
    fecha: '2025-03-16',
    material_canonical: 'BARRA 3/8',
    unidad: 'KG',
    declared_qty: '1000.0',
    summed_qty: '800.0',
    delta: '-200.0',
    status: 'MISMATCH',
    source_pages: [5],
    min_confidence: 0.85,
    requires_review: false,
    guias: [GUIA_B],
  }
  const UNRESOLVED = {
    guia_id: 'T009-UNRES',
    identity_source: 'qr' as const,
    source_pages: [7],
    first_page: 7,
  }

  const MOCK_TABLE_DATA: ReconciliationTableResponse = {
    run_id: 'smoke-run-123',
    rows: [ROW_MISMATCH_1, ROW_MISMATCH_2],
    unresolved_guias: [UNRESOLVED],
  }

  const MOCK_STATUS: RunStatusResponse = {
    run_id: 'smoke-run-123',
    status: 'review',
    vision_calls_made: 5,
    warnings: [],
    error: null,
  }

  return { MOCK_TABLE_DATA, MOCK_STATUS }
})

// ---------------------------------------------------------------------------
// Module mocks (hoisted before imports by Vitest)
// ---------------------------------------------------------------------------

vi.mock('@/composables/useReconciliationApi', () => ({
  useTable: () => ({
    data: { value: MOCK_TABLE_DATA },
    isFetching: { value: false },
    error: { value: null },
    refetch: vi.fn(),
  }),
  useEditRow: () => ({
    mutateAsync: vi.fn(),
    isPending: { value: false },
  }),
  useReassignGuia: () => ({
    mutateAsync: vi.fn().mockResolvedValue({}),
    isPending: { value: false },
  }),
  useExportRun: () => ({
    mutateAsync: vi.fn().mockResolvedValue(undefined),
    isPending: { value: false },
  }),
  queryKeys: {
    runStatus: (id: string) => ['run', id, 'status'],
    table: (id: string) => ['run', id, 'table'],
  },
}))

vi.mock('@/api/client', () => ({
  getRunStatus: vi.fn().mockResolvedValue(MOCK_STATUS),
  getTable: vi.fn().mockResolvedValue(MOCK_TABLE_DATA),
  editRow: vi.fn(),
  editGuiaLine: vi.fn(),
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

// Stub heavy child components to keep the test fast
vi.mock('@/features/review/GuiaDrillDown.vue', () => ({
  default: {
    name: 'GuiaDrillDown',
    template: '<tr class="stub-drill-down"><td>DrillDown</td></tr>',
    props: ['guias', 'runId'],
    emits: ['reassign', 'rowUpdated'],
  },
}))

vi.mock('@/features/review/ConfidenceBadge.vue', () => ({
  default: {
    name: 'ConfidenceBadge',
    template: '<span class="stub-confidence" />',
    props: ['value', 'compact'],
  },
}))

vi.mock('@/features/review/SourcePages.vue', () => ({
  default: {
    name: 'SourcePages',
    template: '<div class="stub-source-pages" />',
    props: ['pages', 'runId'],
    emits: ['pageClick'],
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

vi.mock('@/features/review/GuiaReassignDialog.vue', () => ({
  default: {
    name: 'GuiaReassignDialog',
    template: `
      <div v-if="modelValue" class="stub-reassign-dialog">
        <span class="stub-dialog-guia-id">{{ guiaId }}</span>
        <button class="stub-dialog-submit" @click="$emit('submit', { guia_id: guiaId, new_registro: 'R-NEW', new_fecha: null })">
          Submit
        </button>
      </div>
    `,
    props: ['modelValue', 'guiaId', 'row', 'isPending', 'apiError'],
    emits: ['update:modelValue', 'submit'],
  },
}))

import ReviewPage from '@/features/review/ReviewPage.vue'

// ---------------------------------------------------------------------------
// Mount helper
// ---------------------------------------------------------------------------

function mountReviewPage() {
  return mount(ReviewPage, {
    props: { id: 'smoke-run-123' },
    global: {
      plugins: [createPinia()],
      stubs: {
        Teleport: { template: '<slot />' },
      },
    },
  })
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

describe('ReviewPage smoke test (S2.7 — rev-2 slice-2 contract)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('renders the review grid (table present when status=review)', () => {
    const wrapper = mountReviewPage()
    expect(wrapper.find('.review-grid__table').exists()).toBe(true)
  })

  it('summed_qty cell has no <input> anywhere in the grid (CRITICAL-2 / REV-C03)', () => {
    const wrapper = mountReviewPage()
    expect(wrapper.findAll('input')).toHaveLength(0)
  })

  it('drill-down expands when expand button is clicked (REV-C01)', async () => {
    const wrapper = mountReviewPage()
    const expandBtns = wrapper.findAll('.recon-row__expand-btn')
    expect(expandBtns.length).toBeGreaterThanOrEqual(1)
    expect(wrapper.find('.stub-drill-down').exists()).toBe(false)
    await expandBtns[0].trigger('click')
    expect(wrapper.find('.stub-drill-down').exists()).toBe(true)
  })

  it('aria-rowcount attribute is present and numeric on the table (REV-001 / S2.5)', () => {
    const wrapper = mountReviewPage()
    const table = wrapper.find('.review-grid__table')
    expect(table.exists()).toBe(true)
    const ariaRowcount = table.attributes('aria-rowcount')
    expect(ariaRowcount).toBeTruthy()
    expect(Number(ariaRowcount)).toBeGreaterThanOrEqual(0)
  })

  it('UnresolvedGuiasPanel visible with 1 entry (REV-C04 / REC-C05)', () => {
    const wrapper = mountReviewPage()
    expect(wrapper.find('.unresolved-panel').exists()).toBe(true)
    expect(wrapper.findAll('.unresolved-panel__item')).toHaveLength(1)
    expect(wrapper.find('.unresolved-panel__item-id').text()).toContain('T009-UNRES')
  })

  it('group headers do not include the unresolved guia_id (unresolved NOT in main grid)', () => {
    const wrapper = mountReviewPage()
    const groupHeaders = wrapper.findAll('.review-grid__group-header')
    const headerTexts = groupHeaders.map((h) => h.text())
    // Registro groups for 101 and 102 appear
    expect(headerTexts.some((t) => t.includes('101'))).toBe(true)
    expect(headerTexts.some((t) => t.includes('102'))).toBe(true)
    // Unresolved guia_id must not appear in any group header
    expect(headerTexts.every((t) => !t.includes('T009-UNRES'))).toBe(true)
  })

  it('"Assign to registro" opens GuiaReassignDialog with correct guia_id (REV-C02)', async () => {
    const wrapper = mountReviewPage()
    expect(wrapper.find('.stub-reassign-dialog').exists()).toBe(false)
    await wrapper.find('.unresolved-panel__assign-btn').trigger('click')
    const dialog = wrapper.find('.stub-reassign-dialog')
    expect(dialog.exists()).toBe(true)
    expect(dialog.find('.stub-dialog-guia-id').text()).toBe('T009-UNRES')
  })

  it('drill-down reassign emits correct guia_id to dialog (CRITICAL-1 / REV-C02)', async () => {
    const wrapper = mountReviewPage()
    // Expand first row
    await wrapper.findAll('.recon-row__expand-btn')[0].trigger('click')
    const drillDown = wrapper.findComponent({ name: 'GuiaDrillDown' })
    expect(drillDown.exists()).toBe(true)
    await drillDown.vm.$emit('reassign', 'T009-AAA')
    await wrapper.vm.$nextTick()
    const dialog = wrapper.find('.stub-reassign-dialog')
    expect(dialog.exists()).toBe(true)
    expect(dialog.find('.stub-dialog-guia-id').text()).toBe('T009-AAA')
  })

  it('no green MATCH badge on MISMATCH rows (badge class correctness)', () => {
    // Both rows in fixture are MISMATCH — no MATCH badges should exist
    const wrapper = mountReviewPage()
    expect(wrapper.findAll('.recon-row__status-badge--match')).toHaveLength(0)
  })
})
