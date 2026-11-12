// Recovery drill: ?drill=create seeds a draft and timezone; ?drill=absent checks
// a fresh install. Default verifies the exact restored values in Mixpost Lite.
// Run with the canonical owner item (mixpost-drill-owner) as lab-browser's instance.
module.exports = async ({ page, step, url, secrets }) => {
  const target = new URL(url);
  const mode = target.searchParams.get('drill') || 'verify';
  const base = target.origin;
  const canary = 'p2-canary-mixpost-676: exact Docker recovery draft; no social account connected.';
  const timezone = 'America/Denver';
  const check = (value, message) => { if (!value) throw new Error(message); };
  const props = () => page.locator('#app').getAttribute('data-page').then(JSON.parse).then(p => p.props);

  await step('HTTPS and canonical owner login', async () => {
    const response = await page.goto(`${base}/mixpost/login`);
    check(response.ok(), 'HTTPS login page failed');
    await page.locator('input[type=email]').fill(secrets.email || secrets.username || secrets.admin_user);
    await page.locator('input[type=password]').fill(secrets.password || secrets.admin_password);
    await Promise.all([
      page.waitForURL(u => !u.pathname.endsWith('/login')),
      page.locator('button[type=submit]').click()
    ]);
  });

  await step(mode === 'create' ? 'Create draft and timezone canaries' : 'Read exact recovery canaries', async () => {
    await page.goto(`${base}/mixpost/posts`);
    if (mode === 'create' && !(await page.getByText(canary, { exact: false }).count())) {
      await page.goto(`${base}/mixpost/posts/create`);
      const editor = page.locator('[contenteditable=true]').first();
      await editor.fill(canary);
      await page.waitForURL(/\/posts\/(?!create)[^/]+$/);
      await page.getByText('Saved', { exact: true }).waitFor();
    }
    await page.goto(`${base}/mixpost/posts`);
    const matches = page.getByText(canary, { exact: false });
    if (mode === 'absent') {
      check(await matches.count() === 0, 'Fresh instance contains draft canary');
    } else {
      check(await matches.count() === 1, 'Expected exactly one draft canary');
      const listed = (await props()).posts.data.find(p =>
        p.versions.some(v => v.content.some(c => c.body.includes(canary))));
      check(listed, 'Draft is missing from the post list');
      await page.goto(`${base}/mixpost/posts/${listed.id}`);
      const post = (await props()).post;
      check(post && String(post.status).toLowerCase() === 'draft', 'Canary is not a draft');
      const bodies = post.versions.flatMap(v => v.content.map(c => c.body));
      check(bodies.length === 1 && bodies[0] === `<div>${canary}</div>`, 'Restored draft body differs');
    }
    await page.goto(`${base}/mixpost/settings`);
    const timezoneSelect = page.locator('select').filter({ has: page.locator('option[value="America/Denver"]') });
    if (mode === 'create') {
      await page.locator('input[type=email]').fill(secrets.email || secrets.username || secrets.admin_user);
      await timezoneSelect.selectOption(timezone);
      await Promise.all([
        page.waitForResponse(r => r.request().method() === 'PUT' && r.url().includes('/settings')),
        page.getByRole('button', { name: 'Save settings', exact: true }).click()
      ]);
      await page.getByText('Settings have been saved', { exact: false }).waitFor();
      await page.reload();
    }
    check((await timezoneSelect.inputValue() === timezone) === (mode !== 'absent'),
      mode === 'absent' ? 'Fresh instance contains timezone canary' : 'Restored timezone differs');
  });
};
