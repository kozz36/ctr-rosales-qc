/**
 * Reconciliation store — client state for the review grid.
 *
 * Scope (PR-5a): store shape + row cache loading. Grid rendering and
 * dirty-tracking mutation are implemented in PR-5b (ReviewGrid.vue).
 *
 * Server state ownership: TanStack Query owns the GET /runs/{id}/table cache.
 * This store owns:
 *   - The hydrated rows array (written once per successful table fetch)
 *   - The dirty-edit set (row_id → partial edit pending PATCH)
 *   - The active status filter
 *   - Selected run_id (mirrors run store, kept here for grid isolation)
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { ReconciliationRowResponse, RowStatus } from '@/api/types'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** A pending client-side edit before it is committed via PATCH. */
export interface PendingEdit {
  guia_id: string
  field: 'fecha' | 'registro'
  value: string | null
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useReconciliationStore = defineStore('reconciliation', () => {
  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------

  /** Active run being reviewed. */
  const runId = ref<string | null>(null)

  /** Rows as returned by GET /runs/{id}/table (or updated by PATCH/reassign). */
  const rows = ref<ReconciliationRowResponse[]>([])

  /**
   * Dirty edits indexed by row_id.
   * Shape: Map<row_id, PendingEdit> — populated by ReviewGrid (PR-5b).
   */
  const pendingEdits = ref<Map<string, PendingEdit>>(new Map())

  /** Currently active status filter. Null means "show all". */
  const statusFilter = ref<RowStatus | null>(null)

  // ---------------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------------

  /** Hydrate rows from a successful table fetch. Clears pending edits. */
  function setRows(runIdValue: string, incoming: ReconciliationRowResponse[]): void {
    runId.value = runIdValue
    rows.value = incoming
    pendingEdits.value = new Map()
  }

  /**
   * Merge an updated rows array from a PATCH or reassign response.
   * Only the affected rows are replaced — unaffected rows remain in place
   * to avoid full re-render.
   */
  function mergeRows(updated: ReconciliationRowResponse[]): void {
    const index = new Map(updated.map((r) => [r.row_id, r]))
    rows.value = rows.value.map((r) => index.get(r.row_id) ?? r)
    // Add new rows that do not exist yet (e.g. a previously empty grupo)
    const existing = new Set(rows.value.map((r) => r.row_id))
    for (const r of updated) {
      if (!existing.has(r.row_id)) rows.value.push(r)
    }
  }

  /** Register a pending edit (called by editable cell in PR-5b). */
  function setPendingEdit(rowId: string, edit: PendingEdit): void {
    pendingEdits.value.set(rowId, edit)
  }

  /** Remove a pending edit after the PATCH commits. */
  function clearPendingEdit(rowId: string): void {
    pendingEdits.value.delete(rowId)
  }

  /** Set the status filter (null = all). */
  function setFilter(filter: RowStatus | null): void {
    statusFilter.value = filter
  }

  /** Full reset (user navigates back to upload). */
  function reset(): void {
    runId.value = null
    rows.value = []
    pendingEdits.value = new Map()
    statusFilter.value = null
  }

  return {
    // State
    runId,
    rows,
    pendingEdits,
    statusFilter,
    // Actions
    setRows,
    mergeRows,
    setPendingEdit,
    clearPendingEdit,
    setFilter,
    reset,
  }
})
