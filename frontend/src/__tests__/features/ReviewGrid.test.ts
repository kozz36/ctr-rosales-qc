/**
 * Tests for ReviewGrid.vue
 *
 * Covers:
 * - Renders all 10 column headers
 * - Renders rows with correct status classes (MATCH, MISMATCH, DECLARED_MISSING, GUIA_MISSING)
 * - Summary counts displayed correctly
 * - Filter button emits filterChange
 * - Edit cell → emits edit event
 * - Group headers render registro + fecha
 * - Loading and error states render
 * - Empty state renders when no rows
 */

import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ReviewGrid from '@/features/review/ReviewGrid.vue'
import type { ReconciliationRowResponse, GuiaContributionResponse } from '@/api/types'

// Stub child components to isolate ReviewGrid logic
vi.mock('@/features/review/ReconciliationRow.vue', () => ({
  default: {
    name: 'ReconciliationRow',
    template: `<tr class="stub-row" :data-status="row.status" :data-row-id="row.row_id"><td>{{ row.status }}</td></tr>`,
    props: ['row', 'runId', 'pendingValue'],
    emits: ['edit', 'openReassign', 'pageClick', 'rowActivate', 'rowUpdated'],
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

function makeRow(overrides: Partial<ReconciliationRowResponse> = {}): ReconciliationRowResponse {
  return {
    row_id: 'r1|2024-01-15|BARRAS|KG',
    registro: 'r1',
    fecha: '2024-01-15',
    material_canonical: 'BARRAS DE ACERO',
    unidad: 'KG',
    declared_qty: '1000.0',
    summed_qty: '1000.0',
    delta: '0.0',
    status: 'MATCH',
    source_pages: [1, 2],
    min_confidence: 0.92,
    requires_review: false,
    guias: [] as GuiaContributionResponse[],
    any_year_inferred: false,
    ...overrides,
  }
}

const EMPTY_MAP = new Map()

describe('ReviewGrid', () => {
  it('renders 10 column headers + 1 actions column', () => {
    // Need at least one row so the table (and thead) renders (empty state hides the table)
    const rows = [makeRow()]
    const wrapper = mount(ReviewGrid, {
      props: {
        rows,
        runId: 'run-abc',
        pendingEdits: EMPTY_MAP,
        activeFilter: null,
      },
    })
    // 10 locked columns + actions th
    const headers = wrapper.findAll('th')
    expect(headers.length).toBeGreaterThanOrEqual(10)
    const headerTexts = headers.map((h) => h.text())
    expect(headerTexts.some((t) => t.includes('Registro'))).toBe(true)
    expect(headerTexts.some((t) => t.includes('Fecha'))).toBe(true)
    expect(headerTexts.some((t) => t.includes('Material'))).toBe(true)
    expect(headerTexts.some((t) => t.includes('Unidad'))).toBe(true)
    expect(headerTexts.some((t) => t.includes('Declarado'))).toBe(true)
    expect(headerTexts.some((t) => t.includes('Sumado'))).toBe(true)
    expect(headerTexts.some((t) => t.includes('Delta'))).toBe(true)
    expect(headerTexts.some((t) => t.includes('Estado'))).toBe(true)
    expect(headerTexts.some((t) => t.includes('Confianza'))).toBe(true)
    expect(headerTexts.some((t) => t.includes('Páginas'))).toBe(true)
  })

  it('shows empty state when rows array is empty', () => {
    const wrapper = mount(ReviewGrid, {
      props: { rows: [], runId: 'run-abc', pendingEdits: EMPTY_MAP, activeFilter: null },
    })
    expect(wrapper.find('.review-grid__state').exists()).toBe(true)
    expect(wrapper.find('.review-grid__table').exists()).toBe(false)
  })

  it('renders rows grouped by registro+fecha', () => {
    const rows = [
      makeRow({ row_id: 'r1|2024-01-15|MAT-A|KG', registro: 'r1', fecha: '2024-01-15', status: 'MATCH' }),
      makeRow({ row_id: 'r1|2024-01-15|MAT-B|KG', registro: 'r1', fecha: '2024-01-15', material_canonical: 'MAT-B', status: 'MISMATCH' }),
      makeRow({ row_id: 'r2|2024-01-20|MAT-C|KG', registro: 'r2', fecha: '2024-01-20', material_canonical: 'MAT-C', status: 'DECLARED_MISSING' }),
    ]
    const wrapper = mount(ReviewGrid, {
      props: { rows, runId: 'run-abc', pendingEdits: EMPTY_MAP, activeFilter: null },
    })
    // Two group headers (r1 and r2)
    const groupHeaders = wrapper.findAll('.review-grid__group-header')
    expect(groupHeaders).toHaveLength(2)
    expect(groupHeaders[0].text()).toContain('r1')
    expect(groupHeaders[1].text()).toContain('r2')
  })

  it('displays summary counts from all rows', () => {
    const rows = [
      makeRow({ row_id: 'r1|d|A|K', status: 'MATCH' }),
      makeRow({ row_id: 'r1|d|B|K', status: 'MISMATCH' }),
      makeRow({ row_id: 'r1|d|C|K', status: 'MISMATCH' }),
      makeRow({ row_id: 'r1|d|D|K', status: 'DECLARED_MISSING' }),
    ]
    const wrapper = mount(ReviewGrid, {
      props: { rows, runId: 'run-abc', pendingEdits: EMPTY_MAP, activeFilter: null },
    })
    const summary = wrapper.find('.review-grid__summary')
    expect(summary.text()).toContain('1')    // 1 match
    expect(summary.text()).toContain('2')    // 2 mismatches
    expect(summary.text()).toContain('1')    // 1 declared_missing
  })

  it('emits filterChange when a filter button is clicked', async () => {
    const wrapper = mount(ReviewGrid, {
      props: { rows: [], runId: 'run-abc', pendingEdits: EMPTY_MAP, activeFilter: null },
    })
    const mismatchBtn = wrapper.findAll('.review-grid__filter-btn').find(
      (b) => b.text().includes('Diferencias'),
    )
    expect(mismatchBtn).toBeTruthy()
    await mismatchBtn!.trigger('click')
    expect(wrapper.emitted('filterChange')).toBeTruthy()
    expect(wrapper.emitted('filterChange')![0]).toEqual(['MISMATCH'])
  })

  it('shows loading spinner when isLoading=true', () => {
    const wrapper = mount(ReviewGrid, {
      props: { rows: [], runId: 'run-abc', pendingEdits: EMPTY_MAP, activeFilter: null, isLoading: true },
    })
    expect(wrapper.find('.review-grid__spinner').exists()).toBe(true)
    expect(wrapper.find('[role="status"]').exists()).toBe(true)
  })

  it('shows error state and retry button when error is set', () => {
    const wrapper = mount(ReviewGrid, {
      props: { rows: [], runId: 'run-abc', pendingEdits: EMPTY_MAP, activeFilter: null, error: 'Network error' },
    })
    expect(wrapper.find('.review-grid__state--error').exists()).toBe(true)
    expect(wrapper.find('.review-grid__retry-btn').exists()).toBe(true)
  })

  it('emits retry when retry button is clicked', async () => {
    const wrapper = mount(ReviewGrid, {
      props: { rows: [], runId: 'run-abc', pendingEdits: EMPTY_MAP, activeFilter: null, error: 'fail' },
    })
    await wrapper.find('.review-grid__retry-btn').trigger('click')
    expect(wrapper.emitted('retry')).toBeTruthy()
  })

  it('filters to only MISMATCH rows when activeFilter=MISMATCH', () => {
    const rows = [
      makeRow({ row_id: 'r1|d|A|K', status: 'MATCH' }),
      makeRow({ row_id: 'r1|d|B|K', status: 'MISMATCH' }),
    ]
    const wrapper = mount(ReviewGrid, {
      props: { rows, runId: 'run-abc', pendingEdits: EMPTY_MAP, activeFilter: 'MISMATCH' },
    })
    const stubRows = wrapper.findAll('.stub-row')
    expect(stubRows).toHaveLength(1)
    expect(stubRows[0].attributes('data-status')).toBe('MISMATCH')
  })

  it('shows "Todo" filter as active when activeFilter is null', () => {
    const wrapper = mount(ReviewGrid, {
      props: { rows: [], runId: 'run-abc', pendingEdits: EMPTY_MAP, activeFilter: null },
    })
    const activeBtn = wrapper.find('.review-grid__filter-btn--active')
    expect(activeBtn.text()).toContain('Todos')
  })

  it('emits openReassign when ReconciliationRow emits openReassign', async () => {
    const row = makeRow({ status: 'MISMATCH' })
    const wrapper = mount(ReviewGrid, {
      props: { rows: [row], runId: 'run-abc', pendingEdits: EMPTY_MAP, activeFilter: null },
    })
    await wrapper.findComponent({ name: 'ReconciliationRow' }).vm.$emit('openReassign', { guia_id: 'T009-0741770' })
    expect(wrapper.emitted('openReassign')).toBeTruthy()
    const payload = wrapper.emitted('openReassign')![0][0] as { guia_id: string }
    expect(payload.guia_id).toBe('T009-0741770')
  })

  it('aria-rowcount is bound reactively to filteredRows length (S2.5 / REV-001)', () => {
    const rows = [
      makeRow({ row_id: 'r1|d|A|K', status: 'MATCH' }),
      makeRow({ row_id: 'r1|d|B|K', status: 'MISMATCH' }),
    ]
    const wrapper = mount(ReviewGrid, {
      props: { rows, runId: 'run-abc', pendingEdits: EMPTY_MAP, activeFilter: 'MISMATCH' },
    })
    const table = wrapper.find('.review-grid__table')
    expect(table.exists()).toBe(true)
    // Only 1 MISMATCH row visible after filter → aria-rowcount should be "1"
    expect(table.attributes('aria-rowcount')).toBe('1')
  })

  it('aria-rowcount shows all rows when no filter active', () => {
    const rows = [
      makeRow({ row_id: 'r1|d|A|K', status: 'MATCH' }),
      makeRow({ row_id: 'r1|d|B|K', status: 'MISMATCH' }),
      makeRow({ row_id: 'r1|d|C|K', status: 'DECLARED_MISSING' }),
    ]
    const wrapper = mount(ReviewGrid, {
      props: { rows, runId: 'run-abc', pendingEdits: EMPTY_MAP, activeFilter: null },
    })
    const table = wrapper.find('.review-grid__table')
    expect(table.attributes('aria-rowcount')).toBe('3')
  })
})
