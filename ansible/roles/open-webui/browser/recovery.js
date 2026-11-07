// Recovery drill: use the drill-owner Vaultwarden item as the browser instance.
// URL query: ?drill=create|fresh|verify (default verify). Creates a prompt and disables
// signup through the app API; fresh checks absence before onboarding the same owner.
module.exports = async ({ page, step, url, secrets }) => {
  const target = new URL(url);
  const mode = target.searchParams.get('drill') || 'verify';
  const base = target.origin;
  const email = secrets.email || secrets.admin_email || secrets.username;
  const password = secrets.password || secrets.admin_password;
  const canary = { command: '/p2-canary-open-webui-20261107',
    name: 'p2-canary-open-webui-20261107', content: 'Docker LXC recovery canary 627: exact content.' };
  let headers;
  const api = async (path, data) => {
    const response = await page.request[data === undefined ? 'get' : 'post'](`${base}${path}`,
      { headers, ...(data === undefined ? {} : { data }) });
    if (!response.ok()) throw new Error(`${path}: HTTP ${response.status()}`);
    return response.json();
  };
  await step('HTTPS and owner login', async () => {
    if (!email || !password) throw new Error(`owner credential fields missing: ${Object.keys(secrets).join(',')}`);
    await page.goto(`${base}/auth`);
    let response = await page.request.post(`${base}/api/v1/auths/signin`, { data: { email, password } });
    if (!response.ok() && ['create', 'fresh'].includes(mode)) {
      response = await page.request.post(`${base}/api/v1/auths/signup`,
        { data: { email, password, name: 'Recovery drill owner' } });
    }
    if (!response.ok()) throw new Error(`owner login: HTTP ${response.status()}`);
    const owner = await response.json();
    if (owner.role !== 'admin') throw new Error('owner is not admin');
    headers = { Authorization: `Bearer ${owner.token}` };
    await page.evaluate(token => localStorage.setItem('token', token), owner.token);
    await page.goto(base);
    await page.locator('body').waitFor();
    if (new URL(page.url()).pathname === '/auth') throw new Error('login did not reach the app');
  });
  await step('prompt and signup setting', async () => {
    let config = await api('/api/v1/auths/admin/config');
    let prompts = await api('/api/v1/prompts/');
    if (mode === 'fresh') {
      if (prompts.some(p => p.command === canary.command) || config.ENABLE_SIGNUP !== true)
        throw new Error('fresh target contains a canary');
      return;
    }
    if (mode === 'create') {
      if (!prompts.some(p => p.command === canary.command)) await api('/api/v1/prompts/create', canary);
      await api('/api/v1/auths/admin/config', { ...config, ENABLE_SIGNUP: false });
      prompts = await api('/api/v1/prompts/');
      config = await api('/api/v1/auths/admin/config');
    }
    const matches = prompts.filter(p => p.command === canary.command);
    if (matches.length !== 1 || matches[0].name !== canary.name || matches[0].content !== canary.content)
      throw new Error('prompt canary differs');
    if (config.ENABLE_SIGNUP !== false) throw new Error('signup canary differs');
  });
  await step('Ollama models reachable through Open WebUI', async () => {
    const models = await api('/ollama/api/tags');
    if (!Array.isArray(models.models) || !models.models.length) throw new Error('Ollama has no models');
  });
};
