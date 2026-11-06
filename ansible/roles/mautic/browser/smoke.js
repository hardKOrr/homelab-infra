// Mautic smoke check: the admin signs in and the main pages render.
// Run: tools/lab-browser <instance> <url> ansible/roles/mautic/browser/smoke.js
// Uses the admin_user and admin_password fields of the instance's Vaultwarden item.
module.exports = async ({ page, step, url, secrets, text }) => {
  await step('login page renders', async () => {
    await page.goto(`${url}/s/login`);
    await page.locator('#username').waitFor();
  });

  await step('admin signs in', async () => {
    await page.fill('#username', secrets.admin_user);
    await page.fill('#password', secrets.admin_password);
    await Promise.all([page.waitForURL(/\/s\/dashboard/), page.click('button[type=submit]')]);
  });

  await step('dashboard renders', async () => {
    await page.getByRole('heading', { name: /dashboard/i }).first().waitFor();
  });

  await step('contacts page renders', async () => {
    await page.goto(`${url}/s/contacts`);
    await page.getByRole('heading', { name: /contacts/i }).first().waitFor();
    if (/error|exception/i.test(await text())) throw new Error('contacts page shows an error');
  });
};
