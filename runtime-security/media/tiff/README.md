# TIFF 4.7.2 Ubuntu package candidate

This directory prepares a **held** Aelira Core native TIFF package candidate.
It rebases Ubuntu 26.04 `tiff 4.7.0-3ubuntu5` packaging on the official
libtiff 4.7.2 release. The package version is `4.7.2-0aelira1`. It does not
modify the application image or Python wheels.

`build.sh` starts a disposable preparation container pinned to Ubuntu 26.04 OCI digest
`f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7`:

```sh
bash runtime-security/media/tiff/build.sh ./scratch/tiff-candidate
```

The online preparation obtains Ubuntu's `4.7.0-3ubuntu5` source through
APT's signed Sources index, saves the signed binary and source indexes,
Ubuntu keyring, exact base and final package inventories and all added or
upgraded package archives. It checks the original tar and Debian delta
SHA-256 values, then downloads the official 4.7.2 tarball and detached
signature from OSGeo. It
checks both hashes and verifies the signature against Even Rouault's public
key from his GitLab profile. The expected signing fingerprint is
`B1FA7D81EEB8E66399178B9733EBBFC47B3DD87D`; GitLab marks the official
`v4.7.2` tag verified at commit
`d01a94be176f5f6a87f7ee1c0b32e65416aa2b4d`. The fingerprint is also
listed by Arch's libtiff packaging. A locally imported key has no Web of Trust
certification; this process establishes signature integrity and the stated
publisher linkage, subject to independent release review.

The Debian files retain Ubuntu's build rules, package split, copyright,
changelog and nine historical patches. [ubuntu-patches.md](ubuntu-patches.md)
records why the patch series is inactive on 4.7.2. The only symbols-file
change adds 12 new versioned exports; no prior export is removed. The
`dpkg-buildpackage` runs twice in separate fresh work directories, with the
same frozen builder image, saved inputs, and network disabled. Each build
executes upstream tests through Ubuntu's original `debian/rules` and emits
binary and unsigned source packages, buildinfo, change manifests and
`SHA256SUMS`. The wrapper compares every `.deb`, source `.dsc`, and Debian
source archive byte for byte. The build epoch comes from the authenticated
Ubuntu changelog; Debian source metadata is normalized to that epoch.

The wrapper then runs `test-installed.sh` in a fresh, offline native container
based on the frozen builder. The saved `provenance/frozen-builder-id.txt`,
`provenance/frozen-builder.tar` and its SHA-256, `source-input/` and
`provenance/` inputs should be retained with build output for independent
replay. A native amd64 runner uses the same commands with
its own amd64 builder; arm64 output is not an amd64 substitute.

For a repeat run from retained inputs on the same native architecture, set
`AELIRA_TIFF_BUILDER_IMAGE` to the saved image ID,
`AELIRA_TIFF_SOURCE_INPUT` to the saved `source-input` directory, and
`AELIRA_TIFF_PROVENANCE_INPUT` to the saved `provenance` directory, then
pass a new empty output directory to `build.sh`.

It asserts the actual installed package versions and architecture, SONAME,
codec linkage, and every declared versioned export. It checks all eight codec
registrations by encoding and decoding two pages with pixel comparison
(JPEG tolerance at most two), writes and reads two Deflate-compressed pages
through the public C API, and compares pixels after `tiffcp` LZW and ZIP
copies.
The package still needs exact final-image linkage checks, Python-wheel TIFF
repair, native amd64 testing and release approval. Version alone is not a
vulnerability clearance claim.

`ci-evidence.py` can export a bounded public subset from a completed native
build, or available allowlisted files plus `ci-status.json` after failure. It
requires a clean checkout with an exact Git revision and tree, copies only
named archives, signed indexes, package inventories and relevant logs, and
hashes every exported file. Run it in an always-executed CI step with the
original build exit code; an incomplete export returns nonzero. The required
native CI job runs the exporter controls and full package recipe separately
on amd64 and arm64 and retains this bounded evidence even after failure.
Native CI execution and final-image acceptance remain required. The local replay retention above may
include the frozen builder, but the public CI export excludes its tar and
inspect JSON.
