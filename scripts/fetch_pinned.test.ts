import { describe, expect, test } from 'bun:test';
import { createHash } from 'node:crypto';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, chmodSync, existsSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// Execute the production helper unchanged, without running a wheel build.
// Only transport responses are simulated; verification computes real SHA-256.
const helper = readFileSync(new URL('../docker/fetch-pinned.sh', import.meta.url), 'utf8');
const good = 'pinned source fixture\n';
const digest = createHash('sha256').update(good).digest('hex');
const bad = '<html>untrusted response with PRIVATE_RESPONSE_MARKER</html>\n';

function run(modes: string[], pin = digest, stale = false, fallbacks: string[] = [], cached: string | null = null) {
  const dir = mkdtempSync(join(tmpdir(), 'lxml-fetch-test-'));
  try {
    const bin = join(dir, 'bin');
    mkdirSync(bin);
    // macOS sha256sum lacks GNU long flags. shasum implements the same strict
    // checking flags; Linux exercises the production GNU tool directly.
    if (process.platform === 'darwin') {
      writeFileSync(join(bin, 'sha256sum'), '#!/bin/sh\nexec /usr/bin/shasum -a 256 "$@"\n');
      chmodSync(join(bin, 'sha256sum'), 0o755);
    }
    writeFileSync(join(dir, 'good'), good);
    writeFileSync(join(dir, 'bad'), bad);
    writeFileSync(join(dir, 'modes'), modes.join('\n') + '\n');
    if (stale) writeFileSync(join(dir, 'archive.tar.gz'), good);
    // The build-inputs cache holds files named by their pinned SHA-256.
    if (cached !== null) {
      mkdirSync(join(dir, 'inputs', 'sha256'), { recursive: true });
      writeFileSync(join(dir, 'inputs', 'sha256', pin), cached);
    }
    writeFileSync(join(bin, 'curl'), `#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$FIXTURE/calls"
count=0
if [ -f "$FIXTURE/count" ]; then count=$(cat "$FIXTURE/count"); fi
count=$((count + 1))
printf '%s' "$count" > "$FIXTURE/count"
mode=$(sed -n "$count"p "$FIXTURE/modes")
output=
while [ "$#" -gt 0 ]; do
  if [ "$1" = --output ]; then shift; output=$1; fi
  shift
done
[ -n "$output" ]
# No accepted archive may be visible to the build during a transfer.
[ ! -e "$FIXTURE/archive.tar.gz" ] || exit 99
case "$mode" in
  good) cp "$FIXTURE/good" "$output" ;;
  bad) cp "$FIXTURE/bad" "$output" ;;
  empty) : > "$output" ;;
  failure) printf 'PRIVATE_TRANSPORT_MARKER\\n' >&2; exit 28 ;;
  good-failure) cp "$FIXTURE/good" "$output"; exit 28 ;;
  partial-failure) cp "$FIXTURE/bad" "$output"; exit 18 ;;
  *) exit 98 ;;
esac
`);
    chmodSync(join(bin, 'curl'), 0o755);
    // Keep tests fast while recording that only bounded backoff was requested.
    writeFileSync(join(bin, 'sleep'), '#!/bin/sh\nprintf "%s\\n" "$*" >> "$FIXTURE/sleeps"\n');
    chmodSync(join(bin, 'sleep'), 0o755);
    const result = Bun.spawnSync(['sh', '-eu', '-c', `${helper}\nfetch https://example.invalid/source "$1" archive.tar.gz ${fallbacks.join(' ')}\nprintf accepted > accepted\n`, 'test', pin], {
      cwd: dir,
      env: { ...process.env, FIXTURE: dir, AELIRA_BUILD_INPUTS: join(dir, 'inputs'), PATH: `${bin}:${process.env.PATH}` },
      timeout: 5000,
    });
    return {
      status: result.exitCode,
      stderr: result.stderr.toString(),
      stdout: result.stdout.toString(),
      count: existsSync(join(dir, 'count')) ? Number(readFileSync(join(dir, 'count'), 'utf8')) : 0,
      calls: existsSync(join(dir, 'calls')) ? readFileSync(join(dir, 'calls'), 'utf8').trim().split('\n') : [],
      sleeps: existsSync(join(dir, 'sleeps')) ? readFileSync(join(dir, 'sleeps'), 'utf8').trim().split('\n') : [],
      archive: existsSync(join(dir, 'archive.tar.gz')) ? readFileSync(join(dir, 'archive.tar.gz'), 'utf8') : null,
      accepted: existsSync(join(dir, 'accepted')),
      partials: readdirSync(dir).filter((name) => name.includes('.partial.')),
    };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

function refused(result: ReturnType<typeof run>, attempts = 3) {
  expect(result.status).not.toBe(0);
  expect(result.count).toBe(attempts);
  expect(result.archive).toBeNull();
  expect(result.accepted).toBe(false);
  expect(result.partials).toEqual([]);
  expect(result.stdout).toBe('');
  expect(result.stderr).not.toContain('PRIVATE_');
  expect(result.stderr).not.toContain(tmpdir());
}

describe('pinned source fetch (offline production shell)', () => {
  test('accepts a verified first response with no retry', () => {
    const result = run(['good']);
    expect(result.status).toBe(0);
    expect(result.count).toBe(1);
    expect(result.archive).toBe(good);
    expect(result.accepted).toBe(true);
    expect(result.stderr).toBe('');
    expect(result.sleeps).toEqual([]);
    expect(result.partials).toEqual([]);
  });

  test('retries a successful transport with wrong bytes and accepts only the pin', () => {
    const result = run(['bad', 'good']);
    expect(result.status).toBe(0);
    expect(result.count).toBe(2);
    expect(result.archive).toBe(good);
    expect(result.sleeps).toEqual(['1']);
    expect(result.partials).toEqual([]);
    expect(result.stderr).toBe(`source archive.tar.gz attempt=1/3 curl_status=0 bytes=${Buffer.byteLength(bad)} sha256=${createHash('sha256').update(bad).digest('hex')} expected=${digest}\n`);
  });

  test('persistently wrong bytes exhaust exactly three bounded attempts', () => {
    const result = run(['bad', 'bad', 'bad', 'good']);
    refused(result);
    expect(result.sleeps).toEqual(['1', '1']);
    expect(result.stderr.trim().split('\n')).toHaveLength(3);
    for (const call of result.calls) {
      expect(call).toContain('--connect-timeout 30 --speed-limit 1024 --speed-time 60 --max-time 900');
      expect(call).toContain("--proto =https --proto-redir =https --tlsv1.2");
      expect(call).not.toContain('--retry');
    }
  });

  for (const mode of ['failure', 'good-failure', 'partial-failure', 'empty']) {
    test(`fails closed for ${mode}, even with previously pinned destination bytes`, () => {
      refused(run([mode, mode, mode, 'good'], digest, true));
    });
  }

  test('transport failure cannot reuse a previous attempt partial response', () => {
    const result = run(['bad', 'failure', 'good']);
    expect(result.status).toBe(0);
    expect(result.count).toBe(3);
    expect(result.archive).toBe(good);
    expect(result.stderr).toContain('attempt=2/3 curl_status=28 bytes=0 ');
    expect(result.stderr).not.toContain('PRIVATE_');
  });

  test('a malformed checksum cannot accept otherwise good bytes', () => {
    refused(run(['good', 'good', 'good'], 'malformed'));
  });

  test('a fallback is used only after every primary attempt fails, and only with pinned bytes', () => {
    const fallback = 'https://mirror.example.invalid/source';
    const result = run(['bad', 'bad', 'bad', 'good'], digest, false, [fallback]);
    expect(result.status).toBe(0);
    expect(result.count).toBe(4);
    expect(result.archive).toBe(good);
    expect(result.accepted).toBe(true);
    expect(result.partials).toEqual([]);
    expect(result.calls.slice(0, 3).every((call) => call.endsWith('https://example.invalid/source'))).toBe(true);
    expect(result.calls[3].endsWith(fallback)).toBe(true);
    // Each source keeps its own bounded attempts; no wait follows the last one.
    expect(result.sleeps).toEqual(['1', '1']);
    const lines = result.stderr.trim().split('\n');
    expect(lines).toHaveLength(3);
    for (const line of lines) expect(line).not.toContain('fallback=');
    expect(result.stderr).not.toContain('example.invalid');
  });

  test('a working primary never touches the fallback', () => {
    const result = run(['good', 'good'], digest, false, ['https://mirror.example.invalid/source']);
    expect(result.status).toBe(0);
    expect(result.count).toBe(1);
    expect(result.calls[0].endsWith('https://example.invalid/source')).toBe(true);
    expect(result.stderr).toBe('');
  });

  test('a fallback with the wrong bytes is refused after its own bounded attempts', () => {
    const result = run(['bad', 'bad', 'bad', 'bad', 'bad', 'bad', 'good'], digest, false, ['https://mirror.example.invalid/source']);
    refused(result, 6);
    const lines = result.stderr.trim().split('\n');
    expect(lines).toHaveLength(6);
    expect(lines.slice(3).every((line) => line.startsWith('source archive.tar.gz fallback=1/1 attempt='))).toBe(true);
    expect(result.stderr).not.toContain('mirror.example.invalid');
    expect(result.sleeps).toEqual(['1', '1', '1', '1']);
  });

  test('fallbacks are tried in order', () => {
    const second = 'https://second.example.invalid/source';
    const result = run(['bad', 'bad', 'bad', 'failure', 'failure', 'failure', 'good'], digest, false, ['https://first.example.invalid/source', second]);
    expect(result.status).toBe(0);
    expect(result.count).toBe(7);
    expect(result.calls[6].endsWith(second)).toBe(true);
    expect(result.stderr).toContain('fallback=1/2 attempt=3/3 curl_status=28');
    expect(result.stderr).not.toContain('fallback=2/2');
  });

  test('uses a cached copy that matches the pin without any transfer', () => {
    const result = run(['bad'], digest, false, [], good);
    expect(result.status).toBe(0);
    expect(result.count).toBe(0);
    expect(result.archive).toBe(good);
    expect(result.accepted).toBe(true);
    expect(result.partials).toEqual([]);
    expect(result.stderr).toBe('source archive.tar.gz cache=hit\n');
  });

  test('ignores a cached copy with the wrong bytes and fetches the pin', () => {
    const result = run(['good'], digest, false, [], bad);
    expect(result.status).toBe(0);
    expect(result.count).toBe(1);
    expect(result.archive).toBe(good);
    expect(result.partials).toEqual([]);
    expect(result.stderr).toBe(`source archive.tar.gz cache=mismatch sha256=${createHash('sha256').update(bad).digest('hex')} expected=${digest}\n`);
  });

  test('a cached copy with the wrong bytes cannot be accepted when every transfer fails', () => {
    const result = run(['bad', 'failure', 'empty'], digest, true, [], bad);
    refused(result);
    expect(result.stderr).toContain('cache=mismatch');
  });

  for (const pin of ['malformed', '../../etc/passwd', digest.toUpperCase(), digest.slice(1)]) {
    test(`a pin that is not 64 lowercase hex digits never reads the cache (${pin.slice(0, 16)})`, () => {
      // The transfers never deliver the pinned bytes, so only the cache could accept.
      const result = run(['bad', 'bad', 'bad'], pin, false, [], pin.includes('/') ? null : good);
      refused(result);
      expect(result.stderr).not.toContain('cache=');
    });
  }
});
