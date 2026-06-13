<template>
  <section
    v-if="erroredGuias.length > 0"
    class="errored-panel"
    aria-labelledby="errored-heading"
  >
    <!-- Panel header — collapsible toggle -->
    <button
      class="errored-panel__header"
      :aria-expanded="isOpen"
      aria-controls="errored-panel-body"
      @click="isOpen = !isOpen"
    >
      <span
        class="errored-panel__chevron"
        :class="{ 'errored-panel__chevron--open': isOpen }"
        aria-hidden="true"
      >▶</span>
      <h2 id="errored-heading" class="errored-panel__title">
        Guías con error (0 líneas de materiales)
      </h2>
      <span class="errored-panel__count" aria-label="cantidad">
        {{ erroredGuias.length }}
      </span>
    </button>

    <!-- Panel body -->
    <div
      v-show="isOpen"
      id="errored-panel-body"
      class="errored-panel__body"
      role="list"
      aria-label="Guías con error de extracción"
    >
      <div
        v-for="guia in erroredGuias"
        :key="guia.guia_id"
        class="errored-panel__item"
        role="listitem"
      >
        <div class="errored-panel__item-info">
          <!-- Registro badge -->
          <span v-if="guia.registro" class="errored-panel__item-registro">
            Reg. {{ guia.registro }}
          </span>
          <!-- guia_id -->
          <span class="errored-panel__item-id mono">
            {{ guia.guia_id }}
          </span>
          <!-- source_pages -->
          <span class="errored-panel__item-pages">
            Error en páginas {{ guia.source_pages.join(', ') }}
          </span>
        </div>

        <!-- Action area: REINTENTAR (PR#2) + Reprocesar con IA (PR#3) -->
        <div class="errored-panel__item-actions">
          <!-- hint when retry_attempted=true -->
          <span
            v-if="guia.retry_attempted"
            class="errored-panel__retry-hint"
            aria-label="SUNAT no disponible para este intento"
          >
            SUNAT no disponible
          </span>

          <button
            class="errored-panel__retry-btn"
            :disabled="guia.retry_attempted || retryingId === guia.guia_id || undefined"
            :aria-busy="retryingId === guia.guia_id"
            :title="guia.retry_attempted ? 'REINTENTAR ya fue ejecutado para esta guía' : 'Recuperar guía vía SUNAT'"
            @click="handleRetry(guia)"
          >
            {{ retryingId === guia.guia_id ? 'Reintentando…' : 'REINTENTAR' }}
          </button>

          <!-- PR#3 / REV-R18: Reprocesar con IA — only shown when retry_attempted=true -->
          <button
            v-if="guia.retry_attempted"
            class="errored-panel__reprocess-btn"
            :disabled="reprocessingIds.has(guia.guia_id) || !visionEnabled || undefined"
            :aria-busy="reprocessingIds.has(guia.guia_id)"
            :title="visionEnabled ? 'Recuperar guía usando visión IA (Reprocesar con IA)' : VISION_DISABLED_TOOLTIP"
            @click="handleReprocess(guia)"
          >
            {{ reprocessingIds.has(guia.guia_id) ? 'Reprocesando…' : 'Reprocesar con IA' }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
/**
 * ErroredGuiasPanel — collapsible panel listing guías with 0 material lines (REV-E05).
 *
 * T-8 / REV-R09: REINTENTAR button per guía entry.
 *   - Enabled when !guia.retry_attempted && retryingId !== guia.guia_id.
 *   - Disabled + "SUNAT no disponible" hint when retry_attempted=true.
 *   - Loading/disabled via retryingId ref during in-flight request.
 *   - On success: emits 'retry-success' for the parent to invalidate the table query.
 *
 * T7 / PR#3: Reprocesar con IA button per guía entry.
 *   - Shown ONLY when guia.retry_attempted=true (REINTENTAR must have been tried first).
 *   - In-flight tracking via reprocessingIds: reactive(new Set<string>()) — REV-R18
 *     CRITICAL: reactive() NOT ref() so Vue tracks Set.add/delete mutations.
 *   - On success: emits 'reprocess-success'.
 *
 * Props:
 *   erroredGuias — list of ErroredGuiaResponse from GET /table.
 *   runId        — current run ID (required for retry/reprocess endpoint calls).
 */

import { ref, reactive } from 'vue'
import { storeToRefs } from 'pinia'
import type { ErroredGuiaResponse } from '@/api/types'
import { retryGuia, reprocessGuia } from '@/api/client'
import { useCapabilitiesStore } from '@/stores/capabilities'
import { VISION_DISABLED_TOOLTIP } from './visionGate'

// REV-R34/R35: store-driven gating of "Reprocesar con IA" (vision-key off →
// visible-but-disabled with explanatory tooltip). Fail-safe disabled default.
const { visionEnabled } = storeToRefs(useCapabilitiesStore())

const props = defineProps<{
  /** Errored guías from the table response (REV-E04). */
  erroredGuias: ErroredGuiaResponse[]
  /** Run ID — required for REINTENTAR / Reprocesar calls. */
  runId?: string
}>()

const emit = defineEmits<{
  /** Emitted when a guía is successfully recovered via REINTENTAR. */
  (e: 'retry-success', payload: { guiaId: string; erroredGuias: ErroredGuiaResponse[] }): void
  /** Emitted when a retry attempt completes (success or failure). */
  (e: 'retry', guiaId: string): void
  /** Emitted when a retry attempt settles (success, recovered=false, or error). */
  (e: 'retry-settled', guiaId: string): void
  /** Emitted when a guía is successfully recovered via Reprocesar con IA. */
  (e: 'reprocess-success', payload: { guiaId: string; erroredGuias: ErroredGuiaResponse[] }): void
  /** Emitted when a reprocess attempt starts. */
  (e: 'reprocess', guiaId: string): void
  /** Emitted when a reprocess attempt settles (success, recovered=false, or error). */
  (e: 'reprocess-settled', guiaId: string): void
}>()

const isOpen = ref(true)

/** ID of the guía currently being retried (loading state); null when idle. */
const retryingId = ref<string | null>(null)

/**
 * Set of guia_ids currently in-flight for Reprocesar con IA.
 *
 * REV-R18 CRITICAL: MUST be reactive(new Set()) NOT ref(new Set()).
 * Vue 3's reactivity system tracks object property access, not value replacement.
 * ref(new Set()) wraps the Set in a ref; calling .add/.delete does NOT trigger
 * reactive updates because the ref's .value reference didn't change.
 * reactive(new Set()) makes the Set itself reactive — Vue intercepts .add/.delete
 * via the Proxy and triggers re-renders correctly.
 */
const reprocessingIds = reactive(new Set<string>())

async function handleRetry(guia: ErroredGuiaResponse): Promise<void> {
  if (!props.runId || guia.retry_attempted || retryingId.value !== null) return

  retryingId.value = guia.guia_id
  emit('retry', guia.guia_id)

  try {
    const result = await retryGuia(props.runId, guia.guia_id)
    if (result.recovered) {
      emit('retry-success', {
        guiaId: guia.guia_id,
        erroredGuias: result.errored_guias,
      })
    }
  } catch {
    // Failure is non-blocking — the button will remain in its current state.
    // The parent refetches via retry-settled so the new backend state is visible.
  } finally {
    retryingId.value = null
    emit('retry-settled', guia.guia_id)
  }
}

async function handleReprocess(guia: ErroredGuiaResponse): Promise<void> {
  if (!props.runId || reprocessingIds.has(guia.guia_id)) return

  reprocessingIds.add(guia.guia_id)
  emit('reprocess', guia.guia_id)

  try {
    const result = await reprocessGuia(props.runId, guia.guia_id)
    if (result.recovered) {
      emit('reprocess-success', {
        guiaId: guia.guia_id,
        erroredGuias: result.errored_guias,
      })
    }
  } catch {
    // Non-blocking — parent refetches via reprocess-settled so new backend state is visible.
  } finally {
    reprocessingIds.delete(guia.guia_id)
    emit('reprocess-settled', guia.guia_id)
  }
}
</script>

<style scoped>
.errored-panel {
  background-color: var(--surface-raised);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

/* Header button */
.errored-panel__header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  padding: var(--space-3) var(--space-4);
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text-primary);
  text-align: left;
  transition: background-color var(--transition-fast);
}

.errored-panel__header:hover {
  background-color: var(--surface-hover);
}

.errored-panel__header:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}

.errored-panel__chevron {
  font-size: var(--text-2xs);
  color: var(--text-tertiary);
  transition: transform var(--transition-fast);
  flex-shrink: 0;
}

.errored-panel__chevron--open {
  transform: rotate(90deg);
}

.errored-panel__title {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-primary);
  flex: 1;
}

.errored-panel__count {
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

/* Body */
.errored-panel__body {
  border-top: 1px solid var(--border-subtle);
}

/* List items */
.errored-panel__item {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--border-subtle);
  transition: background-color var(--transition-fast);
}

.errored-panel__item:last-child {
  border-bottom: none;
}

.errored-panel__item:hover {
  background-color: var(--surface-hover);
}

.errored-panel__item-info {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex: 1;
  min-width: 0;
  flex-wrap: wrap;
}

.errored-panel__item-registro {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--text-secondary);
  background-color: var(--surface-overlay);
  padding: 1px var(--space-2);
  border-radius: var(--radius-pill);
  flex-shrink: 0;
}

.errored-panel__item-id {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--text-primary);
}

.errored-panel__item-pages {
  font-size: var(--text-xs);
  color: var(--text-tertiary);
}

/* T-8: action area per item */
.errored-panel__item-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}

.errored-panel__retry-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-1) var(--space-3);
  font-size: var(--text-xs);
  font-weight: 600;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-default);
  background-color: var(--surface-raised);
  color: var(--text-primary);
  cursor: pointer;
  transition: background-color var(--transition-fast), opacity var(--transition-fast);
  white-space: nowrap;
}

.errored-panel__retry-btn:hover:not(:disabled) {
  background-color: var(--surface-hover);
}

.errored-panel__retry-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.errored-panel__retry-hint {
  font-size: var(--text-2xs);
  color: var(--text-tertiary);
  white-space: nowrap;
}

/* PR#3: Reprocesar con IA button — distinct from REINTENTAR */
.errored-panel__reprocess-btn {
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

.errored-panel__reprocess-btn:hover:not(:disabled) {
  background-color: var(--surface-hover);
}

.errored-panel__reprocess-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
</style>
