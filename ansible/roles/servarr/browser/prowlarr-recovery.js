// Prowlarr drill through its HTTPS route. Use the existing drill-owner credential as
// the browser instance. ?drill=create|fresh|verify (default verify). The comparison
// file stays in private runner scratch; the API key comes from the runtime vault.
module.exports = async ({ page, step, url, secrets }) => {
  const fs = require('fs');
  const target = new URL(url);
  const base = target.origin;
  const mode = target.searchParams.get('drill') || 'verify';
  const label = 'p2-canary-prowlarr-kubernetes';
  const saved = '/var/lib/rundeck/scratch/prowlarr-kubernetes-canaries.json';
  const vault = JSON.parse(process.env.HOMELABINFRA_VAULT_JSON || '{}');
  const key = vault.media?.prowlarr?.api_key;
  const api = async (path, method = 'get', data) => {
    const response = await page.request[method](`${base}/api/v1/${path}`, {
      headers: { 'X-Api-Key': key }, ...(data === undefined ? {} : { data })
    });
    if (!response.ok()) {
      const errors = await response.json().catch(() => []);
      const detail = Array.isArray(errors) ? errors.map(e => e.errorMessage).join('; ') : '';
      throw new Error(`${path}: HTTP ${response.status()} ${detail}`);
    }
    return response.json();
  };
  await step('HTTPS and existing Authentik owner login', async () => {
    if (!['create', 'fresh', 'verify'].includes(mode)) throw new Error('Invalid drill mode');
    if (!secrets.username || !secrets.password || !key) throw new Error('Canonical credentials missing');
    await page.goto(base);
    await page.locator('input[name="uidField"]').waitFor({ timeout: 60000 });
    await page.locator('input[name="uidField"]').fill(secrets.username);
    await page.getByRole('button', { name: /log in|continue/i }).click();
    await page.locator('input[name="password"]').click();
    await page.locator('input[name="password"]').pressSequentially(secrets.password, { delay: 50 });
    await page.getByRole('button', { name: /log in|continue/i }).click();
    await page.waitForURL(`${base}/**`, { timeout: 60000 });
    await page.getByRole('link', { name: 'Indexers', exact: true }).waitFor({ timeout: 60000 });
  });
  await step('Tag, account-free indexer and UI canaries', async () => {
    let tags = await api('tag');
    let indexers = await api('indexer');
    let ui = await api('config/ui');
    if (mode === 'fresh') {
      if (tags.some(t => t.label === label) || indexers.some(i => i.name === label) || ui.firstDayOfWeek === 2)
        throw new Error('Fresh instance contains a canary');
      return;
    }
    if (mode === 'create') {
      if (!tags.some(t => t.label === label)) await api('tag', 'post', { label });
      if (!indexers.some(i => i.name === label)) {
        const schema = (await api('indexer/schema')).find(i => i.name === 'Nyaa.si');
        if (!schema) throw new Error('Account-free Nyaa.si definition missing');
        const [profile] = await api('appprofile');
        if (!profile?.id) throw new Error('Prowlarr app profile missing');
        await api('indexer', 'post', { ...schema, name: label, enable: false, appProfileId: profile.id });
      }
      await api('config/ui', 'put', { ...ui, firstDayOfWeek: 2 });
      tags = await api('tag'); indexers = await api('indexer'); ui = await api('config/ui');
    }
    const state = { tags: tags.filter(t => t.label === label),
      indexers: indexers.filter(i => i.name === label), ui, key };
    if (state.tags.length !== 1 || state.indexers.length !== 1 || ui.firstDayOfWeek !== 2)
      throw new Error('Canary count or UI setting differs');
    if (mode === 'create') {
      fs.mkdirSync(require('path').dirname(saved), { recursive: true, mode: 0o700 });
      fs.writeFileSync(saved, JSON.stringify(state), { mode: 0o600 });
    } else if (JSON.stringify(state) !== fs.readFileSync(saved, 'utf8')) {
      throw new Error('Canaries or API key differ from the source backup');
    }
  });
};
