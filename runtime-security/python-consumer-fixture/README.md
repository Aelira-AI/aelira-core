# Official-wheel Python consumer baseline

This disposable fixture is the **before replacement** state for the existing
Pillow and psycopg2 component consumer wrappers. It installs official PyPI
Pillow 12.3.0 and psycopg2-binary 2.9.13 native wheels with their original
wheel-private library directories. It also installs a selected exact subset of
the public application requirements and exact committed `src` bytes, so the
real PDF and handwritten-math modules load. It is not a production runtime.

The source operator supplies a real repository path, public origin, exact
40-character commit, and new output path to `prepare.py`. The exporter checks
the checkout/ref and exports only committed `requirements.txt`, complete
`src`, and the fixture recipe. It emits the archive SHA-256, source Git blob
and tree objects, and SHA-256 values for `native.py` and `integrity.py`.

For a native CI job, anchor the arguments to its exact clean public checkout:

```sh
commit=$(/usr/bin/git -C "$repo" rev-parse HEAD)
requirements_blob=$(/usr/bin/git -C "$repo" rev-parse "$commit:requirements.txt")
src_tree=$(/usr/bin/git -C "$repo" rev-parse "$commit:src")
python3 "$repo/runtime-security/python-consumer-fixture/prepare.py" \
  --repo "$repo" --expected-ref "$commit" \
  --expected-origin https://github.com/Aelira-AI/aelira-core.git \
  --out "$new_export"
```

Capture the bounded exporter JSON and archive as immutable job inputs. On each
native worker, verify the separately transferred bootstrap file hashes before
executing `native.py`; pass the same commit, blob, tree, origin and archive
digest as explicit arguments, along with a distinct unused scratch path. The
worker emits a one-line pass status and retains `receipt.json`, the frozen
`input/input.sha256` lock, public input files, image ID and private logs. A
successful ARM64 receipt is not evidence for AMD64. Each architecture needs
its own capture, build and receipt; no image is pushed by this recipe.

The native operator transfers **three separate files**: the context tar,
`native.py`, and `integrity.py`. The latter two form the trusted bootstrap
outside the archive. Compare their SHA-256 values with the exporter's values
using `sha256sum -c` **before** starting Python. Invoke `native.py` with an
operator-chosen new scratch, the expected archive hash, public origin, exact
commit, requirements Git blob, and source Git tree. Never execute a script
extracted from an unverified archive. The bootstrap checks the archive digest,
every canonical member and hash, strict JSON/pin schemas, and recomputed Git
objects before extracting any member or executing context code.

On native Ubuntu-compatible ARM64/AMD64, the trusted bootstrap checks host,
daemon and pinned base architecture. It first creates a new private input
bundle: APT authenticates signed Ubuntu indexes using the pinned base's
verified public archive keyring; pip downloads exact public pins and the two
official wheels from the public PyPI index; PyPI's TLS metadata SHA-256 values
are matched to every wheel. The bundle retains signed indexes, all downloaded
DEBs, wheel files, metadata identities and a complete pre-install SHA-256
lock. The fixture image then builds from that bundle with Docker networking
disabled and checks the lock before installing. Each online capture is a new
candidate; the script never pushes or publishes its image.

The final container runs as UID 1000 with read-only storage, no network, no
capabilities and CPU/memory/PID limits. Its verifier checks exact selected
public pins, Pillow and psycopg2 installed RECORD hashes, both original
wheel-private library directories and actual loader linkage, loaded source
module hashes against the committed source inventory, and key system package
identities. `receipt.json` retains bounded provenance. Component replacement
and semantic checks must still run from separately frozen, reviewed component
recipes. A downstream CI job must bind the exact baseline image ID and source
receipt to its separately reviewed Pillow and psycopg2 wrappers, and bind any
accepted TIFF packages by actual per-architecture digest. A passing baseline
import alone does not satisfy those consumer checks. This fixture does not
cover OCR, LaTeX, browser, PostgreSQL/TLS semantics, hosted CI, full final
images, or advisory/all-copy clearance.
