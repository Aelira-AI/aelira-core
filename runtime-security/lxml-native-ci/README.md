# Native lxml CI runner

`run.py` orchestrates the reviewed `runtime-security/lxml` recipe in a clean,
committed checkout of `https://github.com/Aelira-AI/aelira-core.git`. It needs a
nonroot native Linux AMD64 or ARM64 host with a matching Docker daemon. Give it
the externally selected full Git commit and tree, a new owned private scratch
path, and a separate new owned public output path:

```sh
python3 -I runtime-security/lxml-native-ci/run.py \
  --repo "$PWD" \
  --expected-ref "$REVIEWED_COMMIT" \
  --expected-tree "$REVIEWED_TREE" \
  --arch amd64 \
  --scratch "$RUNNER_TEMP/lxml-native-private" \
  --dest "$RUNNER_TEMP/lxml-native-public"
```

Run independently on native AMD64 and ARM64. The runner requires the exact
18-file recipe and six runner/exporter files to be committed, checks selected
application source and all required files against Git blobs, and requires an
otherwise clean checkout. It accepts only the fixed public GitHub HTTPS origin,
with or without `.git`. After source and native preflight checks, it records
which trusted form was observed and normalizes the no-suffix form to the fixed
`.git` URL required by the reviewed recipe. It never accepts another remote.

The runner computes the recipe digest from the verified committed bytes, then
invokes `prepare-native.sh`, `accept-native.sh`, and `run-consumer.sh` with the
same reviewed Git and recipe identities. It checks the newly frozen manifest
through `input-gate.py` before passing its hash to acceptance. It requires two
distinct, byte-equal native wheel files with the declared filename and hash
before passing that hash to the consumer. All command output stays in private
scratch. Missing commands, timeouts, signals, original nonzero exits and
dependent steps have distinct status values; a dependent step uses exit 125.

After all three original commands succeed, `evidence.py` verifies a private
record of their actual exits and log hashes, checks source/frozen/wheel/consumer
receipts, and exports a bounded evidence candidate. The runner invokes the
exporter's checker and independently checks the bundle's exact identities,
manifest-to-file hashes, frozen recipe and application source against the
still-clean committed checkout, file hashes, paths, types and size bounds before publishing it as
`DEST/evidence/`. The only public file on failure is `DEST/summary.json`, with
scalar statuses, hashes, native architecture and false clearance flags. The
private scratch retains partial work for diagnosis. The public bundle includes
bounded authenticated upstream inputs and selected typed receipts; verifier
image archives, installer environment archives and raw logs remain private.

This is lxml component evidence for #598 only. It does not establish a full
application runtime, all-copy acceptance, advisory clearance or a release.
The portable controls compare the caller's file/status/bounds contract to the
exporter and recipe, exercise benign process outcomes and source/refusal paths,
and simulate publication. They do not claim a native build:

```sh
python3 -B runtime-security/lxml-native-ci/test-run.py
python3 -B runtime-security/lxml-native-ci/test-evidence.py
python3 -m py_compile runtime-security/lxml-native-ci/run.py runtime-security/lxml-native-ci/evidence.py
ruff check runtime-security/lxml-native-ci
```
