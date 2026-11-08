// Radarr recovery canaries through HTTPS. Use ?drill=create|fresh|verify and the
// existing drill-owner credential. Recovery comparisons stay in private scratch.
module.exports = async ({ page, step, url, secrets }) => {
  const fs = require('fs');
  const target = new URL(url);
  const base = target.origin;
  const mode = target.searchParams.get('drill') || 'verify';
  const label = 'p2-canary-radarr-docker-lxc';
  const saved = '/var/lib/rundeck/scratch/issue640/radarr-canaries.json';
  const vault = JSON.parse(process.env.HOMELABINFRA_VAULT_JSON || '{}');
  const key = vault.media?.radarr?.api_key;
  const { username, password } = secrets;
  const api = async (path, method = 'get', data) => {
    const response = await page.request[method](`${base}/api/v3/${path}`, {
      headers: { 'X-Api-Key': key }, ...(data === undefined ? {} : { data })
    });
    if (!response.ok()) throw new Error(`${path}: HTTP ${response.status()}`);
    return response.json();
  };
  await step('HTTPS and canonical owner login', async () => {
    if (!['create', 'fresh', 'verify'].includes(mode)) throw new Error('Invalid drill mode');
    if (!username || !password || !key) throw new Error('Canonical credentials missing');
    await page.goto(base);
    await page.locator('input[name="uidField"]').waitFor({ timeout: 60000 });
    await page.locator('input[name="uidField"]').fill(username);
    await page.getByRole('button', { name: /log in|continue/i }).click();
    await page.locator('input[name="password"]').click();
    await page.locator('input[name="password"]').pressSequentially(password, { delay: 50 });
    await page.getByRole('button', { name: /log in|continue/i }).click();
    await page.waitForURL(`${base}/**`, { timeout: 60000 });
    await page.getByRole('link', { name: 'Movies', exact: true }).first().waitFor({ timeout: 60000 });
  });
  await step('Movie and media-management recovery canaries', async () => {
    let movies = await api('movie');
    let tags = await api('tag');
    let settings = await api('config/mediamanagement');
    if (mode === 'fresh') {
      if (movies.some(m => m.tmdbId === 603) || tags.some(t => t.label === label) ||
          settings.recycleBinCleanupDays === 17) throw new Error('Fresh instance contains a canary');
      return;
    }
    if (mode === 'create') {
      let tag = tags.find(t => t.label === label);
      if (!tag) tag = await api('tag', 'post', { label });
      if (!movies.some(m => m.tmdbId === 603)) {
        const movie = await api('movie/lookup/tmdb?tmdbId=603');
        const [profile] = await api('qualityprofile');
        const [root] = await api('rootfolder');
        if (!movie || !profile || !root) throw new Error('Movie lookup, quality profile or root folder missing');
        await api('movie', 'post', { ...movie, qualityProfileId: profile.id,
          rootFolderPath: root.path, monitored: false, tags: [tag.id],
          addOptions: { searchForMovie: false } });
      }
      await api('config/mediamanagement', 'put', { ...settings, recycleBinCleanupDays: 17 });
      movies = await api('movie'); tags = await api('tag');
      settings = await api('config/mediamanagement');
    }
    const movie = movies.find(m => m.tmdbId === 603);
    const state = { movie: movie && { id: movie.id, tmdbId: movie.tmdbId, title: movie.title,
      path: movie.path, monitored: movie.monitored, qualityProfileId: movie.qualityProfileId,
      tags: movie.tags }, tags: tags.filter(t => t.label === label), settings, key };
    if (!movie || movie.monitored || state.tags.length !== 1 ||
        !movie.tags.includes(state.tags[0].id) || settings.recycleBinCleanupDays !== 17)
      throw new Error('Movie, tag or media-management canary differs');
    if (mode === 'create') {
      fs.mkdirSync(require('path').dirname(saved), { recursive: true, mode: 0o700 });
      fs.writeFileSync(saved, JSON.stringify(state), { mode: 0o600 });
    } else if (JSON.stringify(state) !== fs.readFileSync(saved, 'utf8')) {
      throw new Error('Canaries or API key differ from the source backup');
    }
  });
};
