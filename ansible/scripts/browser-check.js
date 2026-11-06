// browser-check.js <run dir> <url> — run one browser spec headless and report each step.
//
// Called by playbooks/maintenance/browser-check.yml on the runner, which writes
// <run dir>/spec.js and <run dir>/secrets.json (the instance's Vaultwarden fields, 0600)
// and deletes the secrets afterwards. A spec is a CommonJS module:
//
//   module.exports = async ({ page, step, url, secrets, text }) => {
//     await step('login', async () => { ... });
//   };
//
// `step` runs its body and prints PASS or FAIL; after the first failure it screenshots the
// page and skips the remaining steps. `text()` returns the page's visible text (cut at
// 2000 characters). Every secret value is masked in the output. Exit 0 when all steps pass.
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright-core');

const [run, rawUrl] = process.argv.slice(2);
const secrets = JSON.parse(fs.readFileSync(path.join(run, 'secrets.json'), 'utf8'));
const masks = Object.values(secrets).map(String).filter((v) => v.length >= 4);
const say = (line) => console.log(masks.reduce((s, m) => s.split(m).join('***'), String(line)));

(async () => {
  const spec = require(path.join(run, 'spec.js'));
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message));
  let count = 0;
  let failed = false;

  const step = async (name, body) => {
    if (failed) return;
    count += 1;
    const started = Date.now();
    try {
      await body();
      say(`PASS ${count}. ${name} (${((Date.now() - started) / 1000).toFixed(1)}s)`);
    } catch (error) {
      failed = true;
      const shot = path.join(run, `fail-${count}.png`);
      await page.screenshot({ path: shot, fullPage: true }).catch(() => {});
      say(`FAIL ${count}. ${name}: ${String(error.message).split('\n')[0]}`);
      say(`  at ${page.url()}`);
      say(`SCREENSHOT ${shot}`);
    }
  };
  const text = async () => (await page.locator('body').innerText()).slice(0, 2000);

  try {
    await spec({ page, context, step, url: rawUrl.replace(/\/$/, ''), secrets, text });
  } catch (error) {
    failed = true;
    say(`FAIL spec: ${String(error.message).split('\n')[0]}`);
  }
  if (pageErrors.length) say(`page errors: ${pageErrors.slice(-3).join(' | ')}`);
  say(`RESULT ${failed ? 'fail' : 'pass'} after ${count} step(s)`);
  await browser.close();
  process.exit(failed ? 1 : 0);
})().catch((error) => {
  say(`FAIL runner: ${error.message}`);
  process.exit(1);
});
