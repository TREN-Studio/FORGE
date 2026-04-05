const { test, expect } = require('@playwright/test');

test.describe.serial('FORGE public portal', () => {
  test('registers a normal user and stores a provider key', async ({ page }) => {
    await page.goto('/');

    await page.getByTestId('display-name').fill('Portal User');
    await page.getByTestId('email-input').fill('portal-user@example.com');
    await page.getByTestId('password-input').fill('StrongPass123!');
    await page.getByTestId('register-button').click();

    await expect(page.locator('#viewer-email')).toContainText('portal-user@example.com');
    await expect(page.locator('#account-role-pill')).toContainText('User');

    await page.getByTestId('provider-select').selectOption('nvidia');
    await page.getByTestId('provider-api-key').fill('abcd1234efgh5678');
    await page.getByTestId('save-provider-button').click();

    await expect(page.locator('#provider-status')).toContainText('Provider set saved.');
    await expect(page.locator('#provider-list')).toContainText('nvidia');
    await expect(page.locator('#provider-list')).toContainText('abcd');
    await expect(page.locator('#provider-list')).toContainText('5678');
    await expect(page.locator('#admin-panel')).toHaveClass(/hidden/);
  });

  test('registers the manager and exposes the admin panel', async ({ page }) => {
    await page.goto('/');

    await page.getByTestId('display-name').fill('Manager');
    await page.getByTestId('email-input').fill('larbilife@gmail.com');
    await page.getByTestId('password-input').fill('StrongPass123!');
    await page.getByTestId('register-button').click();

    await expect(page.locator('#account-role-pill')).toContainText('Manager');
    await expect(page.locator('#manager-gate')).toContainText('Open');
    await expect(page.locator('#admin-panel')).not.toHaveClass(/hidden/);
    await expect(page.locator('#admin-users-count')).toContainText('2');
    await expect(page.locator('#admin-users-list')).toContainText('portal-user@example.com');
    await expect(page.locator('#admin-users-list')).toContainText('larbilife@gmail.com');
  });
});
