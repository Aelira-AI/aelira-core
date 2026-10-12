# Native Python component evidence runner

This runner exercises the pinned official-wheel consumer baseline before the
reviewed Pillow and psycopg2 source-wheel replacements. It is for native,
nonroot Linux `amd64` and `arm64` CI hosts with a matching Docker daemon.
The checkout must be a clean, exact public Git commit of this repository.

The CI job supplies a previously accepted, exact-architecture TIFF artifact
ZIP as a local file. The runner authenticates the complete ZIP SHA-256, safely
selects the three `build-a` TIFF DEBs, and invokes the Pillow recipe's pinned
DEB/control verifier. It then exports a Git-bound fixture context, checks the
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
