# Rebuilt Debian zlib for 0.13.0

**Retired (unreleased).** On 1 November 2026 Debian published
`1:1.3.dfsg+really1.3.1-1+deb13u1` (DSA-6613-1), which carries Mark Adler's
fix to the `gz_write` failure path documented below — Debian labels it
CVE-2026-85091, and the Debian tracker marks CVE-2026-76844 fixed by the same
upload — plus a `crc32_combine` negative-length guard (CVE-2026-27171).
Following this document's own update path, the recipe was retired rather than
rebased: Debian's patch and the adapted one collide at the same hunk. The
images now install Debian's fixed `zlib1g` directly. What follows records the
0.13.0 build as shipped.

The API and development images run on Debian 13 (trixie). Debian has no fixed
trixie package for CVE-2026-76844 in `zlib`; the Debian tracker lists the fix
only for sid (`1:1.3.dfsg+really1.3.2-4`), and Ubuntu shows it unfixed as
well. From 0.13.0 the image build rebuilds Debian's own zlib source package
with the upstream fix adapted to trixie's 1.3.1 and installs the result in the
same build step that first installs the stock package, so no image layer holds
the unpatched library. The rebuild machinery is the one described in the
[0.10.0 backport document](debian-backports-0.10.0.md).

| Package | Debian source | Rebuilt version | Fixes |
| --- | --- | --- | --- |
| `zlib1g` | `1:1.3.dfsg+really1.3.1-1` | `...-1+b1+aelira1` | CVE-2026-76844 |

The image ships `zlib1g` `1:1.3.dfsg+really1.3.1-1+b1` (a binNMU of the
source), so the rebuilt version extends `+b1` and sorts above it. The recipe
ships `zlib1g` and `zlib1g-dev`; the 32-bit compatibility packages are
excluded at build time by the `nobiarch` profile (which also drops the
`gcc-multilib` build dependency). If any other binary package of the `zlib`
source were installed in either image — `libminizip1t64` and `minizip` ship
from the same source — `install-backports.sh` would fail the build rather
than leave a mixed version, so the green image build is itself the check that
none is.

## CVE-2026-76844 and why the upstream patch does not apply

The advisory (CNA range 1.2.11 through 1.3.2): after an underlying `write()`
fails, `gz_write()` returns without resetting `strm.next_in`, leaving it
pointed at the caller's buffer. A later `gz*` write call then derives a
position from the stale pointer and writes past a heap allocation. For the
code trixie ships the CNA states the precondition explicitly: in versions
before 1.3.1.2 the failed write must be followed by a `gzclearerr()` call.

In trixie's 1.3.1 `gz_write`, the direct path (writes of `state->size` bytes
or more) sets `strm.next_in` to the caller's buffer, and when `gz_comp()`
returns a write error it returns with `avail_in` still counting the
unconsumed input and `next_in` still pointing at the caller's buffer. After
`gzclearerr()` clears the stream error, a later small write takes the
buffered path, which computes its fill level as
`(strm.next_in + strm.avail_in) - state->in`. From the stale pointer that is
garbage; the subtraction `state->size - have` underflows, and the following
`memcpy(state->in + have, buf, copy)` writes past the heap allocation.

The upstream fix, [`df84af25`](https://github.com/madler/zlib/commit/df84af25dc1942490e1d1c899a07619152a46148)
("Fix buffer overflow bug in non-blocking gzwrite"), repairs the direct-write
loop of the 1.4 development line, where the non-blocking rewrite of 1.3.1.2
had introduced a different structure. It does not apply to 1.3.1: a
`patch --fuzz=0` dry-run against the pinned source fails its single hunk. The
recipe therefore ships `patches/CVE-2026-76844.patch`, the same fix adapted to
1.3.1's direct path: when the direct write fails, zero `avail_in` and reset
`next_in` to `state->in` before returning, so a stream whose error is cleared
can never derive a position from the abandoned caller buffer. The adapted
hunk applies at offset 0 with zero fuzz, and the recipe asserts the fix text
is present before building.

## Where the sources come from

Every source file was fetched through the distribution's signed archive index
and is pinned by SHA-256 in `docker/debian-backports/zlib/backport.conf`.

- The `.dsc`, upstream tarball and Debian packaging for `zlib`
  `1:1.3.dfsg+really1.3.1-1` match the `Checksums-Sha256` of the package's
  stanza in trixie `main/source/Sources.xz`
  (`6002f81f463a2d976d84b34170367cc16da2d60bd0ba9ab9d868d6e1c1a935d9`), whose
  integrity is carried by the trixie `InRelease`.
- **Verified:** that `InRelease` signature checks against Debian's published
  keyring (package `debian-archive-keyring` `2025.1`) with three valid
  signatures: Debian Archive Automatic Signing Key (12/bookworm)
  `4CB50190207B4758A3F73A796ED0E7B82643E131`, Debian Archive Automatic
  Signing Key (13/trixie) `B8E5F13176D2A7A75220028078DBA3BC47EF2265`, and
  Debian Stable Release Key (13/trixie)
  `41587F7DB8C774BCCF131416762F67A0B2C39DE4`.
- The `.dsc` pins its own inputs; `dpkg-source` re-checks every checksum, and
  the build refuses on any mismatch. Unlike the 0.10.0 packages there is no
  separate `.orig.tar.gz.asc`; trixie's zlib `.dsc` carries an inline
  uploader signature block, which was not independently verified. The trust
  for this recipe rests on the signed archive index, the same chain the other
  recipes use, plus review of the one-hunk diff.

The adapted patch is pinned by SHA-256
(`98ca233ecee6c954ab279454c5704d31e5e3969a4f829dd7a4fb17cdd3548d17`).

## What the control proves

`controls/gzwrite-stale-pointer` compiles the exact `gz_write` from the
patched tree under AddressSanitizer and UndefinedBehaviorSanitizer at `-O0`
and `-O2`, with `gz_comp` stubbed to consume sixteen bytes and then report a
failed underlying write, and with no file I/O at all. The harness writes once
through the direct path (which must fail and return 0), clears the error as
`gzclearerr()` does, and writes again through the buffered path.

- **Patched:** the second write is buffered in full; the harness asserts the
  return value, that `avail_in` is the written length, and that `next_in`
  points at the internal buffer, then exits cleanly with leak detection on.
- **Baseline:** the same sequence run against the function with only the
  fix text (the two assignments and the comment) removed, so
  `if (gz_comp(...) == -1) { return 0; }` — the original code, byte for byte
  outside the guard. AddressSanitizer reports
  `heap-buffer-overflow, WRITE of size 64` into the poisoned region past the
  256-byte input buffer, at both optimization levels. This proves the fix
  text is exactly what stops the overflow.

**Reachability.** None of this was shown to be reachable from user input in
this image. The trigger needs a `gz*` write whose underlying write fails
(e.g. a closed or broken descriptor, `EPIPE`) followed by `gzclearerr()` and
a further write. The rebuild removes the need to rely on that argument, as
with the 0.10.0 rebuilds.

## Relation to CVE-2026-85091

The [0.9.12 known-unfixed advisories](known-unfixed-advisories-0.9.12.md)
dispositioned CVE-2026-85091 as not affected because its upstream range
begins at 1.3.1.2, where the non-blocking `gzwrite` change introduced the
`gz_vacate` code. That reasoning stands for 85091: trixie's 1.3.1 has no
`gz_vacate`. The CNA later assigned CVE-2026-76844 against the range before
1.3.1.2 — the same upstream commit `df84af25` fixes both advisories, and the
Debian tracker records CVE-2026-76844 as the "same fixing change". The 0.9.12
document's range analysis predated that second CVE, so the advisory now
scanned against trixie's `zlib1g` is the one covering the code 1.3.1
actually contains, which is why this rebuild exists and the earlier
not-affected disposition did not cover it.

## Layer purity and the pinned base image copy

The image check (`scripts/verify_debian_backports.ts`) requires every Docker
layer of the shipped image that contains a backported library file to carry
the patched bytes. `libxml2`, `libX11` and `libXrender` enter the image only
through this build, so their unpatched forms are never written to any layer:
they are installed and replaced inside the same `RUN`, before the layer
commits.

`zlib1g` is different. The runtime stage inherits the pinned
`debian:trixie-slim` base image, whose single layer
(`sha256:258ba1f62b513e7bb7492d3cc8ce445e4ef176a14980f193f789b6d7270ae61c`)
already ships the stock `libz.so.1.3.1`. Docker layers are immutable history:
the backport replaces the file — the effective, loaded, dpkg-owned library is
the patched one — but no build step can delete bytes from a base layer it
merely inherits.

The guarantee for this library is therefore explicitly scoped, and recorded
as `preseeded zlib1g libz.so.1.3.1` in the recipe and receipt: stock copies
are tolerated strictly in layers *before* the layer holding the patched file,
and any stock occurrence after it — a later reinstall, a stray copy — still
fails the check, as does a missing patched occurrence. What remains enforced
without exception: the merged filesystem the container actually loads is
patched (`install-backports.sh` verifies the receipt's `library=` hash against
the installed file in the same layer that replaces it, and the scanner reports
the CVE patched from that state). The residual exposure is a historical base
layer identical to the one every `debian:trixie-slim`-derived image carries;
removing it would mean rebuilding Debian's base image itself.

## Updating this rebuild

As with the other recipes: when Debian publishes a fixed trixie `zlib`, the
scanner reports a fixed version, the inventory gate stops accepting the
rebuilt row, and installing the rebuild would be a downgrade, which the
install script refuses. Rebase the recipe on the new source or drop it once
Debian's package carries the fix.
