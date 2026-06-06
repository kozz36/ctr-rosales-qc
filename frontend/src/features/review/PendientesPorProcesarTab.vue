<template>
  <section class="pendientes-tab" aria-labelledby="pendientes-heading">
    <h2 id="pendientes-heading" class="pendientes-tab__heading">
      Pendientes por procesar
    </h2>

    <p v-if="groups.length === 0" class="pendientes-tab__empty" role="status">
      No hay guías con error pendientes de procesar.
    </p>

    <!-- Per-Registro groups: header + count + bulk button + summary -->
    <div
      v-for="group in groups"
      :key="group.registro"
      class="pendientes-tab__group"
    >
      <div class="pendientes-tab__group-header">
        <span class="pendientes-tab__group-registro">
          Registro {{ group.registro }}
        </span>
        <span
          class="pendientes-tab__group-count"
          :aria-label="`${group.guias.length} guías con error en el registro ${group.registro}`"
        >
          {{ group.guias.length }}
        </span>

        <button
          class="pendientes-tab__bulk-btn"
          :disabled="isInFlight(group.registro) || undefined"
          :aria-busy="isInFlight(group.registro)"
          :title="`Reprocesar con IA todas las guías con error del registro ${group.registro}`"
          @click="openConfirm(group.registro)"
        >
          {{ isInFlight(group.registro) ? 'Procesando…' : 'Procesar todos con IA' }}
        </button>
      </div>

      <!-- Completion summary (REV-R21-S03): "N recuperadas / M fallaron" -->
      <p
        v-if="summaries[group.registro]"
        class="pendientes-tab__summary"
        role="status"
        aria-live="polite"
      >
        {{ summaries[group.registro]!.recovered }} recuperadas /
        {{ summaries[group.registro]!.failed }} fallaron
      </p>

      <!-- Friendly error (e.g. vision disabled → 503) -->
      <p
        v-if="errors[group.registro]"
        class="pendientes-tab__error"
        role="alert"
      >
        {{ errors[group.registro] }}
      </p>
    </div>

    <!-- Per-guía actions (REINTENTAR / Reprocesar con IA single) reuse the
         existing panel; bulk lives in the group headers above. -->
    <ErroredGuiasPanel
      v-if="erroredGuias.length > 0"
      :errored-guias="erroredGuias"
      :run-id="runId"
      @retry="emit('refetch')"
      @retry-success="emit('refetch')"
      @retry-settled="emit('refetch')"
      @reprocess="emit('refetch')"
      @reprocess-success="emit('refetch')"
      @reprocess-settled="emit('refetch')"
    />

    <!-- Confirm dialog (REV-R21-S01): shows the call count before firing -->
    <div
      v-if="confirmRegistro !== null"
      class="pendientes-tab__dialog-backdrop"
      @click.self="cancelConfirm"
    >
      <div
        ref="dialogEl"
        class="pendientes-tab__dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="pendientes-confirm-title"
        @keydown.esc="cancelConfirm"
      >
        <h3 id="pendientes-confirm-title" class="pendientes-tab__dialog-title">
          Reprocesar con IA
        </h3>
        <p class="pendientes-tab__dialog-body">
          ¿Procesar {{ confirmCount }} guías con IA?
          = {{ confirmCount }} llamadas cloud.
        </p>
        <div class="pendientes-tab__dialog-actions">
          <button
            class="pendientes-tab__dialog-cancel"
            @click="cancelConfirm"
          >
            Cancelar
          </button>
          <button
            ref="confirmBtnEl"
            class="pendientes-tab__dialog-confirm"
            @click="confirmReprocess"
          >
            Confirmar ({{ confirmCount }})
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
/**
 * PendientesPorProcesarTab — Pendientes por procesar tab body (F1 / REV-R21, D3, D7).
 *
 * Single-Responsibility extraction (D7): hosts the errored-guías panel and the
 * per-Registro "Procesar todos con IA" bulk reprocess surface so ReviewPage
 * stays thin and this surface is isolatable for Playwright (SA-5, PR-C).
 *
 * Bulk flow (REV-R21):
 *   1. Click → confirm dialog showing the call count (N guías = N llamadas).
 *   2. Confirm → reprocessRegistroBatch(runId, registro) (202, background batch).
 *   3. Button disabled + "Procesando…" while in-flight (REV-R21-S04).
 *   4. Poll: emit 'refetch' on an interval so the parent re-feeds erroredGuias;
 *      recovered guías leave the prop list incrementally (REV-R21-S02).
 *   5. When the registro's remaining count stabilizes (no shrink across a poll),
 *      finalize: recovered = total - remaining, failed = remaining → show
 *      "N recuperadas / M fallaron" (REV-R21-S03). N + M = total.
 *
 * D3: the 202 ReprocessBatchResponse carries only `count` (guías queued); the
 * recovered/failed split is DERIVED from the errored-list delta via polling.
 */

import { ref, reactive, computed, onBeforeUnmount, nextTick } from 'vue'
import type { ErroredGuiaResponse, ReconciliationRowResponse } from '@/api/types'
import { reprocessRegistroBatch } from '@/api/client'
import ErroredGuiasPanel from './ErroredGuiasPanel.vue'

const props = defineProps<{
  /** Errored guías from the table response (REV-E04). */
  erroredGuias: ErroredGuiaResponse[]
  /** Run ID — required for the bulk reprocess endpoint. */
  runId: string
  /** Reconciliation rows (forwarded for F4 Corregir-manual in PR-C). */
  rows?: ReconciliationRowResponse[]
}>()

const emit = defineEmits<{
  /** Ask the parent to refetch GET /table (incremental live progress). */
  (e: 'refetch'): void
}>()

const POLL_INTERVAL_MS = 2000

interface Group {
  registro: string
  guias: ErroredGuiaResponse[]
}

/** Errored guías grouped by registro (null registro bucketed under "—"). */
const groups = computed<Group[]>(() => {
  const map = new Map<string, ErroredGuiaResponse[]>()
  for (const g of props.erroredGuias) {
    const key = g.registro ?? '—'
    const bucket = map.get(key)
    if (bucket) bucket.push(g)
    else map.set(key, [g])
  }
  return [...map.entries()].map(([registro, guias]) => ({ registro, guias }))
})

function remainingFor(registro: string): number {
  return props.erroredGuias.filter((g) => (g.registro ?? '—') === registro).length
}

// ---------------------------------------------------------------------------
// In-flight + poll state, per registro
// ---------------------------------------------------------------------------

interface BatchState {
  total: number // guías queued at firing time
  lastRemaining: number // remaining at the previous poll (stability detector)
  timer: ReturnType<typeof setInterval> | null
}

const inFlight = reactive(new Map<string, BatchState>())
const summaries = reactive<Record<string, { recovered: number; failed: number }>>({})
const errors = reactive<Record<string, string>>({})

function isInFlight(registro: string): boolean {
  return inFlight.has(registro)
}

// ---------------------------------------------------------------------------
// Confirm dialog
// ---------------------------------------------------------------------------

const confirmRegistro = ref<string | null>(null)
const dialogEl = ref<HTMLElement | null>(null)
const confirmBtnEl = ref<HTMLElement | null>(null)

const confirmCount = computed<number>(() =>
  confirmRegistro.value === null ? 0 : remainingFor(confirmRegistro.value),
)

function openConfirm(registro: string): void {
  if (isInFlight(registro)) return
  delete summaries[registro]
  delete errors[registro]
  confirmRegistro.value = registro
  // Focus management: move focus into the dialog (accessibility).
  void nextTick(() => confirmBtnEl.value?.focus())
}

function cancelConfirm(): void {
  confirmRegistro.value = null
}

async function confirmReprocess(): Promise<void> {
  const registro = confirmRegistro.value
  confirmRegistro.value = null
  if (registro === null || isInFlight(registro)) return

  const total = remainingFor(registro)
  inFlight.set(registro, { total, lastRemaining: total, timer: null })

  try {
    await reprocessRegistroBatch(props.runId, registro)
  } catch (err: unknown) {
    inFlight.delete(registro)
    const status = (err as { response?: { status?: number } })?.response?.status
    errors[registro] =
      status === 503
        ? 'Reproceso con IA no disponible: la visión está deshabilitada en este run.'
        : 'No se pudo iniciar el reproceso con IA. Inténtalo de nuevo.'
    return
  }

  startPolling(registro)
}

// ---------------------------------------------------------------------------
// Live polling — emit refetch every interval so the parent re-feeds
// erroredGuias; recovered guías leave the list incrementally (REV-R21-S02).
// The batch is settled when the registro's remaining count stops shrinking
// between two consecutive poll ticks (or reaches 0) → derive the N/M summary.
// ---------------------------------------------------------------------------

function startPolling(registro: string): void {
  const state = inFlight.get(registro)
  if (!state) return

  state.timer = setInterval(() => {
    const state2 = inFlight.get(registro)
    if (!state2) return

    const remaining = remainingFor(registro)
    if (remaining === 0 || remaining >= state2.lastRemaining) {
      // No further shrink since the previous tick (or fully recovered) → settled.
      finalize(registro)
      return
    }
    // Still making progress — record and keep polling for more updates.
    state2.lastRemaining = remaining
    emit('refetch')
  }, POLL_INTERVAL_MS)

  // Kick off the first refetch immediately so progress starts without waiting
  // a full interval.
  emit('refetch')
}

function finalize(registro: string): void {
  const state = inFlight.get(registro)
  if (!state) return
  if (state.timer) clearInterval(state.timer)

  const remaining = remainingFor(registro)
  summaries[registro] = {
    recovered: Math.max(0, state.total - remaining),
    failed: remaining,
  }
  inFlight.delete(registro)
}

onBeforeUnmount(() => {
  for (const state of inFlight.values()) {
    if (state.timer) clearInterval(state.timer)
  }
})
</script>

<style scoped>
.pendientes-tab {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.pendientes-tab__heading {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--text-primary);
}

.pendientes-tab__empty {
  padding: var(--space-6) var(--space-4);
  font-size: var(--text-sm);
  color: var(--text-secondary);
  background-color: var(--surface-raised);
  border: 1px dashed var(--border-default);
  border-radius: var(--radius-lg);
  text-align: center;
}

/* Per-Registro group */
.pendientes-tab__group {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.pendientes-tab__group-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  background-color: var(--surface-raised);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
}

.pendientes-tab__group-registro {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-primary);
}

.pendientes-tab__group-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  height: 22px;
  padding: 0 var(--space-1);
  border-radius: var(--radius-pill);
  background-color: var(--status-mismatch-bg, #fde8e8);
  color: var(--status-mismatch-fg, #c0392b);
  font-size: var(--text-xs);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.pendientes-tab__bulk-btn {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-1) var(--space-3);
  font-size: var(--text-xs);
  font-weight: 600;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-primary, #4f46e5);
  background-color: var(--surface-raised);
  color: var(--color-primary, #4f46e5);
  cursor: pointer;
  transition: background-color var(--transition-fast), opacity var(--transition-fast);
  white-space: nowrap;
}

.pendientes-tab__bulk-btn:hover:not(:disabled) {
  background-color: var(--surface-hover);
}

.pendientes-tab__bulk-btn:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}

.pendientes-tab__bulk-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.pendientes-tab__summary {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--status-match-fg, #1a7f37);
}

.pendientes-tab__error {
  font-size: var(--text-xs);
  color: var(--status-mismatch-fg, #c0392b);
}

/* Confirm dialog */
.pendientes-tab__dialog-backdrop {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal, 1000);
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: rgba(0, 0, 0, 0.45);
}

.pendientes-tab__dialog {
  width: min(420px, 90vw);
  padding: var(--space-5);
  background-color: var(--surface-overlay, var(--surface-raised));
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg, 0 10px 30px rgba(0, 0, 0, 0.3));
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.pendientes-tab__dialog-title {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--text-primary);
}

.pendientes-tab__dialog-body {
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.pendientes-tab__dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}

.pendientes-tab__dialog-cancel,
.pendientes-tab__dialog-confirm {
  padding: var(--space-1) var(--space-4);
  font-size: var(--text-sm);
  font-weight: 600;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.pendientes-tab__dialog-cancel {
  border: 1px solid var(--border-default);
  background-color: var(--surface-raised);
  color: var(--text-primary);
}

.pendientes-tab__dialog-cancel:hover {
  background-color: var(--surface-hover);
}

.pendientes-tab__dialog-confirm {
  border: 1px solid var(--color-primary, #4f46e5);
  background-color: var(--color-primary, #4f46e5);
  color: #fff;
}

.pendientes-tab__dialog-confirm:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}
</style>
