/**
 * Tests for FechaDivergenceBadge.vue (R9 — FDR-009 / ADR-8)
 *
 * Covers:
 * - Default (non-compact) renders icon + "Fecha no coincide" label
 * - Compact mode: icon visible, label hidden via class
 * - RED indicator (not the yellow advisory) — distinct CSS class
 * - a11y: role="img" + aria-label present (state not color-only, WCAG 1.4.1)
 * - Tooltip explains the divergence
 */

import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import FechaDivergenceBadge from '@/features/review/FechaDivergenceBadge.vue'

describe('FechaDivergenceBadge', () => {
  it('renders in default (non-compact) mode with icon and label', () => {
    const wrapper = mount(FechaDivergenceBadge)
    expect(wrapper.find('.fecha-divergence-badge__icon').exists()).toBe(true)
    expect(wrapper.find('.fecha-divergence-badge__label').text()).toBe('Fecha no coincide')
  })

  it('has role="img" for a11y (not color-only per WCAG 1.4.1)', () => {
    const wrapper = mount(FechaDivergenceBadge)
    expect(wrapper.attributes('role')).toBe('img')
  })

  it('has a non-empty aria-label', () => {
    const wrapper = mount(FechaDivergenceBadge)
    expect(wrapper.attributes('aria-label')).toBeTruthy()
    expect(wrapper.attributes('aria-label')).toContain('coincide')
  })

  it('has a non-empty tooltip (title) attribute', () => {
    const wrapper = mount(FechaDivergenceBadge)
    const title = wrapper.attributes('title')
    expect(title).toBeTruthy()
    expect(title).toContain('Protocolo')
  })

  it('compact mode: has compact class (label hidden via CSS)', () => {
    const wrapper = mount(FechaDivergenceBadge, { props: { compact: true } })
    expect(wrapper.classes()).toContain('fecha-divergence-badge--compact')
  })

  it('non-compact mode: does not have compact class', () => {
    const wrapper = mount(FechaDivergenceBadge, { props: { compact: false } })
    expect(wrapper.classes()).not.toContain('fecha-divergence-badge--compact')
  })

  it('uses the RED divergence class, not the yellow year-inferred class', () => {
    const wrapper = mount(FechaDivergenceBadge)
    expect(wrapper.classes()).toContain('fecha-divergence-badge')
    expect(wrapper.classes()).not.toContain('year-inferred-badge')
  })
})
