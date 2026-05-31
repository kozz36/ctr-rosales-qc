<template>
  <section class="upload-panel" aria-labelledby="upload-heading">
    <div class="upload-panel__header">
      <h1 id="upload-heading" class="upload-panel__title">Reconciliación de materiales</h1>
      <p class="upload-panel__subtitle">
        Suba el PDF de declaración (protocolo + guías) para iniciar el análisis.
      </p>
    </div>

    <!-- Drop zone -->
    <div
      class="upload-zone"
      :class="{
        'upload-zone--dragging': isDragging,
        'upload-zone--error': !!validationError,
        'upload-zone--loading': runStore.uploading,
      }"
      role="button"
      tabindex="0"
      :aria-disabled="runStore.uploading"
      aria-label="Zona de carga. Arrastre un archivo PDF aquí o presione Enter para abrir el selector."
      @dragover.prevent="onDragOver"
      @dragleave.prevent="onDragLeave"
      @drop.prevent="onDrop"
      @click="openFilePicker"
      @keydown.enter.prevent="openFilePicker"
      @keydown.space.prevent="openFilePicker"
    >
      <input
        ref="fileInput"
        type="file"
        accept="application/pdf,.pdf"
        class="upload-zone__input"
        aria-hidden="true"
        tabindex="-1"
        @change="onFileChange"
      />

      <div v-if="!runStore.uploading" class="upload-zone__idle">
        <!-- Upload icon -->
        <svg
          class="upload-zone__icon"
          aria-hidden="true"
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.5"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
          <polyline points="17 8 12 3 7 8" />
          <line x1="12" y1="3" x2="12" y2="15" />
        </svg>

        <p class="upload-zone__label">
          <span v-if="isDragging">Suelte el archivo aquí</span>
          <span v-else>
            Arrastre un PDF aquí o
            <span class="upload-zone__label-action">seleccione un archivo</span>
          </span>
        </p>
        <p class="upload-zone__hint">Solo PDF · Máx. 100 MB</p>
      </div>

      <!-- Uploading state -->
      <div v-else class="upload-zone__uploading" aria-live="polite" aria-busy="true">
        <div class="upload-zone__spinner" role="img" aria-label="Cargando…" />
        <p class="upload-zone__upload-label">Subiendo PDF…</p>
      </div>
    </div>

    <!-- Validation error -->
    <p v-if="validationError" class="upload-panel__error" role="alert" aria-live="assertive">
      <svg
        aria-hidden="true"
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <circle cx="12" cy="12" r="10" />
        <line x1="12" y1="8" x2="12" y2="12" />
        <line x1="12" y1="16" x2="12.01" y2="16" />
      </svg>
      {{ validationError }}
    </p>

    <!-- Upload network error (from run store) -->
    <p
      v-else-if="runStore.error && !runStore.uploading"
      class="upload-panel__error"
      role="alert"
      aria-live="assertive"
    >
      <svg
        aria-hidden="true"
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <circle cx="12" cy="12" r="10" />
        <line x1="12" y1="8" x2="12" y2="12" />
        <line x1="12" y1="16" x2="12.01" y2="16" />
      </svg>
      {{ runStore.error }}
    </p>

    <!-- Run created — show run_id and navigate indicator -->
    <div v-if="runStore.runId && !runStore.uploading" class="upload-panel__run-created">
      <p class="upload-panel__run-id">
        Run creado:
        <code class="upload-panel__run-id-value">{{ runStore.runId }}</code>
      </p>
      <RunProgress :run-id="runStore.runId" @completed="onRunCompleted" />
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useRunStore } from '@/stores/run'
import RunProgress from './RunProgress.vue'

const router = useRouter()
const runStore = useRunStore()

const fileInput = ref<HTMLInputElement | null>(null)
const isDragging = ref(false)
const validationError = ref<string | null>(null)

// ---------------------------------------------------------------------------
// File picker interaction
// ---------------------------------------------------------------------------

function openFilePicker(): void {
  if (runStore.uploading) return
  fileInput.value?.click()
}

function onFileChange(event: Event): void {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0] ?? null
  if (file) void processFile(file)
  // Reset input so the same file can be re-selected after an error
  input.value = ''
}

// ---------------------------------------------------------------------------
// Drag & drop
// ---------------------------------------------------------------------------

function onDragOver(): void {
  if (!runStore.uploading) isDragging.value = true
}

function onDragLeave(): void {
  isDragging.value = false
}

function onDrop(event: DragEvent): void {
  isDragging.value = false
  if (runStore.uploading) return
  const file = event.dataTransfer?.files?.[0] ?? null
  if (file) void processFile(file)
}

// ---------------------------------------------------------------------------
// Validation + upload
// ---------------------------------------------------------------------------

const MAX_BYTES = 100 * 1024 * 1024 // 100 MB

function validateFile(file: File): string | null {
  const isPdf =
    file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')
  if (!isPdf) return 'El archivo debe ser un PDF.'
  if (file.size > MAX_BYTES) return 'El archivo excede el límite de 100 MB.'
  return null
}

async function processFile(file: File): Promise<void> {
  validationError.value = null
  runStore.reset()

  const err = validateFile(file)
  if (err) {
    validationError.value = err
    return
  }

  try {
    await runStore.upload(file)
    // runStore.runId is now set; RunProgress component takes over polling
  } catch {
    // Error is stored in runStore.error; template renders it
  }
}

// ---------------------------------------------------------------------------
// Navigation after pipeline completes
// ---------------------------------------------------------------------------

function onRunCompleted(): void {
  if (runStore.runId) {
    void router.push({ name: 'review', params: { id: runStore.runId } })
  }
}
</script>

<style scoped>
.upload-panel {
  max-width: 640px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

.upload-panel__header {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.upload-panel__title {
  font-size: var(--text-2xl);
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: -0.02em;
}

.upload-panel__subtitle {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: 1.6;
}

/* Drop zone */
.upload-zone {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  min-height: 240px;
  padding: var(--space-8);
  border: 2px dashed var(--border-default);
  border-radius: var(--radius-lg);
  background-color: var(--surface-raised);
  cursor: pointer;
  transition:
    border-color var(--transition-normal),
    background-color var(--transition-normal);
  outline: none;
}

.upload-zone:hover:not(.upload-zone--loading),
.upload-zone:focus-visible {
  border-color: var(--border-strong);
  background-color: var(--surface-hover);
}

.upload-zone--dragging {
  border-color: var(--action-primary-hover);
  background-color: var(--surface-hover);
  box-shadow: 0 0 0 4px rgb(56 139 253 / 0.15);
}

.upload-zone--error {
  border-color: var(--action-danger);
}

.upload-zone--loading {
  cursor: not-allowed;
  opacity: 0.8;
}

.upload-zone__input {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}

.upload-zone__idle {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  text-align: center;
}

.upload-zone__icon {
  width: 48px;
  height: 48px;
  color: var(--text-tertiary);
  transition: color var(--transition-normal);
}

.upload-zone:hover .upload-zone__icon,
.upload-zone--dragging .upload-zone__icon {
  color: var(--text-secondary);
}

.upload-zone__label {
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.upload-zone__label-action {
  color: var(--text-link);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.upload-zone__hint {
  font-size: var(--text-xs);
  color: var(--text-tertiary);
  font-family: var(--font-mono);
}

/* Uploading state */
.upload-zone__uploading {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-4);
}

.upload-zone__spinner {
  width: 36px;
  height: 36px;
  border: 3px solid var(--surface-divider);
  border-top-color: var(--action-primary-hover);
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}

.upload-zone__upload-label {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  font-family: var(--font-mono);
}

/* Error message */
.upload-panel__error {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  background-color: var(--status-mismatch-bg);
  border: 1px solid var(--status-mismatch-glow);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  color: var(--status-mismatch-fg);
}

.upload-panel__error svg {
  flex-shrink: 0;
  width: 16px;
  height: 16px;
}

/* Run created info */
.upload-panel__run-created {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-4);
  background-color: var(--surface-raised);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
}

.upload-panel__run-id {
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.upload-panel__run-id-value {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--text-secondary);
  word-break: break-all;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
