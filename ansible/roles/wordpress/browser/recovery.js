// Recovery drill: ?drill=create seeds canaries, ?drill=absent checks a fresh install;
// the default verifies exact restored values. Credentials come from lab-browser.
module.exports = async ({ page, context, step, url, secrets }) => {
  const parsed = new URL(url);
  const mode = parsed.searchParams.get('drill') || 'verify';
  const base = parsed.origin;
  const slug = 'p2-canary-wordpress-2026-11-10-lxc';
  const title = 'WordPress LXC recovery canary 660';
  const content = '<p>Exact WordPress Docker on LXC recovery canary.</p>';
  const tagline = 'WordPress LXC recovery configuration canary 660';
  const mediaSlug = `${slug}-media`;
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j3ioAAAAASUVORK5CYII=', 'base64');
  let nonce;
  let password;
  let passwordId;
  let userId;
  let auth;
  let rest;
  const check = (value, message) => { if (!value) throw new Error(message); };
  const apiUrl = (path) => {
    const [route, query] = path.split('?');
    return `${base}/?rest_route=/wp/v2/${route}${query ? `&${query}` : ''}`;
  };
  const api = async (path, options = {}) => {
    const response = await rest.fetch(apiUrl(path), {
      ...options, headers: { Authorization: auth, ...options.headers }
    });
    check(response.ok(), `REST ${path.split('?')[0]} returned ${response.status()}`);
    return response.json();
  };
  await step('HTTPS and canonical administrator login', async () => {
    const response = await page.goto(`${base}/wp-login.php`);
    check(response.ok(), 'HTTPS login page failed');
    await page.locator('#user_login').fill(secrets.admin_user || 'admin');
    await page.locator('#user_pass').fill(secrets.admin_password);
    await Promise.all([page.waitForURL('**/wp-admin/**'), page.locator('#wp-submit').click()]);
    await page.goto(`${base}/wp-admin/post-new.php`);
    nonce = await page.evaluate(() => window.wpApiSettings && window.wpApiSettings.nonce);
    check(nonce, 'Missing authenticated REST nonce');
    const me = await context.request.get(apiUrl('users/me'), { headers: { 'X-WP-Nonce': nonce } });
    check(me.ok(), 'Administrator identity check failed');
    userId = (await me.json()).id;
    const responsePassword = await context.request.post(apiUrl(`users/${userId}/application-passwords`), {
      headers: { 'X-WP-Nonce': nonce }, data: { name: `LXC recovery drill ${Date.now()}` }
    });
    check(responsePassword.ok(), 'Application password creation failed');
    const item = await responsePassword.json();
    password = item.password; passwordId = item.uuid;
    auth = `Basic ${Buffer.from(`${secrets.admin_user || 'admin'}:${password}`).toString('base64')}`;
    // A separate request context proves application-password auth without browser cookies.
    rest = await require('playwright-core').request.newContext({ ignoreHTTPSErrors: true });
  });
  try {
    await step(mode === 'create' ? 'Create REST post, uploaded media and tagline' : 'Read REST canaries', async () => {
      if (mode === 'create') {
        const existing = await api(`posts?slug=${slug}&context=edit`);
        if (!existing.length) await api('posts', { method: 'POST', data: { slug, title, content, status: 'publish' } });
        const media = await api(`media?slug=${mediaSlug}&context=edit`);
        if (!media.length) {
          const uploaded = await api('media', { method: 'POST', data: png,
            headers: { 'Content-Type': 'image/png', 'Content-Disposition': `attachment; filename="${mediaSlug}.png"` } });
          await api(`media/${uploaded.id}`, { method: 'POST', data: { slug: mediaSlug } });
        }
        const post = (await api(`posts?slug=${slug}&context=edit`))[0];
        const image = (await api(`media?slug=${mediaSlug}&context=edit`))[0];
        await api(`media/${image.id}`, { method: 'POST', data: { post: post.id } });
        await api(`posts/${post.id}`, { method: 'POST', data: { featured_media: image.id } });
        await api('settings', { method: 'POST', data: { description: tagline } });
      }
      const posts = await api(`posts?slug=${slug}&context=edit`);
      const media = await api(`media?slug=${mediaSlug}&context=edit`);
      const settings = await api('settings');
      if (mode === 'absent') {
        check(posts.length === 0 && media.length === 0 && settings.description !== tagline, 'Fresh instance contains canaries');
      } else {
        check(posts.length === 1 && posts[0].title.raw === title && posts[0].content.raw === content && posts[0].status === 'publish', 'Post differs from recovery canary');
        check(media.length === 1 && settings.description === tagline, 'Media or tagline differs from recovery canary');
        check(media[0].post === posts[0].id && posts[0].featured_media === media[0].id, 'Post media attachment differs');
        const response = await context.request.get(media[0].source_url);
        check(response.ok() && (await response.body()).equals(png), 'Uploaded media bytes differ');
        const publicPost = await context.request.get(posts[0].link);
        check(publicPost.ok() && (await publicPost.text()).includes(title), 'Published post is unavailable');
      }
    });
  } finally {
    if (rest) await rest.dispose();
    if (passwordId) await context.request.delete(apiUrl(`users/${userId}/application-passwords/${passwordId}`), { headers: { 'X-WP-Nonce': nonce } });
  }
};
