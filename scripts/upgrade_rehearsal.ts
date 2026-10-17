/**
 * Reusable release upgrade, backup and rollback rehearsal.
 *
 *   bun scripts/upgrade_rehearsal.ts --from 0.9.12 \
 *     --to-api ghcr.io/aelira-ai/aelira-core-api:<tag> \
 *     --to-dashboard ghcr.io/aelira-ai/aelira-core-dashboard:<tag> \
 *     --output receipt.json
 *
 * It installs the `--from` release from its published images with the checked-in
 * production Compose file, seeds synthetic records through the real API and worker
 * (scripts/verify_upgrade_recovery.ts), takes the documented backup, upgrades to
 * the `--to` images, verifies retained and new work, then restores the backup into
 * fresh storage with the old images and verifies that state again. Everything runs
 * in throwaway Compose projects on loopback ports. The result is a JSON receipt of
 * image identities, commands, timings and verification outcomes.
 */
import { createHash, randomBytes } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { homedir, arch as hostArch, platform as hostPlatform } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const RECEIPT_SCHEMA = 'aelira-upgrade-rehearsal/1';

/** Every one of these must be present and passing for a receipt to claim success. */
export const REQUIRED_CHECKS = [
  'from_images_resolved',
  'to_images_resolved',
  'install_ready',
  'baseline_seeded',
  'backup_complete',
  'upgrade_migration_head',
  'upgraded_images_running',
  'upgraded_ready',
  'upgraded_data_survived',
  'upgraded_tables_retained',
  'upgraded_files_retained',
  'upgraded_new_scan',
  'restore_config_identical',
  'restore_tables_exact',
  'restore_files_exact',
  'rollback_images_running',
  'rollback_ready',
  'rollback_data_survived',
  'rollback_post_backup_excluded',
  'rollback_new_scan',
  'cleanup_complete',
] as const;

export class UsageError extends Error {}

export interface Ports { api: number; mail: number; dashboard: number }
export interface Options {
  from: string;
  fromApi: string;
  fromDashboard: string;
  toApi: string;
  toDashboard: string;
  pullTo: boolean;
  output: string;
  workDir: string;
  prefix: string;
  primary: Ports;
  restore: Ports;
  platform: string;
  email: string;
  mailpitImage: string;
  allowLegacyCsrf: boolean;
  expectLegacyNotification: boolean;
  keep: boolean;
}

const VERSION = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-alpha\.(0|[1-9]\d*))?$/;
const REF = /^[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?(@sha256:[0-9a-f]{64})?$/;
const PREFIX = /^[a-z][a-z0-9-]{2,30}$/;
const PLATFORM = /^linux\/(amd64|arm64)$/;
const EMAIL = /^[a-z0-9._+-]+@example\.org$/i;

export const USAGE = `Usage: bun scripts/upgrade_rehearsal.ts --from <version> --to-api <ref> --to-dashboard <ref> --output <receipt.json> [options]

Required
  --from <version>        Published release to install first, for example 0.9.12.
  --to-api <ref>          API/worker image of the candidate (tag or digest, never latest).
  --to-dashboard <ref>    Dashboard image of the candidate.
  --output <file>         Receipt path. The file must not exist.

Options
  --from-api <ref>        Override the from-release API image
                          (default ghcr.io/aelira-ai/aelira-core-api:<version>).
  --from-dashboard <ref>  Override the from-release dashboard image.
  --pull-to               Pull the candidate images. Omit for locally built images.
  --platform <p>          linux/arm64 or linux/amd64 (default: this host).
  --work-dir <dir>        Scratch directory (default ./test-results/upgrade-rehearsal/<run id>).
  --prefix <name>         Compose project prefix (default aelira-rehearsal).
  --api-port, --mail-port, --dashboard-port
                          Primary stack loopback ports (default 18700, 18701, 18702).
  --restore-api-port, --restore-mail-port, --restore-dashboard-port
                          Restore stack loopback ports (default 18710, 18711, 18712).
  --email <address>       Synthetic administrator, must be @example.org.
  --mailpit-image <ref>   Tagged or digest-pinned Mailpit image (default: the tested pinned digest).
  --allow-legacy-csrf-mismatch
                          Tolerate v0.9.11's first-response CSRF mismatch in the from-release.
  --expect-legacy-notification-correction
                          Accept v0.9.11's notification-preference reporting correction.
  --keep                  Do not remove the Compose projects afterwards.
  --validate-receipt <f>  Validate an existing receipt and exit.
  --help
`;

function port(name: string, value: string | undefined, fallback: number): number {
  const n = value === undefined ? fallback : Number(value);
  if (!Number.isInteger(n) || n < 1024 || n > 65535) throw new UsageError(`${name} must be an integer from 1024 to 65535`);
  return n;
}

export function runId(now = new Date()): string {
  return `${now.toISOString().replace(/[-:.]/g, '').toLowerCase()}-${randomBytes(6).toString('hex')}`;
}

/** The tag of an image reference, or null when only a digest (or nothing) names it. */
export function tagOf(ref: string): string | null {
  const name = ref.split('@')[0].split('/').slice(-1)[0];
  const colon = name.indexOf(':');
  return colon < 0 ? null : name.slice(colon + 1);
}

export function parseArgs(argv: string[], defaults: { cwd?: string; now?: Date; platform?: string } = {}): Options {
  const flags = new Map<string, string | true>();
  const valued = new Set(['from', 'from-api', 'from-dashboard', 'to-api', 'to-dashboard', 'output', 'work-dir', 'prefix',
    'api-port', 'mail-port', 'dashboard-port', 'restore-api-port', 'restore-mail-port', 'restore-dashboard-port',
    'platform', 'email', 'mailpit-image']);
  const boolean = new Set(['pull-to', 'allow-legacy-csrf-mismatch', 'expect-legacy-notification-correction', 'keep']);
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (!arg.startsWith('--')) throw new UsageError(`Unexpected argument ${arg}`);
    const eq = arg.indexOf('=');
    const name = arg.slice(2, eq < 0 ? undefined : eq);
    const inline = eq < 0 ? undefined : arg.slice(eq + 1);
    if (flags.has(name)) throw new UsageError(`--${name} given more than once`);
    if (boolean.has(name)) {
      if (inline !== undefined) throw new UsageError(`--${name} takes no value`);
      flags.set(name, true);
    } else if (valued.has(name)) {
      const value = inline ?? argv[++i];
      if (value === undefined || value === '' || value.startsWith('--')) throw new UsageError(`--${name} needs a value`);
      flags.set(name, value);
    } else throw new UsageError(`Unknown option --${name}`);
  }
  const get = (name: string) => flags.get(name) as string | undefined;
  const need = (name: string) => {
    const value = get(name);
    if (!value) throw new UsageError(`--${name} is required`);
    return value;
  };
  const from = need('from');
  if (!VERSION.test(from)) throw new UsageError('--from must be a version such as 0.9.12 (no leading v)');
  const ref = (name: string, value: string) => {
    if (!REF.test(value)) throw new UsageError(`--${name} is not a valid image reference`);
    const tag = tagOf(value);
    if (!value.includes('@sha256:') && !tag) throw new UsageError(`--${name} must name a tag or a digest`);
    if (!value.includes('@sha256:') && tag === 'latest') throw new UsageError(`--${name} must not use the mutable latest tag`);
    return value;
  };
  const fromApi = ref('from-api', get('from-api') ?? `ghcr.io/aelira-ai/aelira-core-api:${from}`);
  const fromDashboard = ref('from-dashboard', get('from-dashboard') ?? `ghcr.io/aelira-ai/aelira-core-dashboard:${from}`);
  const toApi = ref('to-api', need('to-api'));
  const toDashboard = ref('to-dashboard', need('to-dashboard'));
  if (toApi === fromApi && toDashboard === fromDashboard) throw new UsageError('--to must differ from the from-release images');
  const output = resolve(defaults.cwd ?? process.cwd(), need('output'));
  if (existsSync(output)) throw new UsageError('--output already exists; receipts are never overwritten');
  const prefix = get('prefix') ?? 'aelira-rehearsal';
  if (!PREFIX.test(prefix)) throw new UsageError('--prefix must be 3-31 lowercase letters, digits or hyphens, starting with a letter');
  const platform = get('platform') ?? defaults.platform ?? `linux/${hostArch() === 'x64' ? 'amd64' : 'arm64'}`;
  if (!PLATFORM.test(platform)) throw new UsageError('--platform must be linux/amd64 or linux/arm64');
  const email = get('email') ?? 'upgrade.rehearsal@example.org';
  if (!EMAIL.test(email)) throw new UsageError('--email must be a synthetic @example.org address');
  const primary = {
    api: port('--api-port', get('api-port'), 18700), mail: port('--mail-port', get('mail-port'), 18701),
    dashboard: port('--dashboard-port', get('dashboard-port'), 18702),
  };
  const restore = {
    api: port('--restore-api-port', get('restore-api-port'), 18710), mail: port('--restore-mail-port', get('restore-mail-port'), 18711),
    dashboard: port('--restore-dashboard-port', get('restore-dashboard-port'), 18712),
  };
  const all = [...Object.values(primary), ...Object.values(restore)];
  if (new Set(all).size !== all.length) throw new UsageError('All six ports must be distinct');
  return {
    from, fromApi, fromDashboard, toApi, toDashboard, pullTo: flags.has('pull-to'), output,
    workDir: resolve(defaults.cwd ?? process.cwd(), get('work-dir') ?? `test-results/upgrade-rehearsal/${runId(defaults.now)}`),
    prefix, primary, restore, platform, email,
    mailpitImage: ref('mailpit-image', get('mailpit-image') ?? 'axllent/mailpit@sha256:23fe39e81bde596a918000b3135c0f85dc8bb4c9ea6be274b051143f3e300387'),
    allowLegacyCsrf: flags.has('allow-legacy-csrf-mismatch'),
    expectLegacyNotification: flags.has('expect-legacy-notification-correction'),
    keep: flags.has('keep'),
  };
}

// ---------------------------------------------------------------------------
// Receipt

export interface Step {
  id: string; name: string; command: string; started_at: string; duration_ms: number;
  exit_code: number | null; status: 'pass' | 'fail'; detail?: unknown;
}
export interface Check { id: string; description: string; status: 'pass' | 'fail'; evidence: unknown }
export interface Receipt {
  schema: string; run_id: string; started_at: string; finished_at: string | null;
  tool: Record<string, unknown>; host: Record<string, unknown>;
  images: Record<string, unknown>; isolation: Record<string, unknown>;
  steps: Step[]; checks: Check[]; data: Record<string, unknown>;
  result: { passed: boolean; failed_checks: string[]; missing_checks: string[]; error: string | null };
}

export function newReceipt(id: string, now = new Date()): Receipt {
  return {
    schema: RECEIPT_SCHEMA, run_id: id, started_at: now.toISOString(), finished_at: null,
    tool: {}, host: {}, images: {}, isolation: {}, steps: [], checks: [], data: {},
    result: { passed: false, failed_checks: [], missing_checks: [], error: null },
  };
}

/** Settle the overall verdict from the recorded steps and checks. Never trusts a prior value. */
export function finalizeReceipt(receipt: Receipt, error: string | null, now = new Date()): Receipt {
  const seen = new Set(receipt.checks.map((c) => c.id));
  const missing = REQUIRED_CHECKS.filter((id) => !seen.has(id));
  const failed = receipt.checks.filter((c) => c.status !== 'pass').map((c) => c.id);
  const failedSteps = receipt.steps.filter((s) => s.status !== 'pass').map((s) => `step:${s.id}`);
  receipt.finished_at = now.toISOString();
  receipt.result = {
    passed: error === null && missing.length === 0 && failed.length === 0 && failedSteps.length === 0,
    failed_checks: [...failed, ...failedSteps],
    missing_checks: [...missing],
    error,
  };
  return receipt;
}

const SHA = /^sha256:[0-9a-f]{64}$/;

/** Structural and logical validation of a receipt; returns the list of problems. */
export function validateReceipt(value: unknown): string[] {
  const problems: string[] = [];
  const r = value as Receipt;
  if (!r || typeof r !== 'object') return ['receipt is not an object'];
  if (r.schema !== RECEIPT_SCHEMA) problems.push(`schema must be ${RECEIPT_SCHEMA}`);
  for (const key of ['run_id', 'started_at', 'finished_at']) {
    if (typeof (r as any)[key] !== 'string' || !(r as any)[key]) problems.push(`${key} must be a nonempty string`);
  }
  if (!Array.isArray(r.steps) || r.steps.length === 0) problems.push('steps must be a nonempty array');
  else {
    const ids = new Set<string>();
    for (const [i, s] of r.steps.entries()) {
      if (!s.id || ids.has(s.id)) problems.push(`step ${i} id missing or duplicated`);
      ids.add(s.id);
      if (!s.command) problems.push(`step ${s.id} lacks a command`);
      if (!Number.isFinite(s.duration_ms) || s.duration_ms < 0) problems.push(`step ${s.id} lacks a duration`);
      if (!['pass', 'fail'].includes(s.status)) problems.push(`step ${s.id} has an invalid status`);
      if ((s.status === 'pass') !== (s.exit_code === 0)) problems.push(`step ${s.id} status disagrees with its exit code`);
    }
  }
  if (!Array.isArray(r.checks)) problems.push('checks must be an array');
  if (!Array.isArray(r.steps) || !Array.isArray(r.checks)) return problems;
  const checks = new Map((r.checks ?? []).map((c) => [c.id, c]));
  if (checks.size !== (r.checks ?? []).length) problems.push('check identities must be unique');
  for (const c of r.checks ?? []) {
    if (!['pass', 'fail'].includes(c.status)) problems.push(`check ${c.id} has an invalid status`);
    if (!c.description || c.evidence === undefined) problems.push(`check ${c.id} lacks a description or evidence`);
  }
  const images = (r.images ?? {}) as Record<string, any>;
  for (const stage of ['from', 'to']) {
    for (const svc of ['api', 'dashboard']) {
      const id = images[stage]?.[svc];
      if (!id) { problems.push(`images.${stage}.${svc} missing`); continue; }
      if (!SHA.test(id.index_digest ?? '')) problems.push(`images.${stage}.${svc}.index_digest must be a sha256 digest`);
      if (!SHA.test(id.platform_manifest_digest ?? '')) problems.push(`images.${stage}.${svc}.platform_manifest_digest must be a sha256 digest`);
      if (!['registry', 'local'].includes(id.kind)) problems.push(`images.${stage}.${svc}.kind must be registry or local`);
      if (id.kind === 'registry' && !(id.manifests ?? []).some((m: any) => m.digest === id.platform_manifest_digest)) {
        problems.push(`images.${stage}.${svc} platform digest is not in its index`);
      }
    }
  }
  const res = r.result;
  if (!res || typeof res.passed !== 'boolean') problems.push('result.passed missing');
  else {
    const missing = REQUIRED_CHECKS.filter((id) => !checks.has(id));
    const failing = [...checks.values()].filter((c) => c.status !== 'pass').length + (r.steps ?? []).filter((s) => s.status !== 'pass').length;
    if (res.passed && (missing.length || failing || res.error)) problems.push('result.passed is true although required checks are missing or failing');
    if (!res.passed && !missing.length && !failing && !res.error) problems.push('result.passed is false although nothing failed');
    if (JSON.stringify(res.missing_checks) !== JSON.stringify(missing)) problems.push('result.missing_checks disagrees with the recorded checks');
    const failed = [
      ...(r.checks ?? []).filter(c => c.status !== 'pass').map(c => c.id),
      ...(r.steps ?? []).filter(s => s.status !== 'pass').map(s => `step:${s.id}`),
    ];
    if (JSON.stringify(res.failed_checks) !== JSON.stringify(failed)) problems.push('result.failed_checks disagrees with the recorded operations');
  }
  const text = JSON.stringify(r);
  if (/\/Users\/|\/home\/[a-z]/.test(text)) problems.push('receipt contains a local filesystem path');
  if (/(password|secret|token)["']?\s*[:=]\s*["'][^"']{8,}/i.test(text)) problems.push('receipt appears to contain a credential');
  return problems;
}

// ---------------------------------------------------------------------------
// Pure helpers

/** A Fernet key is 32 random bytes in padded URL-safe base64. */
export function fernetKey(): string {
  return randomBytes(32).toString('base64url') + '=';
}

/** Environment file for a rehearsal stack. Ports live in the Compose override so the file is portable between stacks. */
export function buildEnv(secrets: { postgres: string; jwt: string; replay: string; token: string }, apiPort: number): string {
  const url = `http://127.0.0.1:${apiPort}`;
  return [
    'POSTGRES_USER=aelira', `POSTGRES_PASSWORD=${secrets.postgres}`, 'POSTGRES_DB=aelira',
    `JWT_SECRET=${secrets.jwt}`, `SESSION_REPLAY_ENCRYPTION_KEY=${secrets.replay}`, `TOKEN_ENCRYPTION_KEY=${secrets.token}`,
    'ENV=production', 'OPEN_SIGNUP=false', 'ALLOW_PUBLIC_DEPARTMENT_CREATION=false',
    'LLM_PROVIDER=none', 'LLM_FALLBACK_PROVIDER=none', 'EMBEDDING_PROVIDER=none',
    `PUBLIC_API_URL=${url}`, `PUBLIC_DASHBOARD_URL=${url}`, `CORS_ORIGINS=${url}`,
    'SESSION_COOKIE_SECURE=false', 'CSRF_COOKIE_SECURE=false',
    'SMTP_HOST=mailpit', 'SMTP_PORT=1025', 'FROM_EMAIL=rehearsal@example.org', '',
  ].join('\n');
}

/**
 * Compose override that isolates a stack. The production file fixes container names,
 * so a project name alone does not isolate it; this resets them and moves every
 * published port to loopback.
 */
export function buildOverride(images: { api: string; dashboard: string }, ports: Ports, mailpit: string, platform: string): string {
  return [
    'services:',
    '  postgres:', `    platform: ${platform}`, '    container_name: !reset null',
    '  redis:', `    platform: ${platform}`, '    container_name: !reset null',
    '  api:', `    image: ${images.api}`, '    container_name: !reset null',
    `    platform: ${platform}`,
    '    ports: !override', `      - "127.0.0.1:${ports.api}:8000"`,
    '  worker:', `    image: ${images.api}`, `    platform: ${platform}`, '    container_name: !reset null',
    '  dashboard:', `    image: ${images.dashboard}`, '    container_name: !reset null',
    `    platform: ${platform}`,
    '    ports: !override', `      - "127.0.0.1:${ports.dashboard}:80"`,
    '  mailpit:', `    image: ${mailpit}`, `    platform: ${platform}`, '    restart: "no"',
    '    ports:', `      - "127.0.0.1:${ports.mail}:8025"`, '',
  ].join('\n');
}

export const TABLE_COUNT_SQL = "select table_name || '|' || (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', table_schema, table_name), false, true, '')))[1]::text from information_schema.tables where table_schema = 'public' and table_type = 'BASE TABLE' order by 1";

export function parseTableCounts(output: string): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const line of output.split('\n').map((l) => l.trim()).filter(Boolean)) {
    const m = /^([a-z0-9_]+)\|(\d+)$/.exec(line);
    if (!m) throw new Error(`Unparseable table count line: ${line.slice(0, 80)}`);
    counts[m[1]] = Number(m[2]);
  }
  if (Object.keys(counts).length === 0) throw new Error('No application tables were reported');
  return counts;
}

/** Tables whose row count is below the baseline. Empty means every earlier row count was retained. */
export function tablesLost(before: Record<string, number>, after: Record<string, number>): string[] {
  return Object.keys(before).filter((t) => !(t in after) || after[t] < before[t]);
}

export function tablesDiffer(a: Record<string, number>, b: Record<string, number>): string[] {
  return [...new Set([...Object.keys(a), ...Object.keys(b)])].filter((t) => a[t] !== b[t]).sort();
}

/** Parse `sha256sum`-style listings into path -> digest. */
export function parseFileManifest(listing: string): Map<string, string> {
  const files = new Map<string, string>();
  for (const line of listing.split('\n').filter(Boolean)) {
    const m = /^([0-9a-f]{64}) {2}(.+)$/.exec(line);
    if (!m) throw new Error(`Unparseable manifest line: ${line.slice(0, 80)}`);
    files.set(m[2], m[1]);
  }
  return files;
}

export function filesMissingOrChanged(before: Map<string, string>, after: Map<string, string>): string[] {
  return [...before].filter(([path, digest]) => after.get(path) !== digest).map(([path]) => path);
}

export const sha256 = (data: string | Uint8Array) => createHash('sha256').update(data).digest('hex');

export function redact(text: string, paths: Record<string, string>): string {
  let out = text;
  for (const [real, label] of Object.entries(paths).sort((a, b) => b[0].length - a[0].length)) {
    if (real) out = out.split(real).join(label);
  }
  return out;
}

/** Compose interpolation must use the generated private file, never host app settings. */
export function isolatedEnvironment(base: NodeJS.ProcessEnv, compose: string): NodeJS.ProcessEnv {
  const env = { ...base };
  for (const match of compose.matchAll(/\$\{([A-Z_][A-Z0-9_]*)/g)) delete env[match[1]];
  for (const key of Object.keys(env)) {
    if (key.startsWith('COMPOSE_') || key.startsWith('STACK_') || key === 'DOCKER_DEFAULT_PLATFORM') delete env[key];
  }
  return env;
}

export function runningImageMatches(
  expected: { index_digest: string; platform_manifest_digest: string },
  image: string, descriptor: unknown, platform: string,
): boolean {
  if (!descriptor || typeof descriptor !== 'object') return false;
  const d = descriptor as { digest?: string; platform?: { os?: string; architecture?: string } };
  return (image === expected.index_digest || image === expected.platform_manifest_digest)
    && d.digest === expected.platform_manifest_digest
    && `${d.platform?.os}/${d.platform?.architecture}` === platform;
}

// ---------------------------------------------------------------------------
// Orchestration

interface Stack { name: string; project: string; dir: string; ports: Ports; images: { api: string; dashboard: string } }
type ExecOptions = { cwd?: string; env?: Record<string, string>; input?: Buffer | string; allowFail?: boolean; detail?: (out: string, err: string) => unknown };

class Rehearsal {
  receipt: Receipt;
  stacks: Stack[] = [];
  backupDir: string;
  evidenceDir: string;
  private paths: Record<string, string>;
  private environment: NodeJS.ProcessEnv;
  private secrets = { postgres: randomBytes(24).toString('hex'), jwt: randomBytes(48).toString('base64url'), replay: fernetKey(), token: fernetKey() };

  constructor(private o: Options, private repo: string, id: string, now: Date) {
    this.receipt = newReceipt(id, now);
    this.backupDir = join(o.workDir, 'backup');
    this.evidenceDir = join(o.workDir, 'evidence');
    this.paths = { [o.workDir]: '$WORK', [repo]: '$REPO', [homedir()]: '~' };
    for (const secret of Object.values(this.secrets)) this.paths[secret] = '[REDACTED]';
    this.environment = isolatedEnvironment(process.env, readFileSync(join(repo, 'docker-compose.prod.yml'), 'utf8'));
  }

  // -- plumbing -------------------------------------------------------------
  exec(id: string, name: string, argv: string[], opts: ExecOptions = {}) {
    const started = new Date();
    const r = spawnSync(argv[0], argv.slice(1), {
      cwd: opts.cwd ?? this.repo, env: { ...this.environment, ...opts.env }, input: opts.input, maxBuffer: 1 << 30,
    });
    const stdout = r.stdout?.toString('utf8') ?? '';
    const stderr = r.stderr?.toString('utf8') ?? '';
    const code = r.status ?? (r.error ? 127 : 1);
    const step: Step = {
      id, name, command: redact(argv.join(' '), this.paths), started_at: started.toISOString(),
      duration_ms: Date.now() - started.getTime(), exit_code: code, status: code === 0 ? 'pass' : 'fail',
    };
    if (opts.detail) step.detail = opts.detail(stdout, stderr);
    if (code !== 0) step.detail = { ...((step.detail as object) ?? {}), stderr_tail: redact(stderr.slice(-600), this.paths) };
    this.receipt.steps.push(step);
    if (code !== 0 && !opts.allowFail) throw new Error(`Step ${id} failed (exit ${code}): ${redact(stderr.slice(-300), this.paths)}`);
    return { code, stdout, stderr, raw: r.stdout as Buffer };
  }

  check(id: string, description: string, ok: boolean, evidence: unknown) {
    this.receipt.checks.push({ id, description, status: ok ? 'pass' : 'fail', evidence });
    if (!ok) throw new Error(`Check ${id} failed: ${description}`);
  }

  composeArgv(stack: Stack, args: string[]) {
    return ['docker', 'compose', '-p', stack.project, '-f', 'docker-compose.prod.yml', '-f', 'rehearsal.override.yml', ...args];
  }

  compose(stack: Stack, id: string, name: string, args: string[], opts: ExecOptions = {}) {
    return this.exec(id, name, this.composeArgv(stack, args), { ...opts, cwd: stack.dir });
  }

  /** Throwaway container over one volume. Runs as root so ownership survives a restore. */
  helper(id: string, name: string, image: string, volume: string, mode: 'ro' | 'rw', script: string, extra: string[] = []) {
    return this.exec(id, name, ['docker', 'run', '--rm', '--platform', this.o.platform, '--user', '0:0', '--entrypoint', 'sh', '-v', `${volume}:/data:${mode}`, ...extra, image, '-c', script]);
  }

  async waitFor(what: string, probe: () => Promise<boolean> | boolean, seconds = 300) {
    const deadline = Date.now() + seconds * 1000;
    while (Date.now() < deadline) {
      try { if (await probe()) return; } catch { /* retry until the deadline */ }
      await new Promise((r) => setTimeout(r, 2000));
    }
    throw new Error(`${what} was not ready within ${seconds} seconds`);
  }

  async ready(stack: Stack, id: string) {
    const base = `http://127.0.0.1:${stack.ports.api}`;
    await this.waitFor(`API on ${stack.name}`, async () => (await fetch(`${base}/ready`, { signal: AbortSignal.timeout(5000) })).ok);
    const quiet = () => {
      const argv = this.composeArgv(stack, ['exec', '-T', 'worker', 'python', '-m', 'src.jobs.healthcheck', '--mode', 'readiness']);
      return spawnSync(argv[0], argv.slice(1), { cwd: stack.dir, env: this.environment }).status === 0;
    };
    await this.waitFor(`worker on ${stack.name}`, quiet);
    this.compose(stack, `${id}-worker-ready`, 'Worker readiness probe', ['exec', '-T', 'worker', 'python', '-m', 'src.jobs.healthcheck', '--mode', 'readiness']);
    const live = await fetch(`${base}/live`, { signal: AbortSignal.timeout(5000) });
    const ready = await fetch(`${base}/ready`, { signal: AbortSignal.timeout(5000) });
    await this.waitFor(`dashboard on ${stack.name}`, async () => (await fetch(`http://127.0.0.1:${stack.ports.dashboard}/`, { signal: AbortSignal.timeout(5000) })).ok, 60);
    const dash = await fetch(`http://127.0.0.1:${stack.ports.dashboard}/`, { signal: AbortSignal.timeout(5000) });
    return { api_live: live.status, api_ready: ready.status, worker_ready: true, dashboard_http: dash.status };
  }

  // -- stacks ---------------------------------------------------------------
  makeStack(name: string, ports: Ports, images: { api: string; dashboard: string }, envText?: string): Stack {
    const project = `${this.o.prefix}-${this.receipt.run_id}-${name}`;
    for (const [kind, argv, format] of [
      ['container', ['ps', '-a'], '{{.ID}}'], ['volume', ['volume', 'ls'], '{{.Name}}'], ['network', ['network', 'ls'], '{{.ID}}'],
    ] as const) {
      const existing = this.exec(`reserve-${name}-${kind}`, `Refuse pre-existing ${kind} resources for the new project`,
        ['docker', ...argv, '--filter', `label=com.docker.compose.project=${project}`, '--format', format]).stdout.trim();
      if (existing) throw new Error(`Refuse to reuse an existing ${kind} project`);
    }
    const dir = join(this.o.workDir, `stack-${name}`);
    mkdirSync(dir, { recursive: true });
    copyFileSync(join(this.repo, 'docker-compose.prod.yml'), join(dir, 'docker-compose.prod.yml'));
    writeFileSync(join(dir, '.env'), envText ?? buildEnv(this.secrets, this.o.primary.api), { mode: 0o600 });
    const stack = { name, project, dir, ports, images };
    this.writeOverride(stack);
    this.stacks.push(stack);
    return stack;
  }

  writeOverride(stack: Stack) {
    writeFileSync(join(stack.dir, 'rehearsal.override.yml'), buildOverride(stack.images, stack.ports, this.o.mailpitImage, this.o.platform));
  }

  imageIdentity(ref: string, registry: boolean) {
    const key = sha256(ref).slice(0, 8);
    const inspect = (platformFlag: boolean) => JSON.parse(this.exec(`inspect-${key}${platformFlag ? '-platform' : ''}`, `Inspect local image ${ref}`,
      ['docker', 'image', 'inspect', ...(platformFlag ? ['--platform', this.o.platform] : []), ref, '--format', '{{json .}}']).stdout);
    const whole = inspect(false);
    const platform = inspect(true);
    if (!whole.Descriptor?.digest || !platform.Descriptor?.digest) {
      throw new Error('This Docker does not report image descriptors; use Docker with the containerd image store');
    }
    const identity: Record<string, unknown> = {
      ref, kind: registry ? 'registry' : 'local', platform: this.o.platform,
      index_digest: whole.Descriptor.digest, platform_manifest_digest: platform.Descriptor.digest,
      local_image_id: platform.Id, architecture: platform.Architecture, os: platform.Os,
      repo_digests: platform.RepoDigests, created: platform.Created,
    };
    if (registry) {
      const raw = this.exec(`registry-${key}`, `Resolve registry identity of ${ref}`, ['docker', 'buildx', 'imagetools', 'inspect', '--raw', ref]);
      const index = JSON.parse(raw.stdout);
      const registryDigest = `sha256:${sha256(raw.raw)}`;
      identity.manifests = (index.manifests ?? []).map((m: any) => ({ platform: `${m.platform?.os}/${m.platform?.architecture}`, digest: m.digest }));
      if (registryDigest !== identity.index_digest) throw new Error(`${ref} locally resolves to ${identity.index_digest} but the registry index is ${registryDigest}`);
      if (!(identity.manifests as any[]).some((m) => m.platform === this.o.platform && m.digest === identity.platform_manifest_digest)) {
        throw new Error(`${ref} did not resolve to the registry ${this.o.platform} manifest`);
      }
    }
    return identity as { index_digest: string; platform_manifest_digest: string } & Record<string, unknown>;
  }

  runningImages(stack: Stack, id: string, expected: { api: { index_digest: string; platform_manifest_digest: string }; dashboard: { index_digest: string; platform_manifest_digest: string } }) {
    const out: Record<string, unknown> = {};
    for (const [svc, want] of [['api', expected.api], ['worker', expected.api], ['dashboard', expected.dashboard]] as const) {
      const cid = this.compose(stack, `${id}-ps-${svc}`, `Find the ${svc} container`, ['ps', '-q', svc]).stdout.trim();
      const actual = JSON.parse(this.exec(`${id}-img-${svc}`, `Read the running image manifest and platform of ${svc}`,
        ['docker', 'inspect', '-f', '{"image":"{{.Image}}","descriptor":{{json .ImageManifestDescriptor}}}', cid]).stdout);
      out[svc] = actual;
      if (!runningImageMatches(want, actual.image, actual.descriptor, this.o.platform)) {
        throw new Error(`${svc} does not run the expected ${this.o.platform} image manifest`);
      }
    }
    return out;
  }

  psql(stack: Stack, id: string, name: string, sql: string) {
    return this.compose(stack, id, name, ['exec', '-T', 'postgres', 'psql', '-U', 'aelira', '-d', 'aelira', '-At', '-c', sql]).stdout;
  }

  tableCounts(stack: Stack, id: string) {
    return parseTableCounts(this.psql(stack, id, 'Count rows in every application table', TABLE_COUNT_SQL));
  }

  alembicVersion(stack: Stack, id: string) {
    return this.psql(stack, id, 'Read the stored Alembic revision', 'select version_num from alembic_version').trim();
  }

  alembicHead(stack: Stack, id: string) {
    const out = this.compose(stack, id, 'Read the image Alembic head', ['run', '--rm', '--no-deps', '--entrypoint', 'alembic', 'api', 'heads']).stdout;
    const heads = [...out.matchAll(/^([A-Za-z0-9_]+) \(head\)/gm)].map((m) => m[1]);
    if (heads.length !== 1) throw new Error(`Expected one Alembic head, found ${heads.length}`);
    return heads[0];
  }

  uploadsManifest(stack: Stack, id: string, image: string) {
    const volume = `${stack.project}_upload_data`;
    const filesText = this.helper(`${id}-files`, `Hash every file in the ${stack.name} uploads volume`, image, volume, 'ro',
      'cd /data && find . -type f -exec sha256sum {} + | LC_ALL=C sort -k2').stdout;
    const metaText = this.helper(`${id}-owners`, `Record ownership and modes in the ${stack.name} uploads volume`, image, volume, 'ro',
      "cd /data && find . -exec stat -c '%u:%g %a %n' {} + | LC_ALL=C sort -k3").stdout;
    return { files: parseFileManifest(filesText), filesText, metaText };
  }

  verifier(mode: 'seed' | 'verify' | 'exercise', phase: string, stack: Stack, extra: Record<string, string> = {}) {
    this.exec(`${phase}-${mode}`, `Run the persisted-state gate (${mode}, ${phase})`, ['bun', 'scripts/verify_upgrade_recovery.ts', mode], {
      env: {
        STACK_API_URL: `http://127.0.0.1:${stack.ports.api}`, STACK_MAIL_URL: `http://127.0.0.1:${stack.ports.mail}`,
        STACK_EVIDENCE_DIR: this.evidenceDir, STACK_PHASE: phase, STACK_TEST_EMAIL: this.o.email, ...extra,
      },
      detail: (out) => out.split('\n').filter((l) => l.startsWith('PASS')),
    });
    return JSON.parse(readFileSync(join(this.evidenceDir, `${phase}.json`), 'utf8'));
  }

  // -- the rehearsal --------------------------------------------------------
  preflight() {
    const info = this.exec('docker-info', 'Check the Docker daemon', ['docker', 'info', '--format', '{{.ServerVersion}} {{.Architecture}}']).stdout.trim();
    const v = (id: string, argv: string[]) => this.exec(id, `Record ${argv[0]} version`, argv, { allowFail: true }).stdout.trim();
    this.receipt.host = {
      os: hostPlatform(), arch: hostArch(), docker_server: info,
      compose: v('version-compose', ['docker', 'compose', 'version', '--short']),
      buildx: v('version-buildx', ['docker', 'buildx', 'version']), bun: v('version-bun', ['bun', '--version']),
      target_platform: this.o.platform,
    };
    const existing = this.exec('collision-check', 'List published ports of existing containers', ['docker', 'ps', '-a', '--format', '{{.Names}} {{.Ports}}']).stdout;
    const ports = [...Object.values(this.o.primary), ...Object.values(this.o.restore)];
    const used = ports.filter((p) => new RegExp(`:${p}->`).test(existing));
    if (used.length) throw new Error(`Ports already published by other containers: ${used.join(', ')}`);
    for (const p of ports) {
      const res = spawnSync('lsof', ['-nP', `-iTCP:${p}`, '-sTCP:LISTEN'], { encoding: 'utf8' });
      if (res.status === 0 && res.stdout.trim()) throw new Error(`Port ${p} is already listening on this host`);
    }
  }

  async run() {
    const o = this.o;
    mkdirSync(this.backupDir, { recursive: true, mode: 0o700 });
    mkdirSync(this.evidenceDir, { recursive: true });
    const revision = this.exec('git-revision', 'Record the repository revision', ['git', 'rev-parse', 'HEAD']).stdout.trim();
    const dirty = this.exec('git-clean', 'Check the rehearsal inputs for local changes', ['git', 'status', '--porcelain', '--', 'docker-compose.prod.yml', 'scripts/verify_upgrade_recovery.ts']).stdout.trim();
    this.receipt.tool = {
      revision, compose_file_sha256: sha256(readFileSync(join(this.repo, 'docker-compose.prod.yml'))),
      verifier_sha256: sha256(readFileSync(join(this.repo, 'scripts/verify_upgrade_recovery.ts'))),
      rehearsal_script_sha256: sha256(readFileSync(fileURLToPath(import.meta.url))), tracked_inputs_modified: dirty !== '',
    };
    this.preflight();

    // Image identities, resolved before anything runs.
    this.exec('pull-from-api', 'Pull the from-release API image', ['docker', 'pull', '--platform', o.platform, o.fromApi]);
    this.exec('pull-from-dashboard', 'Pull the from-release dashboard image', ['docker', 'pull', '--platform', o.platform, o.fromDashboard]);
    if (o.pullTo) {
      this.exec('pull-to-api', 'Pull the candidate API image', ['docker', 'pull', '--platform', o.platform, o.toApi]);
      this.exec('pull-to-dashboard', 'Pull the candidate dashboard image', ['docker', 'pull', '--platform', o.platform, o.toDashboard]);
    }
    this.exec('pull-mailpit', 'Pull Mailpit', ['docker', 'pull', '--platform', o.platform, o.mailpitImage]);
    const from = { api: this.imageIdentity(o.fromApi, true), dashboard: this.imageIdentity(o.fromDashboard, true) };
    const to = { api: this.imageIdentity(o.toApi, o.pullTo), dashboard: this.imageIdentity(o.toDashboard, o.pullTo) };
    const mail = JSON.parse(this.exec('inspect-mailpit', 'Inspect Mailpit', ['docker', 'image', 'inspect', o.mailpitImage, '--format', '{{json .}}']).stdout);
    this.receipt.images = { from: { version: o.from, ...from }, to, mailpit: { ref: o.mailpitImage, image_id: mail.Id, repo_digests: mail.RepoDigests } };
    this.check('from_images_resolved', 'Both from-release images resolve to the registry manifest for the target platform', true, { api: from.api.platform_manifest_digest, dashboard: from.dashboard.platform_manifest_digest });
    this.check('to_images_resolved', 'Both candidate images resolve to a manifest for the target platform', true, { api: to.api.platform_manifest_digest, dashboard: to.dashboard.platform_manifest_digest });

    // 1. Install the from-release.
    const primary = this.makeStack('a', o.primary, { api: o.fromApi, dashboard: o.fromDashboard });
    this.compose(primary, 'install-up', 'Start the from-release stack', ['up', '-d']);
    const installed = await this.ready(primary, 'install');
    this.runningImages(primary, 'install', from);
    this.check('install_ready', 'The from-release stack serves live and ready endpoints with a healthy worker and dashboard', true, installed);
    const headFrom = this.alembicHead(primary, 'from-head');
    const csrf: Record<string, string> = o.allowLegacyCsrf ? { STACK_ALLOW_LEGACY_CSRF_MISMATCH: 'true' } : {};
    const baseline = this.verifier('seed', 'baseline', primary, csrf);
    const seed = JSON.parse(readFileSync(join(this.evidenceDir, 'seed.json'), 'utf8'));
    const summary = (r: any) => ({ scan_id: r.scan_id, job_id: r.job_id, job_status: r.job.status, download_status: r.download_status, output_sha256: r.output_sha256 });
    this.check('baseline_seeded', 'A user, scans and remediation jobs were created through the real API and worker, with a digest-verified download', baseline.passed === true && seed.records.some((r: any) => r.output_sha256), {
      profile_id: seed.profile.id, csrf_matches: baseline.csrf_matches, records: seed.records.map(summary),
    });

    // 2. Documented backup.
    this.compose(primary, 'backup-stop', 'Stop the API and worker before backup', ['stop', 'api', 'worker']);
    const running = this.compose(primary, 'backup-confirm-stopped', 'Confirm the API and worker stopped', ['ps', '--status', 'running', '-q', 'api', 'worker']).stdout.trim();
    if (running) throw new Error('API or worker still running before backup');
    const before = this.tableCounts(primary, 'backup-counts');
    const revBefore = this.alembicVersion(primary, 'backup-revision');
    const dump = this.compose(primary, 'backup-dump', 'Dump PostgreSQL in custom format', ['exec', '-T', 'postgres', 'pg_dump', '-Fc', '-U', 'aelira', 'aelira']);
    const dumpPath = join(this.backupDir, 'database.dump');
    writeFileSync(dumpPath, dump.raw, { mode: 0o600 });
    const catalog = this.compose(primary, 'backup-catalog', 'Inspect the dump catalog', ['exec', '-T', 'postgres', 'pg_restore', '-l'], { input: dump.raw });
    const entries = catalog.stdout.split('\n').filter((l) => l && !l.startsWith(';')).length;
    this.helper('backup-uploads', 'Archive the uploads volume with ownership', o.fromApi, `${primary.project}_upload_data`, 'ro',
      'tar --numeric-owner -C /data -cf /backup/uploads.tar .', ['-v', `${this.backupDir}:/backup`]);
    const manifest = this.uploadsManifest(primary, 'backup', o.fromApi);
    copyFileSync(join(primary.dir, '.env'), join(this.backupDir, 'config.env'));
    const backupFiles = {
      database_dump: sha256(readFileSync(dumpPath)), uploads_archive: sha256(readFileSync(join(this.backupDir, 'uploads.tar'))),
      config: sha256(readFileSync(join(this.backupDir, 'config.env'))),
    };
    this.check('backup_complete', 'A coordinated database dump, uploads archive and configuration copy were taken with the writers stopped', entries > 0 && manifest.files.size > 0 && revBefore !== '', {
      sha256: backupFiles, catalog_entries: entries, upload_files: manifest.files.size, alembic_revision: revBefore,
    });

    // 3. Upgrade.
    primary.images = { api: o.toApi, dashboard: o.toDashboard };
    this.writeOverride(primary);
    this.compose(primary, 'upgrade-migrate', 'Run the migration explicitly with the candidate image', ['run', '--rm', '--entrypoint', 'alembic', 'api', 'upgrade', 'head']);
    const headTo = this.alembicHead(primary, 'to-head');
    const revAfter = this.alembicVersion(primary, 'upgrade-revision');
    this.check('upgrade_migration_head', "The database is at the candidate image's single Alembic head", revAfter === headTo, {
      from_head: headFrom, to_head: headTo, stored_before: revBefore, stored_after: revAfter, schema_transition: headFrom !== headTo,
    });
    this.compose(primary, 'upgrade-up', 'Start the candidate stack', ['up', '-d']);
    const upgraded = await this.ready(primary, 'upgraded');
    this.check('upgraded_images_running', 'API, worker and dashboard run the candidate images', true, this.runningImages(primary, 'upgraded', to));
    this.check('upgraded_ready', 'The candidate stack serves live and ready endpoints with a healthy worker and dashboard', true, upgraded);
    const notify: Record<string, string> = o.expectLegacyNotification ? { STACK_EXPECT_LEGACY_NOTIFICATION_CORRECTION: 'true' } : {};
    const verified = this.verifier('verify', 'upgraded', primary, notify);
    this.check('upgraded_data_survived', 'The profile, saved scan results, job dispositions and exact artifact bytes match the pre-upgrade snapshot', verified.passed === true && verified.csrf_matches === true && verified.preserved_records === seed.records.length, {
      preserved_records: verified.preserved_records, csrf_matches: verified.csrf_matches, legacy_notification_correction: verified.legacy_notification_correction,
    });
    const afterUpgrade = this.tableCounts(primary, 'upgraded-counts');
    this.check('upgraded_tables_retained', 'No application table has fewer rows than at backup', tablesLost(before, afterUpgrade).length === 0, { lost: tablesLost(before, afterUpgrade) });
    const upgradedFiles = this.uploadsManifest(primary, 'upgraded', o.toApi);
    const changed = filesMissingOrChanged(manifest.files, upgradedFiles.files);
    this.check('upgraded_files_retained', 'Every file in the pre-upgrade uploads volume is present with the same digest', changed.length === 0, { changed_or_missing: changed, file_count: upgradedFiles.files.size });
    const fresh = this.verifier('exercise', 'candidate-new', primary);
    this.check('upgraded_new_scan', 'The upgraded worker scanned and remediated a new document with a verified download', fresh.passed === true && fresh.csrf_matches === true && Boolean(fresh.record?.output_sha256), { ...summary(fresh.record), csrf_matches: fresh.csrf_matches });

    // 4. Roll back: restore the pre-upgrade backup into fresh storage with the from-release images.
    const restore = this.makeStack('r', o.restore, { api: o.fromApi, dashboard: o.fromDashboard }, readFileSync(join(this.backupDir, 'config.env'), 'utf8'));
    const restoredConfig = sha256(readFileSync(join(restore.dir, '.env')));
    this.check('restore_config_identical', 'The restored private configuration is byte-identical to the backed-up file', restoredConfig === backupFiles.config, { sha256: restoredConfig });
    this.compose(restore, 'restore-create', 'Create the restore stack with fresh volumes without starting it', ['up', '--no-start']);
    this.compose(restore, 'restore-db-up', 'Start only PostgreSQL and Redis', ['up', '-d', '--wait', 'postgres', 'redis']);
    this.compose(restore, 'restore-db', 'Restore the database dump', ['exec', '-T', 'postgres', 'pg_restore', '--exit-on-error', '-U', 'aelira', '-d', 'aelira'], { input: readFileSync(dumpPath) });
    this.helper('restore-uploads', 'Extract the uploads archive with ownership', o.fromApi, `${restore.project}_upload_data`, 'rw',
      'tar --numeric-owner -C /data -xpf /backup/uploads.tar', ['-v', `${this.backupDir}:/backup:ro`]);
    const restoredCounts = this.tableCounts(restore, 'restore-counts');
    const restoredRev = this.alembicVersion(restore, 'restore-revision');
    const differing = tablesDiffer(before, restoredCounts);
    this.check('restore_tables_exact', 'Before any application starts, every table row count and the Alembic revision equal the backup', differing.length === 0 && restoredRev === revBefore, { differing, revision: restoredRev });
    const restoredFiles = this.uploadsManifest(restore, 'restored', o.fromApi);
    this.check('restore_files_exact', 'Restored uploads match the backup in file digests, ownership and modes', restoredFiles.filesText === manifest.filesText && restoredFiles.metaText === manifest.metaText, { file_count: restoredFiles.files.size });
    this.compose(restore, 'restore-up', 'Start the matched from-release stack on restored storage', ['up', '-d']);
    const restoredReady = await this.ready(restore, 'rollback');
    this.check('rollback_images_running', 'API, worker and dashboard run the from-release images again', true, this.runningImages(restore, 'rollback', from));
    this.check('rollback_ready', 'The rolled-back stack serves live and ready endpoints with a healthy worker and dashboard', true, restoredReady);
    const back = this.verifier('verify', 'restored', restore, { STACK_ABSENT_SNAPSHOT: join(this.evidenceDir, 'candidate-new.json'), ...csrf });
    this.check('rollback_data_survived', 'The profile, saved scan results, job dispositions and exact artifact bytes match the pre-upgrade snapshot after rollback', back.passed === true && back.preserved_records === seed.records.length, { preserved_records: back.preserved_records });
    this.check('rollback_post_backup_excluded', 'The scan created after the backup returns 404 after rollback', back.passed === true, { absent_scan_id: fresh.record?.scan_id });
    const again = this.verifier('exercise', 'restored-new', restore, csrf);
    this.check('rollback_new_scan', 'The rolled-back worker scanned and remediated a new document with a verified download', again.passed === true && Boolean(again.record?.output_sha256), summary(again.record));

    this.receipt.data = {
      alembic: {
        from_head: headFrom, to_head: headTo, schema_transition: headFrom !== headTo,
        rollback_method: 'restore of the pre-upgrade database dump, uploads archive and configuration into fresh storage with the from-release images',
        migration_downgrade_exercised: false,
      },
      table_counts: { at_backup: before, after_upgrade_verify: afterUpgrade, after_restore_before_start: restoredCounts },
      uploads: { files_at_backup: manifest.files.size, listing_sha256: sha256(manifest.filesText), ownership_listing_sha256: sha256(manifest.metaText) },
      backup: { sha256: backupFiles, catalog_entries: entries },
      baseline: { profile_id: seed.profile.id, records: seed.records.map(summary) },
      new_work: { candidate: summary(fresh.record), rolled_back: summary(again.record) },
    };
  }

  cleanup() {
    const residual: string[] = [];
    for (const stack of this.stacks) {
      this.compose(stack, `cleanup-${stack.name}`, `Remove the ${stack.name} stack, its volumes and network`, ['down', '--volumes', '--remove-orphans'], { allowFail: true });
      for (const [kind, argv, format] of [
        ['container', ['ps', '-a'], '{{.Names}}'], ['volume', ['volume', 'ls'], '{{.Name}}'], ['network', ['network', 'ls'], '{{.Name}}'],
      ] as const) {
        const left = this.exec(`cleanup-verify-${stack.name}-${kind}`, `List remaining ${kind}s of the ${stack.name} stack`,
          ['docker', ...argv, '--filter', `label=com.docker.compose.project=${stack.project}`, '--format', format], { allowFail: true }).stdout.trim();
        if (left) residual.push(...left.split('\n').map((n) => `${kind}:${n}`));
      }
    }
    return { projects: this.stacks.map((s) => s.project), residual };
  }
}

export async function main(argv: string[]): Promise<number> {
  if (argv.includes('--help')) { console.log(USAGE); return 0; }
  const vIdx = argv.indexOf('--validate-receipt');
  if (vIdx >= 0) {
    const file = argv[vIdx + 1];
    if (!file || argv.length !== 2) throw new UsageError('--validate-receipt takes exactly one file');
    const problems = validateReceipt(JSON.parse(readFileSync(file, 'utf8')));
    for (const p of problems) console.error(`INVALID: ${p}`);
    console.log(problems.length ? `${problems.length} problem(s)` : 'receipt is valid');
    return problems.length ? 1 : 0;
  }
  const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..');
  const now = new Date();
  const options = parseArgs(argv, { cwd: process.cwd(), now });
  mkdirSync(dirname(options.output), { recursive: true, mode: 0o700 });
  mkdirSync(options.workDir, { recursive: true, mode: 0o700 });
  const rehearsal = new Rehearsal(options, repo, runId(now), now);
  let error: string | null = null;
  try {
    await rehearsal.run();
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  } finally {
    const ports = { primary: options.primary, restore: options.restore };
    if (options.keep) {
      rehearsal.receipt.isolation = { projects: rehearsal.stacks.map((s) => s.project), ports, loopback_only: true };
      rehearsal.receipt.checks.push({ id: 'cleanup_complete', description: 'Cleanup skipped by --keep; remove the projects by hand', status: 'fail', evidence: { projects: rehearsal.stacks.map((s) => s.project) } });
    } else {
      const { projects, residual } = rehearsal.cleanup();
      rehearsal.receipt.isolation = { projects, ports, loopback_only: true };
      rehearsal.receipt.checks.push({ id: 'cleanup_complete', description: 'Every rehearsal container, volume and network was removed', status: residual.length === 0 ? 'pass' : 'fail', evidence: { projects, residual } });
    }
  }
  finalizeReceipt(rehearsal.receipt, error);
  const problems = validateReceipt(rehearsal.receipt);
  if (problems.length && rehearsal.receipt.result.passed) {
    rehearsal.receipt.result.passed = false;
    rehearsal.receipt.result.error = `Receipt failed self-validation: ${problems.join('; ')}`;
  }
  writeFileSync(options.output, JSON.stringify(rehearsal.receipt, null, 2) + '\n', { flag: 'wx' });
  console.log(`${rehearsal.receipt.result.passed ? 'PASS' : 'FAIL'} receipt written (${rehearsal.receipt.checks.length} checks, ${rehearsal.receipt.steps.length} steps)`);
  if (rehearsal.receipt.result.error) console.error(rehearsal.receipt.result.error);
  return rehearsal.receipt.result.passed ? 0 : 1;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).then((code) => { process.exitCode = code; }, (e) => {
    console.error(e instanceof UsageError ? `${e.message}\n\n${USAGE}` : e);
    process.exitCode = e instanceof UsageError ? 2 : 1;
  });
}
