import { expect, test } from '@playwright/test';

test('workspace loads data sources and opens token monitoring', async ({ page }) => {
  await page.goto('/');
  const sourcesResponse = page.waitForResponse(
    response => new URL(response.url()).pathname === '/api/v1/data-sources',
  );
  await page.getByTestId('open-text2sql').click();
  const sources = await sourcesResponse;
  expect(sources.status()).toBe(200);
  expect((await sources.json()).data.length).toBeGreaterThan(0);
  await expect(page.locator('textarea')).toBeVisible();
  await expect(page.getByTestId('workspace-tab')).toBeVisible();

  await page.getByTestId('token-monitor-tab').click();
  await expect(page.getByTestId('token-monitor-view')).toBeVisible();
});
