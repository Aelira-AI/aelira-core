# Known unfixed advisories in v0.9.12

The v0.9.12 API image is built on Debian 13. The image scanner reports the
advisories below as HIGH, except CVE-2026-6653, which it reports as CRITICAL.
None has a fix in Debian at publication time, so none can be resolved by
updating packages. Each one is reviewed, listed here, and accepted by the
release gate only while its review is current
(`.trivy-unfixed-dispositions.json`, reviewed 2026-10-15, expires 2027-01-12).
Every finding stays in the published scan reports. This historical table
records the v0.9.12 assessment; the Expat rationale and CVE-2026-6653
disposition that proved wrong are corrected under
[Corrections after publication](#corrections-after-publication).

**Not affected** means the vulnerable code cannot be reached in this image, for
the stated reason. **Affected** means the vulnerable library processes content
users submit. Those findings are shipped knowingly and will be repaired when a
fix is available.

Two GnuTLS advisories, CVE-2026-88647 and CVE-2026-88648, are also unfixed in
Debian. Debian rates them low and upstream GnuTLS does not treat them as
security issues. Aelira's own HTTPS traffic does not use GnuTLS.

## Copies bundled in Python and its packages

The image scanner does not see libraries compiled into Python itself or into
Python wheels. The copies the application uses to parse user documents are
handled directly:

- Python 3.14.8 bundles Expat 2.8.5. The original claim that it includes all
  Expat fixes listed below was incorrect: CVE-2026-77214 and CVE-2026-102633
  were fixed in 2.9.0. See the correction and subsequent-image repair below.
- lxml is built from its source release against libxml2 2.15.4 and libxslt
  1.1.45, which include the fixes for the libxml2 advisories below; the
  published lxml wheels still bundle libxml2 2.14.
- The FFmpeg copies inside the PyAV and OpenCV wheels receive the same local
  file, container and codec restrictions as the system FFmpeg, and the
  transcriber only reads WAV produced by that restricted path.
- Pillow 12.3.0 bundles libtiff 4.7.1, which is assumed affected by
  CVE-2026-36849 (denial of service through a large SamplesPerPixel tag) and
  is reachable through uploaded TIFF images. No Pillow release with a fixed
  libtiff is available yet. API images built after 0.9.12 build Pillow from
  source with libtiff 4.7.2, which contains the fix, and the image build
  checks the libtiff version Pillow reports.
- `pi_heif` 1.4.0 bundles libheif 1.23.0 and libde265 1.1.0, which are behind
  upstream security releases. ocrmypdf 17 does not depend on `pi_heif` and
  loads HEIF support only from a different, uninstalled package, and nothing
  in the application imports it. API images built after 0.9.12 do not install
  `pi_heif`.

## Affected

| Advisory | Packages | Assessment |
| --- | --- | --- |
| CVE-2026-36849 | libtiff6 | libtiff may decode TIFF graphics referenced by submitted LaTeX documents. Denial of service only; Debian will not fix it in this release. |
| CVE-2026-74860 | libxml2 | libxml2 is used by LaTeX conversion (LaTeXML) on documents users submit. No distribution fix exists. |
| CVE-2026-86138 | libxml2 | libxml2 is used by LaTeX conversion (LaTeXML) on documents users submit. No distribution fix exists. |
| CVE-2026-86139 | libxml2 | libxml2 is used by LaTeX conversion (LaTeXML) on documents users submit. No distribution fix exists. |
| CVE-2026-86140 | libxml2 | libxml2 is used by LaTeX conversion (LaTeXML) on documents users submit. No distribution fix exists. |
| CVE-2026-86142 | libxml2 | libxml2 is used by LaTeX conversion (LaTeXML) on documents users submit. No distribution fix exists. |
| CVE-2026-86143 | libxml2 | libxml2 is used by LaTeX conversion (LaTeXML) on documents users submit. No distribution fix exists. |
| CVE-2026-86144 | libxml2 | libxml2 is used by LaTeX conversion (LaTeXML) on documents users submit. No distribution fix exists. |
| CVE-2026-86420 | imagemagick, imagemagick-7-common, imagemagick-7.q16, libimage-magick-perl, libimage-magick-q16-perl, libmagickcore-7.q16-10, libmagickwand-7.q16-10 | ImageMagick may process graphics referenced by submitted LaTeX documents. Denial of service only; no distribution fix exists. |
| CVE-2026-86421 | imagemagick, imagemagick-7-common, imagemagick-7.q16, libimage-magick-perl, libimage-magick-q16-perl, libmagickcore-7.q16-10, libmagickwand-7.q16-10 | ImageMagick may process graphics referenced by submitted LaTeX documents. Denial of service only; no distribution fix exists. |

## Not affected

| Advisory | Packages | Justification | Assessment |
| --- | --- | --- | --- |
| CVE-2025-69720 | libncursesw6, libtinfo6, ncurses-base, ncurses-bin | `vulnerable_code_not_in_execute_path` | ncurses infocmp tool. The application never runs infocmp. |
| CVE-2026-12064 | curl, libcurl3t64-gnutls, libcurl4t64 | `vulnerable_code_not_in_execute_path` | curl SFTP/SCP host verification. The image uses curl only for a local HTTP readiness check; the application never uses SFTP or SCP. |
| CVE-2026-15308 | libpython3.13-minimal, libpython3.13-stdlib, python3.13, python3.13-minimal | `vulnerable_code_not_in_execute_path` | Distribution Python 3.13 is installed as a package dependency only. The application runs its own Python 3.14 and virtual environment, which precede it on PATH. |
| CVE-2026-16554 | libcjson1 | `vulnerable_code_not_in_execute_path` | cJSON's only consumer in the image is librist, used solely by FFmpeg's RIST protocol, which media input restrictions exclude. |
| CVE-2026-16742 | libsystemd0, libudev1 | `vulnerable_code_not_present` | systemd-homed. Only the libsystemd and libudev client libraries are installed; systemd-homed is not present. |
| CVE-2026-19445 | libpython3.13-minimal, libpython3.13-stdlib, python3.13, python3.13-minimal | `vulnerable_code_not_in_execute_path` | Distribution Python 3.13 is installed as a package dependency only. The application runs its own Python 3.14 and virtual environment, which precede it on PATH. |
| CVE-2026-19553 | libpython3.13-minimal, libpython3.13-stdlib, python3.13, python3.13-minimal | `vulnerable_code_not_in_execute_path` | Distribution Python 3.13 is installed as a package dependency only. The application runs its own Python 3.14 and virtual environment, which precede it on PATH. |
| CVE-2026-24882 | dirmngr, gnupg, gnupg-l10n, gpg, gpg-agent, gpgconf, gpgsm | `vulnerable_code_not_present` | GnuPG tpm2daemon. The tpm2daemon binary is not installed and the application never runs GnuPG. |
| CVE-2026-29036 | libcjson1 | `vulnerable_code_not_in_execute_path` | cJSON JSON Pointer handling. cJSON's only consumer is librist, used solely by FFmpeg's RIST protocol, which media input restrictions exclude. |
| CVE-2026-34980 | libcups2t64 | `vulnerable_code_not_present` | CUPS print server shared queues. Only the client library is installed as a browser dependency; cupsd is not present. |
| CVE-2026-37555 | libsndfile1 | `vulnerable_code_not_in_execute_path` | libsndfile IMA ADPCM WAV parsing. libsndfile is loaded only through PulseAudio and SDL output-device libraries linked by FFmpeg; FFmpeg decodes WAV itself and the application never plays audio. |
| CVE-2026-52490 | libtiff6 | `vulnerable_code_not_present` | tiffcrop command-line option parsing. The tiffcrop tool is not installed; the library package does not contain this code. |
| CVE-2026-54369 | libacl1 | `vulnerable_code_not_in_execute_path` | libacl pathname functions in privileged processes. The application runs unprivileged and never calls ACL functions on user-controlled paths. |
| CVE-2026-58049 | ffmpeg, libavcodec61, libavdevice61, libavfilter10, libavformat61, libavutil59, libpostproc58, libswresample5, libswscale8 | `inline_mitigations_already_exist` | RASC decoder. Media inputs are restricted to the supported container and codec allowlists, which exclude RASC. |
| CVE-2026-64830 | ffmpeg, libavcodec61, libavdevice61, libavfilter10, libavformat61, libavutil59, libpostproc58, libswresample5, libswscale8 | `inline_mitigations_already_exist` | VobSub subtitle handling. Media inputs are restricted to the supported container and codec allowlists, which exclude VobSub and DVD subtitles. |
| CVE-2026-64832 | ffmpeg, libavcodec61, libavdevice61, libavfilter10, libavformat61, libavutil59, libpostproc58, libswresample5, libswscale8 | `vulnerable_code_not_in_execute_path` | NVDEC hardware decoding. The image has no NVIDIA hardware or hwaccel configuration and never requests hardware decoding. |
| CVE-2026-64833 | ffmpeg, libavcodec61, libavdevice61, libavfilter10, libavformat61, libavutil59, libpostproc58, libswresample5, libswscale8 | `vulnerable_code_not_in_execute_path` | S/PDIF muxer. The application only writes WAV, MP3, PNG, MP4 and Matroska outputs. |
| CVE-2026-64834 | ffmpeg, libavcodec61, libavdevice61, libavfilter10, libavformat61, libavutil59, libpostproc58, libswresample5, libswscale8 | `inline_mitigations_already_exist` | RTP/ASF stream handling. Media inputs are restricted to local files and the supported container allowlist, which excludes RTP and ASF. |
| CVE-2026-64835 | ffmpeg, libavcodec61, libavdevice61, libavfilter10, libavformat61, libavutil59, libpostproc58, libswresample5, libswscale8 | `inline_mitigations_already_exist` | ADX/AAX audio decoder. Media inputs are restricted to the supported codec allowlist, which excludes ADX. |
| CVE-2026-66036 | ffmpeg, libavcodec61, libavdevice61, libavfilter10, libavformat61, libavutil59, libpostproc58, libswresample5, libswscale8 | `vulnerable_code_not_in_execute_path` | hqdn3d video filter. The application never applies this filter. |
| CVE-2026-66039 | ffmpeg, libavcodec61, libavdevice61, libavfilter10, libavformat61, libavutil59, libpostproc58, libswresample5, libswscale8 | `inline_mitigations_already_exist` | MACE audio decoding. Media inputs are restricted to the supported codec allowlist, which excludes mace3 and mace6 before decoder opening; CAF is also outside the container allowlist. |
| CVE-2026-66040 | ffmpeg, libavcodec61, libavdevice61, libavfilter10, libavformat61, libavutil59, libpostproc58, libswresample5, libswscale8 | `inline_mitigations_already_exist` | PNG decoding in FFmpeg. Media inputs are restricted to the supported codec allowlist, which excludes PNG; FFmpeg only writes PNG frames. |
| CVE-2026-66041 | ffmpeg, libavcodec61, libavdevice61, libavfilter10, libavformat61, libavutil59, libpostproc58, libswresample5, libswscale8 | `inline_mitigations_already_exist` | PGS/SUP subtitle decoder. Media inputs are restricted to the supported container and codec allowlists, which exclude PGS. |
| CVE-2026-66046 | libexpat1 | `vulnerable_code_cannot_be_controlled_by_adversary` | The application Python uses a separate bundled Expat 2.8.5; its advisory status is assessed independently below. The distribution libexpat is used directly only by the unused system Python and is otherwise loaded through fontconfig, which parses image-provided font configuration. |
| CVE-2026-6653 | libxml2 | **Withdrawn** (formerly `vulnerable_code_cannot_be_controlled_by_adversary`) | The v0.9.12 assessment claimed submitted content could not reach distribution libxml2 as a parsed document prolog, in part because ImageMagick supposedly permitted only GIF, JPEG, PNG and WebP. That format-policy claim was disproved in native candidate images. The exception was removed; see the [current open assessment](#cve-2026-6653-remains-open). |
| CVE-2026-67215 | libcjson1 | `vulnerable_code_not_in_execute_path` | cJSON JSON Patch handling. cJSON's only consumer is librist, used solely by FFmpeg's RIST protocol, which media input restrictions exclude. |
| CVE-2026-67216 | libcjson1 | `vulnerable_code_not_in_execute_path` | cJSON object comparison. cJSON's only consumer is librist, used solely by FFmpeg's RIST protocol, which media input restrictions exclude. |
| CVE-2026-70628 | ffmpeg, libavcodec61, libavdevice61, libavfilter10, libavformat61, libavutil59, libpostproc58, libswresample5, libswscale8 | `inline_mitigations_already_exist` | WTV demuxer and DVB subtitle parsing. Media inputs are restricted to the supported container and codec allowlists, which exclude both. |
| CVE-2026-70632 | ffmpeg, libavcodec61, libavdevice61, libavfilter10, libavformat61, libavutil59, libpostproc58, libswresample5, libswscale8 | `inline_mitigations_already_exist` | CFHD decoder. Media inputs are restricted to the supported codec allowlist, which excludes CFHD. |
| CVE-2026-7210 | libpython3.13-minimal, libpython3.13-stdlib, python3.13, python3.13-minimal | `vulnerable_code_not_in_execute_path` | Distribution Python 3.13 is installed as a package dependency only. The application runs its own Python 3.14 and virtual environment, which precede it on PATH. |
| CVE-2026-73066 | libtesseract5, tesseract-ocr | `vulnerable_code_cannot_be_controlled_by_adversary` | Tesseract model (.traineddata) parsing. OCR uses only the distribution-installed English model; users cannot supply model files. |
| CVE-2026-75142 | ffmpeg, libavcodec61, libavdevice61, libavfilter10, libavformat61, libavutil59, libpostproc58, libswresample5, libswscale8 | `vulnerable_code_not_in_execute_path` | MPEG-PS muxer. The application never writes MPEG program streams. |
| CVE-2026-75143 | ffmpeg, libavcodec61, libavdevice61, libavfilter10, libavformat61, libavutil59, libpostproc58, libswresample5, libswscale8 | `inline_mitigations_already_exist` | RIST protocol reader. Media inputs are restricted to the local file protocol. |
| CVE-2026-75144 | ffmpeg, libavcodec61, libavdevice61, libavfilter10, libavformat61, libavutil59, libpostproc58, libswresample5, libswscale8 | `inline_mitigations_already_exist` | Dirac decoder. Media inputs are restricted to the supported codec allowlist, which excludes Dirac. |
| CVE-2026-75146 | ffmpeg, libavcodec61, libavdevice61, libavfilter10, libavformat61, libavutil59, libpostproc58, libswresample5, libswscale8 | `inline_mitigations_already_exist` | DASH demuxer. Media inputs are restricted to local files and the supported container allowlist, which excludes DASH. |
| CVE-2026-76642 | bsdutils, libblkid1, liblastlog2-2, libmount1, libsmartcols1, libuuid1, login, mount, util-linux | `vulnerable_code_not_in_execute_path` | util-linux mount helpers. The application runs as an unprivileged user and never mounts filesystems. |
| CVE-2026-76956 | libexpat1 | `vulnerable_code_cannot_be_controlled_by_adversary` | The application Python uses a separate bundled Expat 2.8.5; its advisory status is assessed independently below. The distribution libexpat is used directly only by the unused system Python and is otherwise loaded through fontconfig, which parses image-provided font configuration. |
| CVE-2026-76957 | libexpat1 | `vulnerable_code_cannot_be_controlled_by_adversary` | The application Python uses a separate bundled Expat 2.8.5; its advisory status is assessed independently below. The distribution libexpat is used directly only by the unused system Python and is otherwise loaded through fontconfig, which parses image-provided font configuration. |
| CVE-2026-77214 | libexpat1 | `vulnerable_code_cannot_be_controlled_by_adversary` | The application Python uses a separate bundled Expat 2.8.5; its advisory status is assessed independently below. The distribution libexpat is used directly only by the unused system Python and is otherwise loaded through fontconfig, which parses image-provided font configuration. |
| CVE-2026-78408 | bsdutils, libblkid1, liblastlog2-2, libmount1, libsmartcols1, libuuid1, login, mount, util-linux | `vulnerable_code_not_in_execute_path` | util-linux nsenter. The application never runs nsenter and has no privileges to join namespaces. |
| CVE-2026-78409 | bsdutils, libblkid1, liblastlog2-2, libmount1, libsmartcols1, libuuid1, login, mount, util-linux | `vulnerable_code_not_in_execute_path` | util-linux mount helpers. The application runs as an unprivileged user and never mounts filesystems. |
| CVE-2026-78410 | bsdutils, libblkid1, liblastlog2-2, libmount1, libsmartcols1, libuuid1, login, mount, util-linux | `vulnerable_code_not_in_execute_path` | util-linux bind-mount handling. The application runs as an unprivileged user and never mounts filesystems. |
| CVE-2026-82049 | libpython3.13-minimal, libpython3.13-stdlib, python3.13, python3.13-minimal | `vulnerable_code_not_in_execute_path` | Distribution Python 3.13 is installed as a package dependency only. The application runs its own Python 3.14 and virtual environment, which precede it on PATH. |
| CVE-2026-8458 | curl, libcurl3t64-gnutls, libcurl4t64 | `vulnerable_code_not_in_execute_path` | libcurl Negotiate-authenticated connection reuse. The image uses curl only for a single unauthenticated local readiness request; application HTTP uses Python clients. |
| CVE-2026-87933 | libcjson1 | `vulnerable_code_not_in_execute_path` | cJSON merge-patch handling. cJSON's only consumer is librist, used solely by FFmpeg's RIST protocol, which media input restrictions exclude. |
| CVE-2026-88047 | libtesseract5, tesseract-ocr | `vulnerable_code_cannot_be_controlled_by_adversary` | Tesseract model (.traineddata) parsing. OCR uses only the distribution-installed English model; users cannot supply model files. |
| CVE-2026-88048 | libtesseract5, tesseract-ocr | `vulnerable_code_cannot_be_controlled_by_adversary` | Tesseract LSTM layer deserialization from model files. OCR uses only the distribution-installed English model; users cannot supply model files. |
| CVE-2026-88051 | libtesseract5, tesseract-ocr | `vulnerable_code_cannot_be_controlled_by_adversary` | Tesseract model (.traineddata) parsing. OCR uses only the distribution-installed English model; users cannot supply model files. |
| CVE-2026-88052 | libtesseract5, tesseract-ocr | `vulnerable_code_cannot_be_controlled_by_adversary` | Tesseract unicharset loading from model files. OCR uses only the distribution-installed English model; users cannot supply model files. |
| CVE-2026-88053 | libtesseract5, tesseract-ocr | `vulnerable_code_cannot_be_controlled_by_adversary` | Tesseract int-template loading from model files. OCR uses only the distribution-installed English model; users cannot supply model files. |
| CVE-2026-88806 | libx11-6, libx11-data, libx11-xcb1 | `vulnerable_code_not_in_execute_path` | libX11 responses from a malicious X server. The container has no X server and the browser runs headless without an X connection. |
| CVE-2026-88807 | libxrender1 | `vulnerable_code_not_in_execute_path` | libXrender responses from a malicious X server. The container has no X server and the browser runs headless without an X connection. |
| CVE-2026-93990 | libexpat1 | `vulnerable_code_cannot_be_controlled_by_adversary` | The application Python uses a separate bundled Expat 2.8.5; its advisory status is assessed independently below. The distribution libexpat is used directly only by the unused system Python and is otherwise loaded through fontconfig, which parses image-provided font configuration. |
| CVE-2026-9538 | libperl5.40, perl, perl-base, perl-modules-5.40 | `vulnerable_code_not_in_execute_path` | Perl Archive::Tar. LaTeX conversion processes LaTeX sources; nothing in the application reads tar archives with Archive::Tar. |

## Corrections after publication

### Python's bundled Expat does not contain every Expat fix

This document and the libexpat1 dispositions it listed said that the Expat
bundled in Python 3.14.8 "includes the fixes". That was wrong for
CVE-2026-77214. Python 3.14.8 bundles Expat 2.8.5 (`Modules/expat/expat.h` at
the `v3.14.8` tag defines version 2.8.5). The upstream changelog for Expat 2.9.0
lists two security fixes that 2.8.5 does not have:

- CVE-2026-77214: `XML_ParseBuffer` did not check its `len` argument against
  the available buffer capacity.
- CVE-2026-102633: an integer overflow in `expat_realloc` on 32-bit platforms.
  The published images are 64-bit (amd64 and arm64).

The fixes for CVE-2026-66046, CVE-2026-76956, CVE-2026-76957 and
CVE-2026-93990 are in 2.8.4 and 2.8.5. The historical distribution-package
assessment in the table is separate from the Python copy. Merged
[#671](https://github.com/Aelira-AI/aelira-core/pull/671) rebuilds both CPython
Expat extensions against Debian Expat 2.9.0 for subsequent images and checks
that the bundled extensions do not survive in shipped layers. Final candidate
acceptance remains open in [#589](https://github.com/Aelira-AI/aelira-core/issues/589).

Assessment of reachability for CVE-2026-77214, from reading `pyexpat.c` at
`v3.14.8`: that file calls `XML_ParseBuffer` in one place, `xmlparser.ParseFile`.
Immediately before the call it obtains a buffer with
`XML_GetBuffer(parser, BUF_SIZE)` (`BUF_SIZE` is 2048), reads at most `BUF_SIZE`
bytes into it (`readinst` rejects a longer result), and passes the number of
bytes read as `len`. The length therefore cannot exceed the capacity that was
just obtained, so the over-read looks unreachable through pyexpat. This is an
assessment from reading that one file, not a proof. It does not cover any other
code in the image that loads Expat.

### Debian libexpat1 is fixed; the dispositions are removed

Debian published `libexpat1 2.9.0-1~deb13u1` for trixie (DSA-6566-1, amd64 and
arm64). It covers CVE-2026-66046, CVE-2026-76956, CVE-2026-76957,
CVE-2026-77214, CVE-2026-93990 and CVE-2026-102633. The five dispositions for
`libexpat1 2.8.3-1~deb13u1` (CVE-2026-66046, CVE-2026-76956, CVE-2026-76957,
CVE-2026-77214 and CVE-2026-93990) are removed from
`.trivy-unfixed-dispositions.json` for subsequent images; the historical table
above remains intact. Current runtime Dockerfile images pin that fixed package,
and the Docker CI job fails if the built image has a `libexpat1` older than
2.9.0. The v0.9.12 images retain `libexpat1 2.8.3-1~deb13u1` and the original
disposition assessment still describes that release.

### Embedded libraries that were not disclosed

The image scanner does not see the libraries bundled inside Python wheels.
Two wheels installed in v0.9.12 bundled libraries that were not listed above.
The `pi_heif` copies were a gap in that release. From the next release,
`pi_heif` is absent from `requirements.txt`, the final-image check rejects it,
and a built-image smoke check exercises PDF OCR on both architectures. The
`psycopg2-binary` copies shipped in v0.9.12 and are replaced from the next
release.

| Wheel | Bundled library | Behind upstream | Reachability |
| --- | --- | --- | --- |
| `pi_heif` 1.4.0 (v0.9.12 wheel) | libheif 1.23.0 | libheif 1.23.2 through 1.23.6 are security releases; 1.23.2 and 1.23.3 each fix an issue upstream rates critical. See [libheif releases](https://github.com/strukturag/libheif/releases). | Reachability in v0.9.12 was not verified. No code under `src/` imports `pi_heif`. ocrmypdf 17.13.0 imports `pillow_heif`, a different distribution that was not installed, so on a reading of its source it does not load `pi_heif`. That path was not exercised in a v0.9.12 image. The wheel is absent from images built after v0.9.12. |
| `pi_heif` 1.4.0 (v0.9.12 wheel) | libde265 1.1.0 | libde265 1.1.2 is a security release (use-after-free and double free in multi-threaded decoding); 1.1.3 is current. See [libde265 releases](https://github.com/strukturag/libde265/releases). | Same v0.9.12 reachability limit and next-release removal as above. |
| `psycopg2-binary` 2.9.13 (v0.9.12 wheel) | OpenSSL 3.5.8 on amd64 and arm64; the aarch64 wheel also bundled OpenSSL 1.1.1k | OpenSSL 3.5.9 fixes eight advisories, all rated Low by OpenSSL (see [OpenSSL vulnerabilities](https://openssl-library.org/news/vulnerabilities/)). OpenSSL 1.1.1k is no longer supported upstream. | v0.9.12 shipped these bundled copies. The driver connects only to the deployment's own PostgreSQL service and does not process content that users supply, and the bundled versions were not assessed against every current advisory. From the next release the driver is built from source and linked to Debian's libpq, libssl and libcrypto, and the wheel's bundled library directory is absent ([#645](https://github.com/Aelira-AI/aelira-core/issues/645), refs [#588](https://github.com/Aelira-AI/aelira-core/issues/588)). |

The bundled versions were read from the library files inside the published
wheels for both architectures. The historical `pi_heif` gap and its removal are tracked in
[#583](https://github.com/Aelira-AI/aelira-core/issues/583).

### New scanner findings in the subsequent 0.9.13 candidate

The following review-bound `not_affected` dispositions target ImageMagick
`8:7.1.1.43+dfsg1-1+deb13u12` and `libcups2t64 2.4.10-3+deb13u2` on
native amd64 and arm64 candidates through 2027-01-16. The scanner findings
remain visible.

| Advisory | Affected scanner packages | Basis to verify in each candidate image |
| --- | --- | --- |
| CVE-2026-93589 | `imagemagick`, `imagemagick-7-common`, `imagemagick-7.q16`, `libimage-magick-perl`, `libimage-magick-q16-perl`, `libmagickcore-7.q16-10`, `libmagickwand-7.q16-10` | ImageMagick 7.1.1-43 compiles the FLIF reader and writer only with `MAGICKCORE_FLIF_DELEGATE` and assigns the decoder and encoder handlers only under that guard. The format registration itself remains unconditional. The candidate image must have no FLIF encoder. |
| CVE-2026-93590 | The same seven ImageMagick packages | ImageMagick 7.1.1-43 compiles the UHDR reader and writer only with `MAGICKCORE_UHDR_DELEGATE` and assigns the decoder and encoder handlers only under that guard. The format registration itself remains unconditional. The candidate image must have no UHDR encoder. |
| CVE-2026-55453 | `libcups2t64` | The vulnerable IPP backend is built separately from the CUPS client library. The candidate image must have the client library without the IPP backend executable. |

The [ImageMagick FLIF source](https://raw.githubusercontent.com/ImageMagick/ImageMagick/7.1.1-43/coders/flif.c),
[UHDR source](https://raw.githubusercontent.com/ImageMagick/ImageMagick/7.1.1-43/coders/uhdr.c),
[CUPS backend build](https://raw.githubusercontent.com/OpenPrinting/cups/v2.4.10/backend/Makefile),
and [CUPS advisory](https://github.com/OpenPrinting/cups/security/advisories/GHSA-7hqf-mfhx-7r3v)
identify the affected components. Debian tracks the ImageMagick findings as
[CVE-2026-93589](https://security-tracker.debian.org/tracker/CVE-2026-93589)
and [CVE-2026-93590](https://security-tracker.debian.org/tracker/CVE-2026-93590).
The native-image CI check is configured to assert the exact package versions,
missing capabilities and backend paths, successful PNG/JPEG controls, and
absence of the component names throughout the shipped layers. A changed
package version or newly installed component requires a fresh assessment.
Acceptance requires matching candidate-image evidence and a passing CI run for
the reviewed source revision on both architectures.

### Medium-severity advisories reviewed in the 0.9.13 candidate

The release gate covers HIGH and CRITICAL findings, so the scanner's MEDIUM
findings are not listed in `.trivy-unfixed-dispositions.json`. Three of them
concern the zlib and Cyrus SASL libraries that document conversion depends on,
and were reviewed against the final candidate images on both architectures.
None has a fix in Debian. The scanner findings remain visible.

| Advisory | Scanner packages | Assessment |
| --- | --- | --- |
| [CVE-2026-107161](https://security-tracker.debian.org/tracker/CVE-2026-107161) | `libsasl2-2`, `libsasl2-modules-db` `2.1.28+dfsg1-9` | Not affected, `vulnerable_code_not_present`. The flaw is in the DIGEST-MD5 plugin (`plugins/digestmd5.c`). The images ship only the core library and the `sasldb` plugin; no DIGEST-MD5 plugin is present in any image layer. |
| [CVE-2026-85091](https://security-tracker.debian.org/tracker/CVE-2026-85091) | `zlib1g` `1:1.3.dfsg+really1.3.1-1+b1` | Not affected, `vulnerable_code_not_present`. The tracker's upstream range begins at 1.3.1.2, where the non-blocking `gzwrite` change was introduced. Debian's `gzwrite.c` is byte-identical to upstream 1.3.1 and lacks that code. This is a source comparison; the compiled library was not compared. |
| [CVE-2026-27171](https://security-tracker.debian.org/tracker/CVE-2026-27171) | `zlib1g` `1:1.3.dfsg+really1.3.1-1+b1` | Not affected, `vulnerable_code_not_in_execute_path`. The flaw requires a negative length passed to `crc32_combine` or `crc32_combine_gen`, and is fixed in zlib 1.3.2. Debian's `crc32.c` matches vulnerable upstream 1.3.1. In each image, the only program importing these functions from the shared library is Perl's `Compress::Raw::Zlib`; no installed Perl code calls them, and LaTeXML's archive handling uses `Archive::Zip`, which does not. |

The images also contain zlib copies the scanner does not see. lxml and
PyMuPDF statically include zlib 1.3.2, which contains the CVE-2026-27171 fix.
Both Node.js runtimes carry two Chromium zlib builds, `1.3.2.1-motley` and the
older `1.3.0.1-motley`, which predates the fix, and export the combine
functions for native add-ons. No native add-on in the images imports them, and
the Node.js `zlib` API offers no combine function to JavaScript. Python's
`zlib` module links the shared library without importing the combine
functions. A changed zlib or SASL package, or new code calling these
functions, requires a fresh assessment.

### ImageMagick advisories published after the 0.9.13 candidate review

Six further ImageMagick advisories affect the reviewed
`8:7.1.1.43+dfsg1-1+deb13u12` packages on both architectures. Upstream fixed
them in 7.1.2-30 and 7.1.2-31; Debian has no fixed package for this release.
The dispositions were reviewed on 2026-10-22 and expire on 2027-01-20. The
scanner findings remain visible.

| Advisory | Scanner packages | Status | Assessment |
| --- | --- | --- | --- |
| [CVE-2026-106576](https://github.com/ImageMagick/ImageMagick/security/advisories/GHSA-v45j-x8p4-3mh4) | `imagemagick`, `imagemagick-7-common`, `imagemagick-7.q16`, `libimage-magick-perl`, `libimage-magick-q16-perl`, `libmagickcore-7.q16-10`, `libmagickwand-7.q16-10` | Affected | A crafted XMP profile makes the rational conversion in `MagickCore/profile.c` run for a long time when an image carrying it is written. LaTeXML reads and rewrites submitted PNG and JPEG graphics when a document transforms them, for example by rotation. Denial of service only, bounded by the 60-second post-processing timeout, which kills the process. The image's ImageMagick policy does not mitigate it (see below). |
| [CVE-2026-106573](https://github.com/ImageMagick/ImageMagick/security/advisories/GHSA-qr53-hc3p-fc62) | The same seven ImageMagick packages | Not affected, `vulnerable_code_cannot_be_controlled_by_adversary` | Missing macro expansion limit in the MVG decoder. See the MVG basis below; the image policy also refuses the MVG coder. |
| [CVE-2026-106578](https://github.com/ImageMagick/ImageMagick/security/advisories/GHSA-6xf5-c3jx-rp39) | The same seven ImageMagick packages | Not affected, `vulnerable_code_cannot_be_controlled_by_adversary` | Invalid free in the MVG decoder. See the MVG basis below; the image policy also refuses the MVG coder. |
| [CVE-2026-106579](https://github.com/ImageMagick/ImageMagick/security/advisories/GHSA-vcjj-32hg-qpx5) | The same seven ImageMagick packages | Not affected, `vulnerable_code_cannot_be_controlled_by_adversary` | Policies written in the `coder` domain can be bypassed, because ImageMagick checks the requested format name case-sensitively instead of the name of the coder it resolves. The image policy denies every coder with a `*` rule and then allows only upper-case names, so a name that differs only in case matches just the deny-all rule and is refused; it cannot reach a coder the policy does not allow (see below). |
| [CVE-2026-106580](https://github.com/ImageMagick/ImageMagick/security/advisories/GHSA-r868-pmwh-fv2c) | The same seven ImageMagick packages | Not affected, `vulnerable_code_cannot_be_controlled_by_adversary` | Missing policy check when the CUT reader loads its palette file. CUT declares no magic signature, so only a `cut:` prefix or a `.cut` name selects it, and submitted content can supply neither. The image policy also refuses the CUT coder. |
| [CVE-2026-107209](https://github.com/ImageMagick/ImageMagick/security/advisories/GHSA-f2r2-qw7g-3c8x) | The same seven ImageMagick packages | Not affected, `vulnerable_code_not_present` | Use-after-free in the RSVG path of `coders/svg.c`. Debian ships the SVG coder module only in `libmagickcore-7.q16-10-extra`, which is not installed; `libmagickcore-7.q16-10` contains no `svg` module, so no SVG, MSVG or RSVG format is registered. |

**MVG, CUT and URL basis.** In ImageMagick 7.1.1-43, file sniffing uses the
static signatures declared in each coder's header. `coders/mvg.h`,
`coders/cut.h` and `coders/url.h` declare none, and the per-coder `IsMVG`
handler is used only by `GetImageMagick`, which nothing in ImageMagick calls.
These coders are therefore chosen only by an explicit format prefix or file
extension. No application code calls ImageMagick; it runs only inside
LaTeXML 0.8.8. `LaTeXML::Post::Graphics` resolves each graphic to an existing
local path before reading it, and LaTeXML keeps `\openout` and `filecontents`
output in memory rather than writing files, so a document cannot create a file
for post-processing to read. Uploaded LaTeX projects may contain only `.tex`,
`.bib`, `.sty`, `.cls`, `.bst`, `.png`, `.jpg`, `.jpeg`, `.pdf`, `.eps`, `.svg`
and `.txt` members, and member names cannot contain a colon
(`src/education/latex_project.py`). A single uploaded `.tex` file is converted
alone. A document can still name an absolute path that Landlock lets the tool
read, but such a file is part of the image, not content an attacker supplies.

The native-image CI check now also asserts that no SVG, MSVG or RSVG format or
`svg` coder module is registered, that no ImageMagick `svg` coder module occurs
in any shipped layer, and that a file named `.png` containing MVG drawing
commands is not decoded by `magick` or Perl `Image::Magick`, while the same
file is decoded with an explicit `mvg:` prefix once a test-only policy allows
MVG. The MVG, CUT and URL basis rests on reading the ImageMagick 7.1.1-43 and
LaTeXML 0.8.8 sources, not on a crafted-input test of LaTeXML itself. A changed
ImageMagick or LaTeXML package, a new ImageMagick caller, or a wider project
file allowlist requires a fresh assessment.

**ImageMagick policy.** From [#749](https://github.com/Aelira-AI/aelira-core/issues/749)
the API image replaces Debian's open `policy.xml` and `delegates.xml` with
[`docker/imagemagick/policy.xml`](../../docker/imagemagick/policy.xml) and
[`docker/imagemagick/delegates.xml`](../../docker/imagemagick/delegates.xml).
LaTeXML 0.8.8 reads graphics through `LaTeXML::Util::Image`: it reads PNG,
JPEG, EPS and PDF files, renders EPS and PDF through Ghostscript and reads the
result back as PNG, or as PAM for a CMYK document, and writes PNG and JPEG
files. The policy denies every coder and then allows reading and writing PNG,
JPEG and JPG and reading EPS, PDF and PAM. It allows only the `gs` delegate
program, denies all filters, `@` file lists and URL reads, and lowers the
width and height limits to 16000 pixels, memory to 256 MiB, map to 512 MiB,
area to 64 megapixels, disk to 1 GiB, image lists to 32 and threads to 2.
ImageMagick 7.1.1-43 applies the policy's 50-second time value only as a cap,
so the LaTeX tool environment also sets `MAGICK_TIME_LIMIT=50`. The delegate
list keeps only the two Ghostscript rendering delegates, both run with
`-dSAFER -dBATCH -dNOPAUSE` and a fixed `pngalpha` or `pamcmyk32` device.
Ghostscript 10.05.1 (`10.05.1~dfsg-1+deb13u3`) also applies `-dSAFER` by
default; Debian's open Ghostscript advisories for this release are fixed in
that version or are marked unimportant for components the image does not use.

Before this change, a file named `.png` that held a PostScript program was
identified by its content and rendered through Ghostscript, including inside
LaTeXML, and the MSL, MVG, CUT, TEXT, TXT and PS coders read files when named
with a prefix or extension. The native-image CI check now runs real LaTeXML
conversions of EPS, PDF, PNG and JPEG graphics, including rotated and CMYK
ones, and records every Ghostscript command line. It asserts that those reads
are refused by the policy, that LaTeXML refuses the PostScript `.png` file
before Ghostscript runs, that images wider or taller than the limit are
refused, and that the shipped files equal the reviewed ones. Each refusal has
a control that passes when a test-only policy file allows the coder.

The policy does not mitigate CVE-2026-106576. ImageMagick has no policy rule
for embedded profiles, so denying the `XMP` coder does not stop the
conversion, and its time limit is checked only when pixels are accessed, not
inside the loop: a crafted PNG converted through LaTeXML with a 10-second
ImageMagick time limit ran until it was killed. The upstream fix is ImageMagick
commit `f491576297141ff685dc4460d811986f5b6b6cec` in 7.1.2-31, which replaces
the search in `GetXmpNumeratorAndDenominator` with a bounded continued
fraction; it applies without changes to the Debian 7.1.1-43 source. ImageMagick reads
policy files from `$HOME/.config/ImageMagick` after the system file, and a rule
there can loosen it. LaTeX tools run with their conversion directory as
`$HOME`; TeX may not write hidden names there and LaTeXML keeps written files
in memory, so a document cannot create that file.

### CVE-2026-6653 remains open

The historical table's `not_affected` claim for distribution `libxml2` is
withdrawn. Its claim that the ImageMagick policy permits only GIF, JPEG, PNG
and WebP does not hold in either native candidate image. The old
machine-readable exception was removed, and stock Debian `libxml2` remains
blocked by the CRITICAL Trivy inventory gate. The subsequent `+aelira1`
backport has a [conditional repair disposition](libxml2-6653-backport-0.9.13.md):
acceptance requires the rebuilt package, native consumer checks and shipped
layer checks to pass for the exact image. The other `libxml2` advisories remain
affected and disclosed; the three component-absence dispositions above do not
resolve them.

### Debian FFmpeg is removed; 22 dispositions are retired

From 0.9.15 the API image no longer installs Debian's `ffmpeg` package. The
`ffmpeg` and `ffprobe` programs come from the image's own build of the
hash-verified FFmpeg 9.0.2 release under `/opt/ffmpeg9`, the build PyAV and
OpenCV already used. It compiles in only the `file` and `pipe` protocols, so no
network protocol (RIST, HTTP, RTP and the rest) exists to reach even without
the application's input whitelist. Removing the package also removes Debian's
FFmpeg 7.1.5 libraries, `librist4` with its `libcjson1` dependency, and
`libsndfile1`, which only PulseAudio and SDL loaded for FFmpeg. The final-image
package gate fails if any of them returns.

These dispositions are removed from `.trivy-unfixed-dispositions.json` for
subsequent images, and the historical table above remains intact:

- Debian FFmpeg (`ffmpeg`, `libavcodec61`, `libavdevice61`, `libavfilter10`,
  `libavformat61`, `libavutil59`, `libpostproc58`, `libswresample5`,
  `libswscale8`): CVE-2026-58049, CVE-2026-64830, CVE-2026-64832,
  CVE-2026-64833, CVE-2026-64834, CVE-2026-64835, CVE-2026-66036,
  CVE-2026-66039, CVE-2026-66040, CVE-2026-66041, CVE-2026-70628,
  CVE-2026-70632, CVE-2026-75142, CVE-2026-75143, CVE-2026-75144 and
  CVE-2026-75146.
- `libcjson1`: CVE-2026-16554, CVE-2026-29036, CVE-2026-67215, CVE-2026-67216
  and CVE-2026-87933.
- `libsndfile1`: CVE-2026-37555.

Moving to the in-house build takes FFmpeg out of the image scanner's view; it
does not make FFmpeg free of advisories. Debian's security tracker records each
of the 16 FFmpeg advisories above as fixed in unstable's first FFmpeg 9.0.2
upload, 7:9.0.2-1 (checked 2026-10-23). Whether every fix is in the upstream
9.0.2 release archive, rather than a Debian patch, was not checked commit by
commit. The application's input whitelist still applies to every ffmpeg and
ffprobe input, and PyAV and OpenCV link the same FFmpeg 9.0.2 libraries.
