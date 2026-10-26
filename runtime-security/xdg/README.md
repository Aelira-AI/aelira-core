# Native xdg-email backport

This source recipe repairs Ubuntu `xdg-utils` `1.2.1-2ubuntu2` as
`1.2.1-2ubuntu2+aelira2`. It preserves explicit attachments separately from URI
fields, encodes Thunderbird compose delimiters without removing legitimate
recipient punctuation, keeps attachments through the KDE generic fallback,
uses file URIs for encoded filenames, and rejects raw mailto CR/LF before
frontend normalization.

`upstream-5f3f563.patch` is the upstream CVE-2020-27748 attachment-origin repair.
`upstream-700e589.patch` records the upstream subject/body delimiter repair;
`ubuntu-1.2.1-mailto-backport.patch` adapts it to this exact older Ubuntu source.
`ubuntu-frontend-compatibility.patch` preserves the full recipient and explicit
attachment handoff. The helper authenticates all applied patch bytes, the
original Ubuntu quilt series, and complete original/patched source identities.

Original sources are the three files pinned by `inputs.sha256`.
`verify-provenance.py` separately replays the pinned Ubuntu archive keyring,
signed InRelease→Sources/Packages indexes, all original source bytes, and every
archive in the declared frozen toolchain delta. Source construction alone does
not authenticate a downloaded archive or a claimed builder inventory.

`verify-client-provenance.py` separately authenticates the Debian client
dependency archive set against its pinned archive keyring and signed indexes.
Run it using the authenticated Ubuntu verifier toolchain, which provides gpgv,
dpkg-deb and apt-helper, with that Debian evidence mounted at `/provenance`.
Provide bounded writable `/tmp` scratch; the installed client image itself
does not require gpgv. A signed archive replay does not alone prove that a
claimed base/builder inventory or saved image matches the actual Docker image.

Inside the prepared native Ubuntu builder, with canonical read-only inputs at
`/source-input` and this recipe at `/recipe`, use an empty writable `/work`:

```sh
export LC_ALL=C TZ=UTC SOURCE_DATE_EPOCH=1791849600 TMPDIR=/work/tmp
mkdir /work/tmp
python3 /recipe/verify-provenance.py
python3 /recipe/test-source.py --input /source-input --scratch /work/source-controls
python3 /recipe/build-source.py --input /source-input --out /work/packages
cd /work/packages/xdg-utils-1.2.1-aelira
sh /recipe/test-mailto-native.sh baseline .pc/aelira-attachment-origin.patch/scripts/xdg-email.in
sh /recipe/test-mailto-native.sh patched scripts/xdg-email.in
dpkg-buildpackage -b -us -uc -j2
```

Repeat in a second empty offline build directory and compare the DSC, quilt
archive, original tar and architecture-independent DEB bytes. All upstream
autotest cases remain. One backport-specific assertion changes the obsolete
single-quoted recipient argument to its URI-preserving compose representation;
it retains the executable, option, input recipient and encoded separator.

`test-installed-client.py` checks the actual installed `/usr/bin/xdg-email`
hash and package version, native architecture, pinned Debian Thunderbird
`1:140.17.0esr-1~deb13u1`, and the actual packaged compose parser hash. Debian
trixie-security has since moved to a newer Thunderbird, so `prepare-client.sh`
installs that exact build from the fixed trixie-security snapshot
`snapshot.debian.org/archive/debian-security/20261025T035125Z`. Its signed
InRelease is verified against the pinned Debian archive keyring like the live
sources, only its Valid-Until expiry is waived, and APT preferences forbid
installing any other package from it. Run it
inside an isolated native Debian client image with that exact candidate DEB
installed, an empty writable `/work`, Xvfb, and a session bus. Set
`AELIRA_XDG_HOST_ARCH` to the actual native `arm64` or `amd64` architecture.
Use a read-only root, nonroot user with an isolated passwd/group entry,
network-none, dropped capabilities and no-new-privileges. Its desktop-selection
fixtures forward to the real Thunderbird command line and inspect real compose
fields, recipient mailboxes and attachment URLs. They do not send or save mail.

The fifteen controls cover plain and Unicode/quoted text, apostrophe and quoted
local-part recipients, repeated TO/CC/BCC, encoded recipient lists and one-level
percent decoding, multiple explicit files with punctuation, KDE fallback,
URI-origin attachment exclusion, optional TO, raw and terminal newline
refusal, and unsupported MAILER attachment refusal before client invocation.
The unchanged CLI encoder maps body LF to CRLF; URI `%0A` separately retains LF.
Syntax refusal is the existing public CLI exit1, distinct from the isolated
argument-helper fixture's synthetic exit64.

On a native Linux Docker host, run the complete producer as a nonroot user:

```sh
bash runtime-security/xdg/build.sh /absolute/new-output
bun runtime-security/xdg/test-ci-evidence.ts \
  --source /absolute/new-output --repo "$PWD" --arch arm64 \
  --scratch /absolute/new-controls --dest /absolute/new-evidence
bun runtime-security/xdg/verify-export.ts \
  --source /absolute/new-evidence --repo "$PWD" --arch arm64 \
  --revision "$(git rev-parse HEAD)" --tree "$(git rev-parse 'HEAD^{tree}')"
```

Use `amd64` on an x86-64 host. Bun is pinned to1.3.14 in CI. The producer
prepares immutable Ubuntu and Debian builders from pinned base digests,
captures every installed dependency delta and signed archive index, repeats
offline source/binary builds, installs the candidate package and runs all
fifteen real-client controls. It saves the source builder, client builder and
installed-client images for independent inspection. Preparation needs network
access; the package builds, signature replay and client tests run offline.

The exporter reconciles actual Docker inventories and base layers with every
captured archive, reconstructs source/binary payload identities, checks the
physical compose fields independently, and compares complete saved-image
contents against fresh saves of the actual immutable images. The control suite
requires a real successful export before exercising its refusal paths. A
failed preflight exports only a bounded failure receipt. The separate intake
checks all exported bytes and saved config/layer hashes without loading or
executing those images. The native AMD64 and ARM64 jobs are required by the
aggregate `CI complete` gate.

These are source/native component controls. Other supported clients, final
runtime copies and complete unsuppressed advisory inventories remain separate
requirements under #581 and #578. This recipe does not close either release
issue or approve publication.
