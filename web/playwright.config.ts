import { defineConfig } from '@playwright/test'

// Deterministic by default: Playwright must own both processes and fail clearly
// if either fixed port is occupied. Reuse requires an explicit local opt-in.
const reuseExistingServer = process.env.SCOUT_E2E_REUSE_SERVERS === '1'
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: executablePath ? { executablePath } : undefined,
  },
  webServer: [
    {
      name: 'Scout API',
      command:
        'uv run --project .. --extra web uvicorn server:app --app-dir e2e --host 127.0.0.1 --port 8000',
      url: 'http://127.0.0.1:8000/api/health',
      timeout: 30_000,
      gracefulShutdown: { signal: 'SIGTERM', timeout: 5_000 },
      reuseExistingServer,
    },
    {
      name: 'Vite frontend',
      command: 'npm run dev -- --host 127.0.0.1 --port 5173',
      url: 'http://127.0.0.1:5173',
      timeout: 30_000,
      gracefulShutdown: { signal: 'SIGTERM', timeout: 5_000 },
      reuseExistingServer,
    },
  ],
})
