/** Tests for the scoped npm audit exception gate, including the crafted
 * fixtures: the picomatch-only tree passes with the real exception and fails
 * when the same tree is checked against a dummy identifier, an unrelated extra
 * advisory still fails, and broken audit output hard-fails. */
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  AUDIT_EXCEPTIONS,
  evaluateReport,
  exceptionBanner,
  failureSummary,
  parseAuditStdout,
} from './npm_audit_gate.ts';

const DUMMY_EXCEPTION = {
  advisory: 'GHSA-0000-0000-0000',
  cve: 'CVE-2099-000001',
  issue: 0,
  removal: 'test placeholder',
};

const PICOMATCH_VIA = {
  source: 1130001,
  name: 'picomatch',
  dependency: '*',
  title: 'picomatch vulnerable to ReDoS through single-branch repeated extglobs',
  url: 'https://github.com/advisories/GHSA-fc5q-39xc-vjpf',
  severity: 'high',
  cves: ['CVE-2026-112366'],
};

/** Mirrors the shipped shape: the advisory sits on picomatch and every other
 * high finding reaches it through fdir and tinyglobby. */
const picomatchTree = {
  auditReportVersion: 2,
  vulnerabilities: {
    picomatch: { severity: 'high', via: [PICOMATCH_VIA], effects: ['fdir', 'tinyglobby'], range: '*' },
    fdir: { severity: 'high', via: ['picomatch'], effects: ['tinyglobby'], range: '>=5.2.1' },
    tinyglobby: { severity: 'high', via: ['fdir', 'picomatch'], effects: ['@oclif/core'], range: '*' },
    '@oclif/core': { severity: 'high', via: ['tinyglobby'], effects: [], range: '>=4.3.1' },
  },
};

const cleanReport = { auditReportVersion: 2, vulnerabilities: {} };

/** The picomatch tree plus one unrelated high advisory nothing is excepted for. */
const extraAdvisoryTree = structuredClone(picomatchTree);
extraAdvisoryTree.vulnerabilities['vite'] = {
  severity: 'high',
  via: [
    {
      source: 1130002,
      name: 'vite',
      dependency: '>=6.3.0 <7',
      title: 'Unrelated development server bypass',
      url: 'https://github.com/advisories/GHSA-aaaa-bbbb-cccc',
      severity: 'high',
    },
  ],
  effects: [],
  range: '>=6.3.0 <7',
};

/** picomatch covered by the excepted advisory and one more advisory. */
const doubleAdvisoryTree = structuredClone(picomatchTree);
doubleAdvisoryTree.vulnerabilities['picomatch'].via.push({
  source: 1130003,
  name: 'picomatch',
  dependency: '*',
  title: 'Another open picomatch advisory',
  url: 'https://github.com/advisories/GHSA-dddd-eeee-ffff',
  severity: 'critical',
});

test('tree whose only high findings chain to the excepted advisory passes', () => {
  const verdict = evaluateReport(picomatchTree, AUDIT_EXCEPTIONS);
  assert.equal(verdict.kind, 'excepted');
  if (verdict.kind === 'excepted') {
    assert.deepEqual(verdict.exceptions.map((entry) => entry.advisory), ['GHSA-fc5q-39xc-vjpf']);
    assert.deepEqual(verdict.packages, ['picomatch', 'fdir', 'tinyglobby', '@oclif/core']);
    const banner = exceptionBanner(['npm', 'audit'], verdict.exceptions, verdict.packages);
    assert.match(banner, /GHSA-fc5q-39xc-vjpf/);
    assert.match(banner, /CVE-2026-112366/);
    assert.match(banner, /#1025/);
    assert.match(banner, /REMOVE THIS EXCEPTION when the picomatch 4\.0\.8\+ lockfile refresh lands/);
  }
});

test('the same tree fails when the allowlist holds only a dummy identifier', () => {
  const verdict = evaluateReport(picomatchTree, [DUMMY_EXCEPTION]);
  assert.equal(verdict.kind, 'fail');
  if (verdict.kind === 'fail') {
    assert.deepEqual(verdict.packages, ['picomatch', 'fdir', 'tinyglobby', '@oclif/core']);
    assert.deepEqual(verdict.matched, []);
  }
});

test('a clean tree passes quietly', () => {
  const verdict = evaluateReport(cleanReport, AUDIT_EXCEPTIONS);
  assert.equal(verdict.kind, 'clean');
});

test('an unrelated extra advisory still fails the gate', () => {
  const verdict = evaluateReport(extraAdvisoryTree, AUDIT_EXCEPTIONS);
  assert.equal(verdict.kind, 'fail');
  if (verdict.kind === 'fail') {
    assert.deepEqual(verdict.packages, ['vite']);
    assert.ok(verdict.excepted.includes('picomatch'));
    assert.ok(verdict.excepted.includes('@oclif/core'));
    assert.ok(verdict.matched.some((entry) => entry.advisory === 'GHSA-fc5q-39xc-vjpf'));
    const summary = failureSummary(extraAdvisoryTree, verdict.packages, AUDIT_EXCEPTIONS);
    assert.match(summary, /vite/);
    assert.match(summary, /GHSA-aaaa-bbbb-cccc/);
    assert.doesNotMatch(summary, /picomatch/);
  }
});

test('a package covered by the excepted advisory and one more still fails', () => {
  const verdict = evaluateReport(doubleAdvisoryTree, AUDIT_EXCEPTIONS);
  assert.equal(verdict.kind, 'fail');
  if (verdict.kind === 'fail') {
    assert.ok(verdict.packages.includes('picomatch'));
    assert.ok(verdict.packages.includes('@oclif/core'));
    assert.ok(verdict.matched.some((entry) => entry.advisory === 'GHSA-fc5q-39xc-vjpf'));
  }
});

test('a moderate advisory below the gate level does not fail', () => {
  const report = {
    auditReportVersion: 2,
    vulnerabilities: {
      glob: {
        severity: 'moderate',
        via: [{ name: 'glob', title: 'Moderate issue', url: 'https://github.com/advisories/GHSA-3g3m-2fjr-6pw4', severity: 'moderate' }],
        effects: [],
        range: '9.0.0 - 14.0.1',
      },
    },
  };
  assert.equal(evaluateReport(report, AUDIT_EXCEPTIONS).kind, 'clean');
});

test('a high finding with no via entries fails as unexplained', () => {
  const report = { auditReportVersion: 2, vulnerabilities: { mystery: { severity: 'high', via: [], effects: [], range: '*' } } };
  assert.equal(evaluateReport(report, AUDIT_EXCEPTIONS).kind, 'fail');
});

test('npm audit error envelopes become gate errors', () => {
  const parsed = parseAuditStdout('{"error":{"code":"EAI_AGAIN","summary":"network"}}');
  assert.match(parsed.error ?? '', /EAI_AGAIN/);
});

test('unparseable npm audit output becomes a gate error', () => {
  assert.match(parseAuditStdout('npm ERR! something exploded').error ?? '', /not valid JSON/);
  assert.match(parseAuditStdout('').error ?? '', /no output/);
  assert.match(parseAuditStdout('[]').error ?? '', /not a JSON object/);
});

test('a report that is not auditReportVersion 2 becomes a gate error', () => {
  const parsed = parseAuditStdout('{"auditReportVersion":1,"advisories":{}}');
  assert.equal(parsed.error, undefined);
  const verdict = evaluateReport(JSON.parse('{"auditReportVersion":1,"advisories":{}}'), AUDIT_EXCEPTIONS);
  assert.equal(verdict.kind, 'error');
});
