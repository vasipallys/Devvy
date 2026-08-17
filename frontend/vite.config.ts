/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
  build: { outDir: 'dist' },
  // `base` stays at its default of '/': a served web app resolves assets from the server root.

  // jsdom, not the default node environment. The one function under test builds a real DOM to
  // sanitise HTML — `DOMParser`, `querySelectorAll`, `replaceWith` — so a fake would test a
  // different implementation than the one that ships.
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts?(x)'],
  },
})
