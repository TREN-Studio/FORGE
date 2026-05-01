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
    await expect.poll(() => popup.url(), { timeout: 10000 }).not.toBe('about:blank');
    expect(popup.url()).toContain('device_code=');

    await expect(page.locator('#sidebar-toggle')).toHaveText('Settings', { timeout: 20000 });
    await page.locator('#sidebar-toggle').click();
    await expect(page.locator('#auth-logged-in')).toBeVisible({ timeout: 20000 });
    await expect(page.locator('#account-email')).toContainText('google-user@example.com');
    await expect(page.locator('#account-role')).toContainText('User');
    await expect(page.locator('#provider-select')).toBeVisible();
    await expect(page.locator('#workspace-path')).toBeVisible();
    await expect(page.locator('#auth-status')).toContainText('Desktop session is active.');
    await page.locator('#sidebar-scrim').click({ force: true });
    await expect(page.locator('#provider-setup')).toBeVisible({ timeout: 20000 });
    await expect(page.locator('#provider-setup')).toContainText('Choose how FORGE should think');
    await expect(page.locator('#provider-setup')).toContainText('Ollama is not running');
    await expect(page.locator('#provider-setup-groq')).toBeVisible();
    await expect(page.locator('#provider-setup-ollama')).toBeVisible();
    await expect(page.locator('#provider-setup-byok')).toBeVisible();
    await page.locator('#provider-setup-groq').click();
    await expect(page.locator('#provider-select')).toHaveValue('groq');
    await expect(page.locator('#provider-status')).toContainText('Groq selected');
    await expect(page.locator('#sidebar-toggle')).toHaveText('Settings');
    await expect(page.locator('#send')).toBeEnabled();
    await popup.waitForLoadState('domcontentloaded');
  });

  test('runs the one-click local demo and creates action_items.md', async ({ page }) => {
    await page.goto('/');

    const popupPromise = page.waitForEvent('popup');
    await page.locator('#auth-gate-google').click();
    const popup = await popupPromise;
    await expect.poll(() => popup.url(), { timeout: 10000 }).not.toBe('about:blank');
    expect(popup.url()).toContain('device_code=');

    await expect(page.locator('#sidebar-toggle')).toHaveText('Settings', { timeout: 20000 });
    await page.locator('#sidebar-toggle').click();
    await expect(page.locator('#auth-logged-in')).toBeVisible({ timeout: 20000 });
    await popup.waitForLoadState('domcontentloaded');
    await page.locator('#sidebar-scrim').click({ force: true });

    await expect(page.locator('#demo-task')).toBeVisible({ timeout: 20000 });
    await expect(page.locator('#demo-task')).toContainText('Try a local agent demo');
    await page.locator('#run-demo-task').click();

    await expect(page.locator('.bubble.user .body').last()).toContainText('Run local demo');
    await expect
      .poll(async () => ((await page.locator('.bubble.assistant .body').last().textContent()) || '').trim(), { timeout: 15000 })
      .toMatch(/Plan ready|Starting|Completed|action_items|finished/i);

    await expect
      .poll(async () => page.locator('#workspace-path').inputValue(), { timeout: 15000 })
      .not.toBe('');
    const workspacePath = await page.locator('#workspace-path').inputValue();
    const outputPath = path.join(workspacePath, 'action_items.md');

    await expect.poll(() => fs.existsSync(outputPath), { timeout: 30000 }).toBeTruthy();
    const output = fs.readFileSync(outputPath, 'utf8');
    expect(output).toContain('# Action Items');
    expect(output).toContain('Tighten checkout copy before release');
    expect(output).toContain('Source: demo_input.md');
    await expect(page.locator('#demo-task-status')).toContainText('Demo complete', { timeout: 30000 });
  });

  test('replies naturally in chat and completes a real development mission', async ({ page }) => {
    test.skip(!process.env.FORGE_TEST_NVIDIA_KEY, 'FORGE_TEST_NVIDIA_KEY is required for live provider verification.');

    const workspace = path.resolve(__dirname, '..', '..', '.forge_artifacts', `desktop_ui_live_test_spec_${Date.now()}`);
    fs.mkdirSync(workspace, { recursive: true });

    await page.goto('/');

    const popupPromise = page.waitForEvent('popup');
    await page.locator('#auth-gate-google').click();
    const popup = await popupPromise;
    await expect.poll(() => popup.url(), { timeout: 10000 }).not.toBe('about:blank');
    expect(popup.url()).toContain('device_code=');
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
