<template>
  <tr
    class="recon-row"
    :class="rowClass"
    :data-status="row.status"
    :aria-label="`${row.registro} — ${row.material_canonical} — ${statusLabel}`"
    tabindex="0"
    @keydown.enter="emit('rowActivate', row)"
    @keydown.space.prevent="emit('rowActivate', row)"
  >
    <!-- Col 1: Registro -->
    <td class="recon-row__cell recon-row__cell--registro">
      <span class="recon-row__registro mono">{{ row.registro }}</span>
    </td>

    <!-- Col 2: Fecha -->
    <td class="recon-row__cell recon-row__cell--fecha">
      <span class="recon-row__fecha mono">{{ row.fecha ?? '—' }}</span>
    </td>

    <!-- Col 3: Material -->
    <td class="recon-row__cell recon-row__cell--material">
      <span class="recon-row__material" :title="row.material_canonical">
        {{ row.material_canonical }}
      </span>
    </td>

    <!-- Col 4: Unidad -->
    <td class="recon-row__cell recon-row__cell--unidad">
      <span class="recon-row__unidad mono">{{ row.unidad }}</span>
    </td>

    <!-- Col 5: Declarado (read-only) -->
    <td class="recon-row__cell recon-row__cell--numeric" data-numeric>
      <span class="recon-row__qty">{{ formatDecimal(row.declared_qty) }}</span>
    </td>

    <!-- Col 6: Sumado (guías) — editable when MISMATCH -->
    <td
      class="recon-row__cell recon-row__cell--numeric recon-row__cell--editable"
      data-numeric
      :aria-label="`Sumado guías: ${formatDecimal(row.summed_qty)}${isDirty ? ', editado' : ''}`"
    >
      <div v-if="isEditing" class="recon-row__edit-wrap">
        <input
          ref="summedInputRef"
          v-model="editValue"
          class="recon-row__input mono"
          type="text"
          inputmode="decimal"
          :aria-label="`Editar sumado guías para ${row.material_canonical}`"
          @blur="commitEdit"
          @keydown.enter="commitEdit"
          @keydown.escape="cancelEdit"
        />
      </div>
      <button
        v-else
        class="recon-row__editable-cell"
        :class="{ 'recon-row__editable-cell--dirty': isDirty }"
        :aria-label="`Editar sumado: ${formatDecimal(row.summed_qty)}`"
        :title="row.status === 'MISMATCH' ? 'Clic para editar' : undefined"
        :disabled="row.status === 'MATCH'"
        @click="startEdit"
      >
        <span class="mono">{{ formatDecimal(effectiveSummed) }}</span>
        <span v-if="isDirty" class="recon-row__dirty-dot" aria-label="editado" title="Cambio pendiente" />
        <span v-if="row.status !== 'MATCH' && row.status !== 'GUIA_MISSING'" class="recon-row__edit-hint" aria-hidden="true">✎</span>
      </button>
    </td>

    <!-- Col 7: Delta -->
    <td
      class="recon-row__cell recon-row__cell--numeric recon-row__cell--delta"
      data-numeric
      :data-sign="deltaSign"
    >
      <span class="mono" :class="deltaClass">{{ formatDelta(row.delta) }}</span>
    </td>

    <!-- Col 8: Estado -->
    <td class="recon-row__cell recon-row__cell--status">
      <span class="recon-row__status-badge" :class="`recon-row__status-badge--${row.status.toLowerCase().replace(/_/g, '-')}`">
        <span class="recon-row__status-icon" aria-hidden="true">{{ statusIcon }}</span>
        <span class="recon-row__status-label">{{ statusLabel }}</span>
      </span>
    </td>

    <!-- Col 9: Confianza mín -->
    <td class="recon-row__cell recon-row__cell--confidence">
      <ConfidenceBadge :value="row.min_confidence" compact />
    </td>

    <!-- Col 10: Páginas origen -->
    <td class="recon-row__cell recon-row__cell--pages">
      <SourcePages
        :pages="row.source_pages"
        :run-id="runId"
        @page-click="emit('pageClick', $event)"
      />
    </td>

    <!-- Actions column -->
    <td class="recon-row__cell recon-row__cell--actions">
      <button
        v-if="showReassign"
        class="recon-row__action-btn"
        :aria-label="`Reasignar guía de ${row.registro}`"
        title="Reasignar guía"
        @click="emit('reassign', row)"
      >
        <span aria-hidden="true">⇄</span>
      </button>
    </td>
  </tr>
</template>

<script setup lang="ts">
/**
 * ReconciliationRow — a single row of the reconciliation grid.
 *
 * Responsibilities:
 * - Renders all 10 locked columns
 * - Editable summed_qty cell (MISMATCH rows only) with dirty-tracking
 * - Delegates confidence badge and source pages to sub-components
 * - Emits: edit (commits debounced PATCH), reassign (opens dialog), pageClick
 */

import { ref, computed, nextTick } from 'vue'
import type { ReconciliationRowResponse } from '@/api/types'
import ConfidenceBadge from './ConfidenceBadge.vue'
import SourcePages from './SourcePages.vue'

const props = defineProps<{
  row: ReconciliationRowResponse
  runId: string
  /** Pending edit value from Pinia store (post-edit, pre-commit) */
  pendingValue?: string | null
}>()

const emit = defineEmits<{
  /** Emitted when user commits an edit. Parent debounces + PATCH. */
  edit: [rowId: string, guiaId: string, value: string]
  /** Emitted when reassign button is clicked. */
  reassign: [row: ReconciliationRowResponse]
  /** Emitted when a source page chip is clicked. */
  pageClick: [page: number]
  /** Emitted on row Enter/Space for row-level keyboard activation. */
  rowActivate: [row: ReconciliationRowResponse]
}>()

// ---------------------------------------------------------------------------
// Edit state
// ---------------------------------------------------------------------------

const isEditing = ref(false)
const editValue = ref('')
const summedInputRef = ref<HTMLInputElement | null>(null)

const isDirty = computed(() => props.pendingValue !== undefined && props.pendingValue !== null)
const effectiveSummed = computed(() => props.pendingValue ?? props.row.summed_qty)

function startEdit(): void {
  if (props.row.status === 'MATCH' || props.row.status === 'GUIA_MISSING') return
  editValue.value = effectiveSummed.value
  isEditing.value = true
  void nextTick(() => summedInputRef.value?.select())
}

function commitEdit(): void {
  if (!isEditing.value) return
  const trimmed = editValue.value.trim()
  // Only emit if value actually changed
  if (trimmed && trimmed !== props.row.summed_qty) {
    emit('edit', props.row.row_id, props.row.row_id, trimmed)
  }
  isEditing.value = false
}

function cancelEdit(): void {
  isEditing.value = false
  editValue.value = ''
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function formatDecimal(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  const n = Number(value)
  if (isNaN(n)) return value
  // Format to max 4 decimal places, stripping trailing zeros
  return n.toLocaleString('es-PE', { maximumFractionDigits: 4, minimumFractionDigits: 0 })
}

function formatDelta(delta: string): string {
  const n = Number(delta)
  if (isNaN(n)) return delta
  if (n === 0) return '0'
  const sign = n > 0 ? '+' : ''
  return `${sign}${n.toLocaleString('es-PE', { maximumFractionDigits: 4, minimumFractionDigits: 0 })}`
}

// ---------------------------------------------------------------------------
// Status presentation
// ---------------------------------------------------------------------------

const STATUS_LABELS: Record<string, string> = {
  MATCH: 'Coincide',
  MISMATCH: 'Diferencia',
  DECLARED_MISSING: 'Sin declarado',
  GUIA_MISSING: 'Sin guía',
  UNCLASSIFIED: 'Sin clasificar',
}

const STATUS_ICONS: Record<string, string> = {
  MATCH: '✓',
  MISMATCH: '✕',
  DECLARED_MISSING: '△',
  GUIA_MISSING: '◇',
  UNCLASSIFIED: '?',
}

const statusLabel = computed(() => STATUS_LABELS[props.row.status] ?? props.row.status)
const statusIcon = computed(() => STATUS_ICONS[props.row.status] ?? '?')

// ---------------------------------------------------------------------------
// Delta visual
// ---------------------------------------------------------------------------

const deltaSign = computed(() => {
  const n = Number(props.row.delta)
  if (isNaN(n) || n === 0) return 'zero'
  return n > 0 ? 'positive' : 'negative'
})

const deltaClass = computed(() => ({
  'recon-row__delta--positive': deltaSign.value === 'positive',
  'recon-row__delta--negative': deltaSign.value === 'negative',
  'recon-row__delta--zero': deltaSign.value === 'zero',
}))

// ---------------------------------------------------------------------------
// Row class
// ---------------------------------------------------------------------------

const rowClass = computed(() => ({
  [`recon-row--${props.row.status.toLowerCase().replace(/_/g, '-')}`]: true,
}))

const showReassign = computed(() =>
  props.row.status === 'MISMATCH' ||
  props.row.status === 'DECLARED_MISSING' ||
  props.row.status === 'GUIA_MISSING',
)
</script>

<style scoped>
.recon-row {
  border-left: 3px solid transparent;
  transition:
    background-color var(--transition-fast),
    border-color var(--transition-fast);
}

.recon-row:hover {
  background-color: var(--surface-hover);
}

.recon-row:focus-visible {
  outline: none;
  background-color: var(--surface-active);
  box-shadow: inset 0 0 0 2px var(--border-focus);
}

/* Status left-border glow */
.recon-row--match       { border-left-color: var(--status-match-glow); }
.recon-row--mismatch    { border-left-color: var(--status-mismatch-glow); }
.recon-row--declared-missing { border-left-color: var(--status-declared-missing-glow); }
.recon-row--guia-missing     { border-left-color: var(--status-guia-missing-glow); }
.recon-row--unclassified     { border-left-color: var(--status-unclassified-glow); }

/* Row background tinting */
.recon-row--mismatch {
  background-color: rgba(248, 81, 73, 0.04);
}
.recon-row--declared-missing {
  background-color: rgba(227, 179, 65, 0.04);
}
.recon-row--guia-missing {
  background-color: rgba(88, 166, 255, 0.04);
}

/* Cells */
.recon-row__cell {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  color: var(--text-primary);
  vertical-align: middle;
  border-bottom: 1px solid var(--border-subtle);
  white-space: nowrap;
}

.recon-row__cell--material {
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recon-row__cell--numeric {
  text-align: right;
}

/* Editable cell */
.recon-row__editable-cell {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  background: none;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  padding: 2px var(--space-2);
  cursor: pointer;
  color: inherit;
  font-size: inherit;
  transition:
    border-color var(--transition-fast),
    background-color var(--transition-fast);
  min-width: 60px;
  justify-content: flex-end;
}

.recon-row__editable-cell:not(:disabled):hover {
  border-color: var(--border-default);
  background-color: var(--surface-inset);
}

.recon-row__editable-cell:not(:disabled):focus-visible {
  outline: none;
  border-color: var(--border-focus);
  box-shadow: var(--shadow-focus);
}

.recon-row__editable-cell:disabled {
  cursor: default;
}

.recon-row__editable-cell--dirty {
  border-color: var(--action-primary);
  background-color: rgba(31, 111, 235, 0.08);
}

.recon-row__edit-hint {
  color: var(--text-tertiary);
  font-size: var(--text-2xs);
  opacity: 0;
  transition: opacity var(--transition-fast);
}

.recon-row__editable-cell:not(:disabled):hover .recon-row__edit-hint {
  opacity: 1;
}

.recon-row__dirty-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background-color: var(--action-primary);
  flex-shrink: 0;
}

/* Inline edit input */
.recon-row__edit-wrap {
  display: flex;
  justify-content: flex-end;
}

.recon-row__input {
  width: 80px;
  padding: 2px var(--space-2);
  background-color: var(--surface-inset);
  border: 1px solid var(--border-focus);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font-size: var(--text-sm);
  text-align: right;
  box-shadow: var(--shadow-focus);
}

.recon-row__input:focus {
  outline: none;
}

/* Delta coloring */
.recon-row__delta--positive { color: var(--status-mismatch-fg); }
.recon-row__delta--negative { color: var(--status-mismatch-fg); }
.recon-row__delta--zero     { color: var(--text-tertiary); }

/* Status badge */
.recon-row__status-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: 2px var(--space-2);
  border-radius: var(--radius-pill);
  font-size: var(--text-xs);
  font-weight: 500;
  white-space: nowrap;
}

.recon-row__status-badge--match {
  color: var(--status-match-fg);
  background-color: var(--status-match-bg);
}
.recon-row__status-badge--mismatch {
  color: var(--status-mismatch-fg);
  background-color: var(--status-mismatch-bg);
}
.recon-row__status-badge--declared-missing {
  color: var(--status-declared-missing-fg);
  background-color: var(--status-declared-missing-bg);
}
.recon-row__status-badge--guia-missing {
  color: var(--status-guia-missing-fg);
  background-color: var(--status-guia-missing-bg);
}
.recon-row__status-badge--unclassified {
  color: var(--status-unclassified-fg);
  background-color: var(--status-unclassified-bg);
}

.recon-row__status-icon {
  font-size: 0.65rem;
}

/* Actions column */
.recon-row__cell--actions {
  width: 40px;
  padding: var(--space-1) var(--space-2);
}

.recon-row__action-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-default);
  background-color: transparent;
  color: var(--text-secondary);
  font-size: var(--text-sm);
  cursor: pointer;
  transition:
    border-color var(--transition-fast),
    background-color var(--transition-fast),
    color var(--transition-fast);
}

.recon-row__action-btn:hover {
  border-color: var(--action-primary);
  color: var(--action-primary-hover);
  background-color: rgba(31, 111, 235, 0.08);
}

.recon-row__action-btn:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}
</style>
