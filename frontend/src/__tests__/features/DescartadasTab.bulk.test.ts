/**
 * TDD RED — DescartadasTab bulk recovery (SDD#2 PR-3b — REV-R29, REV-R30, REV-R32).
 *
 * Contract:
 *   - A3: "Recuperar seleccionadas" opens a confirm dialog BEFORE firing the
 *     batch (REV-R30-S01). The dialog shows the selected count prominently,
 *     an APPROXIMATE ETA line (K OCR-empty pages × ~10 s), and a vision-cost
 *     warning ONLY when K > 0 (cached-lines pages are near-instant Tier-1).
 *   - Batch fire: POST /discarded-pages/recover-batch with the INTERSECTION of
 *     the selected Set against the CURRENT discardedPages prop (ctr-reviewer
 *     carry-over: a single-page recover does not prune `selected`, so stale
 *     page numbers must never reach the payload).
 *   - Poll GET /recover-status until `done === true` — the REAL backend
 *     completion signal (PR#49 SA-5 lesson). A mid-batch progress status NEVER
 *     renders the completion summary; incremental progress emits 'refetch' so
 *     recovered pages leave via the parent's refreshed prop. Failed pages stay.
 *   - A4: on mount, poll recover-status ONCE; `done:false` → re-attach (resume
 *     polling, disable all Recuperar buttons); terminal `{total:0, done:true}`
 *     → no polling, buttons stay enabled.
 *
 * Fails before the PR-3b bulk flow replaces the BULK_FLOW_READY=false gate.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'
import type {
  DiscardedPageResponse,
  DiscardedRecoverStatusResponse,
} from '@/api/types'

const { recoverPageMock, recoverBatchMock, recoverStatusMock } = vi.hoisted(() => ({
  recoverPageMock: vi.fn(),
  recoverBatchMock: vi.fn(),
  recoverStatusMock: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  recoverDiscardedPage: recoverPageMock,
  recoverDiscardedBatch: recoverBatchMock,
  getDiscardedRecoverStatus: recoverStatusMock,
}))

vi.mock('@/features/review/PageSheetViewer.vue', () => ({
  default: {
    name: 'PageSheetViewer',
    template: '<div class="stub-viewer" />',
    props: ['modelValue', 'runId', 'page', 'rowPages'],
    emits: ['update:modelValue'],
  },
}))

import DescartadasTab from '@/features/review/DescartadasTab.vue'

function makeEntry(
  page: number,
  overrides: Partial<DiscardedPageResponse> = {},
): DiscardedPageResponse {
  return { page, registro: '232', has_cached_lines: true, ...overrides }
}

function status(
  p: Partial<DiscardedRecoverStatusResponse>,
): DiscardedRecoverStatusResponse {
  return { total: 0, recovered: 0, failed: 0, done: true, ...p }
}

/** Terminal shape when no batch fired — locked by backend test 2.1.15. */
const TERMINAL = status({ total: 0, recovered: 0, failed: 0, done: true })

async function mountTab(discardedPages: DiscardedPageResponse[]) {
  const wrapper = mount(DescartadasTab, {
    props: { discardedPages, runId: 'run-123' },
  })
  // Settle the A4 on-mount status poll before the test takes over.
  await flushPromises()
  return wrapper
}

function bulkButton(wrapper: Awaited<ReturnType<typeof mountTab>>) {
  return wrapper.find('.descartadas-tab__bulk-btn')
}

function dialog(wrapper: Awaited<ReturnType<typeof mountTab>>) {
  return wrapper.find('.descartadas-tab__dialog')
}

async function selectAll(wrapper: Awaited<ReturnType<typeof mountTab>>) {
  await wrapper.find('.descartadas-tab__select-all').trigger('click')
}

async function openDialog(wrapper: Awaited<ReturnType<typeof mountTab>>) {
  await bulkButton(wrapper).trigger('click')
  await nextTick()
}

async function confirmDialog(wrapper: Awaited<ReturnType<typeof mountTab>>) {
  await wrapper.find('.descartadas-tab__dialog-confirm').trigger('click')
  await flushPromises()
}

async function expandGroup(
  wrapper: Awaited<ReturnType<typeof mountTab>>,
  index = 0,
): Promise<void> {
  const toggles = wrapper.findAll('.descartadas-tab__group-toggle')
  await toggles[index].trigger('click')
}

beforeEach(() => {
  vi.useFakeTimers()
  recoverPageMock.mockReset()
  recoverBatchMock.mockReset()
  recoverStatusMock.mockReset()
  recoverStatusMock.mockResolvedValue(TERMINAL)
  recoverBatchMock.mockResolvedValue({ run_id: 'run-123', count: 2 })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('DescartadasTab — A3 confirm dialog before batch fire', () => {
  // 3b.1.1 — confirm required BEFORE the request is sent (REV-R30-S01).
  it('shows the confirm dialog and does NOT fire the batch until confirmed', async () => {
    const wrapper = await mountTab([makeEntry(57), makeEntry(58)])
    await selectAll(wrapper)
    await openDialog(wrapper)

    expect(dialog(wrapper).exists()).toBe(true)
    expect(dialog(wrapper).text()).toContain('2')
    expect(recoverBatchMock).not.toHaveBeenCalled()

    await confirmDialog(wrapper)
    expect(recoverBatchMock).toHaveBeenCalledWith('run-123', [57, 58])
  })

  // 3b.1.2 — ETA line: K OCR-empty pages × ~10 s, labeled approximate; vision
  // warning shown because K > 0 (design A3).
  it('shows an approximate ETA and the vision-cost warning when OCR-empty pages are selected', async () => {
    const wrapper = await mountTab([
      makeEntry(57, { has_cached_lines: false }),
      makeEntry(58, { has_cached_lines: false }),
    ])
    await selectAll(wrapper)
    await openDialog(wrapper)

    const text = dialog(wrapper).text()
    expect(text).toMatch(/≈\s*\d+\s*min/)
    expect(text).toContain('~10 s')
    const warning = wrapper.find('.descartadas-tab__dialog-warning')
    expect(warning.exists()).toBe(true)
    expect(warning.text().toLowerCase()).toMatch(/ia|visión/)
  })

  // 3b.1.3 — all selected pages cached → Tier-1 near-instant → NO vision warning.
  it('does NOT show the vision-cost warning when every selected page has cached lines', async () => {
    const wrapper = await mountTab([
      makeEntry(57),
      makeEntry(58),
      makeEntry(59),
    ])
    await selectAll(wrapper)
    await openDialog(wrapper)

    expect(dialog(wrapper).text()).toContain('3')
    expect(wrapper.find('.descartadas-tab__dialog-warning').exists()).toBe(false)
  })
})

describe('DescartadasTab — stale-selection intersection (ctr-reviewer carry-over)', () => {
  // A single-page recover does NOT prune `selected`; the batch payload MUST be
  // intersected against the refreshed discardedPages prop before firing.
  it('excludes a singly-recovered page from the bulk payload after the list refreshes', async () => {
    recoverPageMock.mockResolvedValue({
      recovered: true,
      page: 152,
      guia_id: 'recovered_152',
      reason: null,
      rows: [],
      discarded_pages: [],
    })
    const wrapper = await mountTab([makeEntry(152), makeEntry(153)])
    await selectAll(wrapper) // selected = {152, 153}
    await expandGroup(wrapper)

    // Recover page 152 individually while it is still selected.
    await wrapper.find('.descartadas-tab__recover-btn').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('refetch')).toBeTruthy()

    // Parent refetches GET /table → page 152 leaves the prop.
    await wrapper.setProps({ discardedPages: [makeEntry(153)] })

    // The live selection count excludes the recovered page.
    expect(bulkButton(wrapper).text()).toContain('1')

    await openDialog(wrapper)
    expect(dialog(wrapper).text()).toContain('1')
    await confirmDialog(wrapper)

    // Payload is the INTERSECTION: stale page 152 never reaches the backend.
    expect(recoverBatchMock).toHaveBeenCalledWith('run-123', [153])
  })
})

describe('DescartadasTab — in-flight gating (REV-R30-S05 + A4)', () => {
  // 3b.1.4 — bulk button disabled while the batch is in-flight.
  it('disables "Recuperar seleccionadas" while the batch is in-flight', async () => {
    recoverStatusMock
      .mockResolvedValueOnce(TERMINAL) // mount poll
      .mockResolvedValue(status({ total: 2, recovered: 0, failed: 0, done: false }))
    const wrapper = await mountTab([makeEntry(57), makeEntry(58)])
    await selectAll(wrapper)
    await openDialog(wrapper)
    await confirmDialog(wrapper)

    expect(bulkButton(wrapper).attributes('disabled')).toBeDefined()
  })

  // 3b.1.9 — single-page Recuperar is ALSO disabled while a batch is in-flight.
  it('disables the per-page "Recuperar" buttons while a batch is in-flight', async () => {
    recoverStatusMock.mockResolvedValue(
      status({ total: 3, recovered: 0, failed: 0, done: false }),
    )
    const wrapper = await mountTab([makeEntry(57), makeEntry(58)])
    await expandGroup(wrapper)

    const recoverBtn = wrapper.find('.descartadas-tab__recover-btn')
    expect(recoverBtn.attributes('disabled')).toBeDefined()
  })
})

describe('DescartadasTab — poll-until-done (PR#49 SA-5 lesson)', () => {
  // 3b.1.5 — incremental progress: recovered pages leave the list as they
  // complete (via parent refetch); the completion summary appears ONLY after
  // done=true (premature-settlement regression lock).
  it('removes recovered pages incrementally and never settles before done=true', async () => {
    recoverStatusMock
      .mockResolvedValueOnce(TERMINAL) // mount poll
      .mockResolvedValueOnce(status({ total: 3, recovered: 1, failed: 0, done: false }))
      .mockResolvedValueOnce(status({ total: 3, recovered: 2, failed: 0, done: false }))
      .mockResolvedValue(status({ total: 3, recovered: 2, failed: 1, done: true }))
    const entries = [makeEntry(152), makeEntry(175), makeEntry(200)]
    const wrapper = await mountTab(entries)
    await selectAll(wrapper)
    await openDialog(wrapper)
    await confirmDialog(wrapper) // fire + immediate first poll: recovered=1, done=false

    // Incremental progress: a refetch was emitted BEFORE done so the parent
    // can refresh the table (the recovered page leaves the prop).
    expect(wrapper.emitted('refetch')).toBeTruthy()
    const refetchesBeforeDone = wrapper.emitted('refetch')!.length

    // Premature-settlement lock: NO completion summary while done=false.
    expect(wrapper.find('.descartadas-tab__batch-summary').exists()).toBe(false)

    // Parent refresh removes the recovered page; failed pages will stay.
    await wrapper.setProps({ discardedPages: [makeEntry(175), makeEntry(200)] })
    expect(wrapper.findAll('.descartadas-tab__group')).toHaveLength(2)
    expect(wrapper.find('.descartadas-tab__batch-summary').exists()).toBe(false)

    // Second poll: recovered=2, still done=false → still NO summary.
    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()
    expect(wrapper.find('.descartadas-tab__batch-summary').exists()).toBe(false)

    // Third poll: done=true → summary with the REAL backend counts.
    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()
    const summary = wrapper.find('.descartadas-tab__batch-summary')
    expect(summary.exists()).toBe(true)
    expect(summary.text()).toContain('2 recuperadas')
    expect(summary.text()).toContain('1 falló')
    expect(wrapper.emitted('refetch')!.length).toBeGreaterThan(refetchesBeforeDone)

    // Failed page stays in the list (parent keeps it in the refreshed prop).
    await wrapper.setProps({ discardedPages: [makeEntry(175)] })
    expect(wrapper.findAll('.descartadas-tab__group')).toHaveLength(1)
  })

  // 3b.1.6 — completion summary from the REAL counts after done=true.
  it('shows the completion summary "2 recuperadas / 1 falló" after done=true', async () => {
    recoverStatusMock
      .mockResolvedValueOnce(TERMINAL) // mount poll
      .mockResolvedValue(status({ total: 3, recovered: 2, failed: 1, done: true }))
    const wrapper = await mountTab([makeEntry(152), makeEntry(175), makeEntry(200)])
    await selectAll(wrapper)
    await openDialog(wrapper)
    await confirmDialog(wrapper)

    const summary = wrapper.find('.descartadas-tab__batch-summary')
    expect(summary.exists()).toBe(true)
    expect(summary.text()).toContain('2 recuperadas')
    expect(summary.text()).toContain('1 falló')
  })

  // 3b.1.10 — refetch emitted after batch completion (REV-R32).
  it('emits refetch after the batch completes so the parent refreshes the grid', async () => {
    recoverStatusMock
      .mockResolvedValueOnce(TERMINAL) // mount poll
      .mockResolvedValue(status({ total: 2, recovered: 2, failed: 0, done: true }))
    const wrapper = await mountTab([makeEntry(57), makeEntry(58)])
    await selectAll(wrapper)
    await openDialog(wrapper)
    await confirmDialog(wrapper)

    expect(wrapper.emitted('refetch')).toBeTruthy()
  })
})

describe('DescartadasTab — A4 mount re-attach', () => {
  // 3b.1.7 — done=false on mount → re-attach: resume polling + disable buttons.
  it('re-attaches to an in-flight batch on mount: resumes polling and disables the bulk button', async () => {
    recoverStatusMock.mockResolvedValue(
      status({ total: 5, recovered: 1, failed: 0, done: false }),
    )
    const wrapper = await mountTab([makeEntry(57), makeEntry(58)])
    await selectAll(wrapper)

    expect(bulkButton(wrapper).attributes('disabled')).toBeDefined()

    // Polling resumed: subsequent interval ticks hit the status endpoint again.
    const callsAfterMount = recoverStatusMock.mock.calls.length
    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()
    expect(recoverStatusMock.mock.calls.length).toBeGreaterThan(callsAfterMount)
  })

  // 3b.1.8 — terminal shape {total:0, done:true} on mount → NO polling resumes.
  it('does not poll after mount when the status is the terminal no-batch shape', async () => {
    const wrapper = await mountTab([makeEntry(57), makeEntry(58)])

    expect(recoverStatusMock).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(recoverStatusMock).toHaveBeenCalledTimes(1)

    // No in-flight batch → selection enables the bulk button.
    await selectAll(wrapper)
    expect(bulkButton(wrapper).attributes('disabled')).toBeUndefined()
  })
})
