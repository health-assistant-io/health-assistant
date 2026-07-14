/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      screens: {
        'xs': '480px',
      },
      colors: {
        brand: {
          50: '#eff6ff',
          100: '#dbeafe',
          200: '#bfdbfe',
          300: '#93c5fd',
          400: '#60a5fa',
          500: '#3b82f6',
          600: '#2563eb',
          700: '#1d4ed8',
          800: '#1e40af',
          900: '#1e3a8a',
          navy: '#1a2b4b',
          cyan: '#0088CC',
          cyanHover: '#0077B3',
        },
        dark: {
          bg: '#0f172a',        // slate-900
          surface: '#1e293b',   // slate-800
          border: '#334155',    // slate-700
          text: '#f8fafc',      // slate-50
          muted: '#94a3b8',     // slate-400
          primary: '#3b82f6',   // blue-500
          hover: '#1e293b',     // slate-800
        }
      },
      zIndex: {
        // Body-level overlay scale. Sidebar internals are scoped with `isolate`
        // so they can never compete with these portals.
        dropdown: 100,   // popovers in normal page context (above isolated sidebar)
        modal: 1000,
        popover: 1050,   // portalled popovers that must float ABOVE a modal
        drawer: 1100,
        toast: 1200,
      },
      animation: {
        'spin-slow': 'spin 3s linear infinite',
        'gradient-x': 'gradient-x 3s ease infinite',
      },
      keyframes: {
        'gradient-x': {
          '0%, 100%': {
            'background-size': '200% 200%',
            'background-position': 'left center',
          },
          '50%': {
            'background-size': '200% 200%',
            'background-position': 'right center',
          },
        },
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
    require('tailwindcss-animate'),
  ],
}
