// Check the compiled web/admin frontends and their same-origin API routing.
// Run: tools/lab-browser <instance> <url> ansible/roles/plane/browser/smoke.js
module.exports = async ({ page, step, url }) => {
  await step('web sign-in or setup page renders', async () => {
    const response = await page.goto(url);
    if (!response.ok()) throw new Error(`Web returned ${response.status()}`);
    await page.locator('input[type="email"]').first()
      .or(page.getByRole('heading', { name: 'Welcome to Plane', exact: true })).first().waitFor();
  });

  await step('API responds through the published route', async () => {
    const response = await page.request.get(`${url}/api/instances/`);
    if (!response.ok()) throw new Error(`API returned ${response.status()}`);
    const data = await response.json();
    if (!data.instance || typeof data.instance !== 'object') {
      throw new Error('API did not return an instance');
    }
  });

  await step('admin sign-in page renders', async () => {
    const response = await page.goto(`${url}/god-mode/`);
    if (!response.ok()) throw new Error(`Admin returned ${response.status()}`);
    await page.locator('input[type="email"]').first().waitFor();
  });
};
