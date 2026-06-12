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

    <!-- Run not found (W1): stale run_id (swept/deleted) → 404. Graceful
         empty-state instead of an infinite spinner; localStorage already cleared. -->
    <div v-if="isNotFound" class="review-page__not-found" role="status">
      <span aria-hidden="true">🔍</span>
      <strong>Ejecución no encontrada</strong>
      <p>Esta ejecución pudo haber sido eliminada.</p>
      <div class="review-page__not-found-actions">
        <RouterLink :to="{ name: 'upload' }" class="review-page__not-found-link">
          Ir al inicio
        </RouterLink>
        <RouterLink :to="{ name: 'historial' }" class="review-page__not-found-link">
          Ver historial
        </RouterLink>
      </div>
    </div>

    <!-- Run still processing — not yet ready for review -->
    <div
      v-if="!isReady && !isStatusError && !isNotFound"
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

    <!-- Tabs: Reconciliación | Pendientes por procesar (F3 / REV-R23 / D7) -->
    <template v-if="isReady">
      <div class="review-page__tabs" role="tablist" aria-label="Vistas de revisión">
        <button
          id="tab-reconciliacion"
          ref="reconTabEl"
          class="review-page__tab"
          :class="{ 'review-page__tab--active': activeTab === 'reconciliacion' }"
          role="tab"
          type="button"
          :aria-selected="activeTab === 'reconciliacion'"
          :tabindex="activeTab === 'reconciliacion' ? 0 : -1"
          aria-controls="tabpanel-reconciliacion"
          @click="activeTab = 'reconciliacion'"
          @keydown="onTabKeydown($event, 'reconciliacion')"
        >
          Reconciliación
        </button>
        <button
          id="tab-pendientes"
          ref="pendientesTabEl"
          class="review-page__tab"
          :class="{ 'review-page__tab--active': activeTab === 'pendientes' }"
          role="tab"
          type="button"
          :aria-selected="activeTab === 'pendientes'"
          :tabindex="activeTab === 'pendientes' ? 0 : -1"
          aria-controls="tabpanel-pendientes"
          @click="activeTab = 'pendientes'"
          @keydown="onTabKeydown($event, 'pendientes')"
        >
          Pendientes por procesar
          <span
            v-if="erroredCount > 0"
            class="review-page__tab-badge"
            :aria-label="`${erroredCount} guías con error pendientes`"
          >
            {{ erroredCount }}
          </span>
        </button>
        <button
          id="tab-descartadas"
          ref="descartadasTabEl"
          class="review-page__tab"
          :class="{ 'review-page__tab--active': activeTab === 'descartadas' }"
          role="tab"
          type="button"
          :aria-selected="activeTab === 'descartadas'"
          :tabindex="activeTab === 'descartadas' ? 0 : -1"
          aria-controls="tabpanel-descartadas"
          @click="activeTab = 'descartadas'"
          @keydown="onTabKeydown($event, 'descartadas')"
        >
          Descartadas para revisión
          <span
            v-if="discardedCount > 0"
            class="review-page__tab-badge"
            :aria-label="`${discardedCount} páginas descartadas para revisión`"
          >
            {{ discardedCount }}
          </span>
        </button>
      </div>

      <!-- Reconciliación tab -->
      <div
        v-show="activeTab === 'reconciliacion'"
        id="tabpanel-reconciliacion"
        role="tabpanel"
        aria-labelledby="tab-reconciliacion"
        tabindex="0"
      >
        <!-- Unresolved guías bucket (REV-C04) — shown above the grid when guías exist -->
        <UnresolvedGuiasPanel
          v-if="unresolvedGuias.length > 0"
          :unresolved-guias="unresolvedGuias"
          @assign-guia="onAssignUnresolved"
        />

        <!-- Review grid -->
        <ReviewGrid
          :rows="rows"
          :run-id="id"
          :is-loading="tableQuery.isFetching.value"
          :error="tableError"
          :pending-edits="reconciliationStore.pendingEdits"
          :active-filter="reconciliationStore.statusFilter"
          @open-reassign="onReassignRequest"
          @page-click="onPageClick"
          @filter-change="reconciliationStore.setFilter"
          @retry="void tableQuery.refetch()"
        />
      </div>

      <!-- Pendientes por procesar tab (errored panel + per-Registro bulk reprocess) -->
      <div
        v-if="activeTab === 'pendientes'"
        id="tabpanel-pendientes"
        role="tabpanel"
        aria-labelledby="tab-pendientes"
        tabindex="0"
      >
        <PendientesPorProcesarTab
          :errored-guias="erroredGuias"
          :run-id="id"
          :rows="rows"
          @refetch="void tableQuery.refetch()"
        />
      </div>

      <!-- Descartadas para revisión tab (SDD#2 / REV-R27): discarded GUIA pages -->
      <div
        v-if="activeTab === 'descartadas'"
        id="tabpanel-descartadas"
        role="tabpanel"
        aria-labelledby="tab-descartadas"
        tabindex="0"
      >
        <DescartadasTab
          :discarded-pages="discardedPages"
          :run-id="id"
          @refetch="void tableQuery.refetch()"
        />
      </div>
    </template>

    <!-- Reassign dialog (rev-2: uses guiaId instead of row_id proxy) -->
    <GuiaReassignDialog
      v-model="showReassignDialog"
      :guia-id="reassignGuiaId"
      :row="reassignTarget"
      :is-pending="reassignMutation.isPending.value"
      :api-error="reassignError"
      @submit="onReassignSubmit"
    />

    <!-- Page-sheet viewer (issue #27): full-res scanned page lightbox -->
    <PageSheetViewer
      v-model="showPageViewer"
      :run-id="id"
      :page="viewerPage"
      :row-pages="viewerRowPages"
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

import { ref, computed, watch, nextTick } from 'vue'
import { useRunStore } from '@/stores/run'
import { useReconciliationStore } from '@/stores/reconciliation'
import { useTable, useReassignGuia, useExportRun, queryKeys } from '@/composables/useReconciliationApi'
import { getRunStatus } from '@/api/client'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import type { ReconciliationRowResponse, ExportFormat, ReassignRequest, UnresolvedGuiaResponse, ErroredGuiaResponse, DiscardedPageResponse } from '@/api/types'
import ReviewGrid from './ReviewGrid.vue'
import GuiaReassignDialog from './GuiaReassignDialog.vue'
import ExportButton from './ExportButton.vue'
import UnresolvedGuiasPanel from './UnresolvedGuiasPanel.vue'
import PendientesPorProcesarTab from './PendientesPorProcesarTab.vue'
import DescartadasTab from './DescartadasTab.vue'
import PageSheetViewer from './PageSheetViewer.vue'

const props = defineProps<{
  /** run_id from router prop */
  id: string
}>()

const runStore = useRunStore()
const reconciliationStore = useReconciliationStore()
const queryClient = useQueryClient()

// ---------------------------------------------------------------------------
// Cold-load mount hook (SDD#3 D6, RH-011-S01): the route param is the
// authoritative run identity. After a server restart, a history click, or a
// browser refresh the store may be null or stale — adopt the param so the
// header nav and [Batch actual] work without a prior upload in this session.
// Runs once per mount (route changes remount via :key="route.fullPath").
// ---------------------------------------------------------------------------

if (props.id && runStore.runId !== props.id) {
  runStore.runId = props.id
}

// ---------------------------------------------------------------------------
// Run status
// ---------------------------------------------------------------------------

const runIdRef = computed(() => props.id)

/**
 * W1: extract the HTTP status from an axios-shaped error (mirrors the
 * `(err as { response?: { status? } }).response.status` pattern used across
 * the review feature). Returns undefined for network/unshaped errors.
 */
function httpStatusOf(err: unknown): number | undefined {
  return (err as { response?: { status?: number } })?.response?.status
}

const { data: runStatus, error: statusError } = useQuery({
  queryKey: computed(() => queryKeys.runStatus(props.id)),
  queryFn: () => getRunStatus(props.id),
  refetchInterval: (query) => {
    const status = query.state.data?.status
    if (status === 'review' || status === 'error') return false
    return 2000
  },
  // W1: a stale run_id (run swept/deleted) returns 404; never retry 4xx — that
  // is a permanent condition and retrying it forever stuck the spinner.
  retry: (_failureCount: number, err: unknown) => {
    const status = httpStatusOf(err)
    if (status !== undefined && status >= 400 && status < 500) return false
    return _failureCount < 3
  },
  staleTime: 0,
})

const isReady = computed(() => runStatus.value?.status === 'review')
const isStatusError = computed(() => runStatus.value?.status === 'error')

// W1: a 404 means the run no longer exists (swept/deleted) — distinct from a
// pipeline 'error' status. Render a graceful empty-state and clear the stale
// localStorage run_id so the next navigation starts clean.
const isNotFound = computed(() => httpStatusOf(statusError.value) === 404)

watch(
  isNotFound,
  (notFound) => {
    if (notFound && runStore.runId === props.id) {
      runStore.reset()
    }
  },
  { immediate: true },
)

// RH-011-S03: mirror the polled status into the run store so the "Revisión"
// nav link (gated on runStore.isReady in App.vue) appears on cold-load too.
// setStatus is the store's documented mirror hook (used by RunProgress during
// the upload flow); immediate:true covers the cached-data mount case.
watch(
  () => runStatus.value,
  (status) => {
    if (status) runStore.setStatus(status.status, status.error)
  },
  { immediate: true },
)

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

/** Rev-2: unresolved guías bucket (REV-C04). Never appears in the main grid. */
const unresolvedGuias = computed<UnresolvedGuiaResponse[]>(
  () => tableQuery.data.value?.unresolved_guias ?? [],
)

/** Rev-3 (REV-E05): errored guías bucket — 0-line guías, read-only surface. */
const erroredGuias = computed<ErroredGuiaResponse[]>(
  () => tableQuery.data.value?.errored_guias ?? [],
)

/** F3 / REV-R23: Pendientes tab badge count — updates as guías recover. */
const erroredCount = computed<number>(() => erroredGuias.value.length)

/** SDD#2 (REV-R27/REV-R33): discarded GUIA pages — structurally separate list. */
const discardedPages = computed<DiscardedPageResponse[]>(
  () => tableQuery.data.value?.discarded_pages ?? [],
)

/** SDD#2 (REV-R27): Descartadas tab badge count — updates as pages recover. */
const discardedCount = computed<number>(() => discardedPages.value.length)

// ---------------------------------------------------------------------------
// Tabs (F3 / REV-R23 / D7; SDD#2 REV-R27 third tab) — local activeTab ref.
// ---------------------------------------------------------------------------

type TabKey = 'reconciliacion' | 'pendientes' | 'descartadas'

const activeTab = ref<TabKey>('reconciliacion')

// WAI-ARIA tabs pattern (W1): roving tabindex + arrow/Home/End keyboard nav.
// REV-R27: indices preserved — Reconciliación=0, Pendientes=1, Descartadas=2.
const TAB_ORDER: TabKey[] = ['reconciliacion', 'pendientes', 'descartadas']
const reconTabEl = ref<HTMLElement | null>(null)
const pendientesTabEl = ref<HTMLElement | null>(null)
const descartadasTabEl = ref<HTMLElement | null>(null)

function tabElFor(key: TabKey): HTMLElement | null {
  switch (key) {
    case 'reconciliacion':
      return reconTabEl.value
    case 'pendientes':
      return pendientesTabEl.value
    case 'descartadas':
      return descartadasTabEl.value
  }
}

function activateTab(key: TabKey): void {
  activeTab.value = key
  void nextTick(() => tabElFor(key)?.focus())
}

function onTabKeydown(event: KeyboardEvent, current: TabKey): void {
  const idx = TAB_ORDER.indexOf(current)
  let next: TabKey | null = null
  switch (event.key) {
    case 'ArrowRight':
    case 'ArrowDown':
      next = TAB_ORDER[(idx + 1) % TAB_ORDER.length]
      break
    case 'ArrowLeft':
    case 'ArrowUp':
      next = TAB_ORDER[(idx - 1 + TAB_ORDER.length) % TAB_ORDER.length]
      break
    case 'Home':
      next = TAB_ORDER[0]
      break
    case 'End':
      next = TAB_ORDER[TAB_ORDER.length - 1]
      break
    default:
      return
  }
  event.preventDefault()
  if (next) activateTab(next)
}

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

/**
 * Handles "Assign to registro" from UnresolvedGuiasPanel.
 * Opens GuiaReassignDialog with row=null (no parent row context — the guía is unresolved).
 */
function onAssignUnresolved(guiaId: string): void {
  reassignGuiaId.value = guiaId
  reassignTarget.value = null // unresolved guías have no parent row
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
// Page click (source page drill-down → full-res lightbox, issue #27)
// ---------------------------------------------------------------------------

const showPageViewer = ref(false)
const viewerPage = ref<number>(0)
const viewerRowPages = ref<number[]>([])

function onPageClick(page: number): void {
  // F2 / REV-R22 refinement: when the click originates from a drill-down guía
  // (chip or serie-número), scope prev/next navigation to THAT guía's own pages.
  // Prefer the owning guía's source_pages; fall back to the owning row's pages,
  // then to the single page.
  let owningGuiaPages: number[] | null = null
  for (const r of rows.value) {
    const guia = r.guias?.find((g) => g.source_pages?.includes(page))
    if (guia) {
      owningGuiaPages = guia.source_pages
      break
    }
  }
  const owningRow = rows.value.find((r) => r.source_pages?.includes(page))
  viewerRowPages.value = owningGuiaPages ?? owningRow?.source_pages ?? [page]
  viewerPage.value = page
  showPageViewer.value = true
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

/* Tabs (F3 / REV-R23) */
.review-page__tabs {
  display: flex;
  gap: var(--space-1);
  border-bottom: 1px solid var(--border-default);
}

.review-page__tab {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-secondary);
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  cursor: pointer;
  transition: color var(--transition-fast), border-color var(--transition-fast);
}

.review-page__tab:hover {
  color: var(--text-primary);
}

.review-page__tab:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
  border-radius: var(--radius-sm);
}

.review-page__tab--active {
  color: var(--action-primary, var(--text-primary));
  border-bottom-color: var(--action-primary, var(--text-primary));
}

.review-page__tab-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 20px;
  padding: 0 var(--space-1);
  border-radius: var(--radius-pill);
  background-color: var(--status-mismatch-bg, #fde8e8);
  color: var(--status-mismatch-fg, #c0392b);
  font-size: var(--text-2xs);
  font-weight: 700;
  font-variant-numeric: tabular-nums;
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
