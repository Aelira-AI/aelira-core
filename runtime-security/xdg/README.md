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
`1:140.17.0esr-1~deb13u1`, and the actual packaged compose parser hash. Run it
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

These are source/native component controls. Both native public CI architectures,
full immutable builder and artifact reconciliation, independent acceptance,
exact main-push CI, other supported clients, final runtime copies and complete
unsuppressed advisory inventories remain required under #581 and #578. This
recipe does not close either release issue or approve publication.
