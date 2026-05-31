<template>
  <div class="export-btn-group" role="group" aria-label="Exportar reconciliación">
    <button
      class="export-btn export-btn--primary"
      :disabled="isPending || disabled"
      :aria-busy="isPending"
      :title="disabled ? 'El run debe estar en estado de revisión para exportar' : 'Exportar a Excel'"
      @click="triggerExport('xlsx')"
    >
      <span v-if="isPending && activeFormat === 'xlsx'" class="export-btn__spinner" aria-hidden="true" />
      <span v-else class="export-btn__icon" aria-hidden="true">↓</span>
      <span>{{ isPending && activeFormat === 'xlsx' ? 'Exportando...' : 'Exportar XLSX' }}</span>
    </button>

    <button
      class="export-btn export-btn--secondary"
      :disabled="isPending || disabled"
      :aria-busy="isPending"
      :title="disabled ? 'El run debe estar en estado de revisión para exportar' : 'Exportar a CSV'"
      @click="triggerExport('csv')"
    >
      <span v-if="isPending && activeFormat === 'csv'" class="export-btn__spinner" aria-hidden="true" />
      <span v-else class="export-btn__icon" aria-hidden="true">↓</span>
      <span>CSV</span>
    </button>

    <!-- Error display -->
    <span v-if="error" class="export-btn__error" role="alert" aria-live="polite">
      <span aria-hidden="true">✕</span>
      {{ error }}
    </span>
  </div>
</template>

<script setup lang="ts">
/**
 * ExportButton — triggers POST /runs/{id}/export and downloads the blob.
 *
 * Two format buttons: XLSX (primary) and CSV (secondary).
 * Blob download is handled by useExportRun composable (PR-5a).
 * Disabled while any export is in-flight or if `disabled` prop is set.
 */

import { ref } from 'vue'
import type { ExportFormat } from '@/api/types'

const props = defineProps<{
  /** Disable the buttons (e.g. run not in review state) */
  disabled?: boolean
  /** Is an export mutation in-flight? */
  isPending?: boolean
  /** Mutation error string */
  error?: string | null
}>()

const emit = defineEmits<{
  export: [format: ExportFormat]
}>()

const activeFormat = ref<ExportFormat | null>(null)

function triggerExport(fmt: ExportFormat): void {
  if (props.isPending || props.disabled) return
  activeFormat.value = fmt
  emit('export', fmt)
}
</script>

<style scoped>
.export-btn-group {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.export-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition:
    background-color var(--transition-fast),
    border-color var(--transition-fast),
    opacity var(--transition-fast);
}

.export-btn:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}

.export-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.export-btn--primary {
  background-color: var(--action-primary);
  border: 1px solid var(--action-primary);
  color: var(--text-primary);
}

.export-btn--primary:not(:disabled):hover {
  background-color: var(--action-primary-hover);
  border-color: var(--action-primary-hover);
}

.export-btn--secondary {
  background-color: transparent;
  border: 1px solid var(--border-default);
  color: var(--text-secondary);
}

.export-btn--secondary:not(:disabled):hover {
  border-color: var(--border-strong);
  color: var(--text-primary);
}

.export-btn__icon {
  font-size: var(--text-sm);
}

.export-btn__spinner {
  width: 12px;
  height: 12px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: white;
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
  flex-shrink: 0;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.export-btn__error {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--text-xs);
  color: var(--status-mismatch-fg);
}
</style>
