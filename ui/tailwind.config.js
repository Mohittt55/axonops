export default {
  content: ["./index.html","./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        mono: ['"JetBrains Mono"', '"Fira Code"', 'ui-monospace', 'monospace'],
        sans: ['"Inter"', 'system-ui', 'sans-serif'],
      },
      colors: {
        bg:      { DEFAULT: '#0a0a0f', 2: '#0f0f17', 3: '#14141e', 4: '#1a1a26' },
        border:  { DEFAULT: '#1e1e2e', 2: '#2a2a3e' },
        text:    { DEFAULT: '#e2e2f0', muted: '#8888a8', dim: '#4a4a6a' },
        accent:  { DEFAULT: '#6366f1', dim: '#312e81', glow: 'rgba(99,102,241,0.15)' },
        green:   { DEFAULT: '#10b981', dim: '#064e3b', glow: 'rgba(16,185,129,0.15)' },
        amber:   { DEFAULT: '#f59e0b', dim: '#78350f', glow: 'rgba(245,158,11,0.15)' },
        red:     { DEFAULT: '#ef4444', dim: '#7f1d1d', glow: 'rgba(239,68,68,0.15)' },
        teal:    { DEFAULT: '#14b8a6', dim: '#134e4a' },
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4,0,0.6,1) infinite',
        'fade-in':    'fadeIn 0.3s ease-out',
        'slide-up':   'slideUp 0.25s ease-out',
      },
      keyframes: {
        fadeIn:  { from: { opacity: '0' }, to: { opacity: '1' } },
        slideUp: { from: { opacity: '0', transform: 'translateY(8px)' }, to: { opacity: '1', transform: 'translateY(0)' } },
      },
    },
  },
  plugins: [],
}
