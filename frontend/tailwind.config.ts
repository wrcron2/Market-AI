import type { Config } from 'tailwindcss'

/**
 * MarketFlow AI — design tokens ("Nocturne" palette).
 * Token names are kept stable (base/surface/line/ink/signal.*) so existing
 * components restyle automatically; only the values changed.
 * preflight is ON: this is now the only stylesheet.
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        base: '#161826',
        surface: {
          DEFAULT: '#232532',
          hover: '#282a38',
          raised: '#1d1f2c',
          sunken: '#161826',
        },
        line: {
          DEFAULT: '#3f424d',
          soft: '#31333d',
          faint: '#262833',
        },
        ink: {
          DEFAULT: '#e9e9ed',
          muted: '#9397ab',
          faint: '#75798c',
        },
        signal: {
          blue: '#968ae0', // accent purple
          green: '#b5abfc', // bright accent — positive numbers / ok states
          red: '#d97b84', // danger
          orange: '#d9a05b', // warn
          yellow: '#d9a05b', // warn
          purple: '#968ae0',
        },
        accent: {
          DEFAULT: '#968ae0',
          bright: '#b5abfc',
          ring: '#423a6a',
        },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['ui-monospace', '"JetBrains Mono"', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      borderColor: { DEFAULT: '#3f424d' },
      keyframes: {
        'pulse-dot': { '0%,100%': { opacity: '1' }, '50%': { opacity: '.35' } },
        'slide-in': {
          from: { transform: 'translateX(24px)', opacity: '0' },
          to: { transform: 'translateX(0)', opacity: '1' },
        },
        glow: {
          '0%,100%': { boxShadow: '0 0 0 0 rgba(217,123,132,.45)' },
          '50%': { boxShadow: '0 0 22px 2px rgba(217,123,132,.65)' },
        },
        spin: { to: { transform: 'rotate(360deg)' } },
      },
      animation: {
        'pulse-dot': 'pulse-dot 2.4s ease-in-out infinite',
        'slide-in': 'slide-in .25s ease',
        glow: 'glow 1.1s ease-in-out infinite',
      },
    },
  },
  plugins: [],
} satisfies Config
