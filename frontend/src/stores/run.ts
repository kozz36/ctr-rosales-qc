/**
 * Run store — owns the current run lifecycle (client state only).
 *
 * Server state (polling GET /runs/{id}) is owned by TanStack Query
 * (useRunStatus composable). This store holds the client-side slice:
 * - Which run_id is currently active
 * - Upload progress state
 * - Navigation intent after pipeline completion
 *
 * Pattern: Pinia for client state + TanStack Query for server state.
 * Crossing these boundaries (e.g. caching server responses in Pinia) is
 * explicitly avoided per the vue-architect skill.
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { createRun } from '@/api/client'
import type { RunStatus } from '@/api/types'

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useRunStore = defineStore('run', () => {
  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------

  /** UUID returned by POST /runs. Null means no active run. */
  const runId = ref<string | null>(null)

  /** Latest status mirrored from polling (set by RunProgress after query). */
  const status = ref<RunStatus | null>(null)

  /** True while the upload POST is in-flight. */
  const uploading = ref(false)

  /** Upload progress 0–100 (client-side read of XHR progress events). */
  const uploadProgress = ref(0)

  /** Error message from upload or pipeline failure. */
  const error = ref<string | null>(null)

  // ---------------------------------------------------------------------------
  // Computed
  // ---------------------------------------------------------------------------

  const isActive = computed(() => runId.value !== null)
  const isReady = computed(() => status.value === 'review')
  const isFailed = computed(() => status.value === 'error')

  // ---------------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------------

  /**
   * Upload a PDF file and register the resulting run_id.
   * Throws on validation failure or HTTP error so UploadPanel can handle UI.
   */
  async function upload(file: File): Promise<string> {
    // Client-side validation (mirrors backend rules so failures are local-fast)
    if (!file.type.includes('pdf') && !file.name.toLowerCase().endsWith('.pdf')) {
      const msg = 'El archivo debe ser un PDF.'
      error.value = msg
      throw new Error(msg)
    }
    const MAX_BYTES = 100 * 1024 * 1024 // 100 MB — matches backend MAX_UPLOAD_BYTES
    if (file.size > MAX_BYTES) {
      const msg = 'El archivo excede el límite de 100 MB.'
      error.value = msg
      throw new Error(msg)
    }

    error.value = null
    uploading.value = true
    uploadProgress.value = 0
    // Reset any prior run so the UI reflects a clean state
    runId.value = null
    status.value = null

    try {
      const response = await createRun(file)
      runId.value = response.run_id
      status.value = response.status
      uploadProgress.value = 100
      return response.run_id
    } catch (err: unknown) {
      const msg = extractErrorMessage(err)
      error.value = msg
      throw new Error(msg)
    } finally {
      uploading.value = false
    }
  }

  /**
   * Mirror the latest polled status into the store.
   * Called by the RunProgress component's TanStack Query onSuccess callback.
   */
  function setStatus(nextStatus: RunStatus, errorMsg?: string | null): void {
    status.value = nextStatus
    if (nextStatus === 'error' && errorMsg) {
      error.value = errorMsg
    }
  }

  /** Reset all state (e.g. when user starts a new upload). */
  function reset(): void {
    runId.value = null
    status.value = null
    uploading.value = false
    uploadProgress.value = 0
    error.value = null
  }

  return {
    // State
    runId,
    status,
    uploading,
    uploadProgress,
    error,
    // Computed
    isActive,
    isReady,
    isFailed,
    // Actions
    upload,
    setStatus,
    reset,
  }
})

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function extractErrorMessage(err: unknown): string {
  // Prefer the API error detail from the response body (Axios error shape)
  if (
    typeof err === 'object' &&
    err !== null &&
    'response' in err &&
    typeof (err as { response?: { data?: { detail?: string } } }).response?.data?.detail ===
      'string'
  ) {
    return (err as { response: { data: { detail: string } } }).response.data.detail
  }
  if (err instanceof Error) return err.message
  return 'Error desconocido.'
}
