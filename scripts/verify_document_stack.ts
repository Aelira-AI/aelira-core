/** Real HTTP/queue/worker/storage acceptance gate. No mocked transport or findings. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { execFileSync } from 'node:child_process';

const api = new URL(process.env.STACK_API_URL || 'http://localhost:18300');
const mail = new URL(process.env.STACK_MAIL_URL || 'http://localhost:18325');
// This gate creates synthetic records. Refuse accidental production targets.
for (const url of [api, mail]) {
  assert(['localhost', '127.0.0.1'].includes(url.hostname), 'Only local stacks are allowed');
  assert.equal(url.protocol, 'http:');
}
const output = resolve(process.env.STACK_EVIDENCE_DIR || 'test-results/document-stack');
const email = process.env.STACK_TEST_EMAIL || 'document.acceptance@example.org';
assert(email.endsWith('@example.org'), 'Use a synthetic example.org identity');
const cookies = new Map<string, string>();
const digest = (bytes: Uint8Array) => createHash('sha256').update(bytes).digest('hex');
function officeContent(file: string, kind: string): string[] {
  if (kind === 'pdf') {
    const text = execFileSync('pdftotext', [file, '-'], { encoding: 'utf8' }).replace(/\s+/g, ' ').trim();
    assert(text.length > 0, 'PDF text-preservation probe must observe source content');
    return [text];
  }
  const entry = kind === 'powerpoint' ? 'ppt/slides/slide1.xml' : 'xl/worksheets/sheet1.xml';
  const xml = execFileSync('unzip', ['-p', file, entry], { encoding: 'utf8' });
  const pattern = kind === 'powerpoint' ? /<a:t(?:\s[^>]*)?>([\s\S]*?)<\/a:t>/g : /<(?:t|v)(?:\s[^>]*)?>([\s\S]*?)<\/(?:t|v)>/g;
  const values = [...xml.matchAll(pattern)].map((match) => match[1]);
  assert(values.length > 0, 'Content-preservation probe must observe source content');
  return values;
}
function headers(extra?: HeadersInit): Headers {
  const h = new Headers(extra);
  h.set('Cookie', [...cookies].map(([k, v]) => `${k}=${v}`).join('; '));
  if (cookies.has('csrf_token')) h.set('X-CSRF-Token', decodeURIComponent(cookies.get('csrf_token')!));
  return h;
}
async function request(path: string, init: RequestInit = {}) {
  const target = new URL(path, api);
  assert.equal(target.origin, api.origin, 'Refuse cross-origin authenticated request');
  const response = await fetch(target, { ...init, headers: headers(init.headers), redirect: 'error', signal: AbortSignal.timeout(30000) });
  for (const line of response.headers.getSetCookie()) {
    const pair = line.split(';')[0];
    const split = pair.indexOf('=');
    cookies.set(pair.slice(0, split), pair.slice(split + 1));
  }
  assert(response.ok, `HTTP ${response.status} on ${target.pathname}`);
  return response.json();
}
async function poll<T>(probe: () => Promise<T>, done: (v: T) => boolean): Promise<T> {
  const deadline = Date.now() + 120000;
  do {
    const value = await probe();
    if (done(value)) return value;
    await delay(500);
  } while (Date.now() < deadline);
  throw new Error('Real-stack operation did not reach the expected state within 120 seconds');
}
await mkdir(output, { recursive: true });
await request('/api/csrf-token');
await request('/auth/magic-link/request', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ email, name: 'Document acceptance', institution: 'Synthetic test institution' }),
});
const inbox = await poll(async () => (await fetch(new URL('/api/v1/messages', mail))).json(),
  (v: any) => v.messages?.some((m: any) => m.To?.some((t: any) => t.Address === email)));
const message = inbox.messages.find((m: any) => m.To?.some((t: any) => t.Address === email));
const body = await (await fetch(new URL(`/api/v1/message/${message.ID}`, mail))).json();
const match = String(body.HTML || body.Text).match(/https?:\/\/[^"<>\s]+\/auth\/verify\?[^"<>\s]+/);
assert(match, 'Synthetic magic link was delivered');
const login = new URL(match[0].replaceAll('&amp;', '&'));
await request('/auth/magic-link/verify', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ email, token: login.searchParams.get('token') }),
});
// Tokens/cookies never enter evidence or stdout.
async function scanBytes(bytes: Uint8Array, filename: string, kind: string) {
  const form = new FormData();
  form.append('file', new Blob([bytes]), filename);
  const upload = await request(`/education/${kind}/scan?generate_alt_text=false&validate_alt_text=false`, { method: 'POST', body: form });
  const result = await poll(() => request(`/education/scans/${upload.scan_id}`), (v: any) => Boolean(v.scan?.result));
  return result.scan;
}
const cases = [
  { name: 'course.pptx', kind: 'powerpoint', publish: true },
  { name: 'course.xlsx', kind: 'excel', publish: true },
  { name: 'course.docx', kind: 'word', publish: false },
  { name: 'metadata.pdf', kind: 'pdf', publish: true },
  { name: 'simple_syllabus.pdf', kind: 'pdf', publish: false },
];
const evidence: object[] = [];
const failures: string[] = [];
for (const test of cases) {
  try {
    const file = test.name === 'simple_syllabus.pdf' ? `tests/fixtures/pdfs/${test.name}` : `tests/fixtures/document_stack/${test.name}`;
    const bytes = await readFile(file);
    const scan = await scanBytes(bytes, test.name, test.kind);
    const scanId = scan.scan_id || scan.id;
    assert(scanId, 'Scan has a durable identifier');
    const total = scan.result.issues.length;
    assert(total > 0, `${test.name} must exercise real findings`);
    const queued = await request(`/education/remediate/${scanId}?use_ai=false&verify_fixes=true`, {
      method: 'POST', headers: { Prefer: 'respond-async', 'Content-Type': 'application/json' }, body: '{}',
    });
    assert(queued.job_id, 'Remediation uses the durable queue');
    const job = await poll(() => request(`/education/remediation/jobs/${queued.job_id}`),
      (v: any) => ['completed', 'failed', 'cancelled', 'dead_letter'].includes(v.status));
    const latest = await request(`/education/scans/${scanId}/remediation/latest`);
    assert.equal(latest.job_id, job.job_id, 'Reload resolves the same authoritative job');
    assert.equal(job.total_issues, total, 'Job retains all source findings');
    const downloadPath = `/education/remediation/jobs/${queued.job_id}/download`;
    const downloaded = await fetch(new URL(downloadPath, api), { headers: headers(), redirect: 'error' });
    if (!test.publish) {
      assert.equal(job.download_available, false);
      assert.equal(job.artifact_id, null);
      assert.equal(downloaded.status, 404, 'Withheld output cannot be downloaded');
      assert.equal(job.fixed_count, 0);
      assert.equal(job.remediated_score, null);
      assert.equal(job.score_verified, false);
      assert.equal(job.remaining_count, total, 'Withheld changes remain unresolved');
      assert.equal(job.fixed_count + job.manual_count + job.failed_count + job.skipped_count + job.withheld_count + job.outcome_unreported_count, total, 'All dispositions reconcile');
    } else {
      assert.equal(job.status, 'completed');
      assert.equal(job.download_available, true);
      assert.equal(downloaded.status, 200);
      assert.equal(job.score_verified, true);
      const saved = new Uint8Array(await downloaded.arrayBuffer());
      assert.equal(job.score_measurement.source_sha256, digest(bytes));
      assert.equal(job.score_measurement.output_sha256, digest(saved));
      assert.equal(job.original_score, scan.result.compliance_score);
      const rescan = await scanBytes(saved, `saved-${test.name}`, test.kind);
      assert.equal(rescan.result.compliance_score, job.remediated_score, 'Downloaded bytes independently rescan to displayed score');
      assert.equal(rescan.result.issues.length, job.remaining_count);
      assert.equal(job.remaining_count, 0, 'Successful fixture has no remaining scanner findings');
      const savedPath = resolve(output, `saved-${test.name}`);
      await writeFile(savedPath, saved);
      assert.deepEqual(officeContent(savedPath, test.kind), officeContent(file, test.kind), 'Fixture text, values and order survive remediation');
    }
    evidence.push({ fixture: test.name, scan_id: scanId, job_id: queued.job_id, status: job.status, total, remaining: job.remaining_count, source_sha256: digest(bytes), score_measurement: job.score_measurement, download_status: downloaded.status });
    console.log(`PASS ${test.name}: real scan, queued worker, persisted outcome, download boundary`);
  } catch (error) {
    const failure = `${test.name}: ${error instanceof Error ? error.message : 'Unknown failure'}`;
    failures.push(failure);
    console.error(`FAIL ${failure}`);
  }
}
await writeFile(resolve(output, 'report.json'), JSON.stringify({ evidence, failures }, null, 2));
assert.equal(failures.length, 0, 'Real document stack gate failed; see report.json');
