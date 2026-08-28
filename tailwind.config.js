/** tailwind.config.js */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./static/js/**/*.js"
  ],
  theme: {
    extend: {
      colors: {
        iron: {
          bg: '#020617',
          card: '#0f172a',
          cardHover: '#1e293b',
          border: '#1e293b',
          borderHover: '#a855f7',
          text: '#f8fafc',
          textMuted: '#94a3b8',
          textDim: '#64748b',
        }
      }
    }
  },
  plugins: []
}