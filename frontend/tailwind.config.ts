import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#f5f5f0',
        surface: '#ffffff',
        'surface-2': '#faf9f4',
        text: {
          DEFAULT: '#1a1a1a',
          2: '#5f5e5a',
          3: '#888780',
        },
        border: 'rgba(0,0,0,0.08)',
        accent: { DEFAULT: '#534AB7', bg: '#EEEDFE' },
        green: { DEFAULT: '#0F6E56', bg: '#E1F5EE' },
        red: { DEFAULT: '#A32D2D', bg: '#FCEBEB' },
        amber: { DEFAULT: '#854F0B', bg: '#FAEEDA' },
      },
      fontFamily: {
        sans: [
          '-apple-system',
          'BlinkMacSystemFont',
          'Inter',
          'Segoe UI',
          'sans-serif',
        ],
        mono: ['ui-monospace', 'SF Mono', 'Menlo', 'monospace'],
      },
      animation: {
        'slide-in': 'slideIn 0.35s cubic-bezier(0.2, 0.8, 0.2, 1)',
        'pulse-dot': 'pulseDot 1.6s ease-in-out infinite',
        'caret-blink': 'caretBlink 0.9s steps(1) infinite',
        'spin-slow': 'spin 0.8s linear infinite',
      },
      keyframes: {
        slideIn: {
          from: { opacity: '0', transform: 'translateY(8px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        pulseDot: {
          '0%, 100%': { opacity: '0.4', transform: 'scale(0.9)' },
          '50%': { opacity: '1', transform: 'scale(1.15)' },
        },
        caretBlink: {
          '0%, 50%': { opacity: '1' },
          '51%, 100%': { opacity: '0' },
        },
      },
    },
  },
  plugins: [],
};
export default config;
