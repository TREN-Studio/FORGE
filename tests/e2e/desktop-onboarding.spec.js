const { test, expect } = require('@playwright/test');

test.describe.serial('FORGE desktop onboarding', () => {
  test('completes Desktop first-run sign-in through the shared portal Google flow', async ({ page }) => {
    await page.goto('/');

    await expect(page.locator('#auth-logged-out')).toBeVisible();
    await expect(page.locator('#account-summary')).toContainText('Login required');

    const popupPromise = page.waitForEvent('popup');
    await page.locator('#google-login-button').click();
    const popup = await popupPromise;

    await expect(page.locator('#auth-logged-in')).toBeVisible({ timeout: 20000 });
    await expect(page.locator('#account-email')).toContainText('google-user@example.com');
    await expect(page.locator('#account-role')).toContainText('User');
    await expect(page.locator('#provider-select')).toBeVisible();
    await expect(page.locator('#workspace-path')).toBeVisible();
    await expect(page.locator('#auth-status')).toContainText('Desktop session is active.');
    await popup.waitForLoadState('domcontentloaded');
  });
});
