# Native lxml component evidence export

`evidence.py` exports one completed `prepare-native.sh` / `accept-native.sh` /
`run-consumer.sh` execution from a native Linux scratch into a new bounded
directory. Run it only after those original commands have exited. Supply their
actual exit codes and retained log hashes in a private status JSON file:

```json
{
  "schema": 1,
  "commands": {
    "prepare-native": {"exit_code": 0, "log_path": "/absolute/private/prepare.log", "log_sha256": "<64 lowercase hex>"},
    "accept-native": {"exit_code": 0, "log_path": "/absolute/private/accept.log", "log_sha256": "<64 lowercase hex>"},
    "run-consumer": {"exit_code": 0, "log_path": "/absolute/private/consumer.log", "log_sha256": "<64 lowercase hex>"}
  }
}
```

Each log path must be a real, single-link regular file whose bytes match the
supplied hash. Only its hash and zero exit code appear in `bundle.json`. Capture
exit codes in the outer caller as each command completes; a shell pipeline
without `pipefail` can hide a failed command. A log hash does not itself prove
an exit status, so the caller remains responsible for that observation.

```sh
python3 -I evidence.py export \
  --scratch /absolute/private/native-scratch \
  --repo /absolute/public/aelira-core \
  --recipe /absolute/public/aelira-core/runtime-security/lxml \
  --status /absolute/private/original-status.json \
  --out /absolute/new/private-or-public-evidence-directory \
  --scope public-commit \
  --commit REVIEWED_FULL_COMMIT --tree REVIEWED_FULL_TREE \
  --recipe-sha256 REVIEWED_RECIPE_SHA256 \
  --frozen-sha256 REVIEWED_FROZEN_SHA256 \
  --wheel-sha256 REVIEWED_NATIVE_WHEEL_SHA256
```

`public-commit` requires the named public origin, exact HEAD/tree, a clean
checkout, all eighteen recipe files tracked at that commit and byte-equal to
both the live and frozen recipe. It also checks the five selected application
source files against Git objects. `candidate-uncommitted` is only for an
explicitly labeled private review of an uncommitted recipe; it cannot be used
as a clean public CI claim. Neither mode contains a hardcoded commit, recipe,
frozen manifest or wheel hash. Both AMD64 and ARM64 are accepted when the
native architecture, exact `lxml-6.1.3-cp314-cp314-linux_<platform>.whl`
filename, embedded `WHEEL` tag, distribution metadata and frozen inventory
agree.

The exporter first checks the frozen inventory and all original receipts,
replays the three retained offline APT simulations against the authenticated
package receipts, and confirms the two wheel files are byte-equal. It stages
the selected files in a new sibling directory, checks the staged bundle, and
renames it to `--out` only after every check passes. Any failure removes the
staging directory. An existing output is refused. CLI output gives counts and
scope without echoing private paths or receipt contents; refusal output gives
only an exception class.

The public bundle contains the frozen recipe, five Git-bound source files,
released Python source/build tools, fixed PyPI wheels and metadata, signed
Ubuntu Releases and Packages indexes, the cached DEB closure, two actual
source-built wheel files, and selected structured result receipts. Its
`apt-selections.json` stores only the parsed package/version/architecture
tuples from each original APT simulation. The checker verifies their exact
equality with the authenticated DEB set. The export rejects unexpected paths,
symlinks, hard links, special files, duplicate JSON keys, invalid constants,
oversized files and excessive file counts or total bytes.

Export and check enforce the exact selected file union: the original frozen
manifest's approved inputs (with only the verifier image archive omitted), the
manifest itself, fixed structured receipt names, parsed APT selection receipt,
and exactly two source wheels. The manifest bytes must match the externally
selected frozen SHA-256 even though the image archive is excluded. Its entries
must be sorted, unique, and match the fixed recipe/source/Python names plus
the exact PyPI metadata-derived wheels and authenticated APT input classes.
Extra files and directories are refused.

Every exported receipt has a closed field schema and bounded values. Host
checks bind the selected Git commit/tree, recipe and frozen manifest;
preinstall inventory, wheel checks and authenticated package receipts must
agree across the three executions. The consumer identity binds those inputs
to the actual wheel and verifier-image ID. The checker requires the exact
reported native extension set and hashes against wheel ELF members, the
reported installed RECORD count through the wheel's hashed rows plus exactly
three pip-created metadata rows (`INSTALLER`, `REQUESTED`, `direct_url.json`),
while the producer also restricts pip-created unhashed bytecode rows to
`__pycache__` files derived from wheel Python sources. It checks loaded XML
versions and synthetic nonroot XML/Office
outcomes. Both compatibility receipts retain the observed gzip/LZMA
`XMLSyntaxError` and Schematron `SchematronValidateError` outcomes. Unknown
fields, `null`, booleans in integer fields, unrelated scalar text and path
aliases are refused. Native library receipt paths are restricted to canonical
multiarch SONAME locations. The three XML library paths and hashes must match
the complete file paths extracted from their authenticated DEBs; the other
system library hashes remain producer observations from the pinned base image.
Every existing path component is checked before
traversal, including bundle and scratch roots, input directories, private log
paths and output ancestors.

For a separate check on the native Linux host, use the same reviewed identity
arguments with `evidence.py check --bundle ABSOLUTE_BUNDLE --repo
ABSOLUTE_PUBLIC_CHECKOUT`. The check requires `gpgv`, `dpkg-deb` and
`/usr/lib/apt/apt-helper`. It verifies signed Release and Packages bytes,
cached DEB metadata/hashes, pinned Python and PyPI hashes, wheel RECORD and
extension ELF hashes, and the installed XML library hashes against the
authenticated DEB payloads. It does not execute a fresh build or consumer.

The saved verifier image archive, installer environments, raw logs, and
arbitrary host files are excluded. Therefore the exported bundle cannot
independently reconstruct the original verifier image or rerun the complete
native build. It supports bounded component review only. Native execution on
both architectures, full application and LaTeXML/Perl/SVG behavior, final
runtime images, and advisory clearance require separate evidence. The
compatibility receipts retain the observed direct gzip/LZMA
`XMLSyntaxError` and native Schematron `SchematronValidateError` for both the
official and source wheels; they are observations, not broader parity claims.
