// Recovery drill, as test(mautic) #413 defined it: ?drill=create saves the canaries,
// ?drill=fresh (absent) checks a fresh install, and the default verifies them.
//   data           contact p2-canary-mautic-recovery@example.invalid and segment
//                  p2-canary-mautic-recovery
//   configuration  default timezone Pacific/Honolulu (a fresh install has UTC)
// Mautic's API is off by default, so the admin Deploy records (admin_user, admin_password
// in the instance's item) drives the UI.
module.exports = async ({ page, step, url, secrets, text }) => {
  const target = new URL(url);
  const requested = target.searchParams.get('drill') || 'verify';
  const mode = requested === 'fresh' ? 'absent' : requested;
  const base = target.origin;
  const canary = 'p2-canary-mautic-recovery';
  const email = `${canary}@example.invalid`;
  const timezone = 'Pacific/Honolulu';
  const check = (value, message) => { if (!value) throw new Error(message); };
  const countRows = async (path, search, needle) => {
    await page.goto(`${base}${path}?search=${encodeURIComponent(search)}`);
    await page.waitForLoadState('networkidle');
    return page.locator('table tbody tr').filter({ hasText: needle }).count();
  };
  const timezoneValue = async () => {
    await page.goto(`${base}/s/config/edit`);
    return page.locator('#config_coreconfig_default_timezone').inputValue();
  };

  await step('admin signs in', async () => {
    await page.goto(`${base}/s/login`);
    await page.fill('#username', secrets.admin_user);
    await page.fill('#password', secrets.admin_password);
    await Promise.all([page.waitForURL(/\/s\/dashboard/), page.click('button[type=submit]')]);
  });

  if (mode === 'create') {
    await step('create the contact', async () => {
      if (await countRows('/s/contacts', email, email) > 0) return;
      await page.goto(`${base}/s/contacts/new`);
      await page.fill('#lead_firstname', 'P2');
      await page.fill('#lead_lastname', canary);
      await page.fill('#lead_email', email);
      await Promise.all([
        page.waitForURL(/\/s\/contacts\/view\/\d+/),
        page.click('#lead_buttons_save_toolbar')
      ]);
    });

    await step('create the segment', async () => {
      if (await countRows('/s/segments', canary, canary) > 0) return;
      await page.goto(`${base}/s/segments/new`);
      await page.fill('#leadlist_name', canary);
      await Promise.all([
        page.waitForURL(u => !u.pathname.endsWith('/s/segments/new')),
        page.click('#leadlist_buttons_save_toolbar')
      ]);
    });

    await step('set the default timezone', async () => {
      if (await timezoneValue() === timezone) return;
      // The select is hidden behind Mautic's chosen widget; set it and let the form save.
      await page.locator('#config_coreconfig_default_timezone').evaluate((select, value) => {
        select.value = value;
        select.dispatchEvent(new Event('change', { bubbles: true }));
      }, timezone);
      await Promise.all([
        page.waitForResponse(r => r.request().method() === 'POST' && r.url().includes('/s/config/edit')),
        page.click('#config_buttons_save_toolbar')
      ]);
    });
  }

  await step(mode === 'absent' ? 'canaries absent on a fresh install' : 'canaries exact', async () => {
    const contacts = await countRows('/s/contacts', email, email);
    const segments = await countRows('/s/segments', canary, canary);
    const zone = await timezoneValue();
    if (mode === 'absent') {
      check(contacts === 0 && segments === 0 && zone !== timezone,
        `fresh install holds ${contacts} contact(s), ${segments} segment(s), timezone ${zone}`);
    } else {
      check(contacts === 1 && segments === 1 && zone === timezone,
        `instance holds ${contacts} contact(s), ${segments} segment(s), timezone ${zone}`);
    }
  });
};
