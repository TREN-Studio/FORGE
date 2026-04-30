const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');

test.describe.serial('FORGE desktop onboarding', () => {
  test('completes Desktop first-run sign-in through the shared portal Google flow', async ({ page }) => {
    await page.goto('/');

    await expect(page.getByRole('heading', { name: 'Talk to FORGE' })).toBeVisible();
    await expect(page.locator('#send')).toHaveText('Send');
    await expect(page.locator('#clear')).toHaveText('New Chat');
    await expect(page.locator('#workspace-subtitle')).toContainText('Sign in first');
    await expect(page.locator('#sidebar-toggle')).toHaveText('Sign In');
    await expect(page.locator('#auth-gate')).toBeVisible();
    await expect(page.locator('#auth-gate')).toContainText('Sign in once, then just chat');

    const popupPromise = page.waitForEvent('popup');
    await page.locator('#auth-gate-google').click();
    const popup = await popupPromise;

    await expect(page.locator('#sidebar-toggle')).toHaveText('Settings', { timeout: 20000 });
    await page.locator('#sidebar-toggle').click();
    await expect(page.locator('#auth-logged-in')).toBeVisible({ timeout: 20000 });
    await expect(page.locator('#account-email')).toContainText('google-user@example.com');
    await expect(page.locator('#account-role')).toContainText('User');
    await expect(page.locator('#provider-select')).toBeVisible();
    await expect(page.locator('#workspace-path')).toBeVisible();
    await expect(page.locator('#auth-status')).toContainText('Desktop session is active.');
    await expect(page.locator('#workspace-subtitle')).toContainText('real assistant');
    await expect(page.locator('#sidebar-toggle')).toHaveText('Settings');
    await expect(page.locator('#send')).toBeEnabled();
    await popup.waitForLoadState('domcontentloaded');
  });

  test('replies naturally in chat and completes a real development mission', async ({ page }) => {
    test.skip(!process.env.FORGE_TEST_NVIDIA_KEY, 'FORGE_TEST_NVIDIA_KEY is required for live provider verification.');

    const workspace = path.resolve(__dirname, '..', '..', '.forge_artifacts', `desktop_ui_live_test_spec_${Date.now()}`);
    fs.mkdirSync(workspace, { recursive: true });

    await page.goto('/');

    const popupPromise = page.waitForEvent('popup');
    await page.locator('#auth-gate-google').click();
    const popup = await popupPromise;
    await page.locator('#sidebar-toggle').waitFor({ state: 'visible', timeout: 20000 });
    await page.locator('#sidebar-toggle').click();
    await page.locator('#auth-logged-in').waitFor({ timeout: 20000 });
    await popup.waitForLoadState('domcontentloaded');

    await page.selectOption('#provider-select', 'nvidia');
    await page.fill('#provider-api-key', process.env.FORGE_TEST_NVIDIA_KEY);
    await page.evaluate(() => {
      const button = document.getElementById('save-provider-key');
      if (button) button.click();
    });
    await expect(page.locator('#provider-status')).toContainText('Saved key set for nvidia', { timeout: 15000 });

    const workspaceResponse = await page.evaluate(async (nextWorkspace) => {
      const response = await fetch('/api/workspace', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ workspace_root: nextWorkspace }),
      });
      return await response.json();
    }, workspace);
    expect(workspaceResponse.workspace_root).toBe(workspace);
    await page.fill('#workspace-path', workspace);
    await page.locator('#sidebar-scrim').click({ force: true });
    await page.evaluate(() => {
      const confirm = document.getElementById('confirm-mode');
      if (confirm) confirm.checked = true;
    });

    await page.fill('#prompt', 'hi');
    await page.locator('#send').click();
    const sawStreamingOrReply = await expect
      .poll(async () => {
        const streamingCount = await page.locator('.bubble.assistant.streaming').count();
        const latestReply = ((await page.locator('.bubble.assistant .body').last().textContent()) || '').trim();
        return streamingCount > 0 || latestReply.length > 0;
      }, { timeout: 5000 })
      .toBeTruthy();
    const chatReply = await expect
      .poll(async () => {
        return ((await page.locator('.bubble.assistant .body').last().textContent()) || '').trim();
      }, { timeout: 60000 })
      .not.toMatch(/^(|\||▍|Selecting the strongest available provider path\.\.\.|Using workspace:.*|Using .* on .*\.?\|?|Planning the response inside your workspace\.\.\.|Running the best path FORGE found for this request\.\.\.|Response ready\. Streaming output\.\.\.)$/s);
    expect(String(chatReply)).not.toContain('FORGE is not a general chatbot');
    expect(String(chatReply)).toMatch(/[A-Za-z\u0600-\u06FF]/);
    await expect(page.locator('#send')).toHaveText('Send', { timeout: 15000 });
    await expect(page.locator('#send')).toBeEnabled({ timeout: 15000 });
    await expect(page.locator('.bubble.assistant').last()).not.toHaveClass(/streaming/, { timeout: 15000 });
    await expect(page.locator('.bubble.assistant .bubble-footer').last()).toContainText(/\|/, { timeout: 15000 });

    await page.fill('#prompt', 'Create notes.txt with the exact content:\nFORGE UI live verification\nThen run `python -m compileall .`');
    await page.locator('#send').click();

    await expect.poll(() => fs.existsSync(path.join(workspace, 'notes.txt')), { timeout: 90000 }).toBeTruthy();
    await expect
      .poll(() => fs.readFileSync(path.join(workspace, 'notes.txt'), 'utf8').trim(), { timeout: 90000 })
      .toBe('FORGE UI live verification');
    await expect(page.locator('.bubble.assistant .body').last()).toContainText(/verification|compileall|finished/i, { timeout: 90000 });
  });
});
