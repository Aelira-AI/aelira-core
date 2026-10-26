# Native Python component evidence runner

This runner exercises the pinned official-wheel consumer baseline before the
reviewed Pillow and psycopg2 source-wheel replacements. It is for native,
nonroot Linux `amd64` and `arm64` CI hosts with a matching Docker daemon.
The checkout must be a clean, exact public Git commit of this repository.

The CI job supplies the exact-architecture TIFF artifact ZIP built earlier in
the same workflow run by the native TIFF package job, as a local file. Workflow
artifacts expire, so the job never depends on an artifact from an older run.
The ZIP itself is not reproducible (it carries logs and timestamps), but the
TIFF recipe builds its packages byte for byte reproducibly. Before creating any
output, the runner safely selects the three consumed `build-a` TIFF DEBs and
refuses the ZIP unless each one matches its accepted SHA-256. It checks the
bytes again as it extracts them, then invokes the Pillow recipe's pinned
DEB/control verifier, which holds the same accepted values. It then exports a Git-bound fixture context, checks the
fixture's source/archive controls, pulls the pinned native Ubuntu base,
authenticates the external bootstrap code, and runs the official-wheel
before-replacement fixture. The exact resulting image ID is passed to both
component consumers.

```sh
python3 runtime-security/python-native-ci/run.py \
  --repo "$GITHUB_WORKSPACE" \
  --arch "$NATIVE_ARCH" \
  --expected-ref "$GITHUB_SHA" \
  --scratch "$RUNNER_TEMP/python-native-private" \
  --tiff-zip "$RUNNER_TEMP/accepted-tiff.zip" \
  --dest "$RUNNER_TEMP/python-native-public"
```

`--scratch` and `--dest` must be distinct, new, canonical absolute paths below
owned existing parents. The TIFF ZIP must be an existing canonical regular
file. The private scratch retains original command output, generated source
context, frozen inputs, images and synthetic database diagnostics. Keep it
private. The public destination contains only bounded status/fixture scalars
and successful validated component subsets created by the reviewed evidence
exporters. Do not publish private scratch or complete logs.

`summary.json` records each actual subprocess exit. `125/not_run` means the
command was not attempted because a prerequisite failed. `null/unavailable`
means the process could not be started, timed out, or ended by signal; the
reason enum distinguishes these cases. A nonzero original exit keeps the
runner failed even if a later exporter produces a receipt. The two components
are independent after the shared baseline, so one failure does not suppress
the other component's run. `fixture.json` contains only validated source IDs,
hashes, counts, and the baseline image ID. All receipts are emitted after
successful identity/destination preflight, including on later failure.

`pillow_consumer_diagnostic` reports only a fixed consumer-stage identifier and
the original observed process exit. It requires a small regular JSON receipt,
exact field types and an exit matching both the process result and its exit
file. Missing or invalid diagnostics are `unavailable`; no raw log, exception
message, path or arbitrary string is copied. A successful consumer with missing
diagnostics cannot export complete component evidence. An early skipped
consumer keeps `not_run`, and a reported failure remains failed.

For a valid `installed_verifier` receipt, `check_code` decodes only the reserved
Pillow verifier exits 40–72 documented in the Pillow recipe README. It is a
fixed check-code label, not an exception message or root-cause diagnosis.
All other stages, success, unknown exits and unavailable diagnostics have a
null check code. Explicit process exits are preserved; an explicit exit that
coincides with a reserved code cannot be distinguished from the named-check
exit by this numeric interface. Interpretation must remain bound to the
executed verifier source and the reported stage. No raw logs are parsed.

`pillow_import_diagnostic` is a separate optional summary field. It is
`not_run` unless the original reported consumer stage is `installed_verifier`
with observed exit 40. For that case, the reader reports a secondary
observation only when its own exit file says 0 and a canonical, single-link
JSON receipt of at most 8192 bytes matches the recorded installed image ID.
It accepts exact v2 keys, types, fixed labels and order, finite loader categories,
an approved SONAME set, first-failure sequence, TIFF file/package identities,
and derived counters. The receipt includes the probe's process ID and native
architecture, which must equal the runner's expected architecture; the
candidate image ID is rechecked before probe execution.
Missing, failed, or invalid secondary
evidence is `unavailable`; it cannot alter the original stage, exit, check
code, component disposition, or export decision. The receipt reports a fresh
process's import and independent library load observations plus fixed TIFF
target and package states, not the original exception or its cause. The
original full traceback stays in private logs. The reserved `SystemExit`
numeric collision remains.

This result is scoped to the two Python components. It does not clear
full-runtime, all-copy, advisory, or release security acceptance. It supports the bounded
component work tracked under #594, #595, and #596; the parent gates remain
open until their separate requirements are met.

Local benign controls (without a native build):

```sh
python3 runtime-security/python-native-ci/test-run.py
python3 -m py_compile runtime-security/python-native-ci/run.py runtime-security/python-native-ci/test-run.py
ruff check runtime-security/python-native-ci/run.py runtime-security/python-native-ci/test-run.py
```
