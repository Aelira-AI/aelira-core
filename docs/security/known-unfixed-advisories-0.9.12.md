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
| CVE-2026-66039 | ffmpeg, libavcodec61, libavdevice61, libavfilter10, libavformat61, libavutil59, libpostproc58, libswresample5, libswscale8 | `inline_mitigations_already_exist` | CAF demuxer. Media inputs are restricted to the supported container allowlist, which excludes CAF. |
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
