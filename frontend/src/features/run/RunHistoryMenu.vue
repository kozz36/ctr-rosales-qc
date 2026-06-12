<template>
  <div ref="rootEl" class="run-history-menu">
    <button
      class="run-history-menu__trigger"
      type="button"
      aria-label="Menú de ejecuciones"
      aria-haspopup="true"
      :aria-expanded="open"
      @click="open = !open"
    >
      <span class="run-history-menu__bars" aria-hidden="true">
        <span /><span /><span />
      </span>
    </button>

    <div
      v-if="open"
      class="run-history-menu__dropdown"
      role="menu"
      aria-label="Acciones de ejecución"
      @keydown.esc="closeMenu"
    >
      <button
        class="run-history-menu__item"
        role="menuitem"
        type="button"
        @click="onNuevoBatch"
      >
        Nuevo batch
      </button>
      <button
        class="run-history-menu__item"
        role="menuitem"
        type="button"
        :disabled="!runStore.runId || undefined"
        :title="runStore.runId ? 'Ir al batch actual' : 'No hay un batch activo'"
        @click="onBatchActual"
      >
        Batch actual
      </button>
      <button
        class="run-history-menu__item"
        role="menuitem"
        type="button"
        @click="onHistorial"
      >
        Historial
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * RunHistoryMenu — always-visible hamburger in the App.vue header (SDD#3 D6,
 * RH-010). Three actions:
 *   [Nuevo batch]  → runStore.reset() + navigate to upload (RH-010-S01).
 *                    reset() also clears the persisted run_id key.
 *   [Batch actual] → navigate to /runs/{runStore.runId}; disabled when no
 *                    run is active (RH-010-S04).
 *   [Historial]    → navigate to the /historial run list.
 *
 * Pure navigation/state component — zero reconciliation logic client-side.
 */

import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { useRunStore } from '@/stores/run'

const router = useRouter()
const runStore = useRunStore()

const open = ref(false)
const rootEl = ref<HTMLElement | null>(null)

function closeMenu(): void {
  open.value = false
}

function onNuevoBatch(): void {
  runStore.reset()
  closeMenu()
  void router.push('/')
}

function onBatchActual(): void {
  if (!runStore.runId) return
  closeMenu()
  void router.push(`/runs/${runStore.runId}`)
}

function onHistorial(): void {
  closeMenu()
  void router.push('/historial')
}

/** Close on outside click (standard disclosure pattern). */
function onDocumentClick(event: MouseEvent): void {
  if (!open.value) return
  const root = rootEl.value
  if (root && !root.contains(event.target as Node)) closeMenu()
}

onMounted(() => document.addEventListener('click', onDocumentClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocumentClick))
</script>

<style scoped>
.run-history-menu {
  position: relative;
  display: inline-flex;
}

.run-history-menu__trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  padding: 0;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  background-color: var(--surface-raised);
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.run-history-menu__trigger:hover {
  background-color: var(--surface-hover);
}

.run-history-menu__trigger:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}

.run-history-menu__bars {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.run-history-menu__bars span {
  display: block;
  width: 14px;
  height: 2px;
  border-radius: 1px;
  background-color: var(--text-primary);
}

.run-history-menu__dropdown {
  position: absolute;
  top: calc(100% + var(--space-2));
  right: 0;
  z-index: var(--z-modal, 1000);
  min-width: 180px;
  display: flex;
  flex-direction: column;
  padding: var(--space-1);
  background-color: var(--surface-overlay, var(--surface-raised));
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg, 0 10px 30px rgba(0, 0, 0, 0.3));
}

.run-history-menu__item {
  display: flex;
  align-items: center;
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  color: var(--text-primary);
  text-align: left;
  background: none;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.run-history-menu__item:hover:not(:disabled) {
  background-color: var(--surface-hover);
}

.run-history-menu__item:focus-visible {
  outline: none;
  box-shadow: var(--shadow-focus);
}

.run-history-menu__item:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
</style>
