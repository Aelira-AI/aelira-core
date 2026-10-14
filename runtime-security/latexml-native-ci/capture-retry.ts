import { constants } from 'node:fs';
import { lstat, open, readFile, realpath, rename, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';

const stages = ['source-check', 'native-check', 'input-capture', 'input-pin', 'prepare',
  'bootstrap', 'native-run', 'caller-export', 'evidence-export', 'evidence-check'];
const runnerHash = '0d7122a995f82d4d06be89121c9cf0ca66cc32e8de2ca925d6bc749b6b9e4f99';
type Identity = { revision: string; tree: string; architecture: string; remote: string };

// A retry never includes a pin, preparation, build or evidence failure.
export function captureRefused(row: any, exit: number, id: Identity): boolean {
  const s = row?.stages;
  return exit === 2 && row?.schema === 'aelira-latexml-ci-outer-v3' &&
    row.operator_origin === 'https://github.com/Aelira-AI/aelira-core.git' &&
    row.operator_ref === id.revision && row.operator_tree === id.tree &&
    row.source_kind === 'public' && row.remote_ref === id.remote && row.architecture === id.architecture &&
    row.captured_lock_sha256 === null && row.producer_status_sha256 === null &&
    row.failure_diagnostic?.state === 'not_run' && row.failure_diagnostic.record === null &&
    row.selected_sources?.['runtime-security/latexml-native-ci/run.py']?.sha256 === runnerHash &&
    s && Object.keys(s).sort().join(',') === [...stages].sort().join(',') &&
    ['source-check', 'native-check'].every(n => s[n]?.state === 'executed' && s[n].exit === 0) &&
    s['input-capture']?.state === 'executed' && s['input-capture'].exit === 2 &&
    stages.slice(3).every(n => s[n]?.state === 'not_run' && s[n].exit === null);
}

async function absent(path: string) {
  try { await lstat(path); throw new Error('New output required'); }
  catch (e: any) { if (e.code !== 'ENOENT') throw e; }
}
async function regular(path: string) {
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const stat = await file.stat();
    if (!stat.isFile() || stat.nlink !== 1 || stat.size > 65_536 || stat.uid !== process.getuid!()) throw new Error('Receipt file refused');
    return await file.readFile();
  } finally { await file.close(); }
}

async function main() {
  if (process.platform !== 'linux' || process.getuid!() === 0 || Bun.version !== '1.3.14') throw new Error('Pinned nonroot native runner required');
  const args = process.argv.slice(2), values = new Map<string, string>();
  const allowed = new Set(['--operator-repo', '--operator-ref', '--operator-tree', '--source-kind',
    '--remote-ref', '--architecture', '--upstream-repo', '--scratch', '--out']);
  let capture = false;
  for (let i = 0; i < args.length; i++) {
    const key = args[i];
    if (key === '--capture-input' && !capture) { capture = true; continue; }
    if (!allowed.has(key) || values.has(key) || !args[i + 1] || args[i + 1].startsWith('--')) throw new Error('Fixed capture arguments required');
    values.set(key, args[++i]);
  }
  if (!capture || values.size !== allowed.size || values.get('--source-kind') !== 'public') throw new Error('Public capture required');
  const id: Identity = { revision: values.get('--operator-ref')!, tree: values.get('--operator-tree')!,
    architecture: values.get('--architecture')!, remote: values.get('--remote-ref')! };
  if (!/^[0-9a-f]{40}$/.test(id.revision) || !/^[0-9a-f]{40}$/.test(id.tree) ||
    !['arm64', 'amd64'].includes(id.architecture) || !/^refs\/(heads\/[A-Za-z0-9._/-]+|pull\/[0-9]+\/merge)$/.test(id.remote)) throw new Error('Source identity refused');
  const temp = process.env.RUNNER_TEMP, repo = values.get('--operator-repo')!, scratch = values.get('--scratch')!, out = values.get('--out')!;
  if (!temp || resolve(temp) !== temp || await realpath(temp) !== temp ||
    repo !== process.env.GITHUB_WORKSPACE || process.env.GITHUB_REPOSITORY !== 'Aelira-AI/aelira-core' ||
    id.revision !== process.env.GITHUB_SHA || id.remote !== process.env.GITHUB_REF ||
    scratch !== join(temp, 'latexml-native-private') || out !== join(temp, 'latexml-native-validated') ||
    dirname(scratch) !== temp) throw new Error('Workflow paths refused');
  await absent(scratch); await absent(out);
  const runner = join(repo, 'runtime-security/latexml-native-ci/run.py');
  if (createHash('sha256').update(await regular(runner)).digest('hex') !== runnerHash) throw new Error('Reviewed runner required');
  const receiptPath = join(temp, 'latexml-capture-attempts.json'); await absent(receiptPath);
  const receipt: any = { schema: 'aelira-latexml-capture-attempts-v1', ...id,
    coordinator_sha256: createHash('sha256').update(await readFile(import.meta.path)).digest('hex'),
    runner_sha256: runnerHash, status: 'fail', attempts: [] };
  for (let attempt = 1; attempt <= 3; attempt++) {
    const log = await open(join(temp, `latexml-native-runner-attempt-${attempt}.log`), constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
    let exit: number;
    try {
      exit = await new Promise<number>((accept, reject) => {
        const child = spawn('/usr/bin/python3', ['-B', runner, ...args], {
          env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' }, stdio: ['ignore', log.fd, log.fd] });
        child.once('error', reject); child.once('exit', (code, signal) => accept(signal || code === null ? 1 : code));
      });
    } finally { await log.close(); }
    let row: any = null, outerHash: string | null = null;
    try { const bytes = await regular(join(scratch, 'outer.json')); outerHash = createHash('sha256').update(bytes).digest('hex'); row = JSON.parse(bytes.toString('utf8')); } catch {}
    const eligible = captureRefused(row, exit, id);
    receipt.attempts.push({ attempt, exit, outer_sha256: outerHash, capture_refusal: eligible });
    receipt.status = exit === 0 ? 'complete' : 'fail';
    await writeFile(receiptPath, JSON.stringify(receipt) + '\n', { flag: attempt === 1 ? 'wx' : 'w', mode: 0o644 });
    if (exit === 0 || !eligible || attempt === 3) { process.exitCode = exit; break; }
    // Preserve the entire failed capture, then start with a new empty scratch.
    const preserved = `${scratch}-capture-refusal-${attempt}`; await absent(preserved); await absent(out);
    await rename(scratch, preserved);
    await Bun.sleep(attempt * 15_000);
  }
}
if (import.meta.main) await main().catch(() => { console.error('Native capture coordinator refused'); process.exitCode = 1; });
