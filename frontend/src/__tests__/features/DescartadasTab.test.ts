/**
 * TDD RED — DescartadasTab (SDD#2 PR-3a — REV-R28, REV-R29, REV-R31 UI, REV-R33).
 *
 * Contract:
 *   - A1: flat sorted discarded list grouped into contiguous page-runs in a
 *     computed; a group breaks at a page-index gap OR a registro change.
 *   - A2: groups render COLLAPSED by default (v-if body → zero <img> on mount);
 *     expanding renders `<img loading="lazy">` thumbnails (SourcePages pattern).
 *   - A3: per-page checkboxes + per-group tri-state header checkbox (usable
 *     collapsed) + global "Seleccionar todas (N)" control.
 *   - Single-page "Recuperar" → POST /discarded-pages/{page}/recover; success
 *     emits 'refetch'; recovered=false reasons are shown honestly.
 *   - REV-R33 MUST-NOT: no REINTENTAR surface (structural discrimination).
 *
 * Fails before DescartadasTab.vue exists.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import type { DiscardedPageResponse } from '@/api/types'

const { recoverPageMock } = vi.hoisted(() => ({
  recoverPageMock: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  recoverDiscardedPage: recoverPageMock,
  recoverDiscardedBatch: vi.fn(),
  getDiscardedRecoverStatus: vi.fn(),
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

function mountTab(discardedPages: DiscardedPageResponse[]) {
  return mount(DescartadasTab, {
    props: { discardedPages, runId: 'run-123' },
  })
}

function groupHeaders(wrapper: ReturnType<typeof mountTab>) {
  return wrapper.findAll('.descartadas-tab__group')
}

async function expandGroup(
  wrapper: ReturnType<typeof mountTab>,
  index = 0,
): Promise<void> {
  const toggles = wrapper.findAll('.descartadas-tab__group-toggle')
  await toggles[index].trigger('click')
}

function bulkButton(wrapper: ReturnType<typeof mountTab>) {
  return wrapper.find('.descartadas-tab__bulk-btn')
}

describe('DescartadasTab — A1 grouping by contiguous runs', () => {
  beforeEach(() => {
    recoverPageMock.mockReset()
  })

  // 3a.1.5 — page-index gap breaks the run.
  it('groups discarded entries into contiguous page runs', () => {
    const wrapper = mountTab([
      makeEntry(57),
      makeEntry(58),
      makeEntry(59),
      makeEntry(81),
      makeEntry(82),
    ])
    const groups = groupHeaders(wrapper)
    expect(groups).toHaveLength(2)
    expect(groups[0].text()).toContain('57')
    expect(groups[0].text()).toContain('59')
    expect(groups[1].text()).toContain('81')
    expect(groups[1].text()).toContain('82')
  })

  // 3a.1.6 — registro change breaks the run even without an index gap.
  it('splits a contiguous run when the registro changes', () => {
    const wrapper = mountTab([
      makeEntry(57, { registro: '232' }),
      makeEntry(58, { registro: '233' }),
      makeEntry(59, { registro: '233' }),
    ])
    const groups = groupHeaders(wrapper)
    expect(groups).toHaveLength(2)
    expect(groups[0].text()).toContain('232')
    expect(groups[1].text()).toContain('233')
    // First group is the single page 57; second is 58–59.
    expect(groups[0].findAll('.descartadas-tab__group-count')[0].text()).toBe('1')
    expect(groups[1].findAll('.descartadas-tab__group-count')[0].text()).toBe('2')
  })
})

describe('DescartadasTab — A2 collapsed groups + lazy thumbnails', () => {
  beforeEach(() => {
    recoverPageMock.mockReset()
  })

  // 3a.1.7 — collapsed by default: zero <img> elements on mount.
  it('renders all groups collapsed by default — no <img> elements exist', () => {
    const wrapper = mountTab([
      makeEntry(57),
      makeEntry(58),
      makeEntry(81),
    ])
    expect(groupHeaders(wrapper).length).toBeGreaterThan(0)
    expect(wrapper.findAll('img')).toHaveLength(0)
  })

  // 3a.1.8 — expand renders lazy thumbnails with correct URLs.
  it('expanding a group renders <img loading="lazy"> with thumbnail URLs', async () => {
    const wrapper = mountTab([makeEntry(57), makeEntry(58)])
    await expandGroup(wrapper)

    const imgs = wrapper.findAll('img')
    expect(imgs).toHaveLength(2)
    for (const img of imgs) {
      expect(img.attributes('loading')).toBe('lazy')
    }
    expect(imgs[0].attributes('src')).toContain('/runs/run-123/pages/57/thumbnail')
    expect(imgs[1].attributes('src')).toContain('/runs/run-123/pages/58/thumbnail')
  })
})

describe('DescartadasTab — A3 selection (per-page, tri-state group, global)', () => {
  beforeEach(() => {
    recoverPageMock.mockReset()
  })

  // 3a.1.9 — per-page checkbox selects only that page.
  it('checking one per-page checkbox selects only that page', async () => {
    const wrapper = mountTab([makeEntry(57), makeEntry(58), makeEntry(59)])
    await expandGroup(wrapper)

    const checkboxes = wrapper.findAll('input.descartadas-tab__page-checkbox')
    expect(checkboxes).toHaveLength(3)

    await checkboxes[0].setValue(true)

    expect((checkboxes[0].element as HTMLInputElement).checked).toBe(true)
    expect((checkboxes[1].element as HTMLInputElement).checked).toBe(false)
    expect((checkboxes[2].element as HTMLInputElement).checked).toBe(false)
    // Selection count surfaces on the bulk button label.
    expect(bulkButton(wrapper).text()).toContain('1')
    // Group header checkbox goes indeterminate (some selected).
    const groupCheckbox = wrapper.find('input.descartadas-tab__group-checkbox')
    expect((groupCheckbox.element as HTMLInputElement).indeterminate).toBe(true)
  })

  // 3a.1.10 — group header tri-state selects the whole run while COLLAPSED.
  it('group header checkbox selects all pages of the run without expanding', async () => {
    const wrapper = mountTab([makeEntry(57), makeEntry(58), makeEntry(59)])

    // Group stays collapsed — the header checkbox must still work.
    const groupCheckbox = wrapper.find('input.descartadas-tab__group-checkbox')
    await groupCheckbox.setValue(true)

    expect((groupCheckbox.element as HTMLInputElement).checked).toBe(true)
    expect((groupCheckbox.element as HTMLInputElement).indeterminate).toBe(false)
    expect(bulkButton(wrapper).text()).toContain('3')
    // Still collapsed — no thumbnails were forced.
    expect(wrapper.findAll('img')).toHaveLength(0)
  })

  // 3a.1.11 — global select-all across all groups (REV-R29-S01).
  it('global "Seleccionar todas (N)" selects every page across groups', async () => {
    const wrapper = mountTab([
      makeEntry(57),
      makeEntry(58),
      makeEntry(59),
      makeEntry(81),
      makeEntry(82),
    ])

    const selectAll = wrapper.find('.descartadas-tab__select-all')
    expect(selectAll.text()).toContain('Seleccionar todas (5)')
    await selectAll.trigger('click')

    expect(bulkButton(wrapper).text()).toContain('5')
    const groupCheckboxes = wrapper.findAll('input.descartadas-tab__group-checkbox')
    for (const cb of groupCheckboxes) {
      expect((cb.element as HTMLInputElement).checked).toBe(true)
    }
  })

  // 3a.1.12 — global deselect-all (REV-R29-S02).
  it('global control toggles back to deselect all', async () => {
    const wrapper = mountTab([
      makeEntry(57),
      makeEntry(58),
      makeEntry(59),
      makeEntry(81),
      makeEntry(82),
    ])

    const selectAll = wrapper.find('.descartadas-tab__select-all')
    await selectAll.trigger('click')
    expect(bulkButton(wrapper).text()).toContain('5')

    await selectAll.trigger('click')
    expect(bulkButton(wrapper).attributes('disabled')).toBeDefined()
    const groupCheckboxes = wrapper.findAll('input.descartadas-tab__group-checkbox')
    for (const cb of groupCheckboxes) {
      expect((cb.element as HTMLInputElement).checked).toBe(false)
    }
  })

  // 3a.1.13 — bulk button disabled with no selection (REV-R29).
  it('disables "Recuperar seleccionadas" when nothing is selected', () => {
    const wrapper = mountTab([makeEntry(57), makeEntry(58)])
    const bulk = bulkButton(wrapper)
    expect(bulk.exists()).toBe(true)
    expect(bulk.attributes('disabled')).toBeDefined()
  })
})

describe('DescartadasTab — single-page Recuperar (REV-R31 UI)', () => {
  beforeEach(() => {
    recoverPageMock.mockReset()
  })

  // 3a.1.14 — single-page recover calls the single-page endpoint + refetch.
  it('clicking "Recuperar" calls recoverDiscardedPage and emits refetch on success', async () => {
    recoverPageMock.mockResolvedValue({
      recovered: true,
      page: 152,
      guia_id: 'recovered_152',
      reason: null,
      rows: [],
      discarded_pages: [],
    })
    const wrapper = mountTab([makeEntry(152)])
    await expandGroup(wrapper)

    const recoverBtn = wrapper.find('.descartadas-tab__recover-btn')
    expect(recoverBtn.exists()).toBe(true)
    await recoverBtn.trigger('click')
    await flushPromises()

    expect(recoverPageMock).toHaveBeenCalledWith('run-123', 152)
    expect(wrapper.emitted('refetch')).toBeTruthy()
  })

  // Honest-failure lock: recovered=false reasons are surfaced, never silent.
  it('shows the failure reason honestly when recovered=false', async () => {
    recoverPageMock.mockResolvedValue({
      recovered: false,
      page: 152,
      guia_id: null,
      reason: 'empty',
      rows: [],
      discarded_pages: [],
    })
    const wrapper = mountTab([makeEntry(152, { has_cached_lines: false })])
    await expandGroup(wrapper)

    await wrapper.find('.descartadas-tab__recover-btn').trigger('click')
    await flushPromises()

    const error = wrapper.find('.descartadas-tab__page-error')
    expect(error.exists()).toBe(true)
    expect(error.text().length).toBeGreaterThan(0)
    // Failure does NOT emit refetch with a recovered row claim.
    expect(wrapper.emitted('refetch')).toBeFalsy()
  })
})

describe('DescartadasTab — empty state, sin registro, REINTENTAR absence', () => {
  beforeEach(() => {
    recoverPageMock.mockReset()
  })

  // 3a.1.15 — empty state (REV-R28-S05).
  it('renders the empty-state message with no checkboxes or thumbnails', () => {
    const wrapper = mountTab([])
    expect(wrapper.find('.descartadas-tab__empty').exists()).toBe(true)
    expect(wrapper.findAll('input[type="checkbox"]')).toHaveLength(0)
    expect(wrapper.findAll('img')).toHaveLength(0)
  })

  // 3a.1.16 — registro=null shows "sin registro" (REV-R28-S03).
  it('shows a "sin registro" label for entries without registro', () => {
    const wrapper = mountTab([makeEntry(88, { registro: null })])
    expect(wrapper.text().toLowerCase()).toContain('sin registro')
    // Entry is still selectable (group checkbox present).
    expect(wrapper.find('input.descartadas-tab__group-checkbox').exists()).toBe(true)
  })

  // 3a.1.17 — REINTENTAR is structurally absent (REV-R33 MUST-NOT).
  it('never renders a REINTENTAR / SUNAT-retry surface', async () => {
    const wrapper = mountTab([makeEntry(152), makeEntry(153)])
    await expandGroup(wrapper)

    expect(wrapper.text().toLowerCase()).not.toContain('reintentar')
    expect(wrapper.text().toLowerCase()).not.toContain('sunat')
    const retryButtons = wrapper
      .findAll('button')
      .filter((b) => /reintentar/i.test(b.text()))
    expect(retryButtons).toHaveLength(0)
  })
})
