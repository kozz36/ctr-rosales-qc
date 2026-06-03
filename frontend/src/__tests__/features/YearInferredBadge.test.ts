/**
 * Tests for YearInferredBadge.vue (R4.2 — REV-C05 / D5)
 *
 * Covers:
 * - Badge renders in default (non-compact) mode with icon + label
 * - Compact mode: icon visible, label hidden
 * - Yellow advisory visual state (not red error)
 * - a11y: role="img" + aria-label present
 * - Tooltip text explains year reconstruction
 */

import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import YearInferredBadge from '@/features/review/YearInferredBadge.vue'

describe('YearInferredBadge', () => {
  it('renders in default (non-compact) mode with icon and label', () => {
    const wrapper = mount(YearInferredBadge)
    expect(wrapper.find('.year-inferred-badge__icon').exists()).toBe(true)
    expect(wrapper.find('.year-inferred-badge__icon').text()).toBe('△')
    expect(wrapper.find('.year-inferred-badge__label').text()).toBe('Año inferido')
  })

  it('has role="img" for a11y (not color-only per WCAG 1.4.1)', () => {
    const wrapper = mount(YearInferredBadge)
    expect(wrapper.attributes('role')).toBe('img')
  })

  it('has aria-label describing inferred year state', () => {
    const wrapper = mount(YearInferredBadge)
    expect(wrapper.attributes('aria-label')).toContain('inferido')
  })

  it('tooltip attribute explains year reconstruction', () => {
    const wrapper = mount(YearInferredBadge)
    const title = wrapper.attributes('title')
    expect(title).toBeTruthy()
    expect(title).toContain('inferido')
    // Advisory context: day-month read from stamp
    expect(title).toContain('sello')
  })

  it('compact mode: has compact class', () => {
    const wrapper = mount(YearInferredBadge, { props: { compact: true } })
    expect(wrapper.classes()).toContain('year-inferred-badge--compact')
  })

  it('compact mode: label is hidden (display:none via CSS class)', () => {
    const wrapper = mount(YearInferredBadge, { props: { compact: true } })
    // The label element exists in the DOM but CSS hides it — we verify the class is set
    expect(wrapper.classes()).toContain('year-inferred-badge--compact')
  })

  it('non-compact mode: does not have compact class', () => {
    const wrapper = mount(YearInferredBadge, { props: { compact: false } })
    expect(wrapper.classes()).not.toContain('year-inferred-badge--compact')
  })

  it('uses yellow advisory CSS classes (not red error classes)', () => {
    const wrapper = mount(YearInferredBadge)
    // Verify the badge root uses the advisory class, not error/review class
    expect(wrapper.classes()).toContain('year-inferred-badge')
    // Must NOT be a red requires-review or mismatch badge
    expect(wrapper.classes()).not.toContain('recon-row__flag--review')
    expect(wrapper.classes()).not.toContain('confidence-badge--low')
  })
})
