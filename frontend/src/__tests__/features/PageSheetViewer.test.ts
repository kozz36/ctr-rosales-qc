/**
 * Tests for PageSheetViewer.vue (issue #27 — full-res page-sheet lightbox).
 *
 * Covers:
 * - Renders a role="dialog" + aria-modal when open
 * - Shows the full-res image pointing at GET /runs/{id}/pages/{page}/image
 * - Displays the page number/label
 * - Has a visible close button that emits close
 * - ESC key emits close
 * - Backdrop click emits close
 * - Renders nothing when closed
 */

import { describe, it, expect, afterEach } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import PageSheetViewer from '@/features/review/PageSheetViewer.vue'

// Teleport renders into document.body — track wrappers and unmount after each
// test so stale dialogs never leak across cases.
const mounted: VueWrapper[] = []

function mountOpen(props: Record<string, unknown> = {}) {
  const wrapper = mount(PageSheetViewer, {
    props: { modelValue: true, runId: 'run-abc', page: 5, ...props },
    attachTo: document.body,
  })
  mounted.push(wrapper)
  return wrapper
}

afterEach(() => {
  while (mounted.length) mounted.pop()!.unmount()
  document.body.innerHTML = ''
})

describe('PageSheetViewer', () => {
  it('renders a role="dialog" with aria-modal when open', () => {
    mountOpen()
    const dialog = document.querySelector('[role="dialog"]')
    expect(dialog).not.toBeNull()
    expect(dialog!.getAttribute('aria-modal')).toBe('true')
  })

  it('shows the full-res image pointing at the /image endpoint', () => {
    mountOpen({ page: 7 })
    const img = document.querySelector('.page-viewer__image') as HTMLImageElement | null
    expect(img).not.toBeNull()
    expect(img!.getAttribute('src')).toContain('/api/v1/runs/run-abc/pages/7/image')
  })

  it('respects a custom apiBase prop', () => {
    mountOpen({ page: 3, apiBase: '/custom-api' })
    const img = document.querySelector('.page-viewer__image') as HTMLImageElement | null
    expect(img!.getAttribute('src')).toContain('/custom-api/runs/run-abc/pages/3/image')
  })

  it('displays the page number / label', () => {
    mountOpen({ page: 12 })
    const dialog = document.querySelector('[role="dialog"]')
    expect(dialog!.textContent).toContain('12')
  })

  it('has a visible close button that emits close', async () => {
    const wrapper = mountOpen()
    const closeBtn = document.querySelector('.page-viewer__close') as HTMLButtonElement | null
    expect(closeBtn).not.toBeNull()
    closeBtn!.click()
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('update:modelValue')).toBeTruthy()
    expect(wrapper.emitted('update:modelValue')![0]).toEqual([false])
  })

  it('emits close on ESC keydown', async () => {
    const wrapper = mountOpen()
    const dialog = document.querySelector('[role="dialog"]') as HTMLElement
    dialog.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('update:modelValue')).toBeTruthy()
    expect(wrapper.emitted('update:modelValue')![0]).toEqual([false])
  })

  it('emits close when the backdrop is clicked', async () => {
    const wrapper = mountOpen()
    const backdrop = document.querySelector('.page-viewer__backdrop') as HTMLElement
    backdrop.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('update:modelValue')).toBeTruthy()
  })

  it('renders nothing when closed', () => {
    mount(PageSheetViewer, {
      props: { modelValue: false, runId: 'run-abc', page: 5 },
      attachTo: document.body,
    })
    expect(document.querySelector('[role="dialog"]')).toBeNull()
  })
})
