/** Synthetic API fixture for the real dashboard. No backend or credentials required.
 * bunx vite --config tests/fixtures/remediation-policy-preview.config.ts
 * Open /remediate/policy403, alt403, permission403, unknown, uncertain, worker or persisted.
 */
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const starts = new Map<string, number>();
const polls = new Map<string, number>();
const reviewed = new Set<string>();
const job = (scenario: string, status = 'failed') => ({
  job_id: scenario, scan_id: scenario, status,
  status_url: `/education/remediation/jobs/${scenario}`,
  progress: 0, error_code: status === 'failed' ? 'policy_not_permitted' : null,
  fixed_count: null, download_available: false, download_url: null,
});

export default defineConfig({
  plugins: [react(), {
    name: 'synthetic-remediation-policy-api',
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const url = new URL(request.url || '/', 'http://localhost');
        if (!url.pathname.startsWith('/fixture-api/')) return next();
        const path = url.pathname.slice('/fixture-api'.length);
        const send = (data: unknown, status = 200) => {
          response.writeHead(status, { 'content-type': 'application/json' });
          response.end(JSON.stringify(data));
        };
        if (path === '/reset') {
          starts.clear(); polls.clear(); reviewed.clear(); return send({ ok: true });
        }
        if (path === '/review-policy') { reviewed.add(url.searchParams.get('scan') || 'persisted'); return send({ ok: true }); }
        if (path === '/metrics') return send({ starts: Object.fromEntries(starts), polls: Object.fromEntries(polls) });
        if (path === '/auth/session/validate') return send({
          valid: true, auth_method: 'session',
          user: { id: 'fixture-user', email: 'fixture@example.edu', name: 'Synthetic preview', role: 'faculty' },
          department: { id: 'fixture-department', name: 'Synthetic preview', tier: 'department' },
        });
        const start = path.match(/^\/education\/remediate\/([^/]+)$/);
        if (start && request.method === 'POST') {
          const scenario = start[1];
          starts.set(scenario, (starts.get(scenario) || 0) + 1);
          if (reviewed.has(scenario)) return send(job(scenario, 'pending'), 202);
          if (scenario === 'policy403') return send({ detail: 'LMS AI remediation is not permitted' }, 403);
          if (scenario === 'alt403') return send({ detail: 'LMS AI alt_text is not permitted' }, 403);
          if (scenario === 'permission403') return send({ detail: 'PRIVATE_DIAGNOSTIC not allowed' }, 403);
          if (scenario === 'unknown') return send({ detail: 'PRIVATE_DIAGNOSTIC unknown' }, 500);
          if (scenario === 'uncertain') {
            // Start the response before disconnecting so the browser does not transparently retry the POST.
            response.writeHead(200, { 'content-type': 'application/json' });
            response.write('{');
            setTimeout(() => response.destroy(), 50);
            return;
          }
          return send(job(scenario, 'pending'), 202);
        }
        const latest = path.match(/^\/education\/scans\/([^/]+)\/remediation\/latest$/);
        if (latest) {
          const scenario = latest[1];
          return send(scenario === 'persisted' || (starts.has(scenario) && scenario === 'worker')
            ? job(scenario) : scenario === 'uncertain' && starts.has(scenario) ? job(scenario, 'pending') : null);
        }
        const status = path.match(/^\/education\/remediation\/jobs\/([^/]+)$/);
        if (status) {
          const scenario = status[1];
          const count = (polls.get(scenario) || 0) + 1;
          polls.set(scenario, count);
          return send(job(scenario, count === 1 ? 'processing' : ['uncertain', 'success'].includes(scenario) || reviewed.has(scenario) ? 'completed' : 'failed'));
        }
        const scan = path.match(/^\/education\/scans\/([^/]+)$/);
        if (scan) return send({
          id: scan[1], file_name: 'Synthetic policy preview.pdf', compliance_score: 75,
          issues: [{ description: 'Image has no alternative text', category: 'Images' }],
        });
        if (path.startsWith('/api/reviews/')) return send({ fixes: [] });
        return send({});
      });
    },
  }],
  define: { 'import.meta.env.VITE_API_URL': JSON.stringify('/fixture-api') },
  server: { host: '127.0.0.1', port: 4326, strictPort: true },
});
