/**
 * Photograph the running stack for the build handbook.
 *
 * Every dashboard figure in the handbook is a real screenshot of a real
 * instance, taken by this script. A drawn approximation of a screen teaches a
 * reader to expect something the software does not do, and the reader only
 * finds out when their own screen disagrees.
 *
 * Requires the stack to be up and seeded:
 *     ./infra/up.sh
 *     python3 scripts/bootstrap_workspace.py
 *     python3 scripts/bootstrap_n8n.py
 *     python3 scripts/twenty_provision.py
 *     python3 scripts/seed_demo_data.py
 *     python3 scripts/n8n_deploy.py --dev --activate
 *
 * Usage:
 *     NODE_PATH=/opt/node22/lib/node_modules node docs/build-handbook/capture.js
 *     ... capture.js --only crm-companies,n8n-canvas
 *     ... capture.js --list
 *
 * Screens showing an API key are never captured. The key is the one secret on
 * screen that grants full write access to the CRM, and a handbook is a
 * document people paste into chat.
 */

const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const OUT = path.join(__dirname, 'img');
const CRM = process.env.CRM_URL || 'http://localhost:3000';
const N8N = process.env.N8N_URL || 'http://localhost:5678';
const MAIL = process.env.MAIL_URL || 'http://localhost:8025';
const USER = process.env.DEMO_EMAIL || 'admin@aspiretss.com';
const PASS = process.env.DEMO_PASSWORD || 'AspireDemo2026!';

// 2x so the figures stay sharp when a PDF scales them down to a column width.
const VIEWPORT = { width: 1440, height: 900 };
const SCALE = 2;

const args = process.argv.slice(2);
const only = (args.find(a => a.startsWith('--only=')) || '').split('=')[1]
  || (args.includes('--only') ? args[args.indexOf('--only') + 1] : '');
const wanted = only ? new Set(only.split(',').map(s => s.trim())) : null;

const results = [];

async function shot(page, name, note) {
  const file = path.join(OUT, `${name}.png`);
  await page.screenshot({ path: file });
  const kb = Math.round(fs.statSync(file).size / 1024);
  console.log(`  ok    ${name.padEnd(28)} ${String(kb).padStart(5)} KB  ${note || ''}`);
  results.push({ name, kb, note: note || '' });
}

function want(name) { return !wanted || wanted.has(name); }

/** Settle animations and lazy-loaded rows before the shutter. */
async function settle(page, ms = 2500) {
  await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(ms);
}

// ---------------------------------------------------------------- Twenty

async function crmLogin(ctx) {
  const page = await ctx.newPage();
  await page.goto(CRM, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await settle(page, 3000);

  // The sign-in panel is a modal over the app shell, so the email field is the
  // only text input on the page rather than the first of several.
  const email = page.locator('input[autocomplete="email"]').first();
  if (await email.count()) {
    await email.fill(USER);
    await page.keyboard.press('Enter');
    await page.waitForTimeout(2500);
    const pw = page.locator('input[type="password"]').first();
    if (await pw.count()) {
      await pw.fill(PASS);
      await page.keyboard.press('Enter');
    }
    await settle(page, 5000);

    // First login onto a freshly bootstrapped workspace asks for a display
    // name before it will show the app. Answer it once so every later
    // capture opens straight onto real data instead of this dialog.
    const firstName = page.locator('input[placeholder="Tim"], input[name="firstName"]').first();
    if (await firstName.count()) {
      await firstName.fill('Rafi');
      const lastName = page.locator('input[placeholder="Cook"], input[name="lastName"]').first();
      if (await lastName.count()) await lastName.fill('Rahman');
      const cont = page.locator('button:has-text("Continue")').first();
      if (await cont.count()) { await cont.click(); await settle(page, 3000); }
      // "Invite your team" is a real onboarding route (/invite-team), and
      // the app's own router bounces any other navigation straight back to
      // it until the step is actually dismissed — so it has to be cleared by
      // clicking its own Skip text, not by navigating away.
      if (page.url().includes('/invite-team')) {
        const skip = page.getByText('Skip', { exact: true });
        if (await skip.count()) {
          await skip.first().click({ force: true });
          await settle(page, 3000);
        }
      }
    }
  }
  return page;
}

// ------------------------------------------------------------------- n8n

async function n8nLogin(ctx) {
  const page = await ctx.newPage();
  await page.goto(`${N8N}/signin`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await settle(page, 2500);
  const email = page.locator('input[type="email"], input[name="email"]').first();
  if (await email.count()) {
    await email.fill(USER);
    await page.locator('input[type="password"]').first().fill(PASS);
    await page.locator('button[type="submit"], button:has-text("Sign in")').first().click();
    await settle(page, 5000);
  }
  return page;
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const ctx = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: SCALE,
    colorScheme: 'light',
  });

  console.log('Capturing the running stack\n');

  // ---- the CRM -----------------------------------------------------------
  const crm = await ctx.newPage();
  if (want('crm-login')) {
    await crm.goto(CRM, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await settle(crm, 3500);
    await shot(crm, 'crm-login', 'the sign-in screen a first-time builder meets');
  }
  await crm.close();

  const page = await crmLogin(ctx);

  const crmScreens = [
    ['crm-companies',   '/objects/companies',            'the Companies table'],
    ['crm-people',      '/objects/people',               'People, populated by the seed'],
    ['crm-opportunities', '/objects/opportunities',      'the pipeline'],
    ['crm-enrollments', '/objects/enrollments',          'a custom object holding real business state'],
    ['crm-cohorts',     '/objects/cohorts',              'cohorts with seat counts'],
    ['crm-consent',     '/objects/consentRecords',       'per-person, per-channel consent'],
    ['crm-automation-runs', '/objects/automationRuns',   'execution history, queryable'],
    ['crm-message-log', '/objects/messageLogs',          'every send and every refusal'],
    ['crm-settings-objects', '/settings/objects',        'the provisioned data model'],
  ];

  for (const [name, url, note] of crmScreens) {
    if (!want(name)) continue;
    try {
      if (page.isClosed()) throw new Error('page was closed');
      await page.goto(CRM + url, { waitUntil: 'domcontentloaded', timeout: 60000 });
      await settle(page);
      await shot(page, name, note);
    } catch (e) {
      console.log(`  SKIP  ${name.padEnd(28)}       ${e.message.slice(0, 60)}`);
    }
  }

  // A single company record, showing its related records — the beat that
  // makes the object model concrete. The record id comes from the REST API
  // rather than from clicking a row: the table virtualises its rows, so the
  // link a selector finds depends on scroll position.
  if (want('crm-company-record')) {
    try {
      const id = await page.evaluate(async (key) => {
        const r = await fetch('/rest/companies?limit=1',
                              { headers: { Authorization: 'Bearer ' + key } });
        const d = await r.json();
        return (d.data?.companies?.[0] || {}).id;
      }, process.env.TWENTY_API_KEY || '');
      if (id) {
        await page.goto(`${CRM}/object/company/${id}`, { waitUntil: 'domcontentloaded' });
        await settle(page, 4000);
        await shot(page, 'crm-company-record', 'one company and everything hanging off it');
      } else {
        console.log('  SKIP  crm-company-record            no company id returned');
      }
    } catch (e) {
      console.log(`  SKIP  crm-company-record            ${e.message.slice(0, 60)}`);
    }
  }

  // ---- the automation editor --------------------------------------------
  // Only opened when a shot actually needs it — logging into n8n on every
  // run, even a CRM-only --only selection, doubled the run time for nothing.
  const needsN8n = want('n8n-workflows') || want('n8n-executions') || want('n8n-canvas');
  if (needsN8n) {
    const n8n = await n8nLogin(ctx);

    if (want('n8n-workflows')) {
      await n8n.goto(`${N8N}/home/workflows`, { waitUntil: 'domcontentloaded' });
      await settle(n8n, 3500);
      await shot(n8n, 'n8n-workflows', 'the deployed workflow library');
    }

    if (want('n8n-executions')) {
      await n8n.goto(`${N8N}/home/executions`, { waitUntil: 'domcontentloaded' });
      await settle(n8n, 3500);
      await shot(n8n, 'n8n-executions', 'what actually ran, and whether it worked');
    }

    if (want('n8n-canvas')) {
      try {
        await n8n.goto(`${N8N}/home/workflows`, { waitUntil: 'domcontentloaded' });
        await settle(n8n, 3000);
        const row = n8n.locator('text=LEAD Form Intake').first();
        await row.click();
        await settle(n8n, 6000);
        await n8n.keyboard.press('1');          // zoom to fit
        await n8n.waitForTimeout(2500);
        await shot(n8n, 'n8n-canvas', 'LEAD Form Intake — the reference workflow');
      } catch (e) {
        console.log(`  SKIP  n8n-canvas                    ${e.message.slice(0, 60)}`);
      }
    }
  }

  // ---- the public form, as a member of the public sees it ----------------
  if (want('public-form')) {
    const anon = await browser.newContext({
      viewport: VIEWPORT, deviceScaleFactor: SCALE, colorScheme: 'light' });
    const f = await anon.newPage();
    await f.goto(`${N8N}/form/aspire-contact`, { waitUntil: 'domcontentloaded' });
    await settle(f, 3000);
    await shot(f, 'public-form', 'hosted by our own stack — no third-party tool');
    await anon.close();
  }

  // ---- the form actually submitted, end to end ---------------------------
  // A picture of an empty form proves nothing. This fills it in and
  // photographs the confirmation, which is the moment the reader is being
  // taught to expect: a public page that lands a scored record in the CRM.
  if (want('form-filled') || want('form-success')) {
    const anon = await browser.newContext({
      viewport: VIEWPORT, deviceScaleFactor: SCALE, colorScheme: 'light' });
    const f = await anon.newPage();
    try {
      await f.goto(`${N8N}/form/aspire-contact`, { waitUntil: 'domcontentloaded' });
      await settle(f, 3000);

      // Form Trigger names its inputs field-0, field-1, ... by position, and
      // that id — not the label — is what the form actually posts. Filling by
      // id is therefore both stable and an accurate demonstration of how the
      // node works; filling the visible inputs in order is not, because the
      // two SELECTs sit in the middle of the sequence.
      const stamp = Date.now().toString(36);
      await f.fill('#field-0', 'Nadia');
      await f.fill('#field-1', 'Okonkwo');
      await f.fill('#field-2', `nadia.okonkwo@meridian-defense-${stamp}.com`);
      await f.fill('#field-3', 'Meridian Defense Systems');
      await f.fill('#field-4', '+1 555 0142');
      await f.selectOption('#field-5', 'Splunk bootcamp');
      await f.selectOption('#field-6', 'Splunk Core Certified Power User');
      await f.fill('#field-7',
        'We are scoping a Splunk bootcamp for eight analysts starting next '
        + 'month. Who should I speak to about pricing?');
      // Consent is a required checkbox: without it the form does not submit,
      // which is the first place the consent rule shows up in the system.
      await f.check('#option0_field-8');
      await f.waitForTimeout(800);

      if (want('form-filled')) {
        await shot(f, 'form-filled', 'the same form, filled in by a real visitor');
      }
      if (want('form-success')) {
        await f.locator('button[type="submit"], button:has-text("Submit")').first().click();
        await settle(f, 6000);
        await shot(f, 'form-success', 'submitted — the record now exists in the CRM');
      }
    } catch (e) {
      console.log(`  SKIP  form submission              ${e.message.slice(0, 60)}`);
    }
    await anon.close();
  }

  // ---- the mail catcher --------------------------------------------------
  if (want('mailpit')) {
    try {
      const m = await ctx.newPage();
      await m.goto(MAIL, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await settle(m, 3000);
      await shot(m, 'mailpit', 'the acknowledgement, caught before it left the host');
      await m.close();
    } catch (e) {
      console.log(`  SKIP  mailpit                       ${e.message.slice(0, 60)}`);
    }
  }

  await browser.close();
  console.log(`\n${results.length} figure(s) written to docs/build-handbook/img/`);
  fs.writeFileSync(path.join(OUT, 'manifest.json'),
                   JSON.stringify(results, null, 2) + '\n');
})().catch(e => { console.error('capture failed:', e); process.exit(1); });
