// Recovery drill, as test(plane) #409 defined it: ?drill=create saves the canaries,
// ?drill=fresh checks a fresh install, the default verifies them, and &final=1 (the last
// verify) deletes them afterwards. &id= is the drill run's id (tools/lab-drill).
//   data           workspace p2-canary-<drill id>, project P2 Canary (CANARY) and work
//                  item p2-canary-plane-<drill id> with a generated text file attached
//                  (the attachment proves object storage)
//   configuration  the project's auto-archive after 3 months (a new project has none)
// Deploy provisions no Plane login, so the drill signs in as the lab-only owner kept in
// homelab-infra/apps/plane-drill-owner (email, password). An instance that is not set up
// yet gets that owner as its instance admin on create; a fresh install is not set up.
// The spec drives Plane's own session API, as its web app does.
module.exports = async ({ page, step, url, secrets }) => {
  const target = new URL(url);
  const requested = target.searchParams.get('drill') || 'verify';
  const mode = requested === 'fresh' ? 'absent' : requested;
  const final = target.searchParams.get('final') === '1';
  const base = target.origin;
  const id = (target.searchParams.get('id') || 'drill').toLowerCase().replace(/[^a-z0-9-]/g, '');
  const slug = `p2-canary-${id}`.slice(0, 48);
  const item = `p2-canary-plane-${id}`;
  const file = { name: 'p2-canary-plane.txt', mimeType: 'text/plain', buffer: Buffer.from(`${item}\n`) };
  const check = (value, message) => { if (!value) throw new Error(message); };
  let csrf = '';
  const call = async (method, path, data) => {
    const response = await page.request.fetch(`${base}${path}`, {
      method, data, headers: { 'X-CSRFToken': csrf, Referer: `${base}/` }, maxRedirects: 0
    });
    if (response.status() >= 400) {
      throw new Error(`${method} ${path} returned ${response.status()}: ${(await response.text()).slice(0, 200)}`);
    }
    return response.status() === 204 ? null : response.json().catch(() => null);
  };
  const form = async (path, fields) => {
    const token = (await (await page.request.get(`${base}/auth/get-csrf-token/`)).json()).csrf_token;
    const response = await page.request.post(`${base}${path}`, {
      form: { csrfmiddlewaretoken: token, ...fields }, headers: { Referer: `${base}/` }, maxRedirects: 0
    });
    const location = response.headers().location || '';
    check(!/error_code=/.test(location), `${path} refused: ${decodeURIComponent(location).slice(0, 200)}`);
    csrf = (await page.context().cookies(base)).find(c => c.name === 'csrftoken')?.value || token;
  };
  const instance = async () => (await (await page.request.get(`${base}/api/instances/`)).json()).instance;

  if (mode === 'absent') {
    await step('a fresh install is not set up, so it holds no canary', async () => {
      const setUp = (await instance()).is_setup_done;
      check(!setUp, 'a fresh install is already set up: data survived Remove');
    });
    return;
  }

  await step('owner signs in (create sets up an empty instance first)', async () => {
    if (mode === 'create' && !(await instance()).is_setup_done) {
      await form('/api/instances/admins/sign-up/', {
        email: secrets.email, password: secrets.password, first_name: 'Drill',
        last_name: 'Owner', company_name: 'homelab-infra lab', is_telemetry_enabled: 'False'
      });
    }
    await form('/auth/sign-in/', { email: secrets.email, password: secrets.password, next_path: '/' });
    check((await call('GET', '/api/users/me/')).email === secrets.email, 'signed in as someone else');
  });

  const find = async () => {
    const workspaces = await call('GET', '/api/users/me/workspaces/');
    if (!workspaces.some(w => w.slug === slug)) return {};
    const projects = await call('GET', `/api/workspaces/${slug}/projects/`);
    const project = projects.find(p => p.identifier === 'CANARY');
    if (!project) return { workspace: true };
    const full = await call('GET', `/api/workspaces/${slug}/projects/${project.id}/`);
    const issues = await call('GET', `/api/workspaces/${slug}/projects/${project.id}/issues/`);
    const list = Array.isArray(issues) ? issues : (issues.results || []);
    const issue = list.find(i => i.name === item);
    return { workspace: true, project: full, issue };
  };
  const attachments = (state) => call('GET',
    `/api/assets/v2/workspaces/${slug}/projects/${state.project.id}/issues/${state.issue.id}/attachments/`);

  if (mode === 'create') {
    await step('create the workspace, project, work item and attachment', async () => {
      let state = await find();
      if (!state.workspace) {
        await call('POST', '/api/workspaces/', { name: slug, slug, organization_size: 'Just myself' });
      }
      if (!state.project) {
        await call('POST', `/api/workspaces/${slug}/projects/`, { name: 'P2 Canary', identifier: 'CANARY', network: 2 });
        state = await find();
      }
      if (!state.issue) {
        await call('POST', `/api/workspaces/${slug}/projects/${state.project.id}/issues/`, { name: item });
        state = await find();
      }
      if ((await attachments(state)).length === 0) {
        const ticket = await call('POST',
          `/api/assets/v2/workspaces/${slug}/projects/${state.project.id}/issues/${state.issue.id}/attachments/`,
          { name: file.name, type: file.mimeType, size: file.buffer.length });
        const upload = ticket.upload_data;
        const stored = await page.request.post(upload.url, { multipart: { ...upload.fields, file } });
        check(stored.ok(), `object storage refused the upload: ${stored.status()} ${(await stored.text()).slice(0, 200)}`);
        await call('PATCH',
          `/api/assets/v2/workspaces/${slug}/projects/${state.project.id}/issues/${state.issue.id}/attachments/${ticket.asset_id}/`, {});
      }
    });

    await step('set the project to auto-archive after 3 months', async () => {
      const { project } = await find();
      if (project.archive_in !== 3) {
        await call('PATCH', `/api/workspaces/${slug}/projects/${project.id}/`, { archive_in: 3 });
      }
    });
  }

  await step('canaries exact', async () => {
    const state = await find();
    check(state.workspace && state.project && state.issue,
      `workspace ${!!state.workspace}, project ${!!state.project}, work item ${!!state.issue}`);
    check(state.project.archive_in === 3, `project auto-archive is ${state.project.archive_in}, not 3`);
    const files = await attachments(state);
    check(files.length === 1 && files[0].attributes.name === file.name,
      `work item has ${files.length} attachment(s): ${files.map(f => f.attributes.name).join(', ')}`);
    const download = await page.request.get(
      `${base}/api/assets/v2/workspaces/${slug}/projects/${state.project.id}/issues/${state.issue.id}/attachments/${files[0].id}/`);
    check(download.ok(), `attachment download returned ${download.status()}`);
    check((await download.body()).equals(file.buffer), 'the attachment came back with different content');
  });

  if (final) {
    await step('delete the canary workspace', async () => {
      await call('DELETE', `/api/workspaces/${slug}/`);
      check(!(await find()).workspace, 'the canary workspace is still listed');
    });
  }
};
