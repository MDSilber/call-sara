import { defineConfig } from '@playwright/test'

// E2E drives the REAL server (python -m sara.server) on a throwaway copy of
// a demo vault: set SARA_E2E_VAULT and e2e/serve.sh does the rest (copy,
// plant an uncategorized transaction, serve on 8793). With SARA_E2E_URL
// set instead, tests run against that already-running server (look.spec's
// screenshot mode). channel: 'chrome' is deliberate — the system Chrome,
// no browser downloads (install with PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1).
const external = process.env.SARA_E2E_URL

export default defineConfig({
  testDir: './e2e',
  timeout: 120_000,
  use: {
    baseURL: external ?? 'http://127.0.0.1:8793',
    viewport: { width: 1360, height: 900 },
    channel: 'chrome',
  },
  reporter: [['list']],
  ...(external
    ? {}
    : {
        webServer: {
          command: './e2e/serve.sh',
          port: 8793,
          reuseExistingServer: false,
          timeout: 30_000,
        },
      }),
})
