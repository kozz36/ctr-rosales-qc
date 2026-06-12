<template>
  <section class="run-history-page" aria-labelledby="historial-heading">
    <h1 id="historial-heading" class="run-history-page__title">
      Historial de ejecuciones
    </h1>

    <div
      v-if="isLoading && runs.length === 0"
      class="run-history-page__loading"
      role="status"
      aria-live="polite"
    >
      <span class="run-history-page__spinner" aria-hidden="true" />
      <span>Cargando historial…</span>
    </div>

    <p v-else-if="listError" class="run-history-page__list-error" role="alert">
      No se pudo cargar el historial. Verifica la conexión e inténtalo de nuevo.
    </p>

    <p v-else-if="runs.length === 0" class="run-history-page__empty" role="status">
      Sin ejecuciones registradas.
    </p>

    <ul v-else class="run-history-page__list">
      <li
        v-for="run in runs"
        :key="run.run_id"
        class="run-history-page__row"
        :data-status="run.status"
      >
        <button
          class="run-history-page__entry"
          type="button"
          :title="`Abrir la ejecución ${run.run_id.slice(0, 8)}`"
          @click="openRun(run)"
        >
          <span class="run-history-page__label">{{ entryLabel(run) }}</span>
          <span class="run-history-page__badge" :data-status="run.status">
            {{ STATUS_LABELS[run.status] ?? run.status }}
          </span>
          <span
            v-if="run.degraded"
            class="run-history-page__degraded"
            title="Ejecución antigua sin manifiesto: algunos datos no están disponibles"
          >
            datos parciales
          </span>
        </button>

        <p v-if="run.error" class="run-history-page__error-reason">
          {{ run.error }}
        </p>
        <p
          v-if="rowErrors[run.run_id]"
          class="run-history-page__row-error"
          role="alert"
        >
          {{ rowErrors[run.run_id] }}
        </p>

        <div class="run-history-page__actions">
          <button
            v-if="run.status === 'error'"
            class="run-history-page__retry-btn"
            type="button"
            :disabled="pending.has(run.run_id) || undefined"
            :aria-busy="pending.has(run.run_id)"
            title="Reintentar la ejecución desde el PDF almacenado"
            @click="onRetry(run)"
          >
            {{ pending.has(run.run_id) ? 'Reintentando…' : 'Reintentar' }}
          </button>
          <button
            class="run-history-page__delete-btn"
            type="button"
            :disabled="pending.has(run.run_id) || undefined"
            title="Eliminar esta ejecución y todos sus archivos"
            @click="askDelete(run)"
          >
            Eliminar
          </button>
        </div>
      </li>
    </ul>

    <!-- Delete confirm dialog (SDD#2 DescartadasTab pattern: backdrop +
         role=dialog + esc + focus on confirm) -->
    <div
      v-if="deleteTarget"
      class="run-history-page__dialog-backdrop"
      @click.self="cancelDelete"
    >
      <div
        class="run-history-page__dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="historial-confirm-title"
        @keydown.esc="cancelDelete"
      >
        <h3 id="historial-confirm-title" class="run-history-page__dialog-title">
          ¿Eliminar esta ejecución?
        </h3>
        <p class="run-history-page__dialog-body">
          {{ entryLabel(deleteTarget) }} — se eliminarán todos sus archivos.
          Esta acción no se puede deshacer.
        </p>
        <div class="run-history-page__dialog-actions">
          <button
            class="run-history-page__dialog-cancel"
            type="button"
            @click="cancelDelete"
          >
            Cancelar
          </button>
          <button
            ref="confirmBtnEl"
            class="run-history-page__dialog-confirm"
            type="button"
            @click="confirmDelete"
          >
            Eliminar definitivamente
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
/**
 * RunHistoryPage — /historial route (SDD#3 D6, RH-010/RH-007/RH-009).
 *
 * Server state: useRunsList (TanStack Query) polls GET /runs so the list
 * reacts to runs added, deleted, or retried elsewhere. List arrives sorted
 * desc from the backend (RH-003); the page renders it verbatim.
 *
 * Row label: `DD-MM-YYYY · Registros {min}–{max} · #{seq}` (es-PE).
 * Degraded legacy entries render "—" for unavailable fields — never crash,
 * never show "undefined" (RH-003-S03).
 *
 * Actions (DescartadasTab pattern — direct client calls + refetch):
 *   [Reintentar] — error runs only (RH-007-S01). POST retry → 202 → refetch
 *     (entry flips to processing; clicking it opens the run's progress view).
 *     409 is surfaced honestly next to the entry — never a silent no-op.
 *   [Eliminar]   — all entries, confirm dialog first (RH-009). DELETE → entry
 *     leaves the list via refetch. Deleting the currently-open run clears
 *     runStore (also wiping the persisted run_id) and navigates home
 *     (RH-009-S04 frontend grace).
 *
 * Identifier discipline: run_id (UUID) is the API key; the registro range and
 * #seq are display-only — never used for routing or grouping.
 */

import { ref, reactive, computed, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useRunsList } from '@/composables/useReconciliationApi'
import { deleteRun, retryRun } from '@/api/client'
import { useRunStore } from '@/stores/run'
import type { RunSummaryResponse } from '@/api/types'

const router = useRouter()
const runStore = useRunStore()

// ---------------------------------------------------------------------------
// Runs list (server state)
// ---------------------------------------------------------------------------

const runsQuery = useRunsList()

const runs = computed<RunSummaryResponse[]>(() => runsQuery.data.value ?? [])
const isLoading = computed<boolean>(() => runsQuery.isFetching.value)
const listError = computed<boolean>(() => runsQuery.error.value !== null)

const STATUS_LABELS: Record<string, string> = {
  pending: 'Pendiente',
  processing: 'Procesando',
  review: 'En revisión',
  error: 'Error',
}

// ---------------------------------------------------------------------------
// Row label — fecha + registro range + per-day seq; "—" for degraded nulls
// ---------------------------------------------------------------------------

function formatFecha(iso: string | null): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  const dd = String(date.getDate()).padStart(2, '0')
  const mm = String(date.getMonth() + 1).padStart(2, '0')
  return `${dd}-${mm}-${date.getFullYear()}`
}

function entryLabel(run: RunSummaryResponse): string {
  const fecha = formatFecha(run.started_at)
  const registros =
    run.registro_min !== null && run.registro_max !== null
      ? `Registros ${run.registro_min}–${run.registro_max}`
      : 'Registros —'
  const seq = run.seq !== null ? `#${run.seq}` : '#—'
  return `${fecha} · ${registros} · ${seq}`
}

// ---------------------------------------------------------------------------
// Navigation — cold-load is transparent: the backend hydrates lazily and
// ReviewPage shows its existing waiting/loading states (RH-011)
// ---------------------------------------------------------------------------

function openRun(run: RunSummaryResponse): void {
  void router.push(`/runs/${run.run_id}`)
}

// ---------------------------------------------------------------------------
// Retry (RH-007) — error runs only; 409 surfaced honestly
// ---------------------------------------------------------------------------

const pending = ref<Set<string>>(new Set())
const rowErrors = reactive<Record<string, string>>({})

function setPending(runId: string, value: boolean): void {
  const next = new Set(pending.value)
  if (value) next.add(runId)
  else next.delete(runId)
  pending.value = next
}

async function onRetry(run: RunSummaryResponse): Promise<void> {
  if (pending.value.has(run.run_id)) return
  delete rowErrors[run.run_id]
  setPending(run.run_id, true)
  try {
    await retryRun(run.run_id)
    // 202 — the entry flips to "processing" via the refreshed list.
    await runsQuery.refetch()
  } catch (err: unknown) {
    const status = (err as { response?: { status?: number } })?.response?.status
    rowErrors[run.run_id] =
      status === 409
        ? 'No se pudo reintentar: hay otra ejecución en curso o el estado cambió.'
        : 'No se pudo reintentar la ejecución. Inténtalo de nuevo.'
  } finally {
    setPending(run.run_id, false)
  }
}

// ---------------------------------------------------------------------------
// Delete (RH-009) — confirm dialog (SDD#2 dialog pattern reuse)
// ---------------------------------------------------------------------------

const deleteTarget = ref<RunSummaryResponse | null>(null)
const confirmBtnEl = ref<HTMLElement | null>(null)

function askDelete(run: RunSummaryResponse): void {
  delete rowErrors[run.run_id]
  deleteTarget.value = run
  void nextTick(() => confirmBtnEl.value?.focus())
}

function cancelDelete(): void {
  deleteTarget.value = null
}

async function confirmDelete(): Promise<void> {
  const target = deleteTarget.value
  if (!target) return
  deleteTarget.value = null
  setPending(target.run_id, true)
  try {
    await deleteRun(target.run_id)
    if (runStore.runId === target.run_id) {
      // Deleted the currently-open run: clear the store (also wipes the
      // persisted run_id) and go home (RH-009-S04).
      runStore.reset()
      void router.push('/')
    }
    await runsQuery.refetch()
  } catch (err: unknown) {
    const status = (err as { response?: { status?: number } })?.response?.status
    rowErrors[target.run_id] =
      status === 409
        ? 'No se puede eliminar una ejecución en curso.'
        : 'No se pudo eliminar la ejecución. Inténtalo de nuevo.'
  } finally {
    setPending(target.run_id, false)
  }
}
</script>

<style scoped>
.run-history-page {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.run-history-page__title {
  font-size: var(--text-xl);
  font-weight: 600;
  color: var(--text-primary);
}

.run-history-page__loading,
.run-history-page__empty {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-6) var(--space-4);
  font-size: var(--text-sm);
  color: var(--text-secondary);
  background-color: var(--surface-raised);
  border: 1px dashed var(--border-default);
  border-radius: var(--radius-lg);
}

.run-history-page__spinner {
  width: 16px;
  height: 16px;
  border: 2px solid var(--border-default);
  border-top-color: var(--action-primary);
  border-radius: 50%;
  flex-shrink: 0;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.run-history-page__list-error {
  padding: var(--space-4);
  font-size: var(--text-sm);
  color: var(--status-mismatch-fg, #c0392b);
  background-color: var(--status-mismatch-bg, #fde8e8);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
}

.run-history-page__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.run-history-page__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  background-color: var(--surface-raised);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
}

.run-history-page__entry {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  flex: 1;
  min-width: 0;
  padding: var(--space-1) 0;
  font-size: var(--text-sm);
  color: var(--text-primary);
  text-align: left;
  background: none;
  border: none;
  cursor: pointer;
}

.run-history-page__entry:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
  border-radius: var(--radius-sm);
}

.run-history-page__label {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.run-history-page__badge {
  display: inline-flex;
  align-items: center;
  padding: 2px var(--space-2);
  border-radius: var(--radius-pill);
  font-size: var(--text-xs);
  font-weight: 500;
  border: 1px solid var(--border-default);
  color: var(--text-secondary);
  white-space: nowrap;
}

.run-history-page__badge[data-status='review'] {
  color: var(--status-match-fg);
  border-color: var(--status-match-glow);
  background-color: var(--status-match-bg);
}

.run-history-page__badge[data-status='error'] {
  color: var(--status-mismatch-fg);
  border-color: var(--status-mismatch-glow);
  background-color: var(--status-mismatch-bg);
}

.run-history-page__badge[data-status='processing'],
.run-history-page__badge[data-status='pending'] {
  color: var(--status-pending-fg, #92400e);
  background-color: var(--status-pending-bg, #fef3c7);
}

.run-history-page__degraded {
  font-size: var(--text-2xs);
  font-weight: 600;
  color: var(--text-secondary);
  white-space: nowrap;
}

.run-history-page__error-reason {
  flex-basis: 100%;
  font-size: var(--text-xs);
  color: var(--status-mismatch-fg, #c0392b);
}

.run-history-page__row-error {
  flex-basis: 100%;
  font-size: var(--text-xs);
  color: var(--status-mismatch-fg, #c0392b);
}

.run-history-page__actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-left: auto;
}

.run-history-page__retry-btn,
.run-history-page__delete-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-1) var(--space-3);
  font-size: var(--text-xs);
  font-weight: 600;
  border-radius: var(--radius-md);
  background-color: var(--surface-raised);
  cursor: pointer;
  transition: background-color var(--transition-fast), opacity var(--transition-fast);
  white-space: nowrap;
}

.run-history-page__retry-btn {
  border: 1px solid var(--color-primary, #4f46e5);
  color: var(--color-primary, #4f46e5);
}

.run-history-page__delete-btn {
  border: 1px solid var(--status-mismatch-glow, #c0392b);
  color: var(--status-mismatch-fg, #c0392b);
}

.run-history-page__retry-btn:hover:not(:disabled),
.run-history-page__delete-btn:hover:not(:disabled) {
  background-color: var(--surface-hover);
}

.run-history-page__retry-btn:focus-visible,
.run-history-page__delete-btn:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}

.run-history-page__retry-btn:disabled,
.run-history-page__delete-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* Confirm dialog (DescartadasTab pattern) */
.run-history-page__dialog-backdrop {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal, 1000);
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: rgba(0, 0, 0, 0.45);
}

.run-history-page__dialog {
  width: min(440px, 90vw);
  padding: var(--space-5);
  background-color: var(--surface-overlay, var(--surface-raised));
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg, 0 10px 30px rgba(0, 0, 0, 0.3));
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.run-history-page__dialog-title {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--text-primary);
}

.run-history-page__dialog-body {
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.run-history-page__dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}

.run-history-page__dialog-cancel,
.run-history-page__dialog-confirm {
  padding: var(--space-1) var(--space-4);
  font-size: var(--text-sm);
  font-weight: 600;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.run-history-page__dialog-cancel {
  border: 1px solid var(--border-default);
  background-color: var(--surface-raised);
  color: var(--text-primary);
}

.run-history-page__dialog-cancel:hover {
  background-color: var(--surface-hover);
}

.run-history-page__dialog-confirm {
  border: 1px solid var(--status-mismatch-fg, #c0392b);
  background-color: var(--status-mismatch-fg, #c0392b);
  color: #fff;
}

.run-history-page__dialog-confirm:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}
</style>
