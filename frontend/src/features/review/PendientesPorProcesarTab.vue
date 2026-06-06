<template>
  <section class="pendientes-tab" aria-labelledby="pendientes-heading">
    <h2 id="pendientes-heading" class="pendientes-tab__heading">
      Pendientes por procesar
    </h2>

    <p v-if="groups.length === 0 && orphanSummaries.length === 0" class="pendientes-tab__empty" role="status">
      No hay guías con error pendientes de procesar.
    </p>

    <!-- Completion summaries for fully-recovered registros (REV-R21-S03):
         once every guía recovers the group leaves the live list, so its
         "N recuperadas / 0 fallaron" summary is surfaced here instead. -->
    <p
      v-for="entry in orphanSummaries"
      :key="`settled-${entry.registro}`"
      class="pendientes-tab__summary pendientes-tab__summary--settled"
      role="status"
      aria-live="polite"
    >
      Registro {{ entry.registro }}: {{ entry.recovered }} recuperadas /
      {{ entry.failed }} fallaron
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

        <!-- W3: the null/"—" bucket has no real registro → the bulk endpoint
             can never match it (dead 404). Hide the button there. -->
        <button
          v-if="group.registro !== NULL_REGISTRO_KEY"
          class="pendientes-tab__bulk-btn"
          :disabled="isInFlight(group.registro) || undefined"
          :aria-busy="isInFlight(group.registro)"
          :title="`Reprocesar con IA todas las guías con error del registro ${group.registro}`"
          @click="openConfirm(group.registro, $event)"
        >
          {{ isInFlight(group.registro) ? 'Procesando…' : 'Procesar todos con IA' }}
        </button>
        <span
          v-else
          class="pendientes-tab__group-hint"
          title="Estas guías no tienen un Registro asignado; reasígnalas antes de reprocesar en lote."
        >
          Sin registro — reasignar primero
        </span>
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
        @keydown.tab="onDialogTab"
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

/**
 * Sentinel bucket key for guías with no Registro. The bulk reprocess endpoint
 * keys on a real Registro N°, so this bucket exposes NO bulk button (W3).
 */
const NULL_REGISTRO_KEY = '—'

/**
 * Robust settlement constants (CRITICAL fix). Backend vision is 6-14s/guía,
 * bounded by Semaphore(3); a 2s poll plateaus on the first ticks while the
 * batch is still running. We therefore never finalize on a first-tick plateau:
 *   - elapsed floor ≈ ceil(N/3) * PER_GUIA_FLOOR_MS — a lower bound for a
 *     Semaphore(3) batch to produce its first result;
 *   - hard cap ≈ N * HARD_CAP_PER_GUIA_MS — avoids an infinite poll if the
 *     errored list never shrinks (all genuinely failed).
 */
const PER_GUIA_FLOOR_MS = 10_000
const HARD_CAP_PER_GUIA_MS = 20_000

interface Group {
  registro: string
  guias: ErroredGuiaResponse[]
}

/** Errored guías grouped by registro (null registro bucketed under "—"). */
const groups = computed<Group[]>(() => {
  const map = new Map<string, ErroredGuiaResponse[]>()
  for (const g of props.erroredGuias) {
    const key = g.registro ?? NULL_REGISTRO_KEY
    const bucket = map.get(key)
    if (bucket) bucket.push(g)
    else map.set(key, [g])
  }
  return [...map.entries()].map(([registro, guias]) => ({ registro, guias }))
})

function remainingFor(registro: string): number {
  return props.erroredGuias.filter(
    (g) => (g.registro ?? NULL_REGISTRO_KEY) === registro,
  ).length
}

// ---------------------------------------------------------------------------
// In-flight + poll state, per registro
// ---------------------------------------------------------------------------

interface BatchState {
  total: number // guías queued at firing time
  lastRemaining: number // remaining at the previous poll (stability detector)
  minRemaining: number // smallest remaining ever observed (recovered floor)
  observedShrink: boolean // have we seen ≥1 guía leave the list since firing?
  startedAt: number // Date.now() when the batch fired (elapsed-floor anchor)
  floorMs: number // elapsed floor before a plateau may finalize (∝ N)
  capMs: number // hard cap on total poll duration (∝ N)
  timer: ReturnType<typeof setInterval> | null
}

const inFlight = reactive(new Map<string, BatchState>())
const summaries = reactive<Record<string, { recovered: number; failed: number }>>({})
const errors = reactive<Record<string, string>>({})

function isInFlight(registro: string): boolean {
  return inFlight.has(registro)
}

/**
 * Settled summaries whose registro no longer appears in the live group list
 * (every guía recovered → the group disappeared). Surfaced at the top so a
 * fully-recovered batch still shows "N recuperadas / 0 fallaron".
 */
const orphanSummaries = computed(() => {
  const liveKeys = new Set(groups.value.map((g) => g.registro))
  return Object.entries(summaries)
    .filter(([registro]) => !liveKeys.has(registro))
    .map(([registro, s]) => ({ registro, recovered: s.recovered, failed: s.failed }))
})

// ---------------------------------------------------------------------------
// Confirm dialog
// ---------------------------------------------------------------------------

const confirmRegistro = ref<string | null>(null)
const dialogEl = ref<HTMLElement | null>(null)
const confirmBtnEl = ref<HTMLElement | null>(null)
/** W2: element that opened the dialog — focus is restored here on close. */
const triggerEl = ref<HTMLElement | null>(null)

const confirmCount = computed<number>(() =>
  confirmRegistro.value === null ? 0 : remainingFor(confirmRegistro.value),
)

function openConfirm(registro: string, event?: Event): void {
  if (isInFlight(registro)) return
  delete summaries[registro]
  delete errors[registro]
  // W2: remember the trigger so focus can be restored on close.
  triggerEl.value = (event?.currentTarget as HTMLElement | null) ?? null
  confirmRegistro.value = registro
  // Focus management: move focus into the dialog (accessibility).
  void nextTick(() => confirmBtnEl.value?.focus())
}

/** W2: restore focus to the element that opened the dialog. */
function restoreTriggerFocus(): void {
  const el = triggerEl.value
  triggerEl.value = null
  void nextTick(() => el?.focus())
}

function cancelConfirm(): void {
  confirmRegistro.value = null
  restoreTriggerFocus()
}

/**
 * W2: focus-trap. Tab/Shift+Tab cycles within the dialog's focusable elements
 * instead of escaping to the page behind the modal (WAI-ARIA dialog pattern).
 */
function onDialogTab(event: KeyboardEvent): void {
  const root = dialogEl.value
  if (!root) return
  const focusable = [
    ...root.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    ),
  ].filter((el) => !el.hasAttribute('disabled'))
  if (focusable.length === 0) return

  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  const active = document.activeElement as HTMLElement | null

  if (event.shiftKey) {
    if (active === first || !root.contains(active)) {
      event.preventDefault()
      last.focus()
    }
  } else if (active === last || !root.contains(active)) {
    event.preventDefault()
    first.focus()
  }
}

async function confirmReprocess(): Promise<void> {
  const registro = confirmRegistro.value
  confirmRegistro.value = null
  // W2: hand focus back to the trigger now that the dialog is closing.
  restoreTriggerFocus()
  if (registro === null || isInFlight(registro)) return

  const total = remainingFor(registro)
  // Elapsed floor ∝ N for a Semaphore(3) batch; hard cap ∝ N.
  const lanes = Math.max(1, Math.ceil(total / 3))
  inFlight.set(registro, {
    total,
    lastRemaining: total,
    minRemaining: total,
    observedShrink: false,
    startedAt: Date.now(),
    floorMs: lanes * PER_GUIA_FLOOR_MS,
    capMs: Math.max(1, total) * HARD_CAP_PER_GUIA_MS,
    timer: null,
  })

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
    void pollTick(registro)
  }, POLL_INTERVAL_MS)

  // Kick off the first refetch immediately so progress starts without waiting
  // a full interval (no sampling here — the tick samples after refetch resolves).
  emit('refetch')
}

/**
 * One poll tick. Robust settlement (CRITICAL fix):
 *   - Ask the parent to refetch, then sample remaining AFTER the async round
 *     trip resolves (await nextTick) so we never read a pre-fetch value.
 *   - remaining === 0 → finalize immediately (all recovered).
 *   - A first-tick plateau (remaining still full, no observed shrink, before the
 *     elapsed floor) must NOT finalize — the batch is still running.
 *   - Finalize when remaining is stable AND (observed ≥1 shrink OR elapsed floor
 *     reached), or when the hard cap elapses (avoids an infinite poll).
 */
async function pollTick(registro: string): Promise<void> {
  let state = inFlight.get(registro)
  if (!state) return

  // Trigger a refresh and wait for the parent's prop update to settle before
  // sampling (the poll must read AFTER refetch resolves, not before).
  emit('refetch')
  await nextTick()

  state = inFlight.get(registro)
  if (!state) return

  const remaining = remainingFor(registro)
  const elapsed = Date.now() - state.startedAt

  // All recovered → settle now.
  if (remaining === 0) {
    finalize(registro)
    return
  }

  // Track the smallest remaining we have observed and whether anything left.
  if (remaining < state.minRemaining) {
    state.minRemaining = remaining
    state.observedShrink = true
  }

  const stable = remaining >= state.lastRemaining
  state.lastRemaining = remaining

  // Hard cap: never poll forever — finalize with whatever delta we observed.
  if (elapsed >= state.capMs) {
    finalize(registro)
    return
  }

  // Plateau handling: only finalize on a stable read once we have EITHER seen a
  // shrink OR crossed the elapsed floor. A first-tick full-plateau keeps polling.
  if (stable && (state.observedShrink || elapsed >= state.floorMs)) {
    finalize(registro)
  }
  // else: still progressing or too early — keep polling.
}

function finalize(registro: string): void {
  const state = inFlight.get(registro)
  if (!state) return
  if (state.timer) clearInterval(state.timer)

  // Use the smallest remaining ever observed so a guía that errors mid-batch
  // (remaining > total) can never make recovered + failed misrepresent the run.
  const remaining = Math.min(remainingFor(registro), state.minRemaining)
  const recovered = Math.max(0, state.total - remaining)
  summaries[registro] = {
    recovered,
    failed: Math.max(0, state.total - recovered),
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

.pendientes-tab__group-hint {
  margin-left: auto;
  font-size: var(--text-xs);
  font-style: italic;
  color: var(--text-secondary);
  white-space: nowrap;
}

.pendientes-tab__summary {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--status-match-fg, #1a7f37);
}

.pendientes-tab__summary--settled {
  padding: var(--space-2) var(--space-3);
  background-color: var(--status-match-bg, #e6f4ea);
  border: 1px solid var(--status-match-glow, #1a7f3733);
  border-radius: var(--radius-md);
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
