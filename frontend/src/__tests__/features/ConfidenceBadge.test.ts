/**
 * Tests for ConfidenceBadge.vue
 *
 * Covers:
 * - Threshold: value < 0.85 → "needs review" visual state
 * - Value >= 0.85 → OK state
 * - null → trusted (digital) state
 * - Icon and label differentiation (a11y: not color-only)
 */

import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ConfidenceBadge from '@/features/review/ConfidenceBadge.vue'

describe('ConfidenceBadge', () => {
  it('renders value below threshold (0.84) as "low" / needs review', () => {
    const wrapper = mount(ConfidenceBadge, { props: { value: 0.84 } })
    expect(wrapper.classes()).toContain('confidence-badge--low')
    expect(wrapper.text()).toContain('84%')
    expect(wrapper.text()).toContain('revisar')
    expect(wrapper.find('.confidence-badge__icon').text()).toBe('!')
  })

  it('renders value at threshold (0.85) as OK, not needing review', () => {
    const wrapper = mount(ConfidenceBadge, { props: { value: 0.85 } })
    expect(wrapper.classes()).toContain('confidence-badge--ok')
    expect(wrapper.classes()).not.toContain('confidence-badge--low')
    expect(wrapper.text()).toContain('85%')
    expect(wrapper.text()).not.toContain('revisar')
    expect(wrapper.find('.confidence-badge__icon').text()).toBe('✓')
  })

  it('renders value above threshold (0.86) as OK', () => {
    const wrapper = mount(ConfidenceBadge, { props: { value: 0.86 } })
    expect(wrapper.classes()).toContain('confidence-badge--ok')
    expect(wrapper.classes()).not.toContain('confidence-badge--low')
    expect(wrapper.find('.confidence-badge__icon').text()).toBe('✓')
  })

  it('renders null value as trusted (digital text)', () => {
    const wrapper = mount(ConfidenceBadge, { props: { value: null } })
    expect(wrapper.classes()).toContain('confidence-badge--trusted')
    expect(wrapper.find('.confidence-badge__value').text()).toBe('—')
    expect(wrapper.find('.confidence-badge__icon').text()).toBe('●')
  })

  it('compact mode hides the "revisar" label', () => {
    const wrapper = mount(ConfidenceBadge, { props: { value: 0.70, compact: true } })
    expect(wrapper.classes()).toContain('confidence-badge--compact')
  })

  it('has aria-label describing the state', () => {
    const wrapper = mount(ConfidenceBadge, { props: { value: 0.72 } })
    expect(wrapper.attributes('aria-label')).toContain('requiere revisión')
  })

  it('trusted state has aria-label describing digital confidence', () => {
    const wrapper = mount(ConfidenceBadge, { props: { value: null } })
    expect(wrapper.attributes('aria-label')).toContain('digital')
  })

  it('renders 100% confidence as 100%', () => {
    const wrapper = mount(ConfidenceBadge, { props: { value: 1.0 } })
    expect(wrapper.find('.confidence-badge__value').text()).toBe('100%')
    expect(wrapper.classes()).toContain('confidence-badge--ok')
  })
})
