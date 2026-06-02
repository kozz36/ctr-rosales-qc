/**
 * TanStack Query composables for the reconciliation API.
 *
 * Server-state ownership pattern (vue-architect skill):
 *   - useRunStatus: polls GET /runs/{id} until terminal state
 *   - useTable: fetches GET /runs/{id}/table (enabled only when status=review)
 *
 * These composables do NOT write to Pinia. Side-effects (writing rows into the
 * reconciliation store, updating run store status) are handled by the consuming
 * components via onSuccess callbacks, keeping the composables pure.
 */

import { computed, type Ref } from 'vue'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { getRunStatus, getTable, editRow, editGuiaLine, reassignGuia, exportRun } from '@/api/client'
import type {
  ExportFormat,
  GuiaLineEditRequest,
  GuiaLineEditResponse,
  ReassignRequest,
  RowEditRequest,
  RowEditResponse,
  ReassignResponse,
} from '@/api/types'

// ---------------------------------------------------------------------------
// Query key factory
// ---------------------------------------------------------------------------

export const queryKeys = {
  runStatus: (runId: string) => ['run', runId, 'status'] as const,
  table: (runId: string) => ['run', runId, 'table'] as const,
  audit: (runId: string) => ['run', runId, 'audit'] as const,
} as const

// ---------------------------------------------------------------------------
// useRunStatus — polls GET /runs/{run_id} with backoff
// ---------------------------------------------------------------------------

export interface UseRunStatusOptions {
  /** Poll interval in ms while status is pending|processing. Default: 2000. */
  pollInterval?: number
}

export function useRunStatus(runId: Ref<string | null>, options: UseRunStatusOptions = {}) {
  const { pollInterval = 2000 } = options

  return useQuery({
    queryKey: computed(() => queryKeys.runStatus(runId.value ?? '')),
    queryFn: () => getRunStatus(runId.value!),
    enabled: computed(() => runId.value !== null),
    // TanStack Query v5: refetchInterval can be a function receiving the Query object
    refetchInterval: (query) => {
      const status = query.state.data?.status
      // Stop polling on terminal states
      if (status === 'review' || status === 'error') return false
      return pollInterval
    },
    // Stale immediately so each poll fires a fresh fetch
    staleTime: 0,
  })
}

// ---------------------------------------------------------------------------
// useTable — fetches the reconciliation table once run reaches review state
// ---------------------------------------------------------------------------

export function useTable(runId: Ref<string | null>, isReady: Ref<boolean>) {
  return useQuery({
    queryKey: computed(() => queryKeys.table(runId.value ?? '')),
    queryFn: () => getTable(runId.value!),
    enabled: computed(() => runId.value !== null && isReady.value),
    // Table data is stable between edits; explicit invalidation handles updates
    staleTime: Infinity,
  })
}

// ---------------------------------------------------------------------------
// useEditRow — PATCH /runs/{run_id}/rows/{row_id}
// ---------------------------------------------------------------------------

export function useEditRow(runId: Ref<string | null>) {
  const queryClient = useQueryClient()

  return useMutation<RowEditResponse, Error, { rowId: string; body: RowEditRequest }>({
    mutationFn: ({ rowId, body }) => editRow(runId.value!, rowId, body),
    onSuccess: () => {
      if (runId.value) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.table(runId.value) })
      }
    },
  })
}

// ---------------------------------------------------------------------------
// useGuiaLineEdit — PATCH /runs/{run_id}/guias/{guia_id}/lines (rev-2)
// ---------------------------------------------------------------------------

/**
 * Mutation for editing an individual guía-line cantidad in the drill-down view.
 *
 * On success the returned rows array replaces the table cache so the parent
 * row's summed_qty (a @computed_field on the backend) reflects the new value
 * without requiring a separate GET /table fetch.
 */
export function useGuiaLineEdit(runId: Ref<string | null>) {
  const queryClient = useQueryClient()

  return useMutation<GuiaLineEditResponse, Error, { guiaId: string; body: GuiaLineEditRequest }>({
    mutationFn: ({ guiaId, body }) => editGuiaLine(runId.value!, guiaId, body),
    onSuccess: () => {
      if (runId.value) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.table(runId.value) })
      }
    },
  })
}

// ---------------------------------------------------------------------------
// useReassignGuia — POST /runs/{run_id}/reassign
// ---------------------------------------------------------------------------

export function useReassignGuia(runId: Ref<string | null>) {
  const queryClient = useQueryClient()

  return useMutation<ReassignResponse, Error, ReassignRequest>({
    mutationFn: (body) => reassignGuia(runId.value!, body),
    onSuccess: () => {
      if (runId.value) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.table(runId.value) })
      }
    },
  })
}

// ---------------------------------------------------------------------------
// useExportRun — POST /runs/{run_id}/export → blob download
// ---------------------------------------------------------------------------

export function useExportRun(runId: Ref<string | null>) {
  return useMutation<void, Error, ExportFormat>({
    mutationFn: async (fmt) => {
      const blob = await exportRun(runId.value!, fmt)
      // Trigger browser download via temporary object URL
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `reconciliacion_${runId.value!.slice(0, 8)}.${fmt}`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    },
  })
}
