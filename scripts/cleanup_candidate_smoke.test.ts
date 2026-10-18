import { test, expect } from 'bun:test';
import { spawnSync } from 'node:child_process';
import { chmod, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const script = join(import.meta.dir, 'cleanup_candidate_smoke.sh');
const owner = '12345678-1234-1234-1234-123456789abc';

test('candidate cleanup checks listing, inspection, removal, and final discovery', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'aelira-candidate-cleanup-test-'));
  try {
    const mock = join(dir, 'docker');
    await writeFile(mock, `#!/bin/sh
printf '%s\\n' "$*" >> "$MOCK_LOG"
case "$1 $2" in
  'container ls')
    if [ "$MOCK_MODE" = container-list-fail ]; then exit 23; fi
    if [ "$MOCK_MODE" = container-verify-fail ] && [ -e "$MOCK_CONTAINER_REMOVED" ]; then exit 30; fi
    if [ ! -e "$MOCK_CONTAINER_REMOVED" ]; then printf 'owned-container\\n'; fi ;;
  'container inspect')
    if [ "$MOCK_MODE" = container-inspect-fail ]; then exit 24; fi
    printf '%s\\n' "$MOCK_OWNER" ;;
  'container rm')
    [ "$5" = owned-container ] || exit 25
    if [ "$MOCK_MODE" = container-remove-fail ]; then exit 31; fi
    : > "$MOCK_CONTAINER_REMOVED" ;;
  'network ls')
    if [ "$MOCK_MODE" = network-list-fail ]; then exit 26; fi
    if [ ! -e "$MOCK_NETWORK_REMOVED" ]; then printf 'owned-network\\n'; fi ;;
  'network inspect')
    if [ "$MOCK_MODE" = network-inspect-fail ]; then exit 27; fi
    printf '%s\\n' "$MOCK_OWNER" ;;
  'network rm')
    [ "$3" = owned-network ] || exit 28
    : > "$MOCK_NETWORK_REMOVED" ;;
  *) exit 29 ;;
esac
`);
    await chmod(mock, 0o755);
    for (const mode of ['success', 'container-list-fail', 'container-inspect-fail', 'container-remove-fail',
      'container-verify-fail', 'network-list-fail', 'network-inspect-fail']) {
      const log = join(dir, `${mode}.log`);
      const containerRemoved = join(dir, `${mode}.container-removed`);
      const networkRemoved = join(dir, `${mode}.network-removed`);
      const result = spawnSync('bash', [script, owner], { encoding: 'utf8',
        env: { ...process.env, PATH: `${dir}:${process.env.PATH}`, MOCK_MODE: mode,
          MOCK_OWNER: owner, MOCK_LOG: log, MOCK_CONTAINER_REMOVED: containerRemoved,
          MOCK_NETWORK_REMOVED: networkRemoved } });
      expect(result.status === 0).toBe(mode === 'success');
      const calls = await readFile(log, 'utf8');
      if (mode === 'container-inspect-fail') expect(calls).not.toContain('container rm');
      if (mode === 'network-inspect-fail') expect(calls).not.toContain('network rm');
      if (mode === 'success') {
        expect(calls).toContain('container rm --force --volumes owned-container');
        expect(calls).toContain('network rm owned-network');
      }
    }
  } finally { await rm(dir, { recursive: true }); }
});
