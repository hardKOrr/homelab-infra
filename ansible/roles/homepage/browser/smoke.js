// Check generated service groups and links on either Homepage hosting backend.
// Run: tools/lab-browser <instance> <url> ansible/roles/homepage/browser/smoke.js
module.exports = async ({ page, step, url }) => {
  let groups;
  await step('dashboard and generated services respond', async () => {
    const response = await page.goto(url);
    if (response.status() !== 200) throw new Error('dashboard did not return HTTP 200');
    const services = await page.request.get(`${url}/api/services`);
    if (!services.ok()) throw new Error('service configuration is unavailable');
    groups = await services.json();
    if (!Array.isArray(groups) || !groups.length) throw new Error('no service groups');
  });

  await step('every generated group and service link renders', async () => {
    let count = 0;
    for (const group of groups) {
      await page.getByText(group.name, { exact: true }).waitFor()
        .catch(() => { throw new Error(`group heading missing: ${group.name}`); });
      for (const service of group.services) {
        const link = page.locator(`a[href=${JSON.stringify(service.href)}]`)
          .filter({ hasText: service.name }).first();
        await link.waitFor()
          .catch(() => { throw new Error(`service link missing: ${service.name}`); });
        if (await link.getAttribute('href') !== service.href) {
          throw new Error('service link differs from generated configuration');
        }
        count += 1;
      }
    }
    if (!count) throw new Error('no service links');
    console.log(`Verified ${groups.length} groups and ${count} service links`);
  });
};
