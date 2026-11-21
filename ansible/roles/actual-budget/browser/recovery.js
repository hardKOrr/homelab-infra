// Recovery drill, as test(actual-budget) #346 defined it: ?drill=create saves the canaries,
// ?drill=fresh checks a fresh install, the default verifies them, and &final=1 (the last
// verify) deletes them afterwards. &id= is the drill run's id (tools/lab-drill).
//   data           budget p2-canary-actual-budget-<id> with account "Drill Checking" and
//                  one cleared transaction: 2026-10-08, -123.45, payee "Recovery Canary"
//   configuration  the budget's synced date format dd/MM/yyyy (a new budget has MM/dd/yyyy)
// Signs in with the server password Deploy records (server_password in the instance's
// item). The spec drives Actual's own web client, through the backend messenger it exposes
// as window.$send: create writes the budget there and syncs it to the server; verify runs
// in a new browser with no local copy, so it downloads the budget from the server first.
module.exports = async ({ page, step, url, secrets }) => {
  const target = new URL(url);
  const requested = target.searchParams.get('drill') || 'verify';
  const mode = requested === 'fresh' ? 'absent' : requested;
  const final = target.searchParams.get('final') === '1';
  const base = target.origin;
  const id = target.searchParams.get('id') || 'drill';
  const budget = `p2-canary-actual-budget-${id}`;
  const account = 'Drill Checking';
  const canary = { date: '2026-10-08', amount: -12345, payee: 'Recovery Canary', notes: budget };
  const dateFormat = 'dd/MM/yyyy';
  const check = (value, message) => { if (!value) throw new Error(message); };
  const send = async (name, args) => {
    const result = await page.evaluate(([n, a]) => window.$send(n, a), [name, args]);
    check(!(result && result.error), `${name} failed: ${JSON.stringify(result && result.error)}`);
    return result;
  };
  // Actual answers null when it cannot reach its server; only a listing proves absence.
  const remote = async () => {
    const files = await send('get-remote-files');
    check(Array.isArray(files), 'the client could not list the budgets on the server');
    return files.filter(f => f.name === budget && !f.deleted);
  };

  await step('sign in with the server password', async () => {
    // Actual 26.11 greets a new browser with a welcome screen; Start budgeting leads to
    // the sign-in page. Older versions open on the sign-in page.
    await page.goto(base);
    const password = page.locator('input[type=password]');
    const start = page.getByText('Start budgeting');
    await password.or(start).first().waitFor({ timeout: 60000 });
    if (await start.isVisible()) await start.click();
    await password.waitFor({ timeout: 60000 });
    await page.locator('input[type=password]').fill(secrets.server_password);
    await page.keyboard.press('Enter');
    // Signed in with no budget open: the file list, or 26.11's welcome screen once more.
    await page.waitForURL(u => !u.pathname.startsWith('/login'), { timeout: 60000 });
    await page.getByText('Create new file').or(page.getByText('Import file'))
      .or(page.getByText('Server online')).first().waitFor({ timeout: 60000 });
    await page.waitForFunction(() => typeof window.$send === 'function', null, { timeout: 60000 });
  });

  if (mode === 'absent') {
    await step('a fresh install holds no canary budget', async () => {
      const files = await remote();
      check(files.length === 0, `the server holds ${files.length} canary budget(s): data survived Remove`);
    });
    return;
  }

  if (mode === 'create') {
    await step('create the budget, account, transaction and date format', async () => {
      if ((await remote()).length === 0) {
        await send('create-budget', { budgetName: budget });
        const accountId = await send('api/account-create', { account: { name: account, offbudget: false }, initialBalance: 0 });
        await send('api/transactions-add', {
          accountId,
          transactions: [{ date: canary.date, amount: canary.amount, payee_name: canary.payee, notes: canary.notes, cleared: true }]
        });
        await send('preferences/save', { id: 'dateFormat', value: dateFormat });
        await send('sync');
        await send('close-budget');
      }
      check((await remote()).length === 1, 'the server does not list the new budget');
    });
  }

  await step('canaries exact, read from the server into a new client', async () => {
    const files = await remote();
    check(files.length === 1, `the server lists ${files.length} canary budget(s)`);
    const opened = await send('download-budget', { cloudFileId: files[0].fileId });
    await send('load-budget', { id: opened.id });
    const accounts = (await send('api/accounts-get')).filter(a => a.name === account);
    check(accounts.length === 1, `budget holds ${accounts.length} "${account}" account(s)`);
    const rows = await send('api/transactions-get', { accountId: accounts[0].id, startDate: '2000-01-01', endDate: '2100-01-01' });
    const payees = await send('api/payees-get');
    const payee = (payees || []).find(p => p.name === canary.payee);
    check(rows.length === 1 && rows[0].date === canary.date && rows[0].amount === canary.amount
      && rows[0].cleared && rows[0].notes === canary.notes && payee && rows[0].payee === payee.id,
      `account holds ${JSON.stringify(rows.map(r => [r.date, r.amount, r.cleared, r.notes]))}`);
    const prefs = await send('preferences/get');
    check(prefs.dateFormat === dateFormat, `date format is ${prefs.dateFormat}, not ${dateFormat}`);
    if (final) {
      await send('close-budget');
      await send('delete-budget', { id: opened.id, cloudFileId: files[0].fileId });
      check((await remote()).length === 0, 'the server still lists the canary budget after delete');
    }
  });
};
