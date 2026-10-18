# CVE-2026-6653 system libxml2 backport for 0.9.13

The API and development images build Debian trixie's `libxml2` source package
`2.12.7+dfsg+really2.9.14-2.1+deb13u3` with the reviewed Canonical entity
amplification patch. The resulting `+aelira1` Debian packages replace the
distribution packages inside the same image build step that first installs
them. Stock Debian `libxml2` remains blocked by the vulnerability inventory.

The pinned source archive has SHA-256
`4fe913dec8b1ab89d13b489b419a8203176ea39e931eaa0d25b17eafb9c279e9`;
the Debian packaging archive has SHA-256
`3b6d265f482d6a8fbe3c056d2006fb3b563b4a838f7258b388ac5f0b29206921`;
and the maintained patch has SHA-256
`6c2b8a7b6fa470efcb1854e5c73bd7e1ae1663d8b111e27ce9232bbc1fe44f54`.
The packaging script applies all 40 Debian patches first, then requires the
backport to apply with zero fuzz and only its observed three-line parser
offset. Debian's own build rules retain its configuration, hardening and
package metadata.

The image gate compares the installed package version with the receipt,
checks the installed library hash and real path loaded by Perl XML::LibXML,
and checks every shipped image layer for a single matching system library
payload. Native checks also require ordinary XML parsing, XSLT, LaTeXML
MathML conversion, FFmpeg DASH XML parsing and ImageMagick's XML-backed MSVG
decoder to work. An 11 MiB plain-text document must parse with
`XML_PARSE_HUGE`; a high-amplification document must be rejected with that
option enabled. The machine-readable CVE-2026-6653 disposition applies only
to the exact rebuilt Debian package when these checks pass.

The patch extends public `xmlEntity` and `xmlParserInput` structures. The
checks above cover the image's native consumers; they are not a general ABI
compatibility claim for external applications. Separate native amd64 and
arm64 image acceptance is required before release. The historical 0.9.12
assessment remains withdrawn. A separate sanitizer differential using the
external CVE fixture has not been completed.
