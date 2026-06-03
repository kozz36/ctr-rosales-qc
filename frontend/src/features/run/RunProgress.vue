<template>
  <div class="run-progress" role="region" :aria-label="`Estado del run ${runId}`">
    <!-- Status row -->
    <div class="run-progress__status-row" aria-live="polite">
      <div
        class="run-progress__status-badge"
        :class="`run-progress__status-badge--${currentStatus}`"
        role="status"
        :aria-label="`Estado: ${statusLabel}`"
      >
        <!-- Pulsing dot for active states -->
        <span
          v-if="isActive"
          class="run-progress__pulse"
          aria-hidden="true"
        />
        <!-- Icon for terminal states -->
        <svg
          v-else-if="isDone"
          aria-hidden="true"
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2.5"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <polyline points="20 6 9 17 4 12" />
        </svg>
        <svg
          v-else-if="isError"
          aria-hidden="true"
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </svg>

        <span class="run-progress__status-label">{{ statusLabel }}</span>
      </div>

      <span v-if="visionCalls > 0" class="run-progress__vision-calls">
        {{ visionCalls }} llamadas LLM
      </span>
    </div>

    <!-- Progress bar (visible while processing) -->
    <div
      v-if="isActive"
      class="run-progress__bar-track"
      role="progressbar"
      aria-label="Progreso del pipeline"
      :aria-valuenow="isDeterminate ? Math.round(progressPercent) : undefined"
      :aria-valuemin="isDeterminate ? 0 : undefined"
      :aria-valuemax="isDeterminate ? 100 : undefined"
      :aria-valuetext="isDeterminate ? progressAriaText : statusLabel"
    >
      <!--
        Determinate: fill width is bound to progress.percent; smooth transition.
        Indeterminate: slide animation, no inline width.
      -->
      <div
        class="run-progress__bar-fill"
        :class="{ 'run-progress__bar-fill--determinate': isDeterminate }"
        :style="isDeterminate ? { width: progressPercent + '%' } : {}"
      />
    </div>

    <!-- Stage detail line (visible when progress info is present) -->
    <p
      v-if="isActive && progressInfo"
      class="run-progress__stage-detail"
      aria-hidden="true"
    >
      {{ progressInfo.stage_label }} · ítem {{ progressInfo.item_done }}/{{ progressInfo.item_total }}
    </p>

    <!-- Elapsed / ETA timing row -->
    <div
      v-if="isActive && startedAt"
      class="run-progress__timing-row"
    >
      <span class="run-progress__elapsed">{{ elapsedFormatted }} transcurrido</span>
      <span v-if="etaFormatted" class="run-progress__eta">~{{ etaFormatted }} estimado</span>
    </div>

    <!-- Warnings -->
    <ul
      v-if="warnings.length"
      class="run-progress__warnings"
      aria-label="Advertencias del pipeline"
    >
      <li
        v-for="(warning, i) in warnings"
        :key="i"
        class="run-progress__warning-item"
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
          <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
          <line x1="12" y1="9" x2="12" y2="13" />
          <line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
        {{ warning }}
      </li>
    </ul>

    <!-- Error detail -->
    <p
      v-if="isError && errorDetail"
      class="run-progress__error-detail"
      role="alert"
    >
      {{ errorDetail }}
    </p>

    <!-- Navigation CTA when done -->
    <div v-if="isDone" class="run-progress__done-cta">
      <p class="run-progress__done-message">
        Pipeline completado. La tabla de reconciliación está lista.
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, watch, ref, onMounted, onUnmounted } from 'vue'
import { useRunStatus } from '@/composables/useReconciliationApi'
import { useRunStore } from '@/stores/run'
import { toRef } from 'vue'

// ---------------------------------------------------------------------------
// Props / emits
// ---------------------------------------------------------------------------

const props = defineProps<{
  runId: string
}>()

const emit = defineEmits<{
  /** Emitted once when status transitions to 'review'. */
  completed: []
  /** Emitted if the pipeline ends in 'error'. */
  failed: [error: string]
}>()

// ---------------------------------------------------------------------------
// TanStack Query — polls GET /runs/{run_id}
// ---------------------------------------------------------------------------

const runStore = useRunStore()
const runIdRef = toRef(props, 'runId')

const { data, isError: queryIsError } = useRunStatus(runIdRef, { pollInterval: 2000 })

// ---------------------------------------------------------------------------
// Derived state
// ---------------------------------------------------------------------------

const currentStatus = computed(() => data.value?.status ?? 'pending')
const visionCalls = computed(() => data.value?.vision_calls_made ?? 0)
const warnings = computed(() => data.value?.warnings ?? [])
const errorDetail = computed(() => data.value?.error ?? null)
const progressInfo = computed(() => data.value?.progress ?? null)
const startedAt = computed(() => data.value?.started_at ?? null)

const isActive = computed(() => currentStatus.value === 'pending' || currentStatus.value === 'processing')
const isDone = computed(() => currentStatus.value === 'review')
const isError = computed(() => currentStatus.value === 'error' || queryIsError.value)

/** True when progress data is available — switches bar from indeterminate to determinate. */
const isDeterminate = computed(() => isActive.value && progressInfo.value !== null)

const progressPercent = computed(() => progressInfo.value?.percent ?? 0)

/** Spanish aria-valuetext summarising current progress. */
const progressAriaText = computed(() => {
  const p = progressInfo.value
  if (!p) return statusLabel.value
  return `${p.stage_label}, ítem ${p.item_done} de ${p.item_total}, ${Math.round(p.percent)}%`
})

const statusLabel = computed<string>(() => {
  switch (currentStatus.value) {
    case 'pending':    return 'En cola'
    case 'processing': return 'Procesando'
    case 'review':     return 'Completado'
    case 'error':      return 'Error'
    default:           return 'Desconocido'
  }
})

// ---------------------------------------------------------------------------
// Elapsed / ETA — ticking clock
// ---------------------------------------------------------------------------

/** Reactive elapsed seconds, updated every ~1s by a setInterval. */
const elapsedSeconds = ref(0)
let tickInterval: ReturnType<typeof setInterval> | null = null

function computeElapsed(): number {
  if (!startedAt.value) return 0
  const start = new Date(startedAt.value).getTime()
  return Math.max(0, Math.floor((Date.now() - start) / 1000))
}

function tick() {
  elapsedSeconds.value = computeElapsed()
}

/** Format seconds as "Xm Ys". */
function formatDuration(secs: number): string {
  const m = Math.floor(secs / 60)
  const s = secs % 60
  return `${m}m ${s}s`
}

const elapsedFormatted = computed(() => formatDuration(elapsedSeconds.value))

/**
 * ETA = elapsed * (100 - percent) / percent.
 * Only computed when percent >= 1 to avoid wild / infinite estimates.
 */
const etaFormatted = computed<string | null>(() => {
  const pct = progressPercent.value
  if (pct < 1) return null
  const eta = Math.round(elapsedSeconds.value * (100 - pct) / pct)
  return formatDuration(eta)
})

onMounted(() => {
  // Start immediately so elapsed is accurate from mount
  tick()
  tickInterval = setInterval(tick, 1000)
})

onUnmounted(() => {
  if (tickInterval !== null) {
    clearInterval(tickInterval)
    tickInterval = null
  }
})

// ---------------------------------------------------------------------------
// Side effects — mirror to store + emit events
// ---------------------------------------------------------------------------

watch(currentStatus, (next) => {
  runStore.setStatus(next, errorDetail.value)

  if (next === 'review') {
    emit('completed')
  } else if (next === 'error') {
    emit('failed', errorDetail.value ?? 'Error desconocido')
  }
})
</script>

<style scoped>
.run-progress {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

/* Status badge */
.run-progress__status-row {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}

.run-progress__status-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-pill);
  font-size: var(--text-xs);
  font-family: var(--font-mono);
  font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.run-progress__status-badge svg {
  width: 12px;
  height: 12px;
  flex-shrink: 0;
}

/* Status variants */
.run-progress__status-badge--pending,
.run-progress__status-badge--processing {
  background-color: var(--status-guia-missing-bg);
  color: var(--status-guia-missing-fg);
}

.run-progress__status-badge--review {
  background-color: var(--status-match-bg);
  color: var(--status-match-fg);
}

.run-progress__status-badge--error {
  background-color: var(--status-mismatch-bg);
  color: var(--status-mismatch-fg);
}

/* Animated pulse dot */
.run-progress__pulse {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background-color: currentColor;
  animation: pulse 1.4s ease-in-out infinite;
}

/* Vision calls count */
.run-progress__vision-calls {
  font-size: var(--text-xs);
  font-family: var(--font-mono);
  color: var(--text-tertiary);
}

/* Progress bar track */
.run-progress__bar-track {
  height: 3px;
  background-color: var(--surface-divider);
  border-radius: var(--radius-pill);
  overflow: hidden;
}

/* Indeterminate fill — sliding animation */
.run-progress__bar-fill {
  height: 100%;
  width: 40%;
  background-color: var(--action-primary-hover);
  border-radius: var(--radius-pill);
  animation: slide 1.6s ease-in-out infinite;
}

/* Determinate fill — smooth width transition, no slide animation */
.run-progress__bar-fill--determinate {
  width: 0%;
  animation: none;
  transition: width 0.4s ease-out;
}

/* Stage detail */
.run-progress__stage-detail {
  font-size: var(--text-xs);
  font-family: var(--font-mono);
  color: var(--text-tertiary);
  margin: 0;
  line-height: 1.4;
}

/* Timing row */
.run-progress__timing-row {
  display: flex;
  gap: var(--space-4);
  font-size: var(--text-xs);
  font-family: var(--font-mono);
}

.run-progress__elapsed {
  color: var(--text-secondary);
}

.run-progress__eta {
  color: var(--text-tertiary);
}

/* Warnings */
.run-progress__warnings {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.run-progress__warning-item {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--status-declared-missing-fg);
  font-family: var(--font-mono);
  line-height: 1.5;
}

.run-progress__warning-item svg {
  width: 12px;
  height: 12px;
  flex-shrink: 0;
  margin-top: 2px;
}

/* Error detail */
.run-progress__error-detail {
  font-size: var(--text-sm);
  color: var(--status-mismatch-fg);
  font-family: var(--font-mono);
  line-height: 1.5;
}

/* Done CTA */
.run-progress__done-cta {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}

.run-progress__done-message {
  font-size: var(--text-sm);
  color: var(--status-match-fg);
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

@keyframes slide {
  0%   { transform: translateX(-250%); }
  50%  { transform: translateX(150%); }
  100% { transform: translateX(400%); }
}
</style>
