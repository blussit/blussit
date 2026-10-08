import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
// NOTE: this project builds with rolldown-vite, whose chunking API differs
// from rollup's manualChunks. The heavy-library problem (Leaflet shipping
// in the entry chunk to anonymous visitors) is solved at the SOURCE instead:
// every authenticated page — including the one manager page that imports
// Leaflet — is lazy-loaded in App.tsx, so the public entry stays lean.
export default defineConfig(({ isSsrBuild, command, mode }) => {
  if (command === 'build') {
    // Which API a build talks to is never implicit: the app used to fall
    // back to the live API when VITE_API_BASE_URL was missing, so a Vercel
    // preview quietly read and wrote production data.
    const env = loadEnv(mode, process.cwd(), 'VITE_')
    const api = env.VITE_API_BASE_URL
    if (!api) {
      throw new Error(
        'VITE_API_BASE_URL is not set. Set it for this build (Vercel: Project Settings → Environment Variables, for Production AND Preview).'
      )
    }
    if (process.env.VERCEL_ENV === 'preview' && /\/\/api\.blussit\.com(\/|$)/.test(api) && process.env.ALLOW_PREVIEW_PROD_API !== '1') {
      throw new Error(
        'This Vercel PREVIEW build points at the production API (api.blussit.com). Give the Preview environment its own VITE_API_BASE_URL (a staging API), or set ALLOW_PREVIEW_PROD_API=1 to do this on purpose.'
      )
    }
  }
  return {
    plugins: [react()],
    // The SSR build only feeds scripts/prerender.mjs — it needs no copy of public/.
    build: isSsrBuild ? { copyPublicDir: false } : {},
  }
})
