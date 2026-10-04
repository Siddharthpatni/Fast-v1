import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The app calls /api on its own origin and Vite forwards it to the backend, so the
// browser never needs to know where the backend runs (works in Docker and locally).
const apiTarget = process.env.API_PROXY_TARGET ?? 'http://localhost:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // MapLibre's worker is an ES module (see components/Map/Basemap.tsx)
  worker: { format: 'es' },
  server: {
    proxy: { '/api': { target: apiTarget, changeOrigin: true } },
    // The e2e container reaches the dev server as http://frontend:5173
    allowedHosts: ['frontend'],
  },
})
