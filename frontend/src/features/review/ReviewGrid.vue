<template>
  <section class="review-grid" aria-labelledby="grid-heading">
    <!-- Toolbar: filter + summary -->
    <div class="review-grid__toolbar" role="toolbar" aria-label="Filtros de la tabla de reconciliación">
      <div class="review-grid__summary" aria-live="polite" aria-atomic="true">
        <span class="review-grid__summary-item">
          <span class="review-grid__summary-dot review-grid__summary-dot--match" aria-hidden="true" />
          <span class="mono">{{ summary.match }}</span>
          <span class="review-grid__summary-label">Coinciden</span>
        </span>
        <span class="review-grid__summary-item review-grid__summary-item--alert" v-if="summary.mismatch > 0">
          <span class="review-grid__summary-dot review-grid__summary-dot--mismatch" aria-hidden="true" />
          <span class="mono">{{ summary.mismatch }}</span>
          <span class="review-grid__summary-label">Diferencias</span>
        </span>
        <span class="review-grid__summary-item" v-if="summary.declared_missing > 0">
          <span class="review-grid__summary-dot review-grid__summary-dot--declared-missing" aria-hidden="true" />
          <span class="mono">{{ summary.declared_missing }}</span>
          <span class="review-grid__summary-label">Sin declarado</span>
        </span>
        <span class="review-grid__summary-item" v-if="summary.guia_missing > 0">
          <span class="review-grid__summary-dot review-grid__summary-dot--guia-missing" aria-hidden="true" />
          <span class="mono">{{ summary.guia_missing }}</span>
          <span class="review-grid__summary-label">Sin guía</span>
        </span>
      </div>

      <div class="review-grid__filters" role="group" aria-label="Filtrar por estado">
        <button
          v-for="f in FILTER_OPTIONS"
          :key="f.value ?? 'all'"
          class="review-grid__filter-btn"
          :class="{ 'review-grid__filter-btn--active': activeFilter === f.value }"
          :aria-pressed="activeFilter === f.value"
          @click="setFilter(f.value)"
        >
          <span aria-hidden="true">{{ f.icon }}</span>
          {{ f.label }}
        </button>
      </div>
    </div>

    <!-- Loading state -->
    <div v-if="isLoading" class="review-grid__state" role="status" aria-label="Cargando datos">
      <span class="review-grid__spinner" aria-hidden="true" />
      <span>Cargando tabla de reconciliación...</span>
    </div>

    <!-- Error state -->
    <div v-else-if="error" class="review-grid__state review-grid__state--error" role="alert">
      <span aria-hidden="true">✕</span>
      <span>Error al cargar los datos: {{ error }}</span>
      <button class="review-grid__retry-btn" @click="emit('retry')">Reintentar</button>
    </div>

    <!-- Empty state -->
    <div v-else-if="visibleRows.length === 0" class="review-grid__state" role="status">
      <span aria-hidden="true">◎</span>
      <span>No hay filas{{ activeFilter ? ` con estado "${activeFilter}"` : '' }}.</span>
    </div>

    <!-- Data table -->
    <div v-else class="review-grid__table-wrap" role="region" aria-labelledby="grid-heading" tabindex="0">
      <table
        class="review-grid__table"
        aria-label="Tabla de reconciliación"
        aria-rowcount="visibleRows.length"
      >
        <caption id="grid-heading" class="sr-only">
          Tabla de reconciliación de materiales — {{ visibleRows.length }} filas
        </caption>
        <thead class="review-grid__thead">
          <tr>
            <th
              v-for="col in COLUMNS"
              :key="col.key"
              class="review-grid__th"
              :class="`review-grid__th--${col.key}`"
              scope="col"
              :aria-sort="sortKey === col.key ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'"
            >
              <button
                v-if="col.sortable"
                class="review-grid__sort-btn"
                @click="toggleSort(col.key)"
                :aria-label="`Ordenar por ${col.label}`"
              >
                {{ col.label }}
                <span class="review-grid__sort-icon" aria-hidden="true">
                  {{ sortKey === col.key ? (sortDir === 'asc' ? '↑' : '↓') : '⇅' }}
                </span>
              </button>
              <span v-else>{{ col.label }}</span>
            </th>
            <th class="review-grid__th review-grid__th--actions" scope="col">
              <span class="sr-only">Acciones</span>
            </th>
          </tr>
        </thead>
        <tbody class="review-grid__tbody">
          <template v-for="group in groupedRows" :key="group.key">
            <!-- Group header: Registro + Fecha -->
            <tr class="review-grid__group-header" :aria-label="`Grupo: Registro ${group.registro}, Fecha ${group.fecha ?? 'sin fecha'}`">
              <td colspan="11" class="review-grid__group-cell">
                <button
                  class="review-grid__group-toggle"
                  :aria-expanded="!collapsedGroups.has(group.key)"
                  :aria-label="`${collapsedGroups.has(group.key) ? 'Expandir' : 'Colapsar'} grupo Registro ${group.registro}`"
                  @click="toggleGroup(group.key)"
                >
                  <span class="review-grid__group-chevron" :class="{ 'review-grid__group-chevron--collapsed': collapsedGroups.has(group.key) }" aria-hidden="true">▼</span>
                  <span class="mono review-grid__group-registro">{{ group.registro }}</span>
                  <span class="review-grid__group-sep" aria-hidden="true">·</span>
                  <span class="mono review-grid__group-fecha">{{ group.fecha ?? '—' }}</span>
                  <span class="review-grid__group-count" aria-label="filas en grupo">{{ group.rows.length }}</span>
                </button>
              </td>
            </tr>
            <!-- Group rows -->
            <ReconciliationRow
              v-for="row in group.rows"
              v-show="!collapsedGroups.has(group.key)"
              :key="row.row_id"
              :row="row"
              :run-id="runId"
              :pending-value="pendingEdits.get(row.row_id)?.value ?? undefined"
              @edit="onEdit"
              @reassign="emit('reassign', $event)"
              @page-click="emit('pageClick', $event)"
              @row-activate="emit('rowActivate', $event)"
            />
          </template>
        </tbody>
      </table>
    </div>
  </section>
</template>

<script setup lang="ts">
/**
 * ReviewGrid — data-grid component for the reconciliation review table.
 *
 * Columns (10, locked per spec EXP-002):
 *   Registro | Fecha | Material | Unidad | Declarado | Sumado(guías) | Delta | Estado | Confianza mín | Páginas origen
 *
 * Architecture:
 * - Receives rows from TanStack Query (via parent ReviewPage)
 * - Reads pendingEdits from Pinia reconciliation store
 * - Emits edit events → parent debounces + calls PATCH mutation
 * - Grouped by (registro, fecha), collapsible
 * - Status filter via Pinia statusFilter
 *
 * A11y: keyboard-navigable table, aria-sort, aria-expanded groups,
 *       status conveyed by icon + label (not color alone).
 */

import { ref, computed } from 'vue'
import type { ReconciliationRowResponse, RowStatus } from '@/api/types'
import type { PendingEdit } from '@/stores/reconciliation'
import ReconciliationRow from './ReconciliationRow.vue'

// ---------------------------------------------------------------------------
// Props / emits
// ---------------------------------------------------------------------------

const props = defineProps<{
  rows: ReconciliationRowResponse[]
  runId: string
  isLoading?: boolean
  error?: string | null
  /** Pinia pendingEdits map, passed in for reactivity */
  pendingEdits: Map<string, PendingEdit>
  activeFilter: RowStatus | null
}>()

const emit = defineEmits<{
  /** Debounced PATCH — parent owns the mutation */
  edit: [rowId: string, guiaId: string, value: string]
  /** Open reassign dialog */
  reassign: [row: ReconciliationRowResponse]
  /** Source page chip clicked */
  pageClick: [page: number]
  /** Row Enter/Space activated */
  rowActivate: [row: ReconciliationRowResponse]
  /** Filter changed */
  filterChange: [filter: RowStatus | null]
  /** Retry fetch */
  retry: []
}>()

// ---------------------------------------------------------------------------
// Filter options
// ---------------------------------------------------------------------------

interface FilterOption {
  value: RowStatus | null
  label: string
  icon: string
}

const FILTER_OPTIONS: FilterOption[] = [
  { value: null, label: 'Todos', icon: '≡' },
  { value: 'MISMATCH', label: 'Diferencias', icon: '✕' },
  { value: 'DECLARED_MISSING', label: 'Sin declarado', icon: '△' },
  { value: 'GUIA_MISSING', label: 'Sin guía', icon: '◇' },
  { value: 'MATCH', label: 'Coinciden', icon: '✓' },
]

// ---------------------------------------------------------------------------
// Columns definition
// ---------------------------------------------------------------------------

interface ColumnDef {
  key: string
  label: string
  sortable: boolean
}

const COLUMNS: ColumnDef[] = [
  { key: 'registro', label: 'Registro', sortable: true },
  { key: 'fecha', label: 'Fecha', sortable: true },
  { key: 'material', label: 'Material', sortable: true },
  { key: 'unidad', label: 'Unidad', sortable: false },
  { key: 'declarado', label: 'Declarado', sortable: false },
  { key: 'sumado', label: 'Sumado (guías)', sortable: false },
  { key: 'delta', label: 'Delta', sortable: true },
  { key: 'estado', label: 'Estado', sortable: true },
  { key: 'confianza', label: 'Confianza mín', sortable: true },
  { key: 'paginas', label: 'Páginas origen', sortable: false },
]

// ---------------------------------------------------------------------------
// Sorting
// ---------------------------------------------------------------------------

const sortKey = ref<string>('registro')
const sortDir = ref<'asc' | 'desc'>('asc')

function toggleSort(key: string): void {
  if (sortKey.value === key) {
    sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortKey.value = key
    sortDir.value = 'asc'
  }
}

// ---------------------------------------------------------------------------
// Filter
// ---------------------------------------------------------------------------

function setFilter(value: RowStatus | null): void {
  emit('filterChange', value)
}

// ---------------------------------------------------------------------------
// Visible rows (after filter + sort)
// ---------------------------------------------------------------------------

const visibleRows = computed(() => {
  let filtered = props.activeFilter
    ? props.rows.filter((r) => r.status === props.activeFilter)
    : [...props.rows]

  // Sort
  filtered.sort((a, b) => {
    let va: string | number | null = null
    let vb: string | number | null = null

    switch (sortKey.value) {
      case 'registro':
        va = a.registro; vb = b.registro; break
      case 'fecha':
        va = a.fecha ?? ''; vb = b.fecha ?? ''; break
      case 'material':
        va = a.material_canonical; vb = b.material_canonical; break
      case 'delta':
        va = Number(a.delta); vb = Number(b.delta); break
      case 'estado':
        va = a.status; vb = b.status; break
      case 'confianza':
        va = a.min_confidence ?? -1; vb = b.min_confidence ?? -1; break
      default:
        return 0
    }

    if (va === null || vb === null) return 0
    const cmp = va < vb ? -1 : va > vb ? 1 : 0
    return sortDir.value === 'asc' ? cmp : -cmp
  })

  return filtered
})

// ---------------------------------------------------------------------------
// Grouping by (registro, fecha)
// ---------------------------------------------------------------------------

interface RowGroup {
  key: string
  registro: string
  fecha: string | null
  rows: ReconciliationRowResponse[]
}

const groupedRows = computed((): RowGroup[] => {
  const groups = new Map<string, RowGroup>()
  for (const row of visibleRows.value) {
    const key = `${row.registro}|${row.fecha ?? ''}`
    if (!groups.has(key)) {
      groups.set(key, { key, registro: row.registro, fecha: row.fecha, rows: [] })
    }
    groups.get(key)!.rows.push(row)
  }
  return Array.from(groups.values())
})

// ---------------------------------------------------------------------------
// Group collapse
// ---------------------------------------------------------------------------

const collapsedGroups = ref<Set<string>>(new Set())

function toggleGroup(key: string): void {
  const next = new Set(collapsedGroups.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  collapsedGroups.value = next
}

// ---------------------------------------------------------------------------
// Summary counts (always from ALL rows, not filtered)
// ---------------------------------------------------------------------------

const summary = computed(() => {
  const counts = { match: 0, mismatch: 0, declared_missing: 0, guia_missing: 0 }
  for (const row of props.rows) {
    if (row.status === 'MATCH') counts.match++
    else if (row.status === 'MISMATCH') counts.mismatch++
    else if (row.status === 'DECLARED_MISSING') counts.declared_missing++
    else if (row.status === 'GUIA_MISSING') counts.guia_missing++
  }
  return counts
})

// ---------------------------------------------------------------------------
// Edit passthrough
// ---------------------------------------------------------------------------

function onEdit(rowId: string, guiaId: string, value: string): void {
  emit('edit', rowId, guiaId, value)
}
</script>

<style scoped>
/* Screen-reader only utility */
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border-width: 0;
}

/* Container */
.review-grid {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  background-color: var(--surface-raised);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-default);
  overflow: hidden;
}

/* Toolbar */
.review-grid__toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-4) 0;
}

.review-grid__summary {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-4);
  align-items: center;
}

.review-grid__summary-item {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.review-grid__summary-item--alert {
  color: var(--status-mismatch-fg);
}

.review-grid__summary-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.review-grid__summary-dot--match           { background-color: var(--status-match-glow); }
.review-grid__summary-dot--mismatch        { background-color: var(--status-mismatch-glow); }
.review-grid__summary-dot--declared-missing { background-color: var(--status-declared-missing-glow); }
.review-grid__summary-dot--guia-missing    { background-color: var(--status-guia-missing-glow); }

.review-grid__summary-label {
  color: var(--text-secondary);
}

/* Filter buttons */
.review-grid__filters {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
}

.review-grid__filter-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-pill);
  border: 1px solid var(--border-default);
  background-color: transparent;
  color: var(--text-secondary);
  font-size: var(--text-xs);
  font-weight: 500;
  cursor: pointer;
  transition:
    border-color var(--transition-fast),
    background-color var(--transition-fast),
    color var(--transition-fast);
}

.review-grid__filter-btn:hover {
  border-color: var(--border-strong);
  color: var(--text-primary);
}

.review-grid__filter-btn:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}

.review-grid__filter-btn--active {
  border-color: var(--action-primary);
  background-color: rgba(31, 111, 235, 0.12);
  color: var(--action-primary-hover);
}

/* State panels */
.review-grid__state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  min-height: 200px;
  padding: var(--space-8);
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.review-grid__state--error {
  color: var(--status-mismatch-fg);
}

.review-grid__spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--border-default);
  border-top-color: var(--action-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.review-grid__retry-btn {
  padding: var(--space-1) var(--space-4);
  border-radius: var(--radius-md);
  border: 1px solid var(--action-danger);
  background-color: transparent;
  color: var(--action-danger);
  font-size: var(--text-sm);
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.review-grid__retry-btn:hover {
  background-color: rgba(218, 54, 51, 0.1);
}

/* Table */
.review-grid__table-wrap {
  overflow-x: auto;
  /* Custom scrollbar */
  scrollbar-width: thin;
  scrollbar-color: var(--border-default) transparent;
}

.review-grid__table-wrap::-webkit-scrollbar {
  height: 6px;
}
.review-grid__table-wrap::-webkit-scrollbar-track {
  background: transparent;
}
.review-grid__table-wrap::-webkit-scrollbar-thumb {
  background-color: var(--border-default);
  border-radius: var(--radius-pill);
}

.review-grid__table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  min-width: 960px;
}

/* Column widths */
.review-grid__th--registro   { width: 90px; }
.review-grid__th--fecha      { width: 100px; }
.review-grid__th--material   { width: 220px; }
.review-grid__th--unidad     { width: 70px; }
.review-grid__th--declarado  { width: 100px; }
.review-grid__th--sumado     { width: 110px; }
.review-grid__th--delta      { width: 90px; }
.review-grid__th--estado     { width: 120px; }
.review-grid__th--confianza  { width: 110px; }
.review-grid__th--paginas    { width: 130px; }
.review-grid__th--actions    { width: 44px; }

.review-grid__thead {
  position: sticky;
  top: 0;
  z-index: 2;
}

.review-grid__th {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-2xs);
  font-weight: 600;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--text-secondary);
  background-color: var(--surface-raised);
  border-bottom: 2px solid var(--border-default);
  text-align: left;
  white-space: nowrap;
}

.review-grid__sort-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  background: none;
  border: none;
  color: inherit;
  font-size: inherit;
  font-weight: inherit;
  letter-spacing: inherit;
  text-transform: inherit;
  cursor: pointer;
  padding: 0;
  white-space: nowrap;
}

.review-grid__sort-btn:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
  border-radius: var(--radius-sm);
}

.review-grid__sort-icon {
  font-size: var(--text-2xs);
  opacity: 0.6;
}

/* Group header */
.review-grid__group-header {
  background-color: var(--surface-inset);
}

.review-grid__group-cell {
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--border-default);
}

.review-grid__group-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  background: none;
  border: none;
  color: var(--text-secondary);
  font-size: var(--text-sm);
  cursor: pointer;
  padding: 0;
}

.review-grid__group-toggle:hover {
  color: var(--text-primary);
}

.review-grid__group-toggle:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
  border-radius: var(--radius-sm);
}

.review-grid__group-chevron {
  font-size: var(--text-2xs);
  color: var(--text-tertiary);
  transition: transform var(--transition-fast);
}

.review-grid__group-chevron--collapsed {
  transform: rotate(-90deg);
}

.review-grid__group-registro {
  font-weight: 600;
  color: var(--text-primary);
  font-size: var(--text-sm);
}

.review-grid__group-sep {
  color: var(--text-tertiary);
}

.review-grid__group-fecha {
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.review-grid__group-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 20px;
  padding: 0 var(--space-1);
  border-radius: var(--radius-pill);
  background-color: var(--surface-overlay);
  color: var(--text-tertiary);
  font-size: var(--text-2xs);
  font-variant-numeric: tabular-nums;
  margin-left: var(--space-1);
}
</style>
