import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  fullyParallel: false,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:8010',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: {
    command: 'cd .. && .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8010',
    url: 'http://127.0.0.1:8010/api/health',
    reuseExistingServer: true,
    timeout: 30_000,
  },
})
