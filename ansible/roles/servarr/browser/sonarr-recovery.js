// Sonarr recovery canaries through HTTPS. Use ?drill=create|fresh|verify and the
// existing drill-owner credential. Recovery comparisons stay in private scratch.
module.exports = async ({ page, step, url, secrets }) => {
  const fs = require('fs');
  const target = new URL(url);
  const base = target.origin;
  const mode = target.searchParams.get('drill') || 'verify';
  const scope = target.searchParams.get('scope');
  if (scope && !/^[a-zA-Z0-9-]+$/.test(scope)) throw new Error('Invalid drill scope');
  const label = `p2-canary-sonarr-${scope || 'docker-lxc'}`;
  const saved = `/var/lib/rundeck/scratch/${scope || 'issue649'}/sonarr-canaries.json`;
  const vault = JSON.parse(process.env.HOMELABINFRA_VAULT_JSON || '{}');
  const key = vault.media?.sonarr?.api_key;
  const username = secrets.username || vault.sso?.admin_user;
  const password = secrets.password || vault.sso?.admin_password;
  const api = async (path, method = 'get', data) => {
    const response = await page.request[method](`${base}/api/v3/${path}`, {
      headers: { 'X-Api-Key': key }, ...(data === undefined ? {} : { data })
    });
    if (!response.ok()) throw new Error(`${path}: HTTP ${response.status()}`);
    return response.json();
  };
  await step('HTTPS and canonical owner login', async () => {
    if (!['create', 'fresh', 'verify'].includes(mode)) throw new Error('Invalid drill mode');
    if (!username || !password || !key) throw new Error(`Canonical credentials missing: username=${!!username}, password=${!!password}, apiKey=${!!key}`);
    await page.goto(base);
    await page.locator('input[name="uidField"]').waitFor({ timeout: 60000 });
    await page.locator('input[name="uidField"]').fill(username);
    await page.getByRole('button', { name: /log in|continue/i }).click();
    await page.locator('input[name="password"]').click();
    await page.locator('input[name="password"]').pressSequentially(password, { delay: 50 });
    await page.getByRole('button', { name: /log in|continue/i }).click();
    await page.waitForURL(`${base}/**`, { timeout: 60000 });
    await page.getByRole('link', { name: 'Series', exact: true }).first().waitFor({ timeout: 60000 });
  });
  await step('Series and media-management recovery canaries', async () => {
    let allSeries = await api('series');
    let tags = await api('tag');
    let settings = await api('config/mediamanagement');
    if (mode === 'fresh') {
      if (allSeries.some(m => m.tvdbId === 73244) || tags.some(t => t.label === label) ||
          settings.recycleBinCleanupDays === 17) throw new Error('Fresh instance contains a canary');
      return;
    }
    if (mode === 'create') {
      let tag = tags.find(t => t.label === label);
      if (!tag) tag = await api('tag', 'post', { label });
      if (!allSeries.some(m => m.tvdbId === 73244)) {
        const [series] = await api('series/lookup?term=tvdb:73244');
        const [profile] = await api('qualityprofile');
        const [root] = await api('rootfolder');
        if (!series || !profile || !root) throw new Error('Series lookup, quality profile or root folder missing');
        await api('series', 'post', { ...series, qualityProfileId: profile.id,
          rootFolderPath: root.path, monitored: false, tags: [tag.id],
          addOptions: { searchForMissingEpisodes: false } });
      }
      await api('config/mediamanagement', 'put', { ...settings, recycleBinCleanupDays: 17 });
      allSeries = await api('series'); tags = await api('tag');
      settings = await api('config/mediamanagement');
    }
    const series = allSeries.find(m => m.tvdbId === 73244);
    const state = { series: series && { id: series.id, tvdbId: series.tvdbId, title: series.title,
      path: series.path, monitored: series.monitored, qualityProfileId: series.qualityProfileId,
      tags: series.tags, seasons: series.seasons }, tags: tags.filter(t => t.label === label), settings, key };
    if (!series || series.monitored || state.tags.length !== 1 ||
        !series.tags.includes(state.tags[0].id) || settings.recycleBinCleanupDays !== 17)
      throw new Error('Series, tag or media-management canary differs');
    if (mode === 'create') {
      fs.mkdirSync(require('path').dirname(saved), { recursive: true, mode: 0o700 });
      fs.writeFileSync(saved, JSON.stringify(state), { mode: 0o600 });
    } else if (JSON.stringify(state) !== fs.readFileSync(saved, 'utf8')) {
      throw new Error('Canaries or API key differ from the source backup');
    }
  });
};
