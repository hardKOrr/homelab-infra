// Stateless drill canaries from #417: instance name and a working JSON search.
// Run: tools/lab-browser searxng <url> ansible/roles/searxng/browser/smoke.js
module.exports = async ({ page, step, url }) => {
  // SearXNG rejects HeadlessChrome and requests without browser language headers.
  await page.setExtraHTTPHeaders({
    'User-Agent': (await page.evaluate(() => navigator.userAgent)).replace('HeadlessChrome', 'Chrome'),
    'Accept-Language': 'en-US,en;q=0.9',
  });
  await step('SearXNG search page renders', async () => {
    await page.goto(url);
    await page.locator('input[name="q"]').waitFor();
    if (!(await page.title()).includes('SearXNG')) {
      throw new Error('configured instance name is missing from the page title');
    }
  });

  await step('JSON search returns results', async () => {
    // Navigate so the limiter receives the browser's document request headers.
    const response = await page.goto(`${url}/search?q=ansible&format=json`);
    if (!response.ok()) throw new Error(`search returned HTTP ${response.status()}`);
    const count = (await response.json()).results.length;
    if (count < 1) throw new Error('search returned no results');
    console.log(`JSON search: ${count} results`);
  });
};
