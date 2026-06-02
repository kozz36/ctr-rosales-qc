<template>
  <!-- Fragment: main row + optional drill-down row (Vue 3 template fragment) -->
  <tr
    class="recon-row"
    :class="rowClass"
    :data-status="row.status"
    :aria-label="`${row.registro} — ${row.material_canonical} — ${statusLabel}`"
    tabindex="0"
    @keydown.enter="emit('rowActivate', row)"
    @keydown.space.prevent="emit('rowActivate', row)"
  >
    <!-- Col 0: Expand/collapse chevron -->
    <td class="recon-row__cell recon-row__cell--expand">
      <button
        v-if="row.guias && row.guias.length > 0"
        class="recon-row__expand-btn"
        :aria-label="isExpanded ? 'Colapsar detalle de guías' : 'Expandir detalle de guías'"
        :aria-expanded="isExpanded"
        @click.stop="toggleExpand"
      >
        <span class="recon-row__chevron" :class="{ 'recon-row__chevron--open': isExpanded }" aria-hidden="true">
          ›
        </span>
      </button>
    </td>

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

    <!-- Col 6: Sumado (guías) — READ-ONLY (REV-C03: summed_qty is derived, not directly editable).
         Edit happens at guía-line level via GuiaDrillDown. -->
    <td
      class="recon-row__cell recon-row__cell--numeric"
      data-numeric
      :aria-label="`Sumado guías: ${formatDecimal(row.summed_qty)}`"
    >
      <span class="mono">{{ formatDecimal(row.summed_qty) }}</span>
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

    <!-- Col 9: Confianza mín + review flags (REV-004: icon+label, not color-only) -->
    <td class="recon-row__cell recon-row__cell--confidence">
      <ConfidenceBadge :value="row.min_confidence" compact />
      <span
        v-if="row.requires_review"
        class="recon-row__flag recon-row__flag--review"
        role="img"
        aria-label="Requiere revisión manual"
        title="Requiere revisión: baja confianza OCR o fecha sin leer"
      >
        <span class="recon-row__flag-icon" aria-hidden="true">⚠</span>
        <span class="recon-row__flag-label">Revisar</span>
      </span>
      <!-- Rev-3 D5 / REV-C05: advisory yellow badge when any guía used year inference -->
      <YearInferredBadge
        v-if="row.any_year_inferred"
        compact
      />
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
      <!-- Row-level reassign button kept for backward compat with parent; drill-down
           now also emits per-guía reassign events. -->
    </td>
  </tr>

  <!-- Drill-down row (conditionally rendered when expanded) -->
  <GuiaDrillDown
    v-if="isExpanded && row.guias && row.guias.length > 0"
    :guias="row.guias"
    :run-id="runId"
    @reassign="onDrillDownReassign"
    @row-updated="emit('rowUpdated')"
  />
</template>

<script setup lang="ts">
/**
 * ReconciliationRow — a single row of the reconciliation grid.
 *
 * Rev-2 changes:
 * - Expand/collapse chevron reveals GuiaDrillDown (REV-C01, no extra API call).
 * - summed_qty is now READ-ONLY — edit happens at guía-line level (REV-C03/CRITICAL-2 fix).
 * - Propagates GuiaDrillDown reassign(guia_id) as openReassign({ guia_id }) (REV-C02).
 * - Emits rowUpdated when drill-down quantity edit succeeds.
 */

import { ref, computed } from 'vue'
import type { ReconciliationRowResponse } from '@/api/types'
import ConfidenceBadge from './ConfidenceBadge.vue'
import SourcePages from './SourcePages.vue'
import GuiaDrillDown from './GuiaDrillDown.vue'
import YearInferredBadge from './YearInferredBadge.vue'

const props = defineProps<{
  row: ReconciliationRowResponse
  runId: string
  /** Kept for API compatibility — no longer used since summed_qty is read-only. */
  pendingValue?: string | null
}>()

const emit = defineEmits<{
  /** Emitted when reassign is requested for a specific guía_id. */
  openReassign: [payload: { guia_id: string }]
  /** Emitted when a source page chip is clicked. */
  pageClick: [page: number]
  /** Emitted on row Enter/Space for row-level keyboard activation. */
  rowActivate: [row: ReconciliationRowResponse]
  /** Emitted after a successful guía-line cantidad edit (drill-down). */
  rowUpdated: []
  /**
   * @deprecated Legacy emit kept for ReviewGrid backward compat.
   * Use openReassign instead.
   */
  reassign: [row: ReconciliationRowResponse]
}>()

// ---------------------------------------------------------------------------
// Expand / collapse
// ---------------------------------------------------------------------------

const isExpanded = ref(false)

function toggleExpand(): void {
  isExpanded.value = !isExpanded.value
}

// ---------------------------------------------------------------------------
// Drill-down event handlers
// ---------------------------------------------------------------------------

function onDrillDownReassign(guiaId: string): void {
  emit('openReassign', { guia_id: guiaId })
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function formatDecimal(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  const n = Number(value)
  if (isNaN(n)) return value
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
  MATCH: 'Conforme',
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

.recon-row__cell--expand {
  width: 32px;
  padding: var(--space-1) var(--space-2);
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

/* Expand/collapse button */
.recon-row__expand-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: var(--radius-sm);
  border: none;
  background: none;
  color: var(--text-tertiary);
  cursor: pointer;
  font-size: var(--text-sm);
  transition: color var(--transition-fast);
}

.recon-row__expand-btn:hover {
  color: var(--text-primary);
}

.recon-row__expand-btn:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}

.recon-row__chevron {
  display: inline-block;
  transition: transform var(--transition-fast);
  line-height: 1;
  font-style: normal;
}

.recon-row__chevron--open {
  transform: rotate(90deg);
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

/* Flagging badges (REV-004: icon + label, not color-only) */
.recon-row__flag {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin-left: var(--space-1);
  padding: 1px var(--space-1);
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  font-weight: 500;
  white-space: nowrap;
}

.recon-row__flag--review {
  color: var(--status-mismatch-fg);
  background-color: var(--status-mismatch-bg);
}

.recon-row__flag-icon {
  font-size: 0.6rem;
}

.recon-row__flag-label {
  font-size: var(--text-xs);
}
</style>
