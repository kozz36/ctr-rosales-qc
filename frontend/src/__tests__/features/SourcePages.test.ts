/**
 * Tests for SourcePages.vue
 *
 * Covers:
 * - Renders chips for each page number
 * - Chip click emits pageClick event
 * - Keyboard (Enter/Space) triggers pageClick
 * - Graceful degradation when thumbnail probe fails (img onerror)
 * - Thumbnail shown when probe succeeds (img onload)
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import SourcePages from '@/features/review/SourcePages.vue'

// Mock Image constructor to control onload/onerror
class MockImage {
  src = ''
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
}

beforeEach(() => {
  vi.stubGlobal('Image', MockImage)
})

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

  it('degrades gracefully when thumbnail probe errors (no img shown)', async () => {
    // Use a mutable container to avoid TypeScript control-flow narrowing to never
    const captured: { img: MockImage | null } = { img: null }

    class CapturingImage extends MockImage {
      constructor() {
        super()
        captured.img = this as MockImage
      }
    }
    vi.stubGlobal('Image', CapturingImage)

    const wrapper = mount(SourcePages, {
      props: { pages: [1], runId: 'run-abc' },
    })

    // Simulate onerror (thumbnail endpoint not found)
    captured.img?.onerror?.()
    await wrapper.vm.$nextTick()

    // No thumbnail img shown
    expect(wrapper.find('.source-pages__thumb').exists()).toBe(false)
    // Chip still renders page number
    expect(wrapper.find('.source-pages__number').text()).toBe('1')
  })

  it('shows thumbnail when probe succeeds (img onload)', async () => {
    const captured: { img: MockImage | null } = { img: null }

    class CapturingImage2 extends MockImage {
      constructor() {
        super()
        captured.img = this as MockImage
      }
    }
    vi.stubGlobal('Image', CapturingImage2)

    const wrapper = mount(SourcePages, {
      props: { pages: [1], runId: 'run-abc' },
    })

    // Simulate successful load
    captured.img?.onload?.()
    await wrapper.vm.$nextTick()

    // Thumbnail img should now be visible
    expect(wrapper.find('.source-pages__thumb').exists()).toBe(true)
    expect(wrapper.find('.source-pages__chip').classes()).toContain('source-pages__chip--has-thumb')
  })

  it('renders empty with no chips when pages array is empty', () => {
    const wrapper = mount(SourcePages, {
      props: { pages: [], runId: 'run-abc' },
    })
    expect(wrapper.findAll('.source-pages__chip')).toHaveLength(0)
  })
})
