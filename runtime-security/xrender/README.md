# Native Xrender repair component

Backport the upstream `XRenderQueryFormats` subpixel count bound from commit
[`0564bce5446550c1b1681f3d8a933c20ea1455f7`](https://gitlab.freedesktop.org/xorg/lib/libxrender/-/commit/0564bce5446550c1b1681f3d8a933c20ea1455f7),
merged in [upstream MR 19](https://gitlab.freedesktop.org/xorg/lib/libxrender/-/merge_requests/19).
This repairs CVE-2026-88807 in the authenticated Ubuntu source version
`1:0.9.12-1build1`, packaged as `1:0.9.12-1build1+aelira1`.

Run `bash runtime-security/xrender/build.sh NEW_OUTPUT_DIRECTORY` on a native
ARM64 or AMD64 Linux host with a matching Docker daemon. The recipe freezes its
inputs before execution, authenticates the four source archives and toolchain
packages through Ubuntu signed indexes, prepares an immutable builder, then
performs two fresh offline builds as the invoking nonroot user. Cold builds
have no network, a read-only container root and no capabilities.

Controls compile the exact patched source function at O0 and O2 under
ASan/UBSan. Valid screen/subpixel counts, older protocol versions and allocation
failure behavior are retained. Removing only the upstream guard in a separate
baseline demonstrates that the control distinguishes the repair. This bounded
source control uses a mocked protocol reply and no X server or network.

A separate disposable installed-package container compares public symbol
names/types, SONAME and machine architecture against the original Ubuntu
library. It then links the consumer to the installed repaired library, renders
red pixels through an isolated Xvfb server, and reads the pixels back. It does
not contact the host display server.

For a frozen replay, supply `AELIRA_XRENDER_BUILDER_IMAGE`,
`AELIRA_XRENDER_SOURCE_INPUT` and `AELIRA_XRENDER_PROVENANCE_INPUT` from a retained
successful preparation. The immutable image identity and pinned base must match.
`AELIRA_XRENDER_SAVE_BUILDER=1` retains the builder archive (the default).

`ci-evidence.py` authenticates the closed source/index/toolchain file set,
checks the actual pinned-base/builder package delta, reproduces the package and
loaded-library bindings, and verifies saved image contents against a fresh
export of the immutable builder. Only authenticated files and typed receipts
are public; raw build logs remain private. `test-ci-evidence.py` runs 25 controls
against a real completed build, including changed inputs, signatures, packages,
loader hashes, unsafe paths, and saved-image content. Tar header metadata may
vary without changing any image file bytes.

This component requires independent review and execution on both architectures.
Final API/dashboard image copies, actual consumers and unsuppressed advisory
inventories remain separate release acceptance gates.
