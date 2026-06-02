<template>
  <section class="review-page" aria-labelledby="review-heading">
    <!-- Page header -->
    <div class="review-page__header">
      <div class="review-page__title-row">
        <h1 id="review-heading" class="review-page__title">Revisión de reconciliación</h1>
        <ExportButton
          :disabled="!isReady"
          :is-pending="exportMutation.isPending.value"
          :error="exportError"
          @export="onExport"
        />
      </div>
      <p class="review-page__meta">
        Run: <code class="review-page__run-id">{{ id }}</code>
        <span v-if="runStatus" class="review-page__status-badge" :data-status="runStatus.status">
          {{ STATUS_LABELS[runStatus.status] ?? runStatus.status }}
        </span>
      </p>
    </div>

    <!-- Run still processing — not yet ready for review -->
    <div
      v-if="!isReady && !isStatusError"
      class="review-page__waiting"
      role="status"
      aria-live="polite"
    >
      <span class="review-page__spinner" aria-hidden="true" />
      <span>Esperando que el pipeline complete... ({{ runStatus?.status ?? 'pendiente' }})</span>
    </div>

    <!-- Pipeline error -->
    <div v-if="isStatusError" class="review-page__error" role="alert">
      <span aria-hidden="true">✕</span>
      <strong>El run finalizó con error</strong>
      <span v-if="runStatus?.error">: {{ runStatus.error }}</span>
    </div>

    <!-- Review grid (only when ready) -->
    <ReviewGrid
      v-if="isReady"
      :rows="rows"
      :run-id="id"
      :is-loading="tableQuery.isFetching.value"
      :error="tableError"
      :pending-edits="reconciliationStore.pendingEdits"
      :active-filter="reconciliationStore.statusFilter"
      @edit="onEdit"
      @open-reassign="onReassignRequest"
      @page-click="onPageClick"
      @filter-change="reconciliationStore.setFilter"
      @retry="void tableQuery.refetch()"
    />

    <!-- Reassign dialog (rev-2: uses guiaId instead of row_id proxy) -->
    <GuiaReassignDialog
      v-model="showReassignDialog"
      :guia-id="reassignGuiaId"
      :row="reassignTarget"
      :is-pending="reassignMutation.isPending.value"
      :api-error="reassignError"
      @submit="onReassignSubmit"
    />
  </section>
</template>

<script setup lang="ts">
/**
 * ReviewPage — PR-5b full implementation.
 *
 * Composes:
 *   - ReviewGrid (10-col data grid, grouped, filterable)
 *   - GuiaReassignDialog (modal)
 *   - ExportButton (xlsx/csv download)
 *
 * State ownership:
 *   - TanStack Query: server state (table rows, run status)
 *   - Pinia reconciliationStore: client state (dirty edits, filter, run_id)
 *   - local refs: UI state (dialog open, export error, reassign target)
 *
 * Edit flow: edit event → debounce 600ms → PATCH → invalidate table query
 * Reassign flow: submit → POST reassign → invalidate table query → close dialog
 */

import { ref, computed, watch } from 'vue'
import { useReconciliationStore } from '@/stores/reconciliation'
import { useTable, useEditRow, useReassignGuia, useExportRun, queryKeys } from '@/composables/useReconciliationApi'
import { getRunStatus } from '@/api/client'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import type { ReconciliationRowResponse, ExportFormat, ReassignRequest } from '@/api/types'
import ReviewGrid from './ReviewGrid.vue'
import GuiaReassignDialog from './GuiaReassignDialog.vue'
import ExportButton from './ExportButton.vue'

const props = defineProps<{
  /** run_id from router prop */
  id: string
}>()

const reconciliationStore = useReconciliationStore()
const queryClient = useQueryClient()

// ---------------------------------------------------------------------------
// Run status
// ---------------------------------------------------------------------------

const runIdRef = computed(() => props.id)

const { data: runStatus } = useQuery({
  queryKey: computed(() => queryKeys.runStatus(props.id)),
  queryFn: () => getRunStatus(props.id),
  refetchInterval: (query) => {
    const status = query.state.data?.status
    if (status === 'review' || status === 'error') return false
    return 2000
  },
  staleTime: 0,
})

const isReady = computed(() => runStatus.value?.status === 'review')
const isStatusError = computed(() => runStatus.value?.status === 'error')

const STATUS_LABELS: Record<string, string> = {
  pending: 'Pendiente',
  processing: 'Procesando',
  review: 'En revisión',
  error: 'Error',
}

// ---------------------------------------------------------------------------
// Table query
// ---------------------------------------------------------------------------

const tableQuery = useTable(runIdRef, isReady)

const rows = computed<ReconciliationRowResponse[]>(
  () => tableQuery.data.value?.rows ?? [],
)

const tableError = computed<string | null>(() =>
  tableQuery.error.value ? String(tableQuery.error.value) : null,
)

// Hydrate reconciliation store when table data arrives
watch(
  () => tableQuery.data.value,
  (data) => {
    if (data) reconciliationStore.setRows(props.id, data.rows)
  },
)

// ---------------------------------------------------------------------------
// Edit flow — debounced PATCH
// ---------------------------------------------------------------------------

const editMutation = useEditRow(runIdRef)
const pendingTimers = new Map<string, ReturnType<typeof setTimeout>>()

function onEdit(rowId: string, _guiaId: string, value: string): void {
  // Track dirty in store
  reconciliationStore.setPendingEdit(rowId, {
    guia_id: rowId,
    field: 'fecha', // only fecha/registro editable per current API contract
    value,
  })

  // Debounce the PATCH — 600ms after last keystroke
  if (pendingTimers.has(rowId)) clearTimeout(pendingTimers.get(rowId)!)
  const timer = setTimeout(async () => {
    pendingTimers.delete(rowId)
    try {
      await editMutation.mutateAsync({
        rowId,
        body: { guia_id: rowId, field: 'fecha', value },
      })
      reconciliationStore.clearPendingEdit(rowId)
    } catch {
      // Error stays visible in grid; edit not cleared
    }
  }, 600)
  pendingTimers.set(rowId, timer)
}

// ---------------------------------------------------------------------------
// Reassign flow
// ---------------------------------------------------------------------------

const reassignMutation = useReassignGuia(runIdRef)
const showReassignDialog = ref(false)
const reassignGuiaId = ref<string>('')
const reassignTarget = ref<ReconciliationRowResponse | null>(null)
const reassignError = ref<string | null>(null)

function onReassignRequest(payload: { guia_id: string }): void {
  reassignGuiaId.value = payload.guia_id
  // Find the row that currently owns this guía for context display in the dialog
  reassignTarget.value =
    rows.value.find((r) => r.guias?.some((g) => g.guia_id === payload.guia_id)) ?? null
  reassignError.value = null
  showReassignDialog.value = true
}

async function onReassignSubmit(payload: ReassignRequest): Promise<void> {
  reassignError.value = null
  try {
    await reassignMutation.mutateAsync(payload)
    // Refetch table to pick up recomputed groups
    await queryClient.invalidateQueries({ queryKey: queryKeys.table(props.id) })
    showReassignDialog.value = false
  } catch (err: unknown) {
    reassignError.value = err instanceof Error ? err.message : 'Error al reasignar'
  }
}

// ---------------------------------------------------------------------------
// Export flow
// ---------------------------------------------------------------------------

const exportMutation = useExportRun(runIdRef)
const exportError = ref<string | null>(null)

async function onExport(fmt: ExportFormat): Promise<void> {
  exportError.value = null
  try {
    await exportMutation.mutateAsync(fmt)
  } catch (err: unknown) {
    exportError.value = err instanceof Error ? err.message : 'Error al exportar'
  }
}

// ---------------------------------------------------------------------------
// Page click (source page drill-down — future lightbox)
// ---------------------------------------------------------------------------

function onPageClick(page: number): void {
  // TODO: open source page thumbnail lightbox (future enhancement)
  console.info(`Source page ${page} clicked — lightbox not yet implemented`)
}
</script>

<style scoped>
.review-page {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
  min-height: 0;
}

.review-page__header {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.review-page__title-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
}

.review-page__title {
  font-size: var(--text-xl);
  font-weight: 600;
  color: var(--text-primary);
}

.review-page__meta {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.review-page__run-id {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--text-secondary);
}

.review-page__status-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px var(--space-2);
  border-radius: var(--radius-pill);
  font-size: var(--text-xs);
  font-weight: 500;
  border: 1px solid var(--border-default);
  color: var(--text-secondary);
}

.review-page__status-badge[data-status="review"] {
  color: var(--status-match-fg);
  border-color: var(--status-match-glow);
  background-color: var(--status-match-bg);
}

.review-page__status-badge[data-status="error"] {
  color: var(--status-mismatch-fg);
  border-color: var(--status-mismatch-glow);
  background-color: var(--status-mismatch-bg);
}

/* Waiting state */
.review-page__waiting {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-6) var(--space-4);
  font-size: var(--text-sm);
  color: var(--text-secondary);
  background-color: var(--surface-raised);
  border-radius: var(--radius-lg);
  border: 1px dashed var(--border-default);
}

.review-page__spinner {
  width: 16px;
  height: 16px;
  border: 2px solid var(--border-default);
  border-top-color: var(--action-primary);
  border-radius: 50%;
  flex-shrink: 0;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* Error state */
.review-page__error {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-4);
  background-color: rgba(248, 81, 73, 0.08);
  border: 1px solid rgba(248, 81, 73, 0.25);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  color: var(--status-mismatch-fg);
}
</style>
