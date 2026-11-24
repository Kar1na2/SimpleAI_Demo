import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

// 127.0.0.1 rather than "localhost": Node may resolve localhost to ::1 first, while
// uvicorn listens on IPv4 only, which shows up as ECONNREFUSED on the proxy.
// uvicorn stays on 127.0.0.1: only this Vite port is exposed (see README > Remote access).
const BACKEND = '127.0.0.1:8000'
const PORT = Number(process.env.FRONTEND_PORT ?? 5180)

// Tailscale MagicDNS names (<machine>.<tailnet>.ts.net). Vite allows IP addresses and localhost
// anyway, so the tailnet IP needs no entry. Add other names (e.g. a MagicDNS short name) with
// FRONTEND_ALLOWED_HOSTS=a,b. No machine-specific names are committed here.
const TAILNET_HOSTS = ['.ts.net']
const EXTRA_HOSTS = (process.env.FRONTEND_ALLOWED_HOSTS ?? '')
  .split(',')
  .map((h) => h.trim())
  .filter(Boolean)

const SCREENSHOT_DIR = fileURLToPath(new URL('./public/screenshots', import.meta.url))

/** Dev server only: GET /__dev/screenshots.json lists public/screenshots, newest first. */
function screenshotIndex(): Plugin {
  return {
    name: 'dev-screenshot-index',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use('/__dev/screenshots.json', (_req, res) => {
        let files: { name: string; url: string; mtime: number; size: number }[] = []
        try {
          files = fs
            .readdirSync(SCREENSHOT_DIR)
            .filter((f) => /\.(png|jpe?g|webp)$/i.test(f))
            .map((f) => {
              const st = fs.statSync(path.join(SCREENSHOT_DIR, f))
              return { name: f, url: `/screenshots/${encodeURIComponent(f)}`, mtime: st.mtimeMs, size: st.size }
            })
            .sort((a, b) => b.mtime - a.mtime)
        } catch {
          // no screenshots yet
        }
        res.setHeader('content-type', 'application/json')
        res.setHeader('cache-control', 'no-store')
        res.end(JSON.stringify(files))
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), screenshotIndex()],
  server: {
    host: '0.0.0.0', // reachable from Windows (and, once exposed, the tailnet); see README
    port: PORT,
    strictPort: true,
    allowedHosts: [...TAILNET_HOSTS, ...EXTRA_HOSTS],
    proxy: {
      '/api': { target: `http://${BACKEND}`, changeOrigin: true },
      '/ws': { target: `ws://${BACKEND}`, ws: true, changeOrigin: true },
    },
  },
})
