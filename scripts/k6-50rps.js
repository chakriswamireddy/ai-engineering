// WS9 — load test: 50 rps sustained against staging.
// Run: k6 run scripts/k6-50rps.js   (or: docker run --rm -i grafana/k6 run - < scripts/k6-50rps.js)
// Accept: 50 rps for 5m, p95 < 2s, 0 data-loss (approval outcomes intact),
// demo_smoke --regress green afterwards. Thresholds fail the run, not a human.
import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '1m', target: 50 },   // ramp to 50 rps
    { duration: '5m', target: 50 },   // sustain (the accept criterion)
    { duration: '30s', target: 0 },   // drain
  ],
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<2000'],
  },
};

const BASE = __ENV.AI_STAGING_URL || 'http://localhost:8000';

export default function () {
  const rag = http.post(`${BASE}/rag`, JSON.stringify({ question: 'How does Saleor handle checkout?', k: 3 }), {
    headers: { 'Content-Type': 'application/json' },
  });
  check(rag, { 'rag 200': (r) => r.status === 200 });
  const traces = http.get(`${BASE}/metrics`);
  check(traces, { 'metrics 200': (r) => r.status === 200 });
  sleep(1);
}
