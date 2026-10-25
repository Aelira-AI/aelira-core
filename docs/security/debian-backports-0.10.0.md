# Rebuilt Debian libxml2, libX11 and libXrender for 0.10.0

The API and development images run on Debian 13 (trixie). Debian has no fixed
trixie packages for the advisories below. Upstream projects and Ubuntu have
published fixes. From 0.10.0 the image build rebuilds Debian's own source
packages with those fixes and installs the result in the same build step that
first installs the stock packages, so no image layer holds the unpatched
libraries.

| Package | Debian source | Rebuilt version | Fixes |
| --- | --- | --- | --- |
| `libxml2` | `2.12.7+dfsg+really2.9.14-2.1+deb13u3` | `...+deb13u3+aelira2` | CVE-2026-6653, CVE-2026-74860, CVE-2026-86138, CVE-2026-86140, CVE-2026-86142, CVE-2026-86143, CVE-2026-86144 |
| `libx11` | `2:1.8.12-1` | `2:1.8.12-1+aelira1` | CVE-2026-88806, CVE-2026-94283, CVE-2026-94284, CVE-2026-94285 |
| `libxrender` | `1:0.9.12-1` | `1:0.9.12-1+aelira1` | CVE-2026-88807 |

CVE-2026-86139 is listed against trixie's `libxml2`, but the vulnerable code
is not present in it; see [below](#cve-2026-86139-not-affected).

This replaces the single-advisory
[CVE-2026-6653 rebuild](libxml2-6653-backport-0.9.13.md). That patch is still
applied, now as the first of seven.

## Where the patches come from

Every source file was fetched through the distribution's signed archive index
and is pinned by SHA-256 in `docker/debian-backports/SOURCE/backport.conf`.

- **Debian sources.** The `.dsc`, upstream tarball, upstream signature and
  Debian packaging for each package were authenticated through Debian's signed
  trixie `InRelease` (Debian archive keys 12 and 13 and the trixie stable
  release key, checked with `gpgv` against `debian-archive-keyring`) and the
  SHA-256 sums in its `main/source/Sources.xz`. The `libxml2` upstream tarball
  and packaging are byte-identical to the ones the 0.9.13 rebuild pinned. Each
  `.dsc` also carries a valid OpenPGP signature from its uploader in the
  Debian keyring: Guilhem Moulin for `libxml2`, Timo Aaltonen for `libx11` and
  `libxrender`.
- **libxml2 patches.** Canonical's patches for Ubuntu 24.04 `libxml2`
  `2.9.14+dfsg-1.3ubuntu3.10`, extracted from its packaging archive (SHA-256
  `d2d821830eb60edbcfcbe30e2827420e9e439e98c85c812352969cdda764d60b`), which was
  authenticated through Ubuntu's signed `noble-security` index. This is the
  same archive the CVE-2026-6653 patch came from. They are applied in
  Canonical's series order. Canonical rewrote them for 2.9.14, which is the
  base Debian trixie ships.
- **libX11 and libXrender patches.** The upstream commits named in each
  recipe, all in the `libX11-1.8.14` and `libXrender-0.9.13` release tags
  (2026-10-16), downloaded from the freedesktop.org GitLab projects.

**Verified:** the archive index chains and the uploader signatures above.

**Partly verified:** the upstream signatures on the libX11 and libXrender
tarballs (`.orig.tar.gz.asc`) verify against Alan Coopersmith's key as
published on his freedesktop.org GitLab account (primary
`4A193C06D35E7C670FA4EF0BA2FB9E081F2D130E`, signing subkey
`2F7DBCAD5B30E668BBD516C1CFDF148828C642A7`). The copy of that key on
keys.openpgp.org lacks the signing subkey, and Debian's
`debian/upstream/signing-key.asc` for these packages does not include it, so
this rests on one key source.

**Not verified:** the upstream git commits carry no signatures. Their trust
comes from the GitLab project over HTTPS and from review of the diffs, which
are small: 3, 33, 29, 11 and 1 changed lines. Canonical's patches are trusted
through Ubuntu's signed archive index, not independently re-derived from
upstream.

## How the rebuild works

`docker/debian-backports/build-backport.sh` runs in a builder stage that never
ships. For each package it:

1. Checks the SHA-256 of every pinned input and patch.
2. Unpacks the `.dsc` with `dpkg-source` and applies Debian's own patches.
3. Dry-runs each reviewed patch with `patch --fuzz=0`. It refuses any fuzz,
   failed or reversed hunk, and any offset not listed in the recipe. The
   reviewed offsets are: CVE-2026-6653 at -3 lines (as in 0.9.13);
   CVE-2026-86140 at +624 and +627 lines, because trixie's `valid.c` is longer
   than noble's before `xmlSnprintfElements` (the hunks match their context
   exactly); and libX11 commit `924fc91f` (CVE-2026-94284) at -9 lines,
   because it was written against 1.8.14.
4. Checks the source assertions and runs the sanitizer controls described
   below.
5. Adds a changelog entry and builds with Debian's own rules, which keep
   Debian's configure options, hardening and symbols files. `libxml2` keeps
   the `nopython` profile.
6. Writes a receipt listing every input, patch and package hash, the CVEs the
   patches fix, the assertions and controls run, and the hash of each shared
   library.

Builds are reproducible, because the image check compares two no-cache builds
by manifest digest:

- **Fixed build path:** each package builds under a fixed path,
  `/tmp/debian-backport-SOURCE`. Compilers record the build path in debug
  information, and the GNU build-id is computed before stripping. A random
  path therefore changed shipped libraries between builds; the retired
  brotli rebuild showed it.
- **Fixed file times:** `SOURCE_DATE_EPOCH` is the generated changelog entry's
  date, so `dpkg-deb` clamps package file times to it. The builder refuses a
  `changelog_date` in the future, because `dpkg-deb` only lowers later times.
- **Second build check:** a recipe with `reproducible` is built
  a second time from scratch. The build fails unless every package and the
  receipt are byte-identical.

`install-backports.sh` installs the rebuilt packages in the same `RUN` as the
first `apt-get install`. It replaces every installed binary package from the
three sources, refuses mixed versions, checks the library hashes and keeps
each receipt in `/usr/share/doc/aelira-debian-backports/`.

`scripts/verify_debian_backports.ts` checks a built image:

- Each receipt must match its recipe exactly.
- Installed package versions, library real paths and hashes, and `dpkg`
  ownership must match the receipt.
- The CVE-2026-6653 entity amplification check and the Perl XML::LibXML,
  XSLT and LaTeXML consumer checks from 0.9.13 must pass. The ImageMagick
  check requires the image policy to refuse XC and MSL, decodes a small PNG
  from memory, and requires the libMagickCore loaded by Perl to map the
  rebuilt libxml2. The FFmpeg DASH check was dropped when the image moved to
  its own FFmpeg 9.0.2 build, which does not link libxml2.
- Ghostscript, Playwright's Chromium, cairo and ImageMagick must resolve libX11
  and libXrender to the receipt's files and still start.
- Each patched library must occur in exactly one image layer with the
  receipt's hash.

`scripts/verify_trivy_inventory.ts` treats a scanner row as patched only when
its package and exact version come from a rebuild whose image receipt passed
these checks and whose patches name that CVE. A row for a rebuilt version that
Debian later fixes is unresolved again, which forces a rebase. The scanner
still reports every one of these advisories, because Debian's tracker has no
fixed trixie version; the build log lists each as patched.

## What the tests prove, and what they do not

**Behavioural controls.** These are small inputs that fail before the patch
and pass after it. Each runs during every image build, on each architecture:

| Advisory | Control |
| --- | --- |
| CVE-2026-86144 | A document parsed with `XML_PARSE_NONET` and expanded with `xmlXIncludeProcess()` includes `http://127.0.0.1:PORT/` as text. A local listener records whether an HTTP request arrives. The rebuilt library sends none; Debian's stock binary of the same source version, downloaded from the archive, sends one. A local `file://` text include must still work, so the probe is known to exercise XInclude. |
| CVE-2026-88806 | The exact patched `_XkbReadKeyActions` from the built tree, compiled under AddressSanitizer and UndefinedBehaviorSanitizer at `-O0` and `-O2` with a mocked reply buffer. Valid key ranges succeed; a range ending one key past `max_key_code` is rejected. With only the upstream guard removed, the same input writes past `key_acts` and the sanitizer stops it. |
| CVE-2026-94285 | The exact patched `byteM_parse_codeset` with a GBK-style two-byte codeset, under the same sanitizers. A lead byte at the end of the input is no longer read past; with the guard removed the sanitizer reports the out-of-bounds read. |
| CVE-2026-88807 | The existing `runtime-security/xrender` control, applied to the Debian source: the exact patched `XRenderQueryFormats` with a mocked reply that has more subpixel entries than screens. |

**Patch application and source review only.** These have no behavioural test:

- CVE-2026-86138, CVE-2026-86142 and CVE-2026-86143 need a single value of 2
  to 4 GiB (a qualified name, an XPointer expression or one output buffer). A
  test at that size is impractical. Only the exact patch application and
  review of the patched source prove them, not an end-to-end test.
- CVE-2026-86140 (`xmlSnprintfElements`). In 2.9.14 both callers pass an empty
  5000-byte buffer, and the loop's existing bounds always leave room for the
  closing parenthesis. We found no input that overflows this version's
  buffer, so the patch is applied as hardening that matches upstream.
- CVE-2026-74860 is in libxml2's Python bindings, which this build does not
  produce (`nopython`). The patch is applied so the source matches Canonical's.
- CVE-2026-94283 and CVE-2026-94284 are in the client side of the X Input
  Method protocol and need a malicious XIM server. A control would need a
  fake XIM server; none was built.
- Perl's XML::LibXML installs its own external-entity loader and refuses
  network loads under `no_network` even with stock libxml2, so a Perl probe
  cannot show the CVE-2026-86144 difference. The C control above calls
  libxml2 directly.

**Reachability.** None of these was shown to be reachable from user input in
this image. The X libraries are loaded by Chromium, cairo, ImageMagick and
Ghostscript, but the image runs no X server. LaTeXML, the main
consumer of system `libxml2`, uses no XInclude or DTD validation in its
default path. The rebuild removes the need to rely on those arguments.

**ABI.** The CVE-2026-6653 patch extends the public `xmlEntity` and
`xmlParserInput` structures, as in 0.9.13. The new libxml2 patches change no
public header. libX11 commit `f263bbca` renames a private field in the
internal `XimintP.h` without changing its size. The consumer checks cover the
image's own native consumers; they are not a general ABI claim.

**Architectures.** Builds, controls and image checks have been run on native
arm64. amd64 runs in CI.

## brotli: fixed by Debian, rebuild retired

CVE-2026-111497 is a buffer overflow in the Brotli decoder (`c/dec/decode.c`)
in brotli 1.0.3 to 1.2.0. Debian fixed it in trixie on 23 October 2026 with
[DSA-6582-1](https://lists.debian.org/debian-security-announce/2026/msg00498.html),
`brotli` `1.1.0-2+deb13u1`, which carries the same upstream fix,
[`da98063`](https://github.com/google/brotli/commit/da9806362874b9bd4b552a3af360b5d6347beb12).

**Retired the same day.** A rebuild of Debian's `1.1.0-2` source with that
commit (`1.1.0-2+b7+aelira1`, [#774](https://github.com/Aelira-AI/aelira-core/issues/774))
was merged to main in [#777](https://github.com/Aelira-AI/aelira-core/pull/777). Debian's update sorts above it, so
the image build then failed installing the rebuild as a downgrade, as the
recipe format intends. The brotli recipe is removed. The image now installs
`libbrotli1` `1.1.0-2+deb13u1` from trixie-security, and the scanner reports
CVE-2026-111497 as fixed, so it no longer appears in the inventory. The
builder improvements made for brotli still apply to the other recipes:

- the fixed build path
- `SOURCE_DATE_EPOCH` taken from the recipe's changelog date
- the `reproducible` and `tests` directives
- the binNMU version rule

**Consumers.** These are the files in the API image that link a `libbrotli`
library directly (ELF `DT_NEEDED`). All now load Debian's fixed library:

| Package | File | Links |
| --- | --- | --- |
| `libcurl4t64` | `libcurl.so.4` | `libbrotlidec` (HTTP `Content-Encoding: br`) |
| `libcurl3t64-gnutls` | `libcurl-gnutls.so.4` | `libbrotlidec` |
| `libfreetype6` | `libfreetype.so.6` | `libbrotlidec` (WOFF2 fonts) |
| `libheif1` | `libheif.so.1` | `libbrotlidec`, `libbrotlienc` |

**Other Brotli copies.** Two copies of Brotli in the image are not the Debian
library:

- The application's Python `brotli` package is the 1.2.1 wheel, which
  contains the upstream fix.
- Node.js, which runs the Pa11y scanner, compiles in its own Brotli. The image
  pins Node.js v24.22.1. Its
  [release notes](https://nodejs.org/en/blog/release/v24.22.1) (21 October
  2026) list "(CVE-2026-111497) deps: brotli: cherry-pick
  0ed4268a4401abcb44720963". That is the upstream change that `da98063`
  cherry-picks to brotli 1.1.1. So v24.22.1 includes the fix, even though
  its `process.versions.brotli` still reads 1.2.0.

## CVE-2026-86139: not affected

The advisory is a missing zero-length check in `xmlURIEscapeStr`, introduced
upstream in libxml2 2.13.0 and fixed by adding `if (len == 0) return(NULL);`.
Debian trixie ships 2.9.14 under a `2.12.7+dfsg+really2.9.14` version string.
Its `uri.c` already has the check as `if (!(len > 0)) return(NULL);`, which
is line 1690 of the pinned source with Debian's patches applied. Canonical's
noble package has no patch for it. Debian's tracker lists trixie as vulnerable
and bookworm as not affected because the bug arrived in 2.13.0; the trixie row
appears to follow the version string rather than the code.

The rebuild fails if that line is missing. The disposition in
`.trivy-unfixed-dispositions.json` is `not_affected` with
`vulnerable_code_not_present`. It applies only to the rebuilt version, and the
inventory gate accepts it only after the rebuild's receipt is verified.

## CVE-2026-76781: not included

Canonical's noble package also fixes CVE-2026-76781 (a NULL dereference in
catalog `nextCatalog` handling). Its patch does not apply to trixie without
editing a hunk by hand, because trixie's loop lacks the `prev->value`
comparison. The scanner does not report it at HIGH or CRITICAL, and it needs a
hostile XML catalog; catalogs here are LaTeXML's and the system's, under
read-only `/usr` and `/etc`. It is left out rather than shipped as a
hand-edited patch.

## Updating a rebuild

When Debian publishes a newer trixie version of any of these packages, the
pinned source is behind. The scanner then reports a fixed version for the
affected rows, the inventory gate stops accepting them, and installing the
rebuilt package would be a downgrade, which `apt-get` refuses because the
install script does not pass `--allow-downgrades`. Rebase
the recipe on the new source, or drop it once Debian's package carries all
the fixes.
