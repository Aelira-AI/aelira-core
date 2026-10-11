import { describe, expect, test } from 'bun:test';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { verifyInventory } from './verify_trivy_inventory';

const image = `ghcr.io/example/api@sha256:${'1'.repeat(64)}`;
function clean(architecture: 'amd64' | 'arm64' = 'arm64') {
  return {
    SchemaVersion: 2,
    Trivy: { Version: '0.74.0' },
    ArtifactType: 'container_image',
    ArtifactName: image,
    Metadata: {
      ImageID: `sha256:${'2'.repeat(64)}`,
      ImageConfig: { architecture, os: 'linux' },
      OS: { Family: 'ubuntu', Name: '26.04' },
    },
    Results: [{ Target: `${image} (ubuntu 26.04)`, Type: 'ubuntu', Class: 'os-pkgs' }],
  };
}
const finding = (Severity: string, FixedVersion?: string) => ({
  VulnerabilityID: 'CVE-2026-6653', PkgName: 'libxml2', InstalledVersion: '2.9.14',
  Severity, ...(FixedVersion === undefined ? {} : { FixedVersion }),
});

describe('complete vulnerability inventory release gate', () => {
  for (const architecture of ['amd64', 'arm64'] as const) {
    test(`accepts a genuinely empty ${architecture} OS scan`, () => {
      expect(verifyInventory(clean(architecture), image, architecture)).toEqual([]);
    });
  }
  for (const Severity of ['HIGH', 'CRITICAL']) {
    for (const FixedVersion of [undefined, '', '2.15.4']) {
      test(`rejects ${Severity} regardless of fix availability ${String(FixedVersion)}`, () => {
        const value = clean();
        const result = { ...value.Results[0], Vulnerabilities: [finding(Severity, FixedVersion)] };
        expect(verifyInventory({ ...value, Results: [result] }, image, 'arm64').join(' '))
          .toContain('Unresolved image findings');
      });
    }
  }
  test('counts every package match rather than hiding duplicate advisory IDs', () => {
    const value = clean();
    const result = { ...value.Results[0], Vulnerabilities: [finding('CRITICAL'), finding('CRITICAL')] };
    expect(verifyInventory({ ...value, Results: [result] }, image, 'arm64'))
      .toContain('Unresolved image findings: 0 HIGH, 2 CRITICAL');
  });
  test('rejects embedded language-package HIGH findings too', () => {
    const value = clean();
    const result = { Target: 'Python', Type: 'python-pkg', Class: 'lang-pkgs', Vulnerabilities: [finding('HIGH')] };
    expect(verifyInventory({ ...value, Results: [...value.Results, result] }, image, 'arm64').length).toBeGreaterThan(0);
  });
  test('accepts standard secret results without an optional Type', () => {
    const value = clean();
    const result = { Target: '/app/example.env', Class: 'secret', Secrets: [] };
    expect(verifyInventory({ ...value, Results: [...value.Results, result] }, image, 'arm64')).toEqual([]);
  });
  test('does not exempt vulnerability arrays on secret results', () => {
    const value = clean();
    const result = { Target: '/app/example.env', Class: 'secret', Vulnerabilities: [finding('HIGH')] };
    expect(verifyInventory({ ...value, Results: [...value.Results, result] }, image, 'arm64'))
      .toContain('Unresolved image findings: 1 HIGH, 0 CRITICAL');
  });
  test('rejects unknown result classes and missing package types', () => {
    const value = clean();
    for (const result of [
      { Target: 'unknown', Class: 'unknown', Type: 'unknown' },
      { Target: 'Python', Class: 'lang-pkgs' },
    ]) {
      expect(verifyInventory({ ...value, Results: [...value.Results, result] }, image, 'arm64').length)
        .toBeGreaterThan(0);
    }
  });
  for (const value of [null, [], {}, { ...clean(), SchemaVersion: 1 },
    { ...clean(), Trivy: undefined }, { ...clean(), Trivy: { Version: '0.70.0' } },
    { ...clean(), Results: [] }, { ...clean(), Results: null },
    { ...clean(), Metadata: {} }, { ...clean(), ArtifactType: 'filesystem' },
    { ...clean(), Results: [{ Target: 'Python', Type: 'python-pkg', Class: 'lang-pkgs' }] },
    { ...clean(), Results: [{ ...clean().Results[0], Vulnerabilities: {} }] },
    { ...clean(), Results: [{ ...clean().Results[0], Vulnerabilities: [{ ...finding('HIGH'), Severity: undefined }] }] },
    { ...clean(), Results: [{ ...clean().Results[0], Vulnerabilities: [{ ...finding('HIGH'), InstalledVersion: '' }] }] },
  ]) {
    test(`fails closed for incomplete or malformed report ${JSON.stringify(value)}`, () => {
      expect(verifyInventory(value, image, 'arm64').length).toBeGreaterThan(0);
    });
  }
  test('rejects the wrong image and architecture', () => {
    expect(verifyInventory(clean(), image + '-other', 'arm64').length).toBeGreaterThan(0);
    expect(verifyInventory(clean(), image, 'amd64').length).toBeGreaterThan(0);
  });
  test('keeps lower severity inventory visible without raising this severity gate', () => {
    const value = clean();
    const result = { ...value.Results[0], Vulnerabilities: [finding('MEDIUM')] };
    expect(verifyInventory({ ...value, Results: [result] }, image, 'arm64')).toEqual([]);
  });
});

test('CLI returns a nonzero exit for findings, malformed JSON and missing inputs', () => {
  const directory = mkdtempSync(join(tmpdir(), 'image-inventory-test-'));
  const file = join(directory, 'inventory.json');
  const run = (args: string[]) => spawnSync(process.execPath,
    [join(import.meta.dir, 'verify_trivy_inventory.ts'), ...args], { encoding: 'utf8' });
  const args = ['--file', file, '--image', image, '--architecture', 'arm64'];
  try {
    writeFileSync(file, JSON.stringify(clean()));
    expect(run(args).status).toBe(0);
    const value = clean();
    writeFileSync(file, JSON.stringify({ ...value,
      Results: [{ ...value.Results[0], Vulnerabilities: [finding('CRITICAL')] }] }));
    const rejected = run(args);
    expect(rejected.status).toBe(1);
    expect(rejected.stderr).toContain('0 HIGH, 1 CRITICAL');
    writeFileSync(file, '{');
    expect(run(args).status).toBe(1);
    expect(run([]).status).toBe(1);
  } finally {
    rmSync(directory, { recursive: true });
  }
});
