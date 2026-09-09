import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
// NOTE: this project builds with rolldown-vite, whose chunking API differs
// from rollup's manualChunks. The heavy-library problem (Leaflet shipping
// in the entry chunk to anonymous visitors) is solved at the SOURCE instead:
// every authenticated page — including the one manager page that imports
// Leaflet — is lazy-loaded in App.tsx, so the public entry stays lean.
export default defineConfig({
  plugins: [react()],
})
