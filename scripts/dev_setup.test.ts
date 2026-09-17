import assert from 'node:assert/strict';
import { chmodSync, copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

const source = new URL('../setup-dev.sh', import.meta.url);
const defaults = {
  LLM_PROVIDER: 'none', LLM_FALLBACK_PROVIDER: 'none', EMBEDDING_PROVIDER: 'none',
  OLLAMA_HOST: 'http://ollama:11434', OLLAMA_TEXT_MODEL: 'gemma3:4b',
  OLLAMA_CODE_MODEL: 'qwen2.5-coder:7b', OLLAMA_VISION_MODEL: 'qwen2.5vl:3b',
  OLLAMA_EMBEDDING_MODEL: 'nomic-embed-text:latest', TOKEN_ENCRYPTION_KEY: '',
};
type Call = { args: string[]; cwd: string; project?: string };
function run(options: {
  config?: Record<string, string | null>; worker?: Record<string, string | null>;
  env?: Record<string, string>; args?: string[]; fail?: string; dotenv?: string;
} = {}) {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'aelira setup test ')));
  const repo = join(root, 'repo with spaces');
  const caller = join(root, 'caller');
  const bin = join(root, 'bin');
  for (const dir of [repo, caller, bin]) mkdirSync(dir);
  copyFileSync(source, join(repo, 'setup-dev.sh'));
  writeFileSync(join(repo, 'docker-compose.dev.yml'), 'services: {}\n');
  const dotfile = options.dotenv ?? 'LLM_PROVIDER=none\nTOKEN_ENCRYPTION_KEY=\n# untouched\n';
  writeFileSync(join(repo, '.env'), dotfile);
  writeFileSync(join(caller, '.env'), 'LLM_PROVIDER=ollama\n');
  writeFileSync(join(caller, 'gpu override.yml'), 'services: {}\n');
  const log = join(root, 'calls.jsonl');
  // The Docker boundary returns resolved Compose config. All actual orchestration,
  // jq validation, argument quoting and failure handling execute in the real shell.
  const fake = join(root, 'docker.cjs');
  writeFileSync(fake, `
const fs = require('node:fs');
const args = process.argv.slice(2);
fs.appendFileSync(process.env.CALL_LOG, JSON.stringify({args, cwd: process.cwd(), project: process.env.COMPOSE_PROJECT_NAME}) + '\\n');
const failure = process.env.FAIL_STAGE;
const isStartup = args.includes('up');
if ((failure === 'compose' && args.includes('version')) ||
    (failure === 'daemon' && args[0] === 'info') ||
    (failure === 'config' && args.includes('config')) ||
    (failure === 'build' && args.includes('build')) ||
    (failure === 'dependencies' && isStartup && !args.includes('api')) ||
    (failure === 'pull' && args.includes('pull')) ||
    (failure === 'stop' && args.includes('stop')) ||
    (failure === 'migration' && args.includes('alembic')) ||
    (failure === 'readiness' && isStartup && args.includes('api'))) process.exit(17);
if (args.includes('config')) {
  const environment = JSON.parse(process.env.API_CONFIG);
  // Selected shell variables model Compose's exported-environment precedence.
  for (const key of ['LLM_PROVIDER', 'LLM_FALLBACK_PROVIDER', 'EMBEDDING_PROVIDER', 'OLLAMA_TEXT_MODEL']) {
    if (process.env[key] !== undefined) environment[key] = process.env[key];
  }
  console.log(JSON.stringify({services: {api: {environment}, worker: {environment: {...environment, ...JSON.parse(process.env.WORKER_CONFIG)}}}}));
}
`);
  const quote = (value: string) => "'" + value.replaceAll("'", "'\\''") + "'";
  writeFileSync(join(bin, 'docker'), `#!/bin/sh\nexec ${quote(process.execPath)} ${process.versions.bun ? '--no-env-file ' : ''}${quote(fake)} "$@"\n`);
  chmodSync(join(bin, 'docker'), 0o755);
  const env = { ...process.env };
  for (const key of Object.keys(env)) {
    if (/^(LLM_|OLLAMA_|EMBEDDING_|COMPOSE_|TOKEN_ENCRYPTION_KEY)/.test(key)) delete env[key];
  }
  try {
    const result = spawnSync('bash', [join(repo, 'setup-dev.sh'), ...(options.args ?? [])], {
      cwd: caller, encoding: 'utf8', timeout: 15_000,
      env: { ...env, PATH: `${bin}:${process.env.PATH}`, CALL_LOG: log,
        API_CONFIG: JSON.stringify({ ...defaults, ...options.config }),
        WORKER_CONFIG: JSON.stringify(options.worker ?? {}), FAIL_STAGE: options.fail ?? '', ...options.env },
    });
    assert.ifError(result.error);
    assert.equal(readFileSync(join(repo, '.env'), 'utf8'), dotfile);
    assert.equal(existsSync(join(repo, 'should-not-exist')), false);
    const calls: Call[] = existsSync(log) ? readFileSync(log, 'utf8').trim().split('\n').map(line => JSON.parse(line)) : [];
    return { status: result.status, output: result.stdout + result.stderr, calls, repo, caller };
  } finally { rmSync(root, { recursive: true, force: true }); }
}
const pulls = (r: ReturnType<typeof run>) => r.calls.filter(c => c.args.includes('pull')).map(c => c.args.at(-1));
const index = (r: ReturnType<typeof run>, arg: string) => r.calls.findIndex(c => c.args.includes(arg));

test('default builds, waits on dependencies, migrates before app startup; AI stays disabled', () => {
  const r = run();
  assert.equal(r.status, 0, r.output);
  assert.match(r.output, /Setup complete!/);
  assert.deepEqual(pulls(r), []);
  assert.ok(index(r, 'build') < index(r, 'up'));
  assert.ok(index(r, 'stop') < index(r, 'alembic'));
  const up = r.calls.filter(c => c.args.includes('up'));
  assert.equal(up.length, 2);
  assert.deepEqual(up[0].args.slice(-2), ['postgres', 'redis']);
  assert.deepEqual(up[1].args.slice(-2), ['api', 'worker']);
  for (const c of up) {
    assert.ok(c.args.includes('--wait'));
    assert.ok(c.args.includes('--wait-timeout'));
    assert.ok(!c.args.includes('--profile'));
  }
  assert.ok(index(r, 'alembic') < r.calls.indexOf(up[1]));
  assert.deepEqual(r.calls[index(r, 'alembic')].args.slice(-8), ['run', '--rm', '--no-deps', '-T', 'api', 'alembic', 'upgrade', 'head']);
});

test('primary Ollama pulls exact text/code/vision models and enables profile; embeddings independent', () => {
  const r = run({ config: { LLM_PROVIDER: 'ollama' } });
  assert.equal(r.status, 0, r.output);
  assert.deepEqual(pulls(r).sort(), ['gemma3:4b', 'qwen2.5-coder:7b', 'qwen2.5vl:3b'].sort());
  const startup = r.calls.find(c => c.args.includes('up'))!;
  assert.deepEqual(startup.args.slice(-3), ['postgres', 'redis', 'ollama']);
  assert.ok(startup.args.includes('--profile'));
  for (const c of r.calls.filter(c => c.args.includes('pull'))) {
    assert.deepEqual(c.args.slice(-6, -1), ['exec', '-T', 'ollama', 'ollama', 'pull']);
  }
});

test('fallback-only Ollama, custom models, plus identifiers and deduplicated pulls', () => {
  const r = run({ config: { LLM_PROVIDER: 'openai', LLM_FALLBACK_PROVIDER: ' OLLAMA ',
    OLLAMA_TEXT_MODEL: 'my-org/custom+text:v1', OLLAMA_CODE_MODEL: 'same:v2',
    OLLAMA_VISION_MODEL: 'same:v2', EMBEDDING_PROVIDER: 'ollama', OLLAMA_EMBEDDING_MODEL: 'same:v2' } });
  assert.equal(r.status, 0, r.output);
  assert.deepEqual(pulls(r).sort(), ['my-org/custom+text:v1', 'same:v2']);
});

test('embedding-only selection pulls only the resolved embedding model', () => {
  const r = run({ config: { EMBEDDING_PROVIDER: 'ollama', OLLAMA_EMBEDDING_MODEL: 'custom/embeddings:v3' } });
  assert.equal(r.status, 0, r.output);
  assert.deepEqual(pulls(r), ['custom/embeddings:v3']);
});

test('legacy model aliases are used only when canonical identifiers are blank', () => {
  const r = run({ config: { LLM_PROVIDER: 'ollama', OLLAMA_TEXT_MODEL: ' ', OLLAMA_FALLBACK_TEXT: ' legacy:v1 ',
    OLLAMA_CODE_MODEL: null, OLLAMA_FALLBACK_CODE: '', OLLAMA_VISION_MODEL: 'canonical:v2', OLLAMA_FALLBACK_VISION: 'ignored:v1' } });
  assert.equal(r.status, 0, r.output);
  assert.deepEqual(pulls(r).sort(), ['canonical:v2', 'legacy:v1', 'qwen2.5-coder:7b']);
});

test('worker model overrides participate in the deduplicated model set', () => {
  const r = run({ worker: { LLM_PROVIDER: 'ollama', OLLAMA_TEXT_MODEL: 'worker:v1' } });
  assert.equal(r.status, 0, r.output);
  assert.ok(pulls(r).includes('worker:v1'));
});

test('uses resolved configured cloud provider; shell choices override without overwriting .env', () => {
  const config = { LLM_PROVIDER: 'openai', OPENAI_API_KEY: 'do-not-print-this-secret' };
  const dotenv = 'LLM_PROVIDER=openai\nOPENAI_API_KEY=do-not-print-this-secret\nNOT_SHELL=$(touch should-not-exist)\n';
  const cloud = run({ config, dotenv });
  assert.equal(cloud.status, 0, cloud.output);
  assert.deepEqual(pulls(cloud), []);
  const selected = run({ config, dotenv, env: { LLM_PROVIDER: 'ollama', OLLAMA_TEXT_MODEL: 'shell-choice:v1' } });
  assert.equal(selected.status, 0, selected.output);
  assert.ok(pulls(selected).includes('shell-choice:v1'));
  assert.doesNotMatch(selected.output + cloud.output, /do-not-print-this-secret/);
});

test('caller cwd, paths with spaces, repeated overrides, project name and no-build are preserved', () => {
  const r = run({ args: ['--compose-override', 'gpu override.yml', '--compose-override', 'gpu override.yml', '--no-build', '--wait-timeout', '43'],
    env: { COMPOSE_PROJECT_NAME: 'isolated-dev', COMPOSE_FILE: 'irrelevant.yml' } });
  assert.equal(r.status, 0, r.output);
  assert.equal(index(r, 'build'), -1);
  for (const c of r.calls.filter(c => c.args.includes('--project-directory'))) {
    assert.equal(c.cwd, r.repo);
    assert.equal(c.project, 'isolated-dev');
    assert.equal(c.args[c.args.indexOf('--project-directory') + 1], r.repo);
    assert.deepEqual(c.args.filter((_, i) => c.args[i - 1] === '-f'), [join(r.repo, 'docker-compose.dev.yml'), join(r.caller, 'gpu override.yml'), join(r.caller, 'gpu override.yml')]);
    if (c.args.includes('up')) assert.equal(c.args[c.args.indexOf('--wait-timeout') + 1], '43');
  }
});

for (const fail of ['compose', 'daemon', 'config', 'build', 'dependencies', 'pull', 'stop', 'migration', 'readiness']) {
  test(`failure at ${fail} stops immediately without success`, () => {
    const r = run({ fail, config: { LLM_PROVIDER: 'ollama' } });
    assert.notEqual(r.status, 0);
    assert.doesNotMatch(r.output, /Setup complete!/);
    assert.match(r.output, /Setup failed/);
    if (!['migration', 'readiness'].includes(fail)) assert.equal(index(r, 'alembic'), -1);
    if (fail === 'migration') assert.equal(r.calls.filter(c => c.args.includes('up')).length, 1);
  });
}

for (const bad of ['your-token-encryption-key-here', 'x'.repeat(44), 'A'.repeat(43) + '=\n']) {
  test(`rejects invalid API encryption key even with valid worker configuration (${bad.length} chars)`, () => {
    const r = run({ config: { TOKEN_ENCRYPTION_KEY: bad }, worker: { TOKEN_ENCRYPTION_KEY: '' } });
    assert.notEqual(r.status, 0);
    assert.match(r.output, /TOKEN_ENCRYPTION_KEY/);
    assert.equal(index(r, 'build'), -1);
    assert.doesNotMatch(r.output, /Setup complete!/);
  });
}
test('valid Fernet key is retained and never printed', () => {
  const key = 'A'.repeat(43) + '=';
  const r = run({ config: { TOKEN_ENCRYPTION_KEY: key } });
  assert.equal(r.status, 0, r.output);
  assert.ok(!r.output.includes(key));
});
for (const model of ['--option', 'bad;command', 'x'.repeat(129), 'embed\n']) {
  test('rejects unsafe or oversized embedding identifier before starting containers: ' + JSON.stringify(model), () => {
    const r = run({ config: { EMBEDDING_PROVIDER: 'ollama', OLLAMA_EMBEDDING_MODEL: model } });
    assert.notEqual(r.status, 0);
    assert.equal(index(r, 'up'), -1);
  });
}
test('external Ollama endpoint fails clearly instead of pulling models into the wrong service', () => {
  const r = run({ worker: { LLM_PROVIDER: 'ollama', OLLAMA_HOST: 'http://remote:11434' } });
  assert.notEqual(r.status, 0);
  assert.match(r.output, /bundled Ollama service only/);
  assert.equal(index(r, 'up'), -1);
});
for (const args of [['--unknown'], ['--wait-timeout', '0'], ['--wait-timeout'], ['--compose-override', 'missing.yml']]) {
  test('invalid CLI arguments fail before Docker: ' + args.join(' '), () => {
    const r = run({ args });
    assert.notEqual(r.status, 0);
    assert.equal(r.calls.length, 0);
  });
}
