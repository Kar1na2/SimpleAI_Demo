import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 127.0.0.1 rather than "localhost": Node may resolve localhost to ::1 first, while
// uvicorn listens on IPv4 only, which shows up as ECONNREFUSED on the proxy.
const BACKEND = '127.0.0.1:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: `http://${BACKEND}`, changeOrigin: true },
      '/ws': { target: `ws://${BACKEND}`, ws: true, changeOrigin: true },
    },
  },
})
