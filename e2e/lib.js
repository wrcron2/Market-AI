// Shared helpers for the staged-signal -> Green Light -> reject e2e checks.
// Runs INSIDE the playwright container. backend:8080 (API) and frontend:3000
// (UI, nginx) are reachable on the docker network marketflow-net.

const BACKEND = process.env.BACKEND_URL || 'http://backend:8080';
const FRONTEND = process.env.FRONTEND_URL || 'http://frontend:3000';

async function api(path, opts) {
  const res = await fetch(BACKEND + path, opts);
  const text = await res.text();
  let body;
  try { body = JSON.parse(text); } catch { body = text; }
  return { status: res.status, ok: res.ok, body };
}

// Stage a signal exactly the way the Python brain does: POST /api/signals.
// confidence 0.95 clears MIN_SIGNAL_CONFIDENCE (0.70) and the <1.0 parse guard.
async function stageSignal({ signalId, symbol, direction = 'BUY', quantity = 3, limitPrice = 10 }) {
  return api('/api/signals', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      signal_id: signalId,
      symbol,
      direction,
      quantity,
      limit_price: limitPrice,
      confidence: 0.95,
      reasoning: `[Signal] ${signalId} synthetic e2e-spike verification signal`,
      strategy_name: 'e2e_spike',
      model_used: 'e2e-harness',
    }),
  });
}

async function pendingHas(signalId) {
  const r = await api('/api/orders/pending?limit=200');
  const orders = (r.body && r.body.orders) || [];
  return { present: orders.some((o) => o.id === signalId), count: orders.length };
}

async function statusOf(signalId) {
  const r = await api('/api/orders/recent?limit=200');
  const orders = (r.body && r.body.orders) || [];
  const found = orders.find((o) => o.id === signalId);
  return found ? found.status : null;
}

module.exports = { api, stageSignal, pendingHas, statusOf, BACKEND, FRONTEND };
