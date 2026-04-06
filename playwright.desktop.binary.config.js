const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests/e2e',
  testMatch: /desktop-onboarding\.spec\.js$/,
  fullyParallel: false,
  retries: 0,
  reporter: 'list',
  timeout: 120000,
  use: {
    baseURL: 'http://127.0.0.1:43019',
    headless: true,
  },
  webServer: [
    {
      command: 'python site_backend/forge_portal/dev_server.py --host 127.0.0.1 --port 43017 --reset-state',
      url: 'http://127.0.0.1:43017',
      reuseExistingServer: true,
      timeout: 120000,
    },
    {
      command: 'powershell -NoProfile -Command "$env:FORGE_PORTAL_API_BASE_URL=\'http://127.0.0.1:43017/api/index.php\'; python tools/run_desktop_binary_server.py"',
      url: 'http://127.0.0.1:43019',
      reuseExistingServer: true,
      timeout: 180000,
    },
  ],
});
