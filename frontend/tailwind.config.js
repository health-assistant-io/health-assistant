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
      animation: {
        'spin-slow': 'spin 3s linear infinite',
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
}
