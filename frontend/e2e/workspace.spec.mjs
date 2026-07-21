import { expect, test } from '@playwright/test';

test('workspace loads data sources and opens token monitoring', async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('open-text2sql').click();
  await expect(page.locator('textarea')).toBeVisible();
  await expect(page.getByTestId('workspace-tab')).toBeVisible();

  await page.getByTestId('token-monitor-tab').click();
  await expect(page.getByTestId('token-monitor-view')).toBeVisible();
});
