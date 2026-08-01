// API end-to-end (no browser): stage a DIFFERENT signal, confirm it is PENDING,
// reject it over plain HTTP, confirm status becomes REJECTED and it leaves the
// pending list. Emits a single JSON line prefixed with RESULT:.
//
// Run inside the playwright container:
//   node /work/api_check.js

const { api, stageSignal, pendingHas, statusOf } = require('./lib');

const SIGNAL_ID = process.env.SIGNAL_ID || `e2e-spike-api-${Date.now()}`;
const SYMBOL = process.env.SYMBOL || 'SPIKEAPI';

const out = { ok: false, signal_id: SIGNAL_ID, symbol: SYMBOL, steps: {}, error: null };
const step = (k, v) => { out.steps[k] = v; console.error(`[api] ${k} = ${JSON.stringify(v)}`); };

(async () => {
  const staged = await stageSignal({ signalId: SIGNAL_ID, symbol: SYMBOL });
  step('stage_status', staged.status);
  step('stage_accepted', !!(staged.body && staged.body.accepted));
  if (!(staged.ok && staged.body && staged.body.accepted)) {
    throw new Error(`stage rejected by backend: ${JSON.stringify(staged.body)}`);
  }

  const before = await pendingHas(SIGNAL_ID);
  step('pending_before', before);
  if (!before.present) throw new Error('staged order not found in pending list');

  const rej = await api('/api/orders/reject', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ signal_id: SIGNAL_ID, comment: 'e2e-spike api reject' }),
  });
  step('reject_status', rej.status);
  step('reject_body', rej.body);
  if (!(rej.ok && rej.body && rej.body.success)) {
    throw new Error(`reject failed: ${JSON.stringify(rej.body)}`);
  }

  const finalStatus = await statusOf(SIGNAL_ID);
  step('final_status', finalStatus);
  const after = await pendingHas(SIGNAL_ID);
  step('pending_after', after);

  out.ok = finalStatus === 'REJECTED' && after.present === false;
})()
  .catch((e) => { out.error = String(e && e.stack || e); })
  .finally(() => { console.log('RESULT:' + JSON.stringify(out)); process.exit(out.ok ? 0 : 1); });
