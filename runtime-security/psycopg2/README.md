# Frozen native psycopg2 source wheel

This recipe keeps the distribution and version `psycopg2-binary 2.9.13` but
builds its official source archive against Ubuntu's system libpq. Supported
pip uninstall/install removes the old prebuilt wheel and private library
directory. No library filename or package version is spoofed.

Run on **each native** Linux AMD64 and ARM64 host with Docker and OpenSSL:

```sh
./prepare-native.sh /absolute/new/scratch
./accept-native.sh /absolute/new/scratch
./run-synthetic-db.sh /absolute/new/scratch NATIVE_AELIRA_RUNTIME_IMAGE
```

`prepare-native.sh` checks host, Docker, base-image and APT architecture. It
uses the pinned Ubuntu 26.04 image digest, fetches signed Ubuntu Release and
Packages indexes, downloads the complete native dependency closure as DEBs,
fetches the three SHA-256-pinned Python inputs in `inputs.sha256`, records
every input hash and makes the snapshot read-only. This online capture is a
new candidate whenever signed indexes change. Save and review its immutable
snapshot, including `frozen/frozen.sha256`, before considering it a release
input. The scripts are the frozen acceptance entrypoint.

Preparation copies exactly the nine executable recipe inputs used by the native
build and consumer. It also copies the public Ubuntu archive keyring from the
exact pinned native base image into `frozen/keyring/ubuntu-archive-keyring.gpg`
before computing the frozen manifest. The offline verifier requires those
retained keyring bytes to match the pinned base image and uses the retained
copy with `gpgv` to authenticate signed Release files, Packages indexes and
the downloaded DEB closure. The verifier's bounded JSON receipt includes the
keyring hash. A changed recipe, keyring or signed index requires a new scratch;
do not modify a previously frozen snapshot.

`accept-native.sh` starts two clean native containers from the same pinned
base. Both have no network, receive the frozen snapshot read-only, use
`SOURCE_DATE_EPOCH=1788993887` (the ceiling of the latest timestamp in the
hash-pinned source archive), install only cached authenticated DEBs, verify
Ubuntu signatures and index/DEB hashes, install the pinned pip and setuptools
wheels, and build from the pinned source archive without isolation, downloads
or dependency resolution. It requires byte-equal wheel files and equal signed
package inventories. A failure in either run fails the outer command. The
output includes `authenticated-packages.json`, full installed toolchain and
package lists, logs, and wheel hashes.

Invoke the native scripts as a nonroot host user. `accept-native.sh` and
`run-synthetic-db.sh` verify the complete frozen manifest and compare the
checkout recipe with the prepared recipe before starting containers. Prepare
a new scratch after changing a recipe file. Runtime probes mount the validated
frozen recipe read-only and run as the invoking host UID/GID, so a private
mode-0600 synthetic password remains readable even when that UID is not 1000.
The disposable client sets `HOME=/tmp` so libpq does not attempt to read the
runtime image's inaccessible default home as another UID.
The private `synthetic-db/harness-identity.txt` receipt binds the host/probe
identity, frozen manifest and probe hashes, wheel hash, runtime image ID and
architecture to the replay.

`run-synthetic-db.sh` takes an existing native Aelira runtime image that
contains the prebuilt wheel and SQLAlchemy. It creates separate PostgreSQL 16
data and socket volumes, a synthetic password and fresh test certificates in
private scratch. It installs the frozen wheel through pip into a disposable
runtime container, then checks package identity, all hashed RECORD entries,
the absence of `psycopg2_binary.libs`, every loaded ELF dependency hash and
its owning Ubuntu package. Real PostgreSQL 16 checks cover Unicode/JSONB,
parameters, savepoint recovery, COPY in/out, SQLAlchemy, verify-full TLS and
wrong-host/unrelated-CA certificate refusals. Keys and passwords stay in
private scratch; client mounts include only public certificates. The new
containers are stopped afterward; the scratch and new volumes remain for
review. Do not publish synthetic secrets or keys with CI artifacts.

This is a bounded component candidate for #595 and #588. Both native builds
and installed consumers, independent review, full native final images,
complete advisory reconciliation and the release's unsuppressed gates remain
required before publication. A successful component run does not clear #578.

`ci-evidence.py` copies an explicit, bounded subset from one completed native
scratch into a **new** separate destination. It requires a clean public
`runtime-security/psycopg2` checkout, its exact revision/tree, native
architecture and the original outer build/consumer exit statuses. The caller
must capture the real shell exits; use `125` for a consumer step that was not
run after an earlier failure. If proof is missing, malformed or unsafe, the
copier leaves only `summary.json` with those original statuses and a refused
disposition, and exits nonzero. A complete export also exits nonzero if either
original step failed. Its relative-path manifest includes copied byte hashes;
private credentials, keys, DSNs, raw logs, container inspection and build VENV
archives remain in the private scratch. The copier does not itself run native
builds or replace the signed-input replay in the offline verifier.
