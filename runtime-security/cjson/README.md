# cJSON 1.7.19 source repair

This quilt patch targets Ubuntu `cjson` source package `1.7.19-2` and produces
the local revision `1.7.19-2+aelira3`. It repairs JSON Pointer escape decoding
and prevalidates JSON Patch paths, bounds composed tree depth before mutation,
uses iterative copying and deletion for deep trees, avoids repeated nested
subtree evaluation in `cJSON_Compare`, and checks string print additions before
allocation. JSON Patch moves resolve their destination after source removal,
with rollback on invalid paths. JSON Patch TEST compares without changing
object links and keeps duplicate-key pairing. Moves carrying direct or nested
references stage an owned value before an old reference owner can be released.
Both cJSON libraries retain their
original sonames and exports.

Mutating JSON Patch operations on a document containing references stage an
owned JSON value and commit each successful operation. This prevents a path
through a borrowed parent from changing or deleting another owner's children.
Allocation or path failures preserve the original document. TEST keeps the
original reference graph unchanged. The commit preserves root sibling links
and its member name, including constant names. A successful mutation may
replace child pointers; callers must reacquire them and manage any references
held outside the document. The patch array must have an independent lifetime
from the document being mutated. Earlier successful operations remain visible
if a later operation fails, as in the original API.

The exact Ubuntu source inputs are:

| File | SHA-256 |
| --- | --- |
| `cjson_1.7.19-2.dsc` | `4c2a8c0418ca719fc4f7039d2a889b67d820da2a288a9fb2c8e9c863cdf01aff` |
| `cjson_1.7.19.orig.tar.gz` | `7fa616e3046edfa7a28a32d5f9eacfd23f92900fe1f8ccd988c1662f30454562` |
| `cjson_1.7.19-2.debian.tar.xz` | `eb82fa71fc33ae882af35ba0a7f5588584f834683848ebfed9f1e14cde6ee344` |
| `ubuntu-1.7.19-security-backport.patch` | `6e2d15d4e4296875cf41b4ebcadcf8c88e39376fd51b4419dba16592c840ba78` |

The source files in the unpacked Ubuntu package match upstream cJSON v1.7.19:
`cJSON.c` SHA-256 `298581a04a36c0165da4b0aade235c23088cb2faa58651d720ea2f3706ed0b0d`
and `cJSON_Utils.c` SHA-256 `bf3092696763a9a446c39365d7c35f23e5acfdca1bd1da33ef66c1161f9243e9`.

Place the three Ubuntu source files in a canonical absolute directory. With
Python 3 and `dpkg-source` available, prepare a new output directory:

```sh
python3 -B runtime-security/cjson/build-source.py \
  --input /path/to/source-input --out /path/to/new-output
```

The script verifies the sizes and hashes of all three archives, the patch and
the changelog before extraction. It verifies the original and patched C source
hashes, keeps private build output in the new directory, and emits a JSON source
receipt. It refuses changed inputs, symlinks and an existing output directory.
These identity checks are separate from Ubuntu signed-index authentication.
Run the real source build and seven input refusal controls with:

```sh
python3 -B runtime-security/cjson/test-source.py \
  --input /path/to/source-input --scratch /path/to/new-controls
```

Build binary packages in an Ubuntu 26.04
arm64 environment with the source package's declared build dependencies:

```sh
cd /path/to/new-output/cjson-1.7.19-aelira
dpkg-buildpackage -b -us -uc
```

Run the synthetic native controls against the patched source:

```sh
gcc -std=c99 -Wall -Wextra -Wswitch-default -Werror \
  -fsanitize=address,undefined -fno-omit-frame-pointer -g -O2 \
  -I /path/to/isolated-directory/cjson-1.7.19-aelira \
  /path/to/isolated-directory/cjson-1.7.19-aelira/cJSON.c \
  /path/to/isolated-directory/cjson-1.7.19-aelira/cJSON_Utils.c \
  runtime-security/cjson/test-native.c -lm -pthread -o cjson-native-test
./cjson-native-test
```

Native tests cover both case modes, six JSON Patch operations, malformed
escapes before mutation, mixed escaped tokens, array moves, duplicate-key
comparison semantics, references, deep comparison, composed-depth rejection,
allocation failures (including 768 borrowed-parent outcomes), external owners,
root metadata, per-operation commits, and a 512 KiB thread stack. Final binary packages and
the release image require their own exact-image validation and vulnerability
gate review.

On a native Linux host with Docker, run the complete component build:

```sh
bash runtime-security/cjson/build.sh /path/to/new-native-output
```

Preparation runs in pinned Ubuntu and retains the signed source indexes and
toolchain inventory. Two clean nonroot containers build offline from frozen
inputs, run the sanitizer controls and all 22 upstream tests, and compare
source and binary packages. A separate installed-package consumer verifies
both loaded libraries, SONAMEs and public symbol names/types against freshly
compiled original sources. This is a bounded ABI check.

The native AMD64 and ARM64 CI jobs export typed receipts, source/packages,
authenticated indexes, the executed recipe and frozen builder. Raw build logs
remain private. `collect-evidence.py` rejects missing or failed phases and
binds executed recipe hashes to the clean CI checkout. A saved builder can be
replayed with `AELIRA_CJSON_BUILDER_IMAGE`, `AELIRA_CJSON_SOURCE_INPUT` and
`AELIRA_CJSON_PROVENANCE_INPUT`; its retained base-image and builder records
must match. `AELIRA_CJSON_SAVE_BUILDER=0` omits only the builder archive.
