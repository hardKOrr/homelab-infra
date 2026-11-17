// Recovery drill, as test(bookstack) #414 defined it: ?drill=create saves the canaries,
// ?drill=fresh checks a fresh install, the default verifies them, and &final=1 (the last
// verify) deletes the book and resets the name afterwards. &id= is the drill run's id
// (tools/lab-drill).
//   data           book p2-canary-bookstack-<id> with page p2-canary-page holding a line of
//                  text, and a small PNG uploaded to the page as an attachment
//   configuration  the application name, set to the book's name (default BookStack)
// Signs in as the administrator Deploy records (admin_user, admin_password in the
// instance's item) and drives BookStack's own web routes with that session.
module.exports = async ({ page, step, url, secrets }) => {
  const target = new URL(url);
  const requested = target.searchParams.get('drill') || 'verify';
  const mode = requested === 'fresh' ? 'absent' : requested;
  const final = target.searchParams.get('final') === '1';
  const base = target.origin;
  const book = `p2-canary-bookstack-${(target.searchParams.get('id') || 'drill').toLowerCase()}`;
  const pageName = 'p2-canary-page';
  const line = `${book}: restored page text`;
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR42mP4dEALAASEAd0cBd44AAAAAElFTkSuQmCC', 'base64');
  const check = (value, message) => { if (!value) throw new Error(message); };
  let token = '';
  const post = async (path, form, multipart) => {
    const response = await page.request.post(`${base}${path}`, {
      form: multipart ? undefined : { _token: token, ...form }, multipart,
      headers: { 'X-CSRF-TOKEN': token, Referer: `${base}/` }, maxRedirects: 0 });
    check(response.status() < 400, `POST ${path} returned ${response.status()}: ${(await response.text()).slice(0, 200)}`);
    return response;
  };
  const appName = async () => {
    await page.goto(`${base}/settings/customization`);
    return page.locator('input[name="setting-app-name"]').inputValue();
  };
  const bookPage = async () => {
    const response = await page.goto(`${base}/books/${book}/page/${pageName}`);
    return response.status() === 200;
  };

  await step('administrator signs in', async () => {
    await page.goto(`${base}/login`);
    await page.fill('input[name=email]', secrets.admin_user);
    await page.fill('input[name=password]', secrets.admin_password);
    await page.getByRole('button', { name: 'Log In' }).click();
    await page.waitForURL(u => !u.pathname.startsWith('/login'));
    token = await page.locator('meta[name=token]').getAttribute('content');
  });

  if (mode === 'absent') {
    await step('a fresh install holds no canary', async () => {
      const response = await page.goto(`${base}/books/${book}`);
      const name = await appName();
      check(response.status() === 404 && name !== book,
        `fresh install answers ${response.status()} for the canary book, application name "${name}"`);
    });
    return;
  }

  if (mode === 'create') {
    await step('create the book, its page and the attachment', async () => {
      if ((await page.goto(`${base}/books/${book}`)).status() === 404) {
        await post('/books/', { name: book, description_html: '' });
      }
      if (!(await bookPage())) {
        await page.goto(`${base}/books/${book}/create-page`);
        const draft = page.url().match(/\/draft\/(\d+)/);
        check(draft, `creating a page landed on ${page.url()}`);
        await post(`/books/${book}/draft/${draft[1]}`, { name: pageName, html: `<p>${line}</p>`, summary: '' });
      }
      check(await bookPage(), 'the canary page does not open');
      if (await page.locator('a[href*="/attachments/"]', { hasText: 'p2-canary.png' }).count() === 0) {
        const pageId = (await page.content()).match(/option:page-display:page-id="(\d+)"/)[1];
        await post('/attachments/upload', null, {
          uploaded_to: pageId, file: { name: 'p2-canary.png', mimeType: 'image/png', buffer: png } });
      }
    });

    await step('set the application name', async () => {
      if (await appName() !== book) {
        await post('/settings/customization', { section: 'customization', 'setting-app-name': book });
      }
    });
  }

  await step('canaries exact', async () => {
    check(await bookPage(), 'the canary page does not open');
    check((await page.locator('.page-content').innerText()).includes(line), 'the page text differs');
    const links = page.locator('a[href*="/attachments/"]', { hasText: 'p2-canary.png' });
    check(await links.count() === 1, `the page lists ${await links.count()} canary attachment(s)`);
    const href = await links.first().getAttribute('href');
    const file = await page.request.get(new URL(href, base).toString());
    check(file.ok() && (await file.body()).equals(png), `the attachment came back ${file.status()} with different bytes`);
    const name = await appName();
    check(name === book, `the application name is "${name}"`);
    if (final) {
      await post('/settings/customization', { section: 'customization', 'setting-app-name': 'BookStack' });
      await page.goto(`${base}/books/${book}/delete`);
      await page.getByRole('button', { name: 'Confirm' }).click();
      await page.waitForURL(u => !u.pathname.endsWith('/delete'));
      check((await page.goto(`${base}/books/${book}`)).status() === 404, 'the canary book survived the delete');
    }
  });
};
