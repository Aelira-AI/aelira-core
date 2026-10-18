import { describe, expect, test } from 'bun:test';
import { createHash } from 'node:crypto';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, chmodSync, existsSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// Execute the production helper unchanged, without running the wheel build.
// Only transport responses are simulated; verification computes real SHA-256.
const source = readFileSync(new URL('../docker/build-lxml-wheel.sh', import.meta.url), 'utf8');
const helper = source.slice(source.indexOf('fetch()'), source.indexOf('\nfetch https://'));
const good = 'pinned source fixture\n';
const digest = createHash('sha256').update(good).digest('hex');
const bad = '<html>untrusted response with PRIVATE_RESPONSE_MARKER</html>\n';

function run(modes: string[], pin = digest, stale = false) {
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
    const result = Bun.spawnSync(['sh', '-eu', '-c', `${helper}\nfetch https://example.invalid/source "$1" archive.tar.gz\nprintf accepted > accepted\n`, 'test', pin], {
      cwd: dir,
      env: { ...process.env, FIXTURE: dir, PATH: `${bin}:${process.env.PATH}` },
      timeout: 5000,
    });
    return {
      status: result.exitCode,
      stderr: result.stderr.toString(),
      stdout: result.stdout.toString(),
      count: Number(readFileSync(join(dir, 'count'), 'utf8')),
      calls: readFileSync(join(dir, 'calls'), 'utf8').trim().split('\n'),
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

describe('lxml pinned source fetch (offline production shell)', () => {
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
    expect(result.stderr).toBe(`lxml source archive.tar.gz attempt=1/3 curl_status=0 bytes=${Buffer.byteLength(bad)} sha256=${createHash('sha256').update(bad).digest('hex')} expected=${digest}\n`);
  });

  test('persistently wrong bytes exhaust exactly three bounded attempts', () => {
    const result = run(['bad', 'bad', 'bad', 'good']);
    refused(result);
    expect(result.sleeps).toEqual(['1', '1']);
    expect(result.stderr.trim().split('\n')).toHaveLength(3);
    for (const call of result.calls) {
      expect(call).toContain('--connect-timeout 10 --max-time 60');
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
});
