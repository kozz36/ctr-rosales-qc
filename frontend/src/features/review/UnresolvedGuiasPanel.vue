<template>
  <section
    v-if="unresolvedGuias.length > 0"
    class="unresolved-panel"
    aria-labelledby="unresolved-heading"
  >
    <!-- Panel header — collapsible toggle -->
    <button
      class="unresolved-panel__header"
      :aria-expanded="isOpen"
      aria-controls="unresolved-panel-body"
      @click="isOpen = !isOpen"
    >
      <span
        class="unresolved-panel__chevron"
        :class="{ 'unresolved-panel__chevron--open': isOpen }"
        aria-hidden="true"
      >▶</span>
      <h2 id="unresolved-heading" class="unresolved-panel__title">
        Guías sin registro asignado
      </h2>
      <span class="unresolved-panel__count" aria-label="cantidad">
        {{ unresolvedGuias.length }}
      </span>
    </button>

    <!-- Panel body -->
    <div
      v-show="isOpen"
      id="unresolved-panel-body"
      class="unresolved-panel__body"
      role="list"
      aria-label="Guías sin registro asignado"
    >
      <div
        v-for="guia in unresolvedGuias"
        :key="guia.guia_id"
        class="unresolved-panel__item"
        role="listitem"
      >
        <!-- Identity info -->
        <div class="unresolved-panel__item-info">
          <span class="unresolved-panel__item-id mono">
            {{ guia.guia_id || `Páginas ${guia.source_pages.join(', ')}` }}
          </span>
          <span class="unresolved-panel__item-source">
            {{ guia.identity_source === 'qr' ? 'QR' : 'OCR fallback' }}
          </span>
          <span class="unresolved-panel__item-pages">
            Págs. {{ guia.source_pages.join(', ') }}
          </span>
        </div>

        <!-- Action: assign to registro -->
        <button
          class="unresolved-panel__assign-btn"
          :aria-label="`Asignar guía ${guia.guia_id} a un registro`"
          @click="onAssign(guia.guia_id)"
        >
          Asignar a registro
        </button>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
/**
 * UnresolvedGuiasPanel — collapsible panel listing unresolved guías (REV-C04).
 *
 * Unresolved guías are those whose registro could not be determined during the
 * pipeline run (GuiaDeRemision.registro == None).  They surface here for human
 * assignment via GuiaReassignDialog.  They MUST NOT appear in the main grid.
 *
 * Props:
 *   unresolvedGuias — list of UnresolvedGuiaResponse from the table endpoint.
 *
 * Emits:
 *   assignGuia(guia_id) — requests parent to open GuiaReassignDialog.
 */

import { ref } from 'vue'
import type { UnresolvedGuiaResponse } from '@/api/types'

defineProps<{
  /** Unresolved guías from the table response (REV-C04). */
  unresolvedGuias: UnresolvedGuiaResponse[]
}>()

const emit = defineEmits<{
  /** Requests parent (ReviewPage) to open GuiaReassignDialog for this guia_id. */
  assignGuia: [guiaId: string]
}>()

const isOpen = ref(true)

function onAssign(guiaId: string): void {
  emit('assignGuia', guiaId)
}
</script>

<style scoped>
.unresolved-panel {
  background-color: var(--surface-raised);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

/* Header button */
.unresolved-panel__header {
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

.unresolved-panel__header:hover {
  background-color: var(--surface-hover);
}

.unresolved-panel__header:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}

.unresolved-panel__chevron {
  font-size: var(--text-2xs);
  color: var(--text-tertiary);
  transition: transform var(--transition-fast);
  flex-shrink: 0;
}

.unresolved-panel__chevron--open {
  transform: rotate(90deg);
}

.unresolved-panel__title {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-primary);
  flex: 1;
}

.unresolved-panel__count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  height: 22px;
  padding: 0 var(--space-1);
  border-radius: var(--radius-pill);
  background-color: var(--status-declared-missing-bg);
  color: var(--status-declared-missing-fg);
  font-size: var(--text-xs);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

/* Body */
.unresolved-panel__body {
  border-top: 1px solid var(--border-subtle);
}

/* List items */
.unresolved-panel__item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--border-subtle);
  transition: background-color var(--transition-fast);
}

.unresolved-panel__item:last-child {
  border-bottom: none;
}

.unresolved-panel__item:hover {
  background-color: var(--surface-hover);
}

.unresolved-panel__item-info {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex: 1;
  min-width: 0;
  flex-wrap: wrap;
}

.unresolved-panel__item-id {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--text-primary);
}

.unresolved-panel__item-source {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  background-color: var(--surface-overlay);
  padding: 1px var(--space-2);
  border-radius: var(--radius-pill);
  flex-shrink: 0;
}

.unresolved-panel__item-pages {
  font-size: var(--text-xs);
  color: var(--text-tertiary);
}

/* Assign button */
.unresolved-panel__assign-btn {
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-md);
  border: 1px solid var(--action-primary);
  background-color: transparent;
  color: var(--action-primary-hover);
  font-size: var(--text-xs);
  font-weight: 500;
  cursor: pointer;
  flex-shrink: 0;
  transition:
    background-color var(--transition-fast),
    color var(--transition-fast);
}

.unresolved-panel__assign-btn:hover {
  background-color: rgba(31, 111, 235, 0.12);
}

.unresolved-panel__assign-btn:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}
</style>
