import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The API runs inside the bot (or scripts/run_web.py) on port 8377. In development Vite serves the
// pages and passes /api through to it.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8377',
    },
  },
})
