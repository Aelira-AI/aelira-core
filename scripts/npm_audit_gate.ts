#!/usr/bin/env node
/** Gate for the shipped-surface npm audits. Wraps the npm audit command it is
 * given in a scoped, loud, time-boxed exception so the single unpatchable
 * advisory can be excepted by identifier while everything else still fails.
 *
 * The command runs with JSON output and the parsed v2 audit report decides:
 * - exit 0, quiet, when there is no high or critical finding;
 * - exit 0 with a loud warning banner when every high or critical finding
 *   chains exclusively to an excepted advisory identifier;
 * - exit 1 with the normal per-package failure summary when any other high or
 *   critical advisory is present;
 * - exit 2 when npm audit itself errors or emits unparseable output, so a
 *   broken audit never reads as a pass.
 *
 * Exceptions are scoped by advisory identifier (GHSA id or CVE id), never by
 * package-name globbing, a lowered audit level, or an error mask.
 * REMOVE THIS EXCEPTION when the picomatch 4.0.8+ lockfile refresh lands
 * (issue #1025): delete the entry below and restore the bare npm audit
 * commands in .github/workflows/ci.yml.
 */
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

type ViaAdvisory = { url?: unknown; title?: unknown; severity?: unknown };
type AuditVulnerability = { severity?: unknown; via?: unknown; range?: unknown };
type AuditReport = {
  auditReportVersion?: unknown;
  vulnerabilities?: Record<string, AuditVulnerability>;
  error?: unknown;
};

export type AuditException = {
  advisory: string;
  cve: string;
  issue: number;
  removal: string;
};

/** REMOVE THIS EXCEPTION when the picomatch 4.0.8+ lockfile refresh lands
 * (issue #1025). Scoped to this one advisory identifier; every other high or
 * critical advisory still fails the gate. */
export const AUDIT_EXCEPTIONS: AuditException[] = [
  {
    advisory: 'GHSA-fc5q-39xc-vjpf',
    cve: 'CVE-2026-112366',
    issue: 1025,
    removal:
      'REMOVE THIS EXCEPTION when the picomatch 4.0.8+ lockfile refresh lands (issue #1025).',
  },
];

const FAILING_SEVERITIES = new Set(['high', 'critical']);
const RULE = '='.repeat(72);

export type Verdict =
  | { kind: 'clean' }
  | { kind: 'excepted'; exceptions: AuditException[]; packages: string[] }
  | { kind: 'fail'; packages: string[]; matched: AuditException[]; excepted: string[] }
  | { kind: 'error'; reason: string };

function advisoryVias(via: unknown): ViaAdvisory[] {
  if (!Array.isArray(via)) return [];
  return via.filter((entry): entry is ViaAdvisory => typeof entry === 'object' && entry !== null);
}

function dependencyVias(via: unknown): string[] {
  if (!Array.isArray(via)) return [];
  return via.filter((entry): entry is string => typeof entry === 'string');
}

/** True when a via entry names one of the excepted advisory identifiers. */
function mentionsException(via: ViaAdvisory, exceptions: AuditException[]): boolean {
  const blob = JSON.stringify(via).toLowerCase();
  return exceptions.some(
    (exception) =>
      blob.includes(exception.advisory.toLowerCase()) || blob.includes(exception.cve.toLowerCase()),
  );
}

function matchedExceptions(vulns: Record<string, AuditVulnerability>, exceptions: AuditException[]): AuditException[] {
  return exceptions.filter((exception) =>
    Object.values(vulns).some((vulnerability) =>
      advisoryVias(vulnerability.via).some((advisory) => mentionsException(advisory, [exception])),
    ),
  );
}

/** Evaluate a parsed npm audit v2 report. Failures are computed with a fixpoint
 * over the via chains: a package fails when it is itself covered by a
 * non-excepted advisory or reaches one through vulnerable dependencies. */
export function evaluateReport(report: AuditReport, exceptions: AuditException[]): Verdict {
  if (
    report.auditReportVersion !== 2 ||
    typeof report.vulnerabilities !== 'object' ||
    report.vulnerabilities === null
  ) {
    return { kind: 'error', reason: 'npm audit output is not a v2 audit report' };
  }
  const vulns = report.vulnerabilities;
  const failing = new Set<string>();
  for (;;) {
    let grew = false;
    for (const [name, vulnerability] of Object.entries(vulns)) {
      if (failing.has(name)) continue;
      if (!FAILING_SEVERITIES.has(String(vulnerability.severity))) continue;
      const advisories = advisoryVias(vulnerability.via);
      const dependencies = dependencyVias(vulnerability.via);
      const unexcepted = advisories.some((advisory) => !mentionsException(advisory, exceptions));
      const reachedFailing = dependencies.some((dependency) => failing.has(dependency));
      const unexplained = advisories.length === 0 && dependencies.length === 0;
      if (unexcepted || reachedFailing || unexplained) {
        failing.add(name);
        grew = true;
      }
    }
    if (!grew) break;
  }
  const matched = matchedExceptions(vulns, exceptions);
  const exceptedPackages = Object.entries(vulns)
    .filter(([name, vulnerability]) => FAILING_SEVERITIES.has(String(vulnerability.severity)) && !failing.has(name))
    .map(([name]) => name);
  const failingPackages = Object.keys(vulns).filter((name) => failing.has(name));
  if (failingPackages.length > 0) return { kind: 'fail', packages: failingPackages, matched, excepted: exceptedPackages };
  if (matched.length === 0) return { kind: 'clean' };
  return { kind: 'excepted', exceptions: matched, packages: exceptedPackages };
}

/** Parse the stdout of npm audit --json. Error envelopes, empty output and
 * anything that is not JSON become error verdicts. */
export function parseAuditStdout(stdout: string): { report?: AuditReport; error?: string } {
  const text = stdout.trim();
  if (text === '') return { error: 'npm audit produced no output at all' };
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    const firstLine = text.split('\n', 1)[0].slice(0, 200);
    return { error: `npm audit output is not valid JSON, first line: ${firstLine}` };
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return { error: 'npm audit output is not a JSON object' };
  }
  const report = parsed as AuditReport;
  if (report.error !== undefined) {
    return { error: `npm audit reported an error: ${JSON.stringify(report.error).slice(0, 500)}` };
  }
  return { report };
}

/** npm-style failure summary over the packages that are not excepted. */
export function failureSummary(
  report: AuditReport,
  packages: string[],
  exceptions: AuditException[],
): string {
  const vulns = report.vulnerabilities ?? {};
  const critical = packages.filter((name) => String(vulns[name]?.severity) === 'critical').length;
  const high = packages.filter((name) => String(vulns[name]?.severity) === 'high').length;
  const counts: string[] = [];
  if (critical > 0) counts.push(`${critical} critical`);
  if (high > 0) counts.push(`${high} high`);
  const label = counts.length > 0 ? counts.join(' and ') : String(packages.length);
  const lines: string[] = [
    `${label} severity vulnerabilit${packages.length === 1 ? 'y' : 'ies'} not covered by the scoped exception`,
    '',
  ];
  for (const name of packages) {
    const vulnerability = vulns[name] ?? {};
    lines.push(`${name}  ${String(vulnerability.range ?? '')}`.trimEnd());
    const advisories = advisoryVias(vulnerability.via).filter(
      (advisory) => !mentionsException(advisory, exceptions),
    );
    const dependencies = dependencyVias(vulnerability.via).filter((dependency) => packages.includes(dependency));
    if (advisories.length > 0) {
      for (const advisory of advisories) {
        lines.push(`  ${String(advisory.title ?? 'advisory')} - ${String(advisory.url ?? '')}`);
      }
    } else if (dependencies.length > 0) {
      lines.push(`  via ${dependencies.join(', ')}`);
    }
    lines.push('');
  }
  return lines.join('\n').trimEnd();
}

export function exceptionBanner(command: string[], exceptions: AuditException[], packages: string[]): string {
  const lines = [RULE, `WARNING: npm audit exception applied while running: ${command.join(' ')}`];
  for (const exception of exceptions) {
    lines.push(
      `  Excepted advisory : ${exception.advisory} (${exception.cve})`,
      `  Tracking issue    : #${exception.issue}`,
      `  ${exception.removal}`,
    );
  }
  lines.push(
    `  High-severity findings excepted in this tree: ${packages.length} (${packages.join(', ')})`,
    '  Every other high or critical advisory still fails this gate.',
    RULE,
  );
  return lines.join('\n');
}

export function main(argv: string[], cwd: string = process.cwd()): number {
  const command = argv;
  if (command.length === 0 || command[0] !== 'npm' || !command.includes('audit')) {
    console.error('usage: npm_audit_gate.ts npm --prefix <dir> audit --audit-level=<level>');
    return 2;
  }
  const shown = command.join(' ');
  const result = spawnSync(command[0], [...command.slice(1), '--json'], { encoding: 'utf8', cwd });
  if (result.error) {
    console.log(`${RULE}\nGATE FAILURE: could not run ${shown}: ${result.error.message}\n${RULE}`);
    return 2;
  }
  if (result.status === null) {
    console.log(`${RULE}\nGATE FAILURE: ${shown} was killed without an exit code\n${RULE}`);
    return 2;
  }
  const { report, error } = parseAuditStdout(result.stdout ?? '');
  if (error || !report) {
    console.log(`${RULE}\nGATE FAILURE: ${shown} could not be verified.\n  ${error ?? 'no report'}\nA failed or unparseable audit is treated as a gate failure, never as a pass.\n${RULE}`);
    return 2;
  }
  if (result.status > 1) {
    console.log(`${RULE}\nGATE FAILURE: ${shown} exited with code ${result.status}.\n${RULE}`);
    return 2;
  }
  const verdict = evaluateReport(report, AUDIT_EXCEPTIONS);
  if (verdict.kind === 'error') {
    console.log(`${RULE}\nGATE FAILURE: ${shown}: ${verdict.reason}\n${RULE}`);
    return 2;
  }
  if (verdict.kind === 'clean' && result.status !== 0) {
    console.log(`${RULE}\nGATE FAILURE: ${shown} exited with code ${result.status} while reporting no vulnerabilities.\n${RULE}`);
    return 2;
  }
  if (verdict.kind === 'excepted') {
    const banner = exceptionBanner(command, verdict.exceptions, verdict.packages);
    console.log(banner);
    for (const exception of verdict.exceptions) {
      console.log(
        `::warning title=npm audit exception applied::${exception.advisory} (${exception.cve}) excepted on ${shown} - tracking issue #${exception.issue} - ${exception.removal}`,
      );
    }
    return 0;
  }
  if (verdict.kind === 'fail') {
    if (verdict.matched.length > 0) {
      console.log(exceptionBanner(command, verdict.matched, verdict.excepted));
    }
    console.log(failureSummary(report, verdict.packages, AUDIT_EXCEPTIONS));
    return 1;
  }
  return 0;
}

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = main(process.argv.slice(2), repoRoot);
}
