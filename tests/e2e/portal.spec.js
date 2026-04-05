const { test, expect } = require('@playwright/test');

function extractDebugToken(text) {
  const match = String(text || '').match(/debug_token=([A-Za-z0-9_\-]+)/);
  if (!match) {
    throw new Error(`No debug token found in status: ${text}`);
  }
  return match[1];
}

test.describe.serial('FORGE public portal', () => {
  test('registers a user, stores a provider key, and verifies the email', async ({ page }) => {
    await page.goto('/');

    await page.getByTestId('display-name').fill('Portal User');
    await page.getByTestId('email-input').fill('portal-user@example.com');
    await page.getByTestId('password-input').fill('StrongPass123!');
    await page.getByTestId('register-button').click();

    await expect(page.locator('#viewer-email')).toContainText('portal-user@example.com');
    await expect(page.locator('#email-verification-state')).toContainText('Pending');

    await page.getByTestId('provider-select').selectOption('nvidia');
    await page.getByTestId('provider-api-key').fill('abcd1234efgh5678');
    await page.getByTestId('save-provider-button').click();

    await expect(page.locator('#provider-status')).toContainText('Provider set saved.');
    await expect(page.locator('#provider-list')).toContainText('nvidia');
    await expect(page.locator('#provider-list')).toContainText('ready');

    await page.getByTestId('send-verification-button').click();
    const verificationText = await page.locator('#account-status').textContent();
    const verificationToken = extractDebugToken(verificationText);
    await page.getByTestId('token-input').fill(verificationToken);
    await page.getByTestId('verify-email-button').click();

    await expect(page.locator('#account-status')).toContainText('Email verified successfully.');
    await expect(page.locator('#email-verification-state')).toContainText('Verified');
  });

  test('requests a password reset and logs back in with the new password', async ({ page }) => {
    await page.goto('/');

    await page.getByTestId('email-input').fill('portal-user@example.com');
    await page.getByTestId('password-input').fill('StrongPass123!');
    await page.getByTestId('login-button').click();
    await expect(page.locator('#viewer-email')).toContainText('portal-user@example.com');

    await page.getByTestId('logout-button').click();
    await page.getByTestId('email-input').fill('portal-user@example.com');
    const resetResponsePromise = page.waitForResponse((response) =>
      response.url().includes('/auth/request-password-reset') && response.request().method() === 'POST'
    );
    await page.getByTestId('request-reset-button').click();
    const resetResponse = await resetResponsePromise;
    const resetPayload = await resetResponse.json();
    const resetToken = resetPayload.reset.debug_token;
    await page.getByTestId('token-input').fill(resetToken);
    await page.getByTestId('new-password-input').fill('StrongerPass456!');
    await page.getByTestId('reset-password-button').click();

    await expect(page.locator('#account-status')).toContainText('Password reset complete.');
    await expect(page.locator('#viewer-email')).toContainText('portal-user@example.com');

    await page.getByTestId('logout-button').click();
    await page.getByTestId('email-input').fill('portal-user@example.com');
    await page.getByTestId('password-input').fill('StrongerPass456!');
    await page.getByTestId('login-button').click();
    await expect(page.locator('#viewer-email')).toContainText('portal-user@example.com');
  });

  test('registers the manager and exposes approvals, missions, and key health', async ({ page }) => {
    await page.goto('/');

    await page.getByTestId('email-input').fill('portal-user@example.com');
    await page.getByTestId('password-input').fill('StrongerPass456!');
    await page.getByTestId('login-button').click();
    await expect(page.locator('#viewer-email')).toContainText('portal-user@example.com');

    await page.evaluate(async () => {
      await fetch('./api/index.php/desktop/missions/sync', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mission_id: 'mission-ui-test',
          objective: 'Sync a desktop mission into the portal',
          status: 'finished',
          validation_status: 'finished',
          summary: 'Desktop mission sync succeeded.',
          workspace_root: 'C:/workspace/sample',
          source: 'desktop',
        }),
      });
      await fetch('./api/index.php/desktop/approvals/sync', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          approvals: [
            {
              approval_id: 'approval-ui-test',
              mission_id: 'mission-ui-test',
              step_id: 'step_publish',
              approval_class: 'external_publish',
              status: 'pending',
              summary: 'Publish report externally',
              request_excerpt: 'Send verified report to external system',
              source: 'desktop',
            },
          ],
        }),
      });
    });

    await page.getByTestId('logout-button').click();

    await page.getByTestId('display-name').fill('Manager');
    await page.getByTestId('email-input').fill('larbilife@gmail.com');
    await page.getByTestId('password-input').fill('StrongPass123!');
    await page.getByTestId('register-button').click();

    await expect(page.locator('#account-role-pill')).toContainText('Manager');
    await expect(page.locator('#manager-gate')).toContainText('Open');
    await expect(page.locator('#admin-panel')).not.toHaveClass(/hidden/);
    await expect(page.locator('#admin-users-list')).toContainText('portal-user@example.com');
    await expect(page.locator('#admin-key-health-list')).toContainText('portal-user@example.com');
    await expect(page.locator('#admin-approvals-list')).toContainText('external_publish');
    await expect(page.locator('#admin-missions-list')).toContainText('Sync a desktop mission into the portal');
    await expect(page.locator('#admin-outbox-list')).toContainText('portal-user@example.com');
  });
});
