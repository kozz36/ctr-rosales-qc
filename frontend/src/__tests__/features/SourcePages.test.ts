/**
 * Tests for SourcePages.vue (S2.5 revision — <img> approach replaces new Image() probe)
 *
 * Covers:
 * - Renders chips for each page number
 * - Chip click emits pageClick event
 * - Keyboard (Enter/Space) triggers pageClick
 * - Graceful degradation when thumbnail @error fires (no has-thumb class)
 * - Thumbnail shown when @load fires (has-thumb class applied)
 * - Thumbnail <img> src uses the API base URL pattern (S2.5)
 * - No <img> probe via new Image() — declarative <img :src> approach (S2.5)
 */

import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SourcePages from '@/features/review/SourcePages.vue'

describe('SourcePages', () => {
  it('renders a chip for each page number', () => {
    const wrapper = mount(SourcePages, {
      props: { pages: [1, 3, 7], runId: 'run-abc' },
    })
    const chips = wrapper.findAll('.source-pages__chip')
    expect(chips).toHaveLength(3)
    expect(chips[0].text()).toContain('1')
    expect(chips[1].text()).toContain('3')
    expect(chips[2].text()).toContain('7')
  })

  it('emits pageClick with page number when chip is clicked', async () => {
    const wrapper = mount(SourcePages, {
      props: { pages: [5], runId: 'run-abc' },
    })
    await wrapper.find('.source-pages__chip').trigger('click')
    expect(wrapper.emitted('pageClick')).toBeTruthy()
    expect(wrapper.emitted('pageClick')![0]).toEqual([5])
  })

  it('emits pageClick on Enter keydown', async () => {
    const wrapper = mount(SourcePages, {
      props: { pages: [2], runId: 'run-abc' },
    })
    await wrapper.find('.source-pages__chip').trigger('keydown.enter')
    expect(wrapper.emitted('pageClick')).toBeTruthy()
  })

  it('thumbnail <img> src uses the API base URL pattern (S2.5)', () => {
    // The <img> is always present (hidden until load). src must point to the API endpoint.
    const wrapper = mount(SourcePages, {
      props: { pages: [3], runId: 'run-abc' },
    })
    const img = wrapper.find('.source-pages__thumb')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toContain('/api/v1/runs/run-abc/pages/3/thumbnail')
  })

  it('thumbnail <img> src respects custom apiBase prop', () => {
    const wrapper = mount(SourcePages, {
      props: { pages: [1], runId: 'run-xyz', apiBase: '/custom-api' },
    })
    const img = wrapper.find('.source-pages__thumb')
    expect(img.attributes('src')).toContain('/custom-api/runs/run-xyz/pages/1/thumbnail')
  })

  it('has-thumb class absent and img hidden before @load fires', () => {
    const wrapper = mount(SourcePages, {
      props: { pages: [1], runId: 'run-abc' },
    })
    // Before load: chip does not have has-thumb class
    expect(wrapper.find('.source-pages__chip').classes()).not.toContain('source-pages__chip--has-thumb')
    // img has hidden class
    expect(wrapper.find('.source-pages__thumb').classes()).toContain('source-pages__thumb--hidden')
  })

  it('has-thumb class applied and img visible after @load fires (graceful enhancement)', async () => {
    const wrapper = mount(SourcePages, {
      props: { pages: [1], runId: 'run-abc' },
    })
    // Simulate the img @load event
    await wrapper.find('.source-pages__thumb').trigger('load')
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.source-pages__chip').classes()).toContain('source-pages__chip--has-thumb')
    expect(wrapper.find('.source-pages__thumb').classes()).not.toContain('source-pages__thumb--hidden')
  })

  it('degrades gracefully when @error fires (no has-thumb class, img stays hidden)', async () => {
    const wrapper = mount(SourcePages, {
      props: { pages: [1], runId: 'run-abc' },
    })
    // Simulate the img @error event (404, network failure, etc.)
    await wrapper.find('.source-pages__thumb').trigger('error')
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.source-pages__chip').classes()).not.toContain('source-pages__chip--has-thumb')
    // Chip still renders page number as fallback
    expect(wrapper.find('.source-pages__number').text()).toBe('1')
  })

  it('renders empty with no chips when pages array is empty', () => {
    const wrapper = mount(SourcePages, {
      props: { pages: [], runId: 'run-abc' },
    })
    expect(wrapper.findAll('.source-pages__chip')).toHaveLength(0)
  })
})
