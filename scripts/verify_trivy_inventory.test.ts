import { describe, expect, test } from 'bun:test';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { inspectInventory, verifyDispositions, verifyInventory } from './verify_trivy_inventory';

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

describe('reviewed dispositions for unfixed distribution findings', () => {
  const today = '2026-10-15';
  const entry = {
    id: 'CVE-2026-6653', osFamily: 'ubuntu', packages: [{ name: 'libxml2', installedVersion: '2.9.14' }],
    status: 'affected', reason: 'No distribution fix exists', reviewed: '2026-10-15', expires: '2027-01-15',
    disclosure: 'docs/releases/example.md',
  };
  const withFindings = (...rows: object[]) => {
    const value = clean();
    return { ...value, Results: [{ ...value.Results[0], Vulnerabilities: rows }] };
  };
  const dispositions = verifyDispositions({ schemaVersion: 1, dispositions: [entry] }, today).dispositions;

  test('reports an exactly matching unfixed HIGH OS finding as accepted, not unresolved', () => {
    for (const FixedVersion of [undefined, '']) {
      const result = inspectInventory(withFindings(finding('HIGH', FixedVersion)), image, 'arm64', dispositions);
      expect(result.errors).toEqual([]);
      expect(result.accepted).toEqual([entry]);
    }
  });
  test('still rejects a HIGH finding once a fix is available', () => {
    expect(verifyInventory(withFindings(finding('HIGH', '2.15.4')), image, 'arm64', dispositions))
      .toContain('Unresolved image findings: 1 HIGH, 0 CRITICAL');
  });
  test('never accepts CRITICAL findings as affected', () => {
    expect(verifyInventory(withFindings(finding('CRITICAL')), image, 'arm64', dispositions))
      .toContain('Unresolved image findings: 0 HIGH, 1 CRITICAL');
  });
  test('accepts unfixed HIGH or CRITICAL findings as justified not_affected', () => {
    const unreachable = { ...entry, status: 'not_affected', justification: 'vulnerable_code_not_in_execute_path' };
    const checked = verifyDispositions({ schemaVersion: 1, dispositions: [unreachable] }, today);
    expect(checked.errors).toEqual([]);
    for (const severity of ['HIGH', 'CRITICAL']) {
      const result = inspectInventory(withFindings(finding(severity)), image, 'arm64', checked.dispositions);
      expect(result.errors).toEqual([]);
      expect(result.accepted).toEqual([unreachable]);
    }
    expect(verifyInventory(withFindings(finding('CRITICAL', '2.15.4')), image, 'arm64', checked.dispositions))
      .toContain('Unresolved image findings: 0 HIGH, 1 CRITICAL');
  });
  test('never accepts language-package findings', () => {
    const value = clean();
    const lang = { Target: 'Python', Type: 'python-pkg', Class: 'lang-pkgs', Vulnerabilities: [finding('HIGH')] };
    expect(verifyInventory({ ...value, Results: [...value.Results, lang] }, image, 'arm64', dispositions))
      .toContain('Unresolved image findings: 1 HIGH, 0 CRITICAL');
  });
  for (const [field, changed] of [
    ['VulnerabilityID', 'CVE-2026-6654'], ['PkgName', 'libxml2-utils'], ['InstalledVersion', '2.9.15'],
  ] as const) {
    test(`requires an exact ${field} match`, () => {
      expect(verifyInventory(withFindings({ ...finding('HIGH'), [field]: changed }), image, 'arm64', dispositions))
        .toContain('Unresolved image findings: 1 HIGH, 0 CRITICAL');
    });
  }
  test('requires the scanned OS family to match', () => {
    const value = withFindings(finding('HIGH'));
    const other = { ...value, Metadata: { ...value.Metadata, OS: { Family: 'debian', Name: '13.7' } },
      Results: [{ ...value.Results[0], Type: 'debian' }] };
    expect(verifyInventory(other, image, 'arm64', dispositions))
      .toContain('Unresolved image findings: 1 HIGH, 0 CRITICAL');
  });
  test('matches any listed binary package of the same advisory', () => {
    const shared = { ...entry, packages: [...entry.packages, { name: 'libxml2-utils', installedVersion: '2.9.14' }] };
    const accepted = verifyDispositions({ schemaVersion: 1, dispositions: [shared] }, today).dispositions;
    const result = inspectInventory(withFindings(finding('HIGH'), { ...finding('HIGH'), PkgName: 'libxml2-utils' }),
      image, 'arm64', accepted);
    expect(result.errors).toEqual([]);
  });
  test('counts unmatched rows alongside accepted ones', () => {
    const result = inspectInventory(withFindings(finding('HIGH'), { ...finding('HIGH'), PkgName: 'other' }),
      image, 'arm64', dispositions);
    expect(result.errors).toContain('Unresolved image findings: 1 HIGH, 0 CRITICAL');
  });
  test('rejects expired, future, overlong, duplicate and malformed dispositions', () => {
    const check = (dispositionList: unknown[]) =>
      verifyDispositions({ schemaVersion: 1, dispositions: dispositionList }, today).errors;
    expect(check([{ ...entry, expires: '2026-10-15' }])).toContain('Disposition expired: CVE-2026-6653');
    expect(check([{ ...entry, reviewed: '2026-10-16' }])).toContain('Disposition reviewed in the future: CVE-2026-6653');
    expect(check([{ ...entry, expires: '2027-03-01' }])).toContain('Disposition review window exceeds 120 days: CVE-2026-6653');
    expect(check([entry, entry]).join(' ')).toContain('Duplicate disposition');
    for (const bad of [{ ...entry, reason: '' }, { ...entry, id: 'GHSA-xxxx' }, { ...entry, expires: '2027-02-30' },
      { ...entry, disclosure: undefined }, { ...entry, packages: [] }, { ...entry, packages: [{ name: 'libxml2' }] },
      { ...entry, status: undefined }, { ...entry, status: 'fixed' },
      { ...entry, justification: 'vulnerable_code_not_in_execute_path' },
      { ...entry, status: 'not_affected' }, { ...entry, status: 'not_affected', justification: 'trust me' },
      null]) {
      expect(check([bad])).toContain('Dispositions contain a malformed entry');
    }
    expect(verifyDispositions({ dispositions: [] }, today).errors.length).toBeGreaterThan(0);
  });
  test('CLI requires the disclosure to name each accepted finding', () => {
    const directory = mkdtempSync(join(tmpdir(), 'image-disposition-test-'));
    const file = join(directory, 'inventory.json');
    const notes = join(directory, 'notes.md');
    const list = join(directory, 'dispositions.json');
    const run = () => spawnSync(process.execPath, [join(import.meta.dir, 'verify_trivy_inventory.ts'),
      '--file', file, '--image', image, '--architecture', 'arm64', '--dispositions', list, '--today', today],
    { encoding: 'utf8' });
    try {
      writeFileSync(file, JSON.stringify(withFindings(finding('HIGH'))));
      writeFileSync(list, JSON.stringify({ schemaVersion: 1, dispositions: [{ ...entry, disclosure: notes }] }));
      writeFileSync(notes, 'No advisories listed.');
      const undisclosed = run();
      expect(undisclosed.status).toBe(1);
      expect(undisclosed.stderr).toContain(`does not name ${entry.id}`);
      writeFileSync(notes, `Known unfixed: ${entry.id}`);
      const disclosed = run();
      expect(disclosed.status).toBe(0);
      expect(disclosed.stdout).toContain(`Reported unfixed ${entry.id} (affected)`);
    } finally {
      rmSync(directory, { recursive: true });
    }
  });
});

test('the repository disposition list is valid and unexpired today', async () => {
  const file = join(import.meta.dir, '..', '.trivy-unfixed-dispositions.json');
  const value = JSON.parse(await Bun.file(file).text());
  const today = new Date().toISOString().slice(0, 10);
  expect(verifyDispositions(value, today).errors).toEqual([]);
  for (const entry of value.dispositions) {
    expect(await Bun.file(join(import.meta.dir, '..', entry.disclosure)).text()).toContain(entry.id);
  }
});
