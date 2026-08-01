// UI end-to-end: stage a signal, see it in the Green Light panel (Signals tab),
// reject it THROUGH the browser, confirm it leaves the queue and its backend
// status becomes REJECTED. Emits a single JSON line prefixed with RESULT:.
//
// Run inside the playwright container:
//   NODE_PATH=$(npm root -g) node /work/ui_reject.js

const { chromium } = require('playwright');
const { stageSignal, pendingHas, statusOf, FRONTEND } = require('./lib');

const SIGNAL_ID = process.env.SIGNAL_ID || `e2e-spike-ui-${Date.now()}`;
const SYMBOL = process.env.SYMBOL || 'SPIKEUI';

const out = { ok: false, signal_id: SIGNAL_ID, symbol: SYMBOL, steps: {}, error: null };
const step = (k, v) => { out.steps[k] = v; console.error(`[ui] ${k} = ${JSON.stringify(v)}`); };

(async () => {
  // 1. Stage the signal (the brain's job) via REST.
  const staged = await stageSignal({ signalId: SIGNAL_ID, symbol: SYMBOL });
  step('stage_status', staged.status);
  step('stage_accepted', !!(staged.body && staged.body.accepted));
  if (!(staged.ok && staged.body && staged.body.accepted)) {
    throw new Error(`stage rejected by backend: ${JSON.stringify(staged.body)}`);
  }

  // 2. Sanity: backend now lists it as PENDING.
  step('pending_before', await pendingHas(SIGNAL_ID));

  // 3. Drive the browser against the real UI (nginx -> React).
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const page = await browser.newPage();
  page.on('console', (m) => console.error(`[browser:${m.type()}] ${m.text()}`));
  page.on('pageerror', (e) => console.error(`[browser:pageerror] ${e.message}`));

  await page.goto(`${FRONTEND}/signals`, { waitUntil: 'domcontentloaded', timeout: 30000 });

  // The Signals tab renders the Green Light panel header.
  await page.getByText('Green Light Queue').waitFor({ timeout: 30000 });
  step('green_light_panel_visible', true);

  // Isolate our card with the symbol search box (unique synthetic symbol).
  await page.getByPlaceholder(/Search symbol/).fill(SYMBOL);

  // Our card must be visible: symbol text + a Reject button.
  await page.getByText(SYMBOL, { exact: true }).first().waitFor({ timeout: 15000 });
  const rejectBtns = page.getByRole('button', { name: 'Reject', exact: true });
  await rejectBtns.first().waitFor({ timeout: 15000 });
  const nBefore = await rejectBtns.count();
  step('cards_shown_for_symbol', nBefore);
  step('order_visible_in_panel', nBefore >= 1);

  await page.screenshot({ path: '/work/ui-before-reject.png', fullPage: true });

  // 4. Reject THROUGH the UI.
  await rejectBtns.first().click();
  step('reject_clicked', true);

  // 5. Card leaves the queue (WS order_rejected -> pendingOrders filtered).
  //    With the symbol filter still applied, the panel shows the empty state.
  let leftQueue = false;
  try {
    await page.getByText('No signals match your filters').waitFor({ timeout: 15000 });
    leftQueue = true;
  } catch {
    // Fallback: the reject button for our symbol detaches.
    try {
      await page.getByRole('button', { name: 'Reject', exact: true }).first()
        .waitFor({ state: 'detached', timeout: 8000 });
      leftQueue = true;
    } catch { /* handled below with a reload */ }
  }
  if (!leftQueue) {
    // Last resort: reload; loadPending() only returns PENDING, so a rejected
    // order must be gone from the panel entirely.
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.getByText('Green Light Queue').waitFor({ timeout: 20000 }).catch(() => {});
    await page.getByPlaceholder(/Search symbol/).fill(SYMBOL).catch(() => {});
    const stillThere = await page.getByRole('button', { name: 'Reject', exact: true }).count();
    leftQueue = stillThere === 0;
  }
  step('left_green_light_queue', leftQueue);

  await page.screenshot({ path: '/work/ui-after-reject.png', fullPage: true });
  await browser.close();

  // 6. Authoritative: backend status is REJECTED and it is no longer pending.
  const finalStatus = await statusOf(SIGNAL_ID);
  step('final_status', finalStatus);
  step('pending_after', await pendingHas(SIGNAL_ID));

  out.ok =
    out.steps.order_visible_in_panel === true &&
    out.steps.reject_clicked === true &&
    finalStatus === 'REJECTED' &&
    out.steps.pending_after.present === false;
})()
  .catch((e) => { out.error = String(e && e.stack || e); })
  .finally(() => { console.log('RESULT:' + JSON.stringify(out)); process.exit(out.ok ? 0 : 1); });
