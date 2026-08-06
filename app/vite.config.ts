import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Build straight into the sara package: the server ships the app prebuilt,
// so `pip install` users never run node. Contributors: `npm run build` here
// regenerates sara/server/static/ and the result is committed.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../skills/finance/sara/sara/server/static',
    emptyOutDir: true,
    rollupOptions: {
      output: { manualChunks: { echarts: ['echarts/core', 'echarts/charts', 'echarts/components', 'echarts/renderers'] } },
    },
  },
  server: {
    // dev flow: run the backend (SARA_DEV_ORIGIN=http://localhost:5173
    // python -m sara.server) and the proxy carries /api across
    proxy: { '/api': 'http://127.0.0.1:8787' },
  },
})
