# Ubuntu Expat source backport candidate

This directory builds an honest `2.7.4-1ubuntu0.2+aelira1` Ubuntu 26.04
`libexpat1` package from Ubuntu's `2.7.4-1ubuntu0.2` source. It keeps Ubuntu's
existing patch stack, build flags, package layout, SONAME, and Expat feature
settings. It adds the upstream fixes for CVE-2026-93990, CVE-2026-102633,
and CVE-2026-77214, with their upstream regression tests. Locally built
packages are candidates for validation; they are not published Ubuntu
security updates.

## Native package acceptance

On a native Linux `x86_64` or `aarch64` Docker host, give the acceptance
harness an empty output directory and the expected native architecture:

```sh
mkdir -p /tmp/expat-native-acceptance
AELIRA_EXPAT_OUTPUT=/tmp/expat-native-acceptance \
  AELIRA_EXPAT_EXPECT_ARCH=amd64 \
  bash runtime-security/expat/native-acceptance.sh
```

Use `arm64` on an aarch64 runner. The harness rejects unsupported runner
architectures, a mismatched Docker daemon, and a runner architecture that
differs from the expected CI matrix entry. With no saved inputs, it pulls the
pinned multi-architecture Ubuntu image, enables signed Ubuntu source indexes,
downloads the exact `2.7.4-1ubuntu0.2` source, checks all three input hashes,
and provisions build dependencies once. The signed APT source and binary
index material, archive keyring, source fetch logs, base/toolchain package
lists, exact added/upgraded package DEBs and hashes, and frozen builder image
ID remain in the output. The pinned base digest and saved package delta retain
the toolchain inputs after an ephemeral runner ends. APT's signed archive
indexes authenticate the source; the original
DSC uploader signature is not verified by this recipe.

Two separate empty work directories then build offline as an unprivileged
user, with read-only recipe/source mounts and container roots, no network,
all capabilities dropped, no-new-privileges, and CPU/memory/PID limits. The
harness requires byte-identical DSC, Debian source delta, and runtime DEB
files, plus full C/C++ upstream suite passes in both builds. It compares
original and candidate narrow/wide SONAMEs and exported symbols, installs the
candidate DEB into a disposable image, and probes the actual installed Python
Expat consumers and native libraries from an unprivileged read-only container.
The probe asserts architecture, version, loaded path and hashes, feature
parity, valid parsing before malformed rejection, six buffer-capacity
boundaries, and wide-library UTF-16 controls. Nothing is published.

For an offline replay with saved authenticated source files and an already
frozen builder image, also supply:

```sh
export AELIRA_EXPAT_SOURCE_INPUT=/absolute/path/to/saved-source
export AELIRA_EXPAT_BUILDER_IMAGE=sha256:operator-supplied-image-id
```

The image ID and saved package list are material inputs to the repeatability
claim. A later online bootstrap can yield a different toolchain. The legacy
ARM64 direct recipe remains available below on a native aarch64 host.

Run on a native aarch64 host with Docker, empty writable scratch, and the
saved authenticated original Ubuntu source files mounted as
`/source-input`:

```sh
mkdir -p /tmp/aelira-expat-build
AELIRA_EXPAT_SOURCE_INPUT=/absolute/path/to/authenticated-source
docker run --rm --user 0 --entrypoint /bin/sh \
  -v "$PWD/runtime-security/expat:/recipe:ro" \
  -v "$AELIRA_EXPAT_SOURCE_INPUT:/source-input:ro" \
  -v /tmp/aelira-expat-build:/work \
  ubuntu@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7 \
  /recipe/build-arm64.sh
```

If the workspace moves, point `AELIRA_EXPAT_SOURCE_INPUT` at the saved
`expat-source-artifacts` directory containing the original Ubuntu `.dsc`,
`.orig.tar.gz`, and `.debian.tar.xz`. If no source-input mount is supplied,
the recipe fetches the exact source version through APT's signed Ubuntu
archive index. It checks the source and patch SHA-256 manifests,
unpacks the source, applies patches with Quilt, creates a new source package,
builds binary packages, and runs `make -C build check`. It does not install
anything on the host or publish a package. The `output-sha256.txt`,
`source-package-build.log`, `package-build.log`, and `upstream-tests.log`
remain in scratch.

For byte-reproducible ARM64 archives, provision the build dependencies once
from Ubuntu's signed APT indexes, then freeze that container as a local image.
One way is to run the online command above with `--name expat-build-deps`
instead of `--rm`; after it succeeds, run `docker commit expat-build-deps
expat-frozen-builder`, inspect the resulting image ID, and save its
`dpkg-query -W` package list. Keep the original build and container as
evidence. Use that same image ID
for two separate, empty work directories; mount the saved source and this
recipe read-only. The offline invocation is:

```sh
AELIRA_EXPAT_BUILDER_IMAGE=sha256:operator-supplied-frozen-image-id
AELIRA_EXPAT_SOURCE_INPUT=/absolute/path/to/authenticated-source
AELIRA_EXPAT_WORK=/absolute/path/to/empty-writable-scratch
docker run --rm --user "$(id -u):$(id -g)" \
  --cap-drop ALL --security-opt no-new-privileges \
  --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=1g \
  -e HOME=/work -e AELIRA_EXPAT_PREINSTALLED_BUILD_DEPS=1 \
  -e AELIRA_EXPAT_HOST_ARCH=arm64 \
  -v "$PWD/runtime-security/expat:/recipe:ro" \
  -v "$AELIRA_EXPAT_SOURCE_INPUT:/source-input:ro" \
  -v "$AELIRA_EXPAT_WORK:/work" \
  "$AELIRA_EXPAT_BUILDER_IMAGE" /bin/sh /recipe/build-arm64.sh
```

Run it again with a different empty `AELIRA_EXPAT_WORK`. With the saved source
mounted, this invocation checks the source and patch manifests and verifies
installed build dependencies with `dpkg-checkbuilddeps`; it makes no APT request. The builder
image ID fixes the toolchain and dependency versions for the comparison.
The recipe derives `SOURCE_DATE_EPOCH` from the authenticated Ubuntu source
changelog before adding the local entry, then normalizes Debian source archive
mtimes. The Ubuntu source entry predates the build, so `dpkg-deb` clamps newly
generated binary archive mtimes to that epoch. The candidate's honest version
and changelog date stay unchanged. This makes archive bytes comparable across
separate builds with the same authenticated inputs and frozen builder. It
does not assert identical bytes across future APT snapshots or architectures.

Upstream commit provenance:

| Advisory | Upstream commit | Candidate patch |
| --- | --- | --- |
| CVE-2026-93990 | `0cfd15bdf4b2c22d6b0df73610709dfb60921091` and tests `28fcfba540f6933aa8904a1514c4811713d2ab72` | `ubuntu-93990.patch`, `ubuntu-93990-tests.patch` |
| CVE-2026-102633 | `69edbec09f2cd3e0bd6e0a093b55ea38af902d7d` | `upstream-102633.patch` |
| CVE-2026-77214 | `4d9b1c499ecb66323260a7274edcf86c5eab0517` | `ubuntu-77214.patch` |

All four commits are ancestors of the signed upstream `R_2_9_0` tag. The tag
signature verifies against signing subkey
`CB8DE70A90CFBF6C3BF5CC5696262ACFFBD3AEC6` of Sebastian Pipping's
primary key `3176EF7DB2367F1FCA4F306B1F9B0E909AF37285`.

The Ubuntu adaptations change only source-context compatibility: the 93990
patch accounts for Ubuntu's existing `FASTCALL` declaration; the 77214 patch
accounts for Ubuntu's existing `XML_INDEX_MAX` overflow guard; and the 93990
test helper casts `malloc` for Ubuntu's C++ test target. The security logic and
test expectations remain upstream's. `upstream-102633.patch` is unmodified.

CVE-2026-76957 is already represented by Ubuntu's two inherited patches.
CVE-2026-76956 concerns Expat 2.8.2/2.8.3 `getentropy` handling; this
2.7.4 source has no `getentropy` call. Package versions and scanner output
alone do not establish advisory clearance. CVE-2025-66382 remains a visible
Low-severity finding; this candidate does not repair it. Native acceptance
on both architectures, independent review, all-copy inventory, and final-image
scans remain release gates. A passing package harness does not clear an
unrelated embedded Expat copy or authorize publication.
