# Frozen native lxml 6.1.3 component

This recipe builds the released `lxml-6.1.3.tar.gz` source with its generated C
against authenticated Ubuntu 26.04 `libxml2-16` and `libxslt1.1` packages. The
Python distribution and version stay `lxml 6.1.3`. The source wheel uses
system XML/XSLT shared libraries. It does not rename an older wheel or change
package metadata to hide a bundled library.

Run these commands separately on a native Linux AMD64 host and a native Linux
ARM64 host with Docker. `PUBLIC_GIT_CHECKOUT` is a checkout or worktree of
`https://github.com/Aelira-AI/aelira-core.git` at the externally reviewed
commit. Pass the full reviewed commit and tree SHA-1 values, the reviewed
recipe SHA-256, and then the retained frozen and wheel SHA-256 values. The
recipe digest binds the exact recipe bytes separately from the selected
consumer Git commit. Review the exact recipe bytes before selecting that
digest. Obtain its candidate value with
`python3 -I input-gate.py recipe-id /absolute/public/recipe`.

```sh
./prepare-native.sh /absolute/new/lxml-scratch /absolute/public/aelira-core REVIEWED_COMMIT REVIEWED_TREE REVIEWED_RECIPE_SHA256
./accept-native.sh /absolute/new/lxml-scratch /absolute/public/aelira-core REVIEWED_COMMIT REVIEWED_TREE REVIEWED_RECIPE_SHA256 REVIEWED_FROZEN_SHA256
./run-consumer.sh /absolute/new/lxml-scratch /absolute/public/aelira-core REVIEWED_COMMIT REVIEWED_TREE REVIEWED_RECIPE_SHA256 REVIEWED_FROZEN_SHA256 REVIEWED_WHEEL_SHA256
```

Preparation pins the Ubuntu 26.04 base digest in the scripts. A separate
bootstrap verifier image is built through the base's normal signed online APT
chain, before the candidate frozen cache exists. Its exact image ID, package
inventory and saved image bytes are retained. This image is only a verifier;
it is never a runtime or security-clearance image. Preparation copies the
base's public archive keyring, captures signed Releases and Packages indexes,
downloads the complete build dependency DEBs, and fetches SHA-256-pinned
lxml/pip/setuptools inputs. It also captures exact PyPI wheel bytes and metadata for the
application-pinned DOCX/PPTX/XLSX libraries and binds selected real public
consumer source to the Git commit and tree. The exact recipe bytes enter the
same frozen manifest. A strict inventory rejects missing, extra, duplicate,
noncanonical, symlink, special and hard-linked inputs; APT lock/partial
housekeeping is removed from the new scratch before freezing. The source sdist SHA-256 is
`45222d94ddd511536f3b2f7d9deae3b2339b4ce0f075f1ca25703b07cad9dd21`.

Acceptance first enforces the externally selected manifest, recipe, public
Git commit/tree, every selected source byte and application requirement pin.
It checks host, Docker daemon, base and verifier architecture. Before each
candidate container starts, the separately authenticated verifier image runs
with `--network none`, rechecks the exact inventory, base keyring, signed
Release and Packages files, and every cached DEB hash/size/architecture.
The pinned untouched base then simulates APT resolution offline against those
authenticated indexes, without mounting a cached DEB; the selected package,
version and architecture set must exactly equal the cached DEB set. This
rejects surplus signed packages as well as omitted packages.
Candidate APT installation starts only after that pre-install proof passes.
Two new containers run with `--network none` and install only authenticated
cached DEBs. Each builds lxml
from the released generated C with no Cython regeneration or build isolation.
The entrypoint requires byte-equal wheel files and matching authenticated
package inventories. The consumer starts a separate clean offline container,
requires the externally selected wheel SHA-256, independently authenticates
the frozen cache again, installs and uninstalls the official lxml wheel with pip, installs the
source-built wheel, and runs XML and Office checks as a nonroot user. The
installed verifier checks the distribution, all hashed RECORD entries, wheel
payloads, extension ELF hashes, dynamic linkage, loaded library hashes and
Ubuntu package owners. The compatibility receipt records local gzip/LZMA and
Schematron behavior of both official and source wheels without treating a
different result as equivalent.

This is component work for #598. It does not establish final-image acceptance
for #579 or all-copy/advisory clearance for #578. Native execution is required
independently on both architectures. The fixture covers selected real public
Office consumers and the application's reading-order validator; complete
application, LaTeXML/Perl/SVG, adversarial parser, and final image behavior
need their own acceptance evidence. An OS package version or scanner omission
alone cannot clear an advisory.
