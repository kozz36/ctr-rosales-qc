import type { Config } from 'tailwindcss'

/**
 * Tailwind configuration aligned with design tokens.
 *
 * Strategy: extend the default palette with named slots that reference the
 * CSS custom properties from tokens.css. Components use Tailwind class names
 * (bg-surface-raised, text-status-match) which resolve to var(--*) at runtime.
 * This gives us Tailwind's JIT purging + token-level theming in one pass.
 *
 * CSS layer order: base → Tailwind components → Tailwind utilities → PrimeVue.
 * PrimeVue's layer is hoisted last so component overrides always win.
 */
export default {
  content: ['./index.html', './src/**/*.{vue,ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['DM Sans', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
        mono: [
          'JetBrains Mono',
          'ui-monospace',
          'SFMono-Regular',
          'SF Mono',
          'Menlo',
          'Consolas',
          'Liberation Mono',
          'monospace',
        ],
      },
      colors: {
        // Surface layers
        surface: {
          base:     'var(--surface-base)',
          raised:   'var(--surface-raised)',
          overlay:  'var(--surface-overlay)',
          hover:    'var(--surface-hover)',
          active:   'var(--surface-active)',
          inset:    'var(--surface-inset)',
          divider:  'var(--surface-divider)',
        },
        // Text tiers
        content: {
          primary:   'var(--text-primary)',
          secondary: 'var(--text-secondary)',
          tertiary:  'var(--text-tertiary)',
          inverse:   'var(--text-inverse)',
          link:      'var(--text-link)',
        },
        // Border
        border: {
          subtle:  'var(--border-subtle)',
          default: 'var(--border-default)',
          strong:  'var(--border-strong)',
          focus:   'var(--border-focus)',
        },
        // Status — tokens expose -bg / -fg / -glow variants
        status: {
          match:             'var(--status-match-fg)',
          'match-bg':        'var(--status-match-bg)',
          'match-glow':      'var(--status-match-glow)',
          mismatch:          'var(--status-mismatch-fg)',
          'mismatch-bg':     'var(--status-mismatch-bg)',
          'mismatch-glow':   'var(--status-mismatch-glow)',
          'declared-missing':       'var(--status-declared-missing-fg)',
          'declared-missing-bg':    'var(--status-declared-missing-bg)',
          'declared-missing-glow':  'var(--status-declared-missing-glow)',
          'guia-missing':       'var(--status-guia-missing-fg)',
          'guia-missing-bg':    'var(--status-guia-missing-bg)',
          'guia-missing-glow':  'var(--status-guia-missing-glow)',
          unclassified:       'var(--status-unclassified-fg)',
          'unclassified-bg':  'var(--status-unclassified-bg)',
          'unclassified-glow':'var(--status-unclassified-glow)',
        },
        // Action
        action: {
          primary:        'var(--action-primary)',
          'primary-hover':'var(--action-primary-hover)',
          danger:         'var(--action-danger)',
          'danger-hover': 'var(--action-danger-hover)',
        },
      },
      spacing: {
        // Map token spacing to Tailwind scale aliases
        '18': '4.5rem',
        '22': '5.5rem',
      },
      borderRadius: {
        sm:   'var(--radius-sm)',
        md:   'var(--radius-md)',
        lg:   'var(--radius-lg)',
        pill: 'var(--radius-pill)',
      },
      boxShadow: {
        sm:    'var(--shadow-sm)',
        md:    'var(--shadow-md)',
        lg:    'var(--shadow-lg)',
        focus: 'var(--shadow-focus)',
      },
      transitionDuration: {
        fast:   '100ms',
        normal: '180ms',
        slow:   '300ms',
      },
      height: {
        header: 'var(--header-height)',
      },
    },
  },
  plugins: [],
} satisfies Config
