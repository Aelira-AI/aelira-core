import { describe, expect, test } from 'bun:test';
import {
  REQUIRED_CHECKS, buildOverride, filesMissingOrChanged, finalizeReceipt,
  isolatedEnvironment, newReceipt, parseArgs, parseFileManifest, redact, runId,
  runningImageMatches, tablesDiffer, tablesLost, validateReceipt,
} from './upgrade_rehearsal';

const digest = `sha256:${'a'.repeat(64)}`;
const args = ['--from', '0.9.12', '--to-api', 'example/api:candidate',
  '--to-dashboard', 'example/dashboard:candidate', '--output', '/tmp/rehearsal-test-unused.json'];

function passingReceipt() {
  const r = newReceipt('synthetic-control');
  r.steps = [{ id: 'run', name: 'Synthetic control', command: 'synthetic',
    started_at: new Date().toISOString(), duration_ms: 1, exit_code: 0, status: 'pass' }];
  r.checks = REQUIRED_CHECKS.map(id => ({ id, description: id, status: 'pass', evidence: true }));
  const image = { kind: 'local', index_digest: digest, platform_manifest_digest: digest };
  r.images = { from: { api: image, dashboard: image }, to: { api: image, dashboard: image } };
  return finalizeReceipt(r, null);
}

describe('rehearsal safety boundaries', () => {
  test('accepts an explicit release pair on six distinct local ports', () => {
    const o = parseArgs(args);
    expect(o.fromApi).toBe('ghcr.io/aelira-ai/aelira-core-api:0.9.12');
    expect(new Set([...Object.values(o.primary), ...Object.values(o.restore)]).size).toBe(6);
    const overlay = buildOverride({ api: o.toApi, dashboard: o.toDashboard }, o.primary, o.mailpitImage, 'linux/amd64');
    expect(overlay).toContain('127.0.0.1:18700:8000');
    expect(overlay).toContain('container_name: !reset null');
    expect(overlay.match(/platform: linux\/amd64/g)?.length).toBe(6);
  });

  test.each([
    ['--to-api', 'example/api:latest'],
    ['--to-api', 'example/api'],
    ['--from', '../../production'],
    ['--email', 'real@example.com'],
    ['--prefix', '../another-project'],
    ['--mailpit-image', 'mailpit:tag\ninvalid-reference'],
  ])('rejects an unsafe %s option', (name, value) => {
    const input = [...args];
    const index = input.indexOf(name);
    if (index < 0) input.push(name, value); else input[index + 1] = value;
    expect(() => parseArgs(input)).toThrow();
  });

  test('rejects collisions between primary and restoration ports', () => {
    expect(() => parseArgs([...args, '--restore-api-port', '18700'])).toThrow();
  });

  test('host application settings and Compose profiles cannot redirect a test stack', () => {
    const env = isolatedEnvironment({ PATH: '/bin', DATABASE_URL: 'outside-test',
      GEMINI_API_KEY: 'outside-test', COMPOSE_PROFILES: 'ollama', COMPOSE_FILE: 'other.yml',
      DOCKER_DEFAULT_PLATFORM: 'linux/amd64', STACK_ALLOW_LEGACY_CSRF_MISMATCH: 'true',
      STACK_SNAPSHOT: '/tmp/other-run.json' },
    '${DATABASE_URL:-local} ${GEMINI_API_KEY:-}');
    expect(env).toEqual({ PATH: '/bin' });
  });

  test('simultaneous invocations receive different resource identities', () => {
    const at = new Date('2026-01-01T00:00:00.100Z');
    expect(runId(at)).not.toBe(runId(at));
  });

  test('a shared multi-platform index cannot stand in for the running platform', () => {
    const manifest = `sha256:${'b'.repeat(64)}`;
    const expected = { index_digest: digest, platform_manifest_digest: manifest };
    expect(runningImageMatches(expected, digest,
      { digest: manifest, platform: { os: 'linux', architecture: 'arm64' } }, 'linux/arm64')).toBe(true);
    expect(runningImageMatches(expected, digest,
      { digest: manifest, platform: { os: 'linux', architecture: 'amd64' } }, 'linux/arm64')).toBe(false);
    expect(runningImageMatches(expected, digest, { digest }, 'linux/arm64')).toBe(false);
  });

  test('redacts generated secrets before recording subprocess errors', () => {
    expect(redact('failure using synthetic-secret-value', { 'synthetic-secret-value': '[REDACTED]' }))
      .toBe('failure using [REDACTED]');
  });
});

describe('receipt conclusions', () => {
  test('accepts a complete positive control', () => {
    const r = passingReceipt();
    expect(r.result.passed).toBe(true);
    expect(validateReceipt(r)).toEqual([]);
  });

  test('malformed collections return validation errors', () => {
    expect(validateReceipt({ ...passingReceipt(), checks: {} }).length).toBeGreaterThan(0);
    expect(validateReceipt({ ...passingReceipt(), steps: {} }).length).toBeGreaterThan(0);
  });

  test('a missing required operation prevents a passing result', () => {
    const r = passingReceipt();
    r.checks = r.checks.filter(c => c.id !== 'restore_files_exact');
    finalizeReceipt(r, null);
    expect(r.result.passed).toBe(false);
    expect(r.result.missing_checks).toContain('restore_files_exact');
    r.result.passed = true;
    expect(validateReceipt(r).length).toBeGreaterThan(0);
  });

  test('a failed subprocess cannot be hidden by a passing summary', () => {
    const r = passingReceipt();
    r.steps[0].status = 'fail';
    r.steps[0].exit_code = 1;
    expect(validateReceipt(r).length).toBeGreaterThan(0);
  });

  test('rejects duplicate check identities', () => {
    const r = passingReceipt();
    r.checks.push({ ...r.checks[0] });
    expect(validateReceipt(r).length).toBeGreaterThan(0);
  });

  test('rejects a failure inventory inconsistent with the recorded operations', () => {
    const r = passingReceipt();
    r.result.failed_checks = ['invented-failure'];
    expect(validateReceipt(r).length).toBeGreaterThan(0);
  });

  test('rejects unredacted local filesystem paths', () => {
    const r = passingReceipt();
    r.steps[0].command = '/Users/example/private/script.ts';
    expect(validateReceipt(r).length).toBeGreaterThan(0);
  });
});

describe('restoration evidence', () => {
  test('detects a missing table after upgrade and extra rows after restoration', () => {
    expect(tablesLost({ scans: 4, users: 1 }, { scans: 4 })).toEqual(['users']);
    expect(tablesDiffer({ scans: 4 }, { scans: 5 })).toEqual(['scans']);
  });

  test('detects changed downloaded bytes and missing files independently of names', () => {
    const before = parseFileManifest(`${'a'.repeat(64)}  ./one.pdf\n${'b'.repeat(64)}  ./two.pdf\n`);
    const after = parseFileManifest(`${'c'.repeat(64)}  ./one.pdf\n`);
    expect(filesMissingOrChanged(before, after)).toEqual(['./one.pdf', './two.pdf']);
  });
});
