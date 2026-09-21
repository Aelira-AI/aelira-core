/** Real HTTP/queue/worker/storage acceptance gate. No mocked transport or findings. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { execFileSync } from 'node:child_process';
import { verifyLatexCorpus } from './verify_latex_corpus_stack.ts';
import { authenticatedTarget } from './document_stack_transport.ts';

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
const harnessFiles = [
  'scripts/verify_document_stack.ts', 'scripts/verify_latex_corpus_stack.ts',
  'scripts/latex_corpus_contract.ts', 'tests/fixtures/latex_research/corpus.json',
  'scripts/document_stack_transport.ts',
];
async function captureHarness() {
  return Object.fromEntries(await Promise.all(harnessFiles.map(async (file) => [file, digest(await readFile(file))])));
}
const harnessHashes = await captureHarness();
const revision = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
const trackedDiff = execFileSync('git', ['diff', 'HEAD', '--binary']);
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
  const target = authenticatedTarget(path, api);
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
assert(csrf.csrf_token && csrf.csrf_token === cookies.get('csrf_token'),
  'The first CSRF response must match the cookie without a retry');
const previousInbox = await (await fetch(new URL('/api/v1/messages', mail))).json();
const previousMessageIds = new Set((previousInbox.messages || []).map((m: any) => m.ID));
await request('/auth/magic-link/request', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ email, name: 'Document acceptance', institution: 'Synthetic test institution' }),
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
const aiHealth = await request('/api/ai/health');
assert.equal(aiHealth.primary_provider, null, 'Acceptance stack must have no primary AI provider');
assert.equal(aiHealth.fallback_provider, null, 'Acceptance stack must have no fallback AI provider');

async function approveRecordedFixes(scanId: string) {
  const before = await request(`/api/reviews/${scanId}`);
  const pending = before.fixes.filter((fix: any) => fix.review_status === 'pending');
  assert(pending.length > 0, 'Successful fixtures must exercise an actual pending review');
  for (const fix of pending) assert.match(fix.review_digest, /^[a-f0-9]{64}$/, 'Each reviewed change has a content digest');
  const [first, ...rest] = pending;
  const approved = await request(`/api/reviews/${scanId}/fixes/${first.id}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'approve', notes: 'Synthetic artifact inspected by acceptance gate' }),
  });
  assert.equal(approved.review_status, 'approved');
  if (rest.length) {
    const batch = await request(`/api/reviews/${scanId}/batch`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'approve', fix_ids: rest.map((fix: any) => fix.id) }),
    });
    assert.equal(batch.affected, rest.length, 'Batch response accounts for every selected fix');
  }
  // A fresh read must reconstruct decisions from storage, not the POST response.
  const after = await request(`/api/reviews/${scanId}`);
  assert.equal(after.status, 'approved');
  assert.equal(after.needs_review_count, 0);
  assert.equal(after.reviewed_count, pending.length);
  assert.deepEqual(after.fixes.map((f: any) => f.id).sort(), before.fixes.map((f: any) => f.id).sort());
  for (const original of pending) {
    const current = after.fixes.find((fix: any) => fix.id === original.id);
    assert.equal(current.review_status, 'approved');
    assert.equal(current.fixed_content, original.fixed_content, 'Approval cannot rewrite a recorded change');
    assert.equal(current.review_digest, original.review_digest);
    assert.equal(current.approved_review_digest, original.review_digest, 'Approval binds the exact reviewed content');
  }
  const audit = await request(`/api/reviews/${scanId}/audit/export?format=json`);
  assert.equal(audit.summary.is_conformance_determination, false);
  assert.equal(audit.summary.review_status_counts.approved, pending.length);
  assert(audit.audit_trail.some((entry: any) => entry.action === 'fix_approve'));
  if (rest.length) {
    const batchAudit = audit.audit_trail.find((entry: any) => entry.action === 'batch_approve');
    assert(batchAudit, 'Batch decisions have durable audit evidence');
    assert.equal(batchAudit.details.count, rest.length);
    assert.deepEqual([...batchAudit.details.fix_ids].sort(), rest.map((fix: any) => fix.id).sort());
  }
  return { reviewed: pending.length, audit_actions: audit.audit_trail.map((entry: any) => entry.action) };
}

async function scanBytes(bytes: Uint8Array, filename: string, kind: string) {
  const form = new FormData();
  form.append('file', new Blob([bytes]), filename);
  const upload = await request(`/education/${kind}/scan?generate_alt_text=false&enhance_descriptions=false&validate_alt_text=false`, { method: 'POST', body: form });
  const result = await poll(() => request(`/education/scans/${upload.scan_id}`),
    (v: any) => Boolean(v.scan?.result) || v.scan?.status?.toLowerCase() === 'failed');
  return result.scan;
}
const cases: { name: string; kind: string; publish: boolean; incomplete?: boolean; useAI?: boolean; file?: string }[] = [
  { name: 'course.pptx', kind: 'powerpoint', publish: true },
  { name: 'course.xlsx', kind: 'excel', publish: true },
  { name: 'course.docx', kind: 'word', publish: false },
  { name: 'metadata.pdf', kind: 'pdf', publish: true },
  { name: 'simple_syllabus.pdf', kind: 'pdf', publish: false },
  { name: 'test_forms_links.pdf', kind: 'pdf', publish: true },
  { name: 'academic_paper.pdf', kind: 'pdf', publish: false, incomplete: true },
  { name: 'image_heavy.pptx', kind: 'powerpoint', file: 'tests/fixtures/powerpoint/image_heavy.pptx', publish: false, useAI: true },
];
const evidence: object[] = [];
const failures: string[] = [];
for (const test of cases) {
  const scenario = test.useAI ? `${test.name} (AI requested, no provider)` : test.name;
  try {
    const file = test.file ?? (['simple_syllabus.pdf', 'test_forms_links.pdf', 'academic_paper.pdf'].includes(test.name)
      ? `tests/fixtures/pdfs/${test.name}` : `tests/fixtures/document_stack/${test.name}`);
    const bytes = await readFile(file);
    const scan = await scanBytes(bytes, test.name, test.kind);
    const scanId = scan.scan_id || scan.id;
    assert(scanId, 'Scan has a durable identifier');
    if (test.incomplete) {
      assert.equal(scan.status.toLowerCase(), 'failed');
      assert.equal(scan.result, null, 'Incomplete scans must not publish a score or findings');
      const progress = await request(`/education/scans/${scanId}/progress`);
      assert.equal(progress.error_message, 'Required accessibility checks could not be completed. No score is available. Review the document manually before relying on its accessibility.');
      assert.equal(progress.progress_message, progress.error_message);
      const retry = await request(`/education/scans/${scanId}`);
      assert.equal(retry.scan.result, null, 'Reload must retain the incomplete outcome');
      evidence.push({ fixture: test.name, scan_id: scanId, status: scan.status, source_sha256: digest(bytes), error_message: progress.error_message });
      console.log(`PASS ${test.name}: incomplete scan, actionable refusal, no certified score`);
      continue;
    }
    assert.equal(scan.status.toLowerCase(), 'completed');
    const total = scan.result.issues.length;
    assert(total > 0, `${test.name} must exercise real findings`);
    if (test.useAI) {
      assert(scan.result.issues.some((issue: any) => /missing alt text/i.test(issue.description)),
        'Unavailable-AI case must include actual missing image descriptions');
    }
    const queued = await request(`/education/remediate/${scanId}?use_ai=${test.useAI === true}&verify_fixes=true`, {
      method: 'POST', headers: { Prefer: 'respond-async', 'Content-Type': 'application/json' }, body: '{}',
    });
    assert(queued.job_id, 'Remediation uses the durable queue');
    const job = await poll(() => request(`/education/remediation/jobs/${queued.job_id}`),
      (v: any) => ['completed', 'failed', 'cancelled', 'dead_letter'].includes(v.status));
    const latest = await request(`/education/scans/${scanId}/remediation/latest`);
    assert.equal(latest.job_id, job.job_id, 'Reload resolves the same authoritative job');
    for (const key of ['status', 'artifact_id', 'download_available', 'fixed_count', 'remaining_count', 'remediated_score', 'score_verified']) {
      assert.deepEqual(latest[key], job[key], `Reload preserves ${key}`);
    }
    assert.equal(job.total_issues, total, 'Job retains all source findings');
    const downloadPath = `/education/remediation/jobs/${queued.job_id}/download`;
    const downloaded = await fetch(new URL(downloadPath, api), { headers: headers(), redirect: 'error' });
    let reviewEvidence: object | undefined;
    if (!test.publish) {
      assert.equal(job.status, 'failed', 'Withheld remediation must not report successful completion');
      assert.equal(job.download_available, false);
      assert.equal(job.artifact_id, null);
      assert.equal(downloaded.status, 404, 'Withheld output cannot be downloaded');
      assert.equal(job.fixed_count, 0);
      assert.equal(job.remediated_score, null);
      assert.equal(job.score_verified, false);
      assert.equal(job.remaining_count, total, 'Withheld changes remain unresolved');
      if (test.useAI) assert(job.manual_count + job.failed_count > 0, 'Missing descriptions remain unresolved without a provider');
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
      reviewEvidence = await approveRecordedFixes(scanId);
      const reviewedJob = await request(`/education/scans/${scanId}/remediation/latest`);
      assert.equal(reviewedJob.job_id, job.job_id);
      assert.equal(reviewedJob.artifact_id, job.artifact_id, 'Review retains the exact managed artifact');
      assert.deepEqual(reviewedJob.score_measurement, job.score_measurement, 'Approval is not a new scanner measurement');
      assert.equal(reviewedJob.score_verified, job.score_verified);
      assert.equal(reviewedJob.fixed_count, job.fixed_count);
      assert.equal(reviewedJob.remaining_count, job.remaining_count);
      const afterReview = await fetch(new URL(downloadPath, api), { headers: headers(), redirect: 'error' });
      assert.equal(afterReview.status, 200);
      assert.equal(digest(new Uint8Array(await afterReview.arrayBuffer())), digest(saved), 'Post-approval download is byte-identical to inspected output');
    }
    evidence.push({ fixture: test.name, scenario, scan_id: scanId, job_id: queued.job_id, status: job.status, total, remaining: job.remaining_count, source_sha256: digest(bytes), score_measurement: job.score_measurement, download_status: downloaded.status, review: reviewEvidence });
    console.log(`PASS ${scenario}: real scan, queued worker, persisted outcome, download boundary${test.publish ? ', approval persistence and audit' : ''}`);
  } catch (error) {
    const failure = `${scenario}: ${error instanceof Error ? error.message : 'Unknown failure'}`;
    failures.push(failure);
    console.error(`FAIL ${failure}`);
  }
}
await verifyLatexCorpus({ request, poll, output, evidence, failures,
  download: (path, init = {}) => fetch(authenticatedTarget(path, api), { ...init, headers: headers(init.headers), redirect: 'error', signal: AbortSignal.timeout(30000) }),
});
assert.deepEqual(await captureHarness(), harnessHashes, 'Queue harness changed during execution');
assert.equal(execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(), revision);
await writeFile(resolve(output, 'report.json'), JSON.stringify({
  revision, tracked_diff_sha256: digest(trackedDiff), harness_sha256: harnessHashes,
  node_version: process.version, configuration: { ai: false, latex_formats: ['tex'] },
  evidence, failures,
}, null, 2));
assert.equal(failures.length, 0, 'Real document stack gate failed; see report.json');
