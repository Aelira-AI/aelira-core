/** One fail-closed channel policy shared by every release publisher. */
const tag = process.argv[2] ?? '';
// Only stable and numbered alpha releases are supported. Reject metadata and
// trailing whitespace rather than allowing an ambiguous tag to reach a registry.
const match = /^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-alpha\.(0|[1-9][0-9]*))?$/.exec(tag);
if (!match || match[0] !== tag || process.argv.length !== 3) {
  console.error('Release tag must be vMAJOR.MINOR.PATCH or vMAJOR.MINOR.PATCH-alpha.N');
  process.exit(1);
}
const version = tag.slice(1);
const alpha = match[4] !== undefined;
console.log(JSON.stringify({
  version,
  channel: alpha ? 'alpha' : 'stable',
  dockerTags: alpha ? [version] : [version, `${match[1]}.${match[2]}`, 'latest'],
  npmTag: alpha ? 'alpha' : 'latest',
  githubArgs: alpha ? ['--prerelease', '--latest=false'] : ['--prerelease=false'],
}));
