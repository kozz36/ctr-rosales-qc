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
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
/**
 * ErroredGuiasPanel — collapsible read-only panel listing guías with 0 material lines (REV-E05).
 *
 * These are guías whose SUNAT/OCR extraction resolved to 0 material lines.
 * They are surfaced here for human awareness.  NO action buttons in this slice
 * (REINTENTAR / Reprocesar are PR #2 / PR #3 scope).
 *
 * Props:
 *   erroredGuias — list of ErroredGuiaResponse from GET /table.
 */

import { ref } from 'vue'
import type { ErroredGuiaResponse } from '@/api/types'

defineProps<{
  /** Errored guías from the table response (REV-E04). */
  erroredGuias: ErroredGuiaResponse[]
}>()

const isOpen = ref(true)
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
</style>
