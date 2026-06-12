<template>
  <div class="app-shell">
    <header class="app-header">
      <div class="app-header__inner">
        <span class="app-header__wordmark">
          <span class="app-header__wordmark-accent">CTR</span> Rosales
          <span class="app-header__wordmark-sub">QC · Reconciliación</span>
        </span>

        <div class="app-header__right">
          <nav v-if="runStore.runId" class="app-header__nav" aria-label="Navegación de sesión">
            <RouterLink
              :to="{ name: 'upload' }"
              class="app-header__nav-link"
              active-class="app-header__nav-link--active"
            >
              Nueva subida
            </RouterLink>
            <RouterLink
              v-if="runStore.isReady"
              :to="{ name: 'review', params: { id: runStore.runId } }"
              class="app-header__nav-link"
              active-class="app-header__nav-link--active"
            >
              Revisión
            </RouterLink>
          </nav>

          <!-- Run-history hamburger (SDD#3, RH-010) — always visible -->
          <RunHistoryMenu />
        </div>
      </div>
    </header>

    <main class="app-main">
      <RouterView v-slot="{ Component, route }">
        <Transition name="page" mode="out-in">
          <component :is="Component" :key="route.fullPath" />
        </Transition>
      </RouterView>
    </main>
  </div>
</template>

<script setup lang="ts">
import { useRunStore } from '@/stores/run'
import RunHistoryMenu from '@/features/run/RunHistoryMenu.vue'

const runStore = useRunStore()
</script>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  min-height: 100dvh;
  background-color: var(--surface-base);
}

.app-header {
  position: sticky;
  top: 0;
  z-index: 100;
  height: var(--header-height);
  background-color: var(--surface-raised);
  border-bottom: 1px solid var(--border-subtle);
  box-shadow: var(--shadow-sm);
}

.app-header__inner {
  display: flex;
  align-items: center;
  gap: var(--space-8);
  height: 100%;
  max-width: var(--content-max-w);
  margin: 0 auto;
  padding: 0 var(--space-6);
}

.app-header__wordmark {
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--text-primary);
  letter-spacing: 0.02em;
  white-space: nowrap;
}

.app-header__wordmark-accent {
  color: var(--action-primary-hover);
  font-weight: 600;
}

.app-header__wordmark-sub {
  color: var(--text-secondary);
  font-size: var(--text-xs);
  font-weight: 400;
  margin-left: var(--space-2);
}

.app-header__right {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  margin-left: auto;
}

.app-header__nav {
  display: flex;
  gap: var(--space-4);
}

.app-header__nav-link {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  text-decoration: none;
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-md);
  transition: color var(--transition-fast), background-color var(--transition-fast);
}

.app-header__nav-link:hover {
  color: var(--text-primary);
  background-color: var(--surface-hover);
}

.app-header__nav-link--active {
  color: var(--text-primary);
  background-color: var(--surface-active);
}

.app-main {
  flex: 1;
  max-width: var(--content-max-w);
  width: 100%;
  margin: 0 auto;
  padding: var(--space-8) var(--space-6);
}

/* Page transition */
.page-enter-active,
.page-leave-active {
  transition: opacity var(--transition-normal);
}

.page-enter-from,
.page-leave-to {
  opacity: 0;
}
</style>
