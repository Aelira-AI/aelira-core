# Opt-in alpha releases

Alpha publication accepts only `vMAJOR.MINOR.PATCH-alpha.N`, where each numeric
component has no leading zero unless it is zero. Stable tags remain
`vMAJOR.MINOR.PATCH`. Beta, release-candidate, build-metadata and malformed tags
are rejected. `scripts/release_channel.ts` supplies the channel policy to every
publisher; it emits a plan and performs no publication itself.

| Surface | Stable release | Alpha release |
| --- | --- | --- |
| Docker | Exact version, minor version and `latest` | Exact alpha version only |
| CLI registry | `latest` distribution tag | `alpha` distribution tag |
| GitHub release | Normal release with automatic latest selection | Prerelease, explicitly excluded from latest |
| Production Compose defaults | Previous checked-in stable reference | Unchanged; operator must select the alpha explicitly |

Both API and dashboard images retain native `linux/amd64` and `linux/arm64`
builds. Alpha publication keeps the same CI gate, signed annotated tag check,
protected release environment, disclosure policy, version matching, image scans,
verified digest receipts, signatures and seven-file SBOM requirement as stable
publication. The workflow remains serialized across both channels.

## Before publishing

Choose an alpha version deliberately and update the application/package metadata
and its release-version tests. The CLI package version, lockfile version and
lockfile root version must exactly match the tag. Preserve stable Compose
defaults and installation examples intended for ordinary deployments. Supply a
nonempty checked-in `docs/releases/<tag>.md` containing limitations, installation,
backup/recovery instructions and the tested revision/artifact identities.

Run the packed CLI check below, the release contract tests and the required
administrator journeys. Obtain release approval before pushing a signed tag.
A passing local channel test proves command selection, not successful registry
publication or package-account authorization.

The reusable CLI publisher already grants `id-token: write`, runs on a
GitHub-hosted runner in the `release` environment, and uses npm 11.6.2 with
Node 22. Confirm that the package's **npm-side trusted publisher** authorizes the
actual repository, workflow identity and environment. A reusable workflow's
identity must be checked in the real release context; granting a GitHub token
permission alone does not establish npm trust. An expired long-lived npm token
is not repaired by changing the alpha distribution tag.

[The npm trusted-publishing documentation](https://docs.npmjs.com/trusted-publishers/)
describes account-side setup and the Node/npm requirements. Confirm authorization
before approving publication; local packing and installation do not test it.

## Verify the packed CLI

Use a fresh source copy so generated packing files do not change a developer's
working tree. These commands build from the selected committed revision. Run
from the repository root with Bun and Node 22 or newer available:

```bash
pack_root=$(mktemp -d)
mkdir -p "$pack_root/source" "$pack_root/artifacts" "$pack_root/installed"
git archive HEAD cli | tar -xf - -C "$pack_root/source"
(
  cd "$pack_root/source/cli"
  bun install
  bun run build
  PATH="$PWD/node_modules/.bin:$PATH" bun pm pack --destination "$pack_root/artifacts"
)
(
  cd "$pack_root/installed"
  printf '{"private":true}\n' > package.json
  bun add --production --ignore-scripts "$pack_root"/artifacts/*.tgz
)
node --experimental-strip-types scripts/verify_installed_cli.ts \
  "$pack_root/installed/node_modules/@aelira/cli"
```

The verifier runs outside the source tree with a separate CLI configuration
directory. It requires the shipped command manifest, checks the installed
version, refuses a source checkout, and invokes help for every packaged command.
It does not require a registry publishing credential.

Also use the installed `aelira` executable with a synthetic API key against an
isolated real API and worker. Scan a document, remediate it, download the result
and independently rescan those bytes. Compare the download SHA-256 with the
server's recorded artifact measurement. Help-only success cannot validate file
upload serialization. Revoke the synthetic key afterward, and keep secrets out
of command arguments and evidence.

## Evaluator opt-in

After an approved alpha has actually been published, select its exact version
or the CLI's explicit `alpha` channel. The following version is illustrative;
these examples do not announce its availability:

```bash
bunx @aelira/cli@0.10.0-alpha.1 --version
# Or deliberately follow the moving CLI alpha channel:
bunx @aelira/cli@alpha --version

# Only after backing up and reviewing the alpha's release notes:
AELIRA_VERSION=0.10.0-alpha.1 docker compose -f docker-compose.prod.yml pull
AELIRA_VERSION=0.10.0-alpha.1 docker compose -f docker-compose.prod.yml up -d
```

There is no moving Docker alpha tag in this policy. Pin the exact alpha version
or digest. Before claiming support, verify both published architectures and run
[the upgrade and recovery rehearsal](upgrade-recovery-rehearsal.md) against the
actual published images. A local source overlay does not establish that gate.

The distribution-tag and release-metadata behavior follows the official
[npm publish](https://docs.npmjs.com/cli/v11/commands/npm-publish/) and
[GitHub release creation](https://cli.github.com/manual/gh_release_create)
contracts. Existing stable publication behavior is preserved.
