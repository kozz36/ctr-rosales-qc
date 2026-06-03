/**
 * Reconciliation store — unit tests
 *
 * Covers: setRows, mergeRows (replace + append), pending edits CRUD, filter, reset.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useReconciliationStore } from '@/stores/reconciliation'
import type { ReconciliationRowResponse } from '@/api/types'

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeRow(partial: Partial<ReconciliationRowResponse> = {}): ReconciliationRowResponse {
  return {
    row_id: 'R001|2024-01-15|FIERRO 1/2|KG',
    registro: 'R001',
    fecha: '2024-01-15',
    material_canonical: 'FIERRO 1/2',
    unidad: 'KG',
    declared_qty: '1000.00',
    summed_qty: '1000.00',
    delta: '0.00',
    status: 'MATCH',
    source_pages: [1, 2],
    min_confidence: 0.92,
    requires_review: false,
    guias: [],
    any_year_inferred: false,
    has_fecha_divergence: false,
    ...partial,
  }
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

describe('useReconciliationStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('initialises with empty state', () => {
    const store = useReconciliationStore()
    expect(store.runId).toBeNull()
    expect(store.rows).toHaveLength(0)
    expect(store.pendingEdits.size).toBe(0)
    expect(store.statusFilter).toBeNull()
  })

  it('setRows populates runId and rows', () => {
    const store = useReconciliationStore()
    const rows = [makeRow(), makeRow({ row_id: 'R002|2024-01-16|FIERRO 3/8|KG' })]
    store.setRows('run-1', rows)

    expect(store.runId).toBe('run-1')
    expect(store.rows).toHaveLength(2)
  })

  it('setRows clears pending edits', () => {
    const store = useReconciliationStore()
    store.setRows('run-1', [makeRow()])
    store.setPendingEdit('R001|2024-01-15|FIERRO 1/2|KG', {
      guia_id: 'g1',
      field: 'fecha',
      value: '2024-01-20',
    })
    expect(store.pendingEdits.size).toBe(1)

    store.setRows('run-1', [makeRow()])
    expect(store.pendingEdits.size).toBe(0)
  })

  it('mergeRows replaces existing rows by row_id', () => {
    const store = useReconciliationStore()
    const original = makeRow({ declared_qty: '1000.00', status: 'MATCH' })
    store.setRows('run-1', [original])

    const updated = makeRow({ declared_qty: '900.00', status: 'MISMATCH', delta: '-100.00' })
    store.mergeRows([updated])

    expect(store.rows).toHaveLength(1)
    expect(store.rows[0].status).toBe('MISMATCH')
    expect(store.rows[0].declared_qty).toBe('900.00')
  })

  it('mergeRows appends rows that did not exist', () => {
    const store = useReconciliationStore()
    store.setRows('run-1', [makeRow()])

    const newRow = makeRow({ row_id: 'R999|2024-02-01|CEMENTO|TN' })
    store.mergeRows([newRow])

    expect(store.rows).toHaveLength(2)
  })

  it('setPendingEdit and clearPendingEdit work', () => {
    const store = useReconciliationStore()
    const rowId = 'R001|2024-01-15|FIERRO 1/2|KG'

    store.setPendingEdit(rowId, { guia_id: 'g1', field: 'fecha', value: '2024-01-20' })
    expect(store.pendingEdits.has(rowId)).toBe(true)
    expect(store.pendingEdits.get(rowId)?.value).toBe('2024-01-20')

    store.clearPendingEdit(rowId)
    expect(store.pendingEdits.has(rowId)).toBe(false)
  })

  it('setFilter stores the active filter', () => {
    const store = useReconciliationStore()
    store.setFilter('MISMATCH')
    expect(store.statusFilter).toBe('MISMATCH')

    store.setFilter(null)
    expect(store.statusFilter).toBeNull()
  })

  it('reset() clears everything', () => {
    const store = useReconciliationStore()
    store.setRows('run-1', [makeRow()])
    store.setPendingEdit('R001|2024-01-15|FIERRO 1/2|KG', {
      guia_id: 'g1',
      field: 'registro',
      value: 'R002',
    })
    store.setFilter('MISMATCH')

    store.reset()

    expect(store.runId).toBeNull()
    expect(store.rows).toHaveLength(0)
    expect(store.pendingEdits.size).toBe(0)
    expect(store.statusFilter).toBeNull()
  })
})
