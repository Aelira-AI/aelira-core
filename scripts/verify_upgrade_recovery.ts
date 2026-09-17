/** Persisted-state upgrade/restore gate against an isolated real API and worker. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

const api = new URL(process.env.STACK_API_URL || 'http://localhost:18300');
const mail = new URL(process.env.STACK_MAIL_URL || 'http://localhost:18325');
// This gate creates synthetic records. Refuse accidental production targets.
for (const url of [api, mail]) {
  assert(['localhost', '127.0.0.1'].includes(url.hostname), 'Only local stacks are allowed');
  assert.equal(url.protocol, 'http:');
  assert(!url.username && !url.password && !url.search && !url.hash, 'Use a plain local origin');
}
const output = resolve(process.env.STACK_EVIDENCE_DIR || 'test-results/upgrade-recovery');
const mode = process.argv[2];
assert(['seed', 'verify', 'exercise'].includes(mode), 'Use seed, verify, or exercise');
const snapshotPath = resolve(process.env.STACK_SNAPSHOT || `${output}/seed.json`);
if (mode === 'seed') assert(!existsSync(snapshotPath), 'Refuse to overwrite an existing seed snapshot');
const phase = process.env.STACK_PHASE || mode;
assert(/^[a-z-]+$/.test(phase));
const email = process.env.STACK_TEST_EMAIL || 'upgrade.rehearsal@example.org';
assert(/^[a-z0-9._+-]+@example\.org$/i.test(email), 'Use a synthetic example.org identity');
const cookies = new Map<string, string>();
const digest = (bytes: Uint8Array) => createHash('sha256').update(bytes).digest('hex');
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
const csrf = await request('/api/csrf-token');
const csrfMatches = Boolean(csrf.csrf_token && csrf.csrf_token === cookies.get('csrf_token'));
// The released v0.9.11 first-response mismatch is a known defect. Its seed and
// restore lanes deliberately use the cookie; the candidate must pass strictly.
if (process.env.STACK_ALLOW_LEGACY_CSRF_MISMATCH !== 'true') {
  assert(csrfMatches, 'First CSRF response must match its cookie');
}
const previousInbox = await (await fetch(new URL('/api/v1/messages', mail))).json();
const previousMessageIds = new Set((previousInbox.messages || []).map((m: any) => m.ID));
await request('/auth/magic-link/request', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ email, name: 'Upgrade rehearsal', institution: 'Synthetic test institution' }),
});
const inbox = await poll(async () => (await fetch(new URL('/api/v1/messages', mail))).json(),
  (v: any) => v.messages?.some((m: any) => !previousMessageIds.has(m.ID) && m.To?.some((t: any) => t.Address === email)));
const message = inbox.messages.find((m: any) => !previousMessageIds.has(m.ID) && m.To?.some((t: any) => t.Address === email));
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
  form.append('file', new Blob([new Uint8Array(bytes).buffer]), filename);
  const upload = await request(`/education/${kind}/scan?generate_alt_text=false&enhance_descriptions=false&validate_alt_text=false`, { method: 'POST', body: form });
  const result = await poll(() => request(`/education/scans/${upload.scan_id}`),
    (v: any) => Boolean(v.scan?.result) || v.scan?.status?.toLowerCase() === 'failed');
  return result.scan;
}

const jobFields = ['job_id', 'status', 'total_issues', 'remaining_count', 'fixed_count', 'manual_count', 'failed_count', 'skipped_count', 'withheld_count', 'outcome_unreported_count', 'download_available', 'artifact_id', 'score_verified', 'original_score', 'remediated_score', 'score_measurement'];
function stableJob(job: any) {
  return Object.fromEntries(jobFields.map((key) => [key, job[key] ?? null]));
}
function stableProfile(profile: any) {
  return Object.fromEntries(['id', 'email', 'name', 'timezone', 'email_notifications', 'email_verified'].map((key) => [key, profile[key]]));
}
async function observe(scanId: string, jobId: string) {
  const scan = (await request('/education/scans/' + scanId)).scan;
  const job = await request('/education/remediation/jobs/' + jobId);
  const latest = await request('/education/scans/' + scanId + '/remediation/latest');
  assert.equal(latest.job_id, jobId);
  const response = await fetch(new URL('/education/remediation/jobs/' + jobId + '/download', api), {headers: headers(), redirect: 'error', signal: AbortSignal.timeout(30000)});
  const sha256 = response.status === 200 ? digest(new Uint8Array(await response.arrayBuffer())) : null;
  assert.equal(response.status, job.download_available ? 200 : 404);
  if (sha256) assert.equal(sha256, job.score_measurement.output_sha256);
  return {scan_id: scanId, job_id: jobId, scan_status: scan.status, result: scan.result, job: stableJob(job), download_status: response.status, output_sha256: sha256};
}
async function createCase(name: string, kind: string, publish: boolean) {
  const bytes = await readFile('tests/fixtures/document_stack/' + name);
  const scan = await scanBytes(bytes, phase + '-' + name, kind);
  assert.equal(scan.status.toLowerCase(), 'completed');
  assert(scan.result.issues.length > 0, 'Probe must exercise real findings');
  const scanId = scan.scan_id || scan.id;
  const queued = await request('/education/remediate/' + scanId + '?use_ai=false&verify_fixes=true', {method: 'POST', headers: {Prefer: 'respond-async', 'Content-Type': 'application/json'}, body: '{}'});
  assert(queued.job_id);
  const job = await poll(() => request('/education/remediation/jobs/' + queued.job_id),
    (v: any) => ['completed', 'failed', 'cancelled', 'dead_letter'].includes(v.status));
  assert.equal(job.download_available, publish);
  assert.equal(job.total_issues, scan.result.issues.length);
  if (publish) {
    assert.equal(job.status, 'completed');
    assert.equal(job.score_verified, true);
    assert.equal(job.remaining_count, 0);
    assert.equal(job.score_measurement.source_sha256, digest(bytes));
  } else {
    assert.equal(job.artifact_id, null);
    assert.equal(job.fixed_count, 0);
    assert.equal(job.remaining_count, scan.result.issues.length);
    assert.equal(job.score_verified, false);
  }
  const state = await observe(scanId, queued.job_id);
  console.log('PASS ' + phase + ' ' + name + ': durable outcome and download boundary');
  return state;
}
if (mode === 'seed') {
  await request('/auth/profile', {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: 'Recovery rehearsal administrator', timezone: 'Australia/Sydney', email_notifications: false})});
  const records = [await createCase('course.xlsx', 'excel', true), await createCase('course.docx', 'word', false)];
  const profile = stableProfile(await request('/auth/profile'));
  await writeFile(snapshotPath, JSON.stringify({profile, records}, null, 2));
  await writeFile(resolve(output, phase + '.json'), JSON.stringify({passed: true, csrf_matches: csrfMatches, snapshot: snapshotPath}, null, 2));
} else if (mode === 'verify') {
  const expected = JSON.parse(await readFile(snapshotPath, 'utf8'));
  assert.deepEqual(stableProfile(await request('/auth/profile')), expected.profile, 'Saved administrator profile survives');
  for (const record of expected.records) assert.deepEqual(await observe(record.scan_id, record.job_id), record, 'Saved scan, review disposition and bytes survive');
  // A post-backup marker must be absent from the independently restored database.
  if (process.env.STACK_ABSENT_SNAPSHOT) {
    const marker = JSON.parse(await readFile(process.env.STACK_ABSENT_SNAPSHOT, 'utf8'));
    const response = await fetch(new URL('/education/scans/' + marker.record.scan_id, api), {headers: headers(), redirect: 'error', signal: AbortSignal.timeout(30000)});
    assert.equal(response.status, 404, 'Restoration excludes records created after backup');
  }
  await writeFile(resolve(output, phase + '.json'), JSON.stringify({passed: true, csrf_matches: csrfMatches, preserved_records: expected.records.length}, null, 2));
  console.log('PASS ' + phase + ': administrator profile, saved scan results, job dispositions and exact artifact hashes');
} else {
  const record = await createCase('course.xlsx', 'excel', true);
  await writeFile(resolve(output, phase + '.json'), JSON.stringify({passed: true, csrf_matches: csrfMatches, record}, null, 2));
}
