# Native Pillow 12.3.0 source-wheel candidate

`build.sh` is the native ARM64/AMD64 CI entrypoint. It needs a Linux host with
a matching native Docker daemon, an empty output directory, and the `build-a`
directory from the independently accepted TIFF 4.7.2-0aelira1 component
artifact for that architecture:

```sh
bash runtime-security/media/pillow/build.sh EMPTY_OUTPUT_DIRECTORY \
  ACCEPTED_TIFF_BUILD_A_DIRECTORY
```

The entrypoint verifies the exact three architecture-specific TIFF DEB
digests and package controls. The accepted public source is TIFF CI run
`38163383036` at tree `34650e41ab4cd2309c727f0da26ea382959d31a9`.
Use its native `tiff-native-{arm64,amd64}` artifact `build-a` packages. The
hosted ZIP SHA-256 values are `bda119ff252cc18c16a73b0e6b6a48a775f052b2796db3fdd2cdba7784b818a5`
(ARM64) and `53909f0ff4bacb69e497b63620562f8f9bd28b9070505c42124125123451c58d`
(AMD64). `verify-tiff-input.py` pins all six individual DEB SHA-256 values
and validates their package controls. The earlier experimental ARM archive
hashes are rejected.

Online preparation starts from Ubuntu 26.04 OCI digest
`f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7`.
It retains Ubuntu's signed InRelease/Packages indexes, keyring, every changed
toolchain DEB, installed package manifest and exact PyPI release metadata.
`verify-frozen-inputs.py` replays signatures and package hashes against the
signed indexes, checks DEB controls, verifies the TIFF inputs, and checks the
Python files against the retained PyPI HTTPS metadata. Pillow's source
archive has the additional fixed SHA-256
`3b8182a766685eaa002637e28b4ec8d6b18819a0c71f579bf0dbaa5830297cce`.
PyPI metadata and downloads are authenticated by TLS and digest comparison;
no Pillow publisher signature is asserted.

The prepared native builder is frozen to an OCI image ID and saved as an
archive. The entrypoint first copies all recipe files into a hash-verified
snapshot bound to the checkout revision and tree. It inventories every retained
input and provenance file before execution and checks those exact bytes after
each build. Two fresh containers then
build with no network and read-only recipe, source, package and provenance
mounts. The source is extracted to
the same path in each container. pip 26.2.1, setuptools 84.0.0 and pybind11
3.1.0 come only from the retained wheels. The entrypoint requires identical
wheel bytes and retains each full log, package inventory, source-wheel RECORD
check, ELF architecture/linkage check and structured build proof. The output is a
component candidate until the native and installed checks pass.

To replay an already prepared output with a recipe snapshot, without any package or source fetch,
run `bash runtime-security/media/pillow/replay-frozen.sh PREPARED_OUTPUT
EMPTY_REPLAY_OUTPUT`. It checks the frozen builder archive, copies the recipe
and authenticated inputs into a new output, inventories them and runs two fresh
offline wheels **from those copied objects**,
compares their bytes and verifies every copied recipe hash after execution.
It refuses a changed live recipe. Legacy prepared output without a recipe
snapshot must first be bound to a separately reviewed source manifest.

For a disposable installed consumer check, pass the exact native API baseline
image ID and the accepted build output:

```sh
bash runtime-security/media/pillow/test-installed.sh \
  ACCEPTED_BUILD_OUTPUT sha256:EXACT_NATIVE_BASELINE_IMAGE_ID
```

This installs from the retained APT indexes, downloaded packages and accepted
TIFF DEB with network disabled. It then replaces the Pillow wheel offline and
runs `verify-installed.py` as UID 1000, with a read-only filesystem, no
network and no capabilities. The verifier checks source-wheel RECORD integrity,
compares every non-RECORD wheel payload with the actual installed distribution,
the actual loaded TIFF bytes, absence of the old `pillow.libs` copy, codec and
text/colour feature flags, PNG/JPEG/WebP/JPEG 2000/AVIF/TIFF round trips,
two-page Deflate TIFF pixels, malformed input refusal, and Aelira's PDF and
handwritten-image validation consumers.

For a failed `installed_verifier` stage only, `verify-installed.py` uses these
fixed process exit codes to identify the executing check. They describe where
execution stopped, not why the check failed. Tracebacks remain in the private
installed-consumer log and are never copied into the bounded CI summary.
Unknown codes, signals, and missing or invalid stage receipts have no mapped
check. Explicit process exits keep their original code; a reserved-code collision
cannot be distinguished by this numeric interface. Interpret the code only
with the executing source and valid stage. A mapped code remains a failure
and cannot complete the component.

If the original verifier exits 40, `test-installed.sh` runs a second process in
the same installed candidate image with the original network, user, security,
temporary filesystem, wheel, source, and Python path settings. It confirms the
candidate ID with Docker inspect before passing that ID to the explicit
`--import-diagnostic` mode. The wrapper records the secondary exit separately
and returns the original 40 even if the probe or receipt writer fails. No
probe runs after success or another exit code.

The secondary JSON records only ordered `Image`, `ImageCms`, `ImageFile`,
`ImageFont`, `features` import outcomes, independent `_imaging`, `_imagingcms`,
`_imagingft` import outcomes, and independent `ctypes.CDLL` load outcomes for
the twelve system libraries pinned by `verify-wheel.py`. Fixed exception
categories contain no messages, names from exceptions, paths, or raw logs.
The first import failure stops the ordered sequence; native modules and library
loads remain separate observations. A secondary probe repeats imports in a
new process, so it cannot recover the exact exception or prove the cause of
the original exit 40. A loaded library does not prove which library the
original process linked. The existing reserved `SystemExit` collision limit
still applies. The diagnostic never changes a failed consumer or authorizes
Pillow evidence export.

| Exit | Executing check |
| --- | --- |
| 40 | Pillow import/setup |
| 41, 42 | installed RECORD; wheel payload equality |
| 43–52 | required features: jpg, zlib, libtiff, webp, jpg_2000, freetype2, littlecms2, raqm, avif, xcb, respectively |
| 53, 54, 55 | disabled libimagequant; disabled tkinter; truncated-image flag |
| 56–61 | PNG, JPEG, WEBP, JPEG2000, AVIF, TIFF round trips, respectively |
| 62, 63 | TIFF pages/pixels; loaded TIFF version/hash/maps |
| 64, 65 | font; colour transform |
| 66–69 | malformed PNG, JPEG, TIFF, random-byte refusal, respectively |
| 70, 71 | actual Aelira PDF; handwritten-math consumers |
| 72 | absent wheel-private `pillow.libs` directory |

After the component and installed check, run the public exporter from a clean
checkout matching the executed recipe:

```sh
python3 runtime-security/media/pillow/ci-evidence.py ACCEPTED_BUILD_OUTPUT \
  NEW_PUBLIC_DIRECTORY --repo "$PWD" --revision "$(git rev-parse HEAD)" \
  --tree "$(git rev-parse HEAD^{tree})" --arch arm64
```

It copies only an explicit, bounded proof subset to a new separate directory
and emits `ci-status.json` with relative paths and copied-byte hashes. It
requires recorded zero build and consumer exits, matching recipe and Aelira
consumer bytes, complete structured build/installed proofs, wheel equality and
the exact Git revision and tree. It replays signed input authentication and
wheel RECORD/ELF checks over the copied bytes. It retains signed APT indexes/keyring, DEB closure, PyPI metadata
and payloads, TIFF packages, both wheels, package inventories and structured
semantic results. The full private build output, including the frozen builder
archive and raw logs, remains local. The public subset supports independent
proof checks; a complete offline rebuild also requires the separately retained
private builder archive. A failed or
incomplete run is never published as complete.

This recipe preserves Pillow 12.3.0 and supported media features. Its system
codec versions differ from the previous wheel-private set, so source/advisory
review and broader semantic parity remain release work. A passing component
run does not clear a vulnerability, prove the final API image or authorize
publication.

The accepted isolated ARM64 candidate has wheel SHA-256
`86f9273a8b532f4d2eff08fddf764364164ecee9d81e69204cc0106ac2560fa3`.
This is a native ARM component result; AMD64 and final application image
acceptance remain separate gates.
