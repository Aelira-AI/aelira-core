/** Verify the installed package boundary, using only shipped files and dependencies. */
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, realpathSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

assert.equal(process.argv.length, 3, 'Pass the installed @aelira/cli package directory');
const root = realpathSync(resolve(process.argv[2]));
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
assert.equal(pkg.name, '@aelira/cli');
assert(!existsSync(join(root, 'src')), 'Use the packed installation, not the source checkout');
const manifest = JSON.parse(readFileSync(join(root, 'oclif.manifest.json'), 'utf8'));
const commands = Object.keys(manifest.commands).sort();
assert(commands.length > 0, 'The packed command manifest must not be empty');
const scratch = mkdtempSync(join(tmpdir(), 'aelira-packed-cli-'));
const env: NodeJS.ProcessEnv = { ...process.env, AELIRA_CONFIG_DIR: join(scratch, 'config'), FORCE_COLOR: '0' };
// Prevent inherited connection credentials from affecting package smoke checks.
delete env.AELIRA_API_KEY;
delete env.AELIRA_API_URL;
const bin = join(root, pkg.bin.aelira);
function run(args: string[]) {
  return execFileSync(process.execPath, [bin, ...args], {
    cwd: scratch, env, encoding: 'utf8', timeout: 30000, stdio: ['ignore', 'pipe', 'pipe'],
  });
}
const version = run(['--version']).trim();
assert(version.includes(`@aelira/cli/${pkg.version}`));
for (const id of commands) {
  const output = run([...id.split(':'), '--help']);
  assert(output.includes('USAGE'), `Missing help for packaged command ${id}`);
}
console.log(JSON.stringify({ version, command_count: commands.length, commands, source_directory_absent: true }, null, 2));
