const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests/e2e',
  testMatch: /portal\.spec\.js$/,
  fullyParallel: false,
  retries: 0,
  reporter: 'list',
  timeout: 60000,
  use: {
    baseURL: 'http://127.0.0.1:43017',
    headless: true,
  },
  webServer: {
    command: 'python site_backend/forge_portal/dev_server.py --host 127.0.0.1 --port 43017 --reset-state',
    url: 'http://127.0.0.1:43017',
    reuseExistingServer: true,
    timeout: 120000,
  },
});
