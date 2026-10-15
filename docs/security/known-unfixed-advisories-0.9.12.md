# Known unfixed advisories in v0.9.12

The v0.9.12 API image is built on Debian 13. The image scanner reports the
advisories below as HIGH. None has a fix in Debian at publication time, so
none can be resolved by updating packages. Each one is reviewed, listed here,
and accepted by the release gate only while its review is current
(`.trivy-unfixed-dispositions.json`, reviewed 2026-10-15, expires 2027-01-12).
Every finding stays in the published scan reports.

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

- Python 3.14.8 bundles Expat 2.8.5, which includes the fixes for the Expat
  advisories below.
- lxml is built from its source release against libxml2 2.15.4 and libxslt
  1.1.45, which include the fixes for the libxml2 advisories below; the
  published lxml wheels still bundle libxml2 2.14.
- The FFmpeg copies inside the PyAV and OpenCV wheels receive the same local
  file, container and codec restrictions as the system FFmpeg, and the
  transcriber only reads WAV produced by that restricted path.

## Affected

| Advisory | Packages | Assessment |
| --- | --- | --- |
| CVE-2026-36849 | libtiff6 | libtiff may decode TIFF graphics referenced by submitted LaTeX documents. Denial of service only; Debian will not fix it in this release. |
| CVE-2026-74860 | libxml2, libxml2-utils | libxml2 is used by LaTeX conversion (LaTeXML) on documents users submit. No distribution fix exists. |
| CVE-2026-86138 | libxml2, libxml2-utils | libxml2 is used by LaTeX conversion (LaTeXML) on documents users submit. No distribution fix exists. |
| CVE-2026-86139 | libxml2, libxml2-utils | libxml2 is used by LaTeX conversion (LaTeXML) on documents users submit. No distribution fix exists. |
| CVE-2026-86140 | libxml2, libxml2-utils | libxml2 is used by LaTeX conversion (LaTeXML) on documents users submit. No distribution fix exists. |
| CVE-2026-86142 | libxml2, libxml2-utils | libxml2 is used by LaTeX conversion (LaTeXML) on documents users submit. No distribution fix exists. |
| CVE-2026-86143 | libxml2, libxml2-utils | libxml2 is used by LaTeX conversion (LaTeXML) on documents users submit. No distribution fix exists. |
| CVE-2026-86144 | libxml2, libxml2-utils | libxml2 is used by LaTeX conversion (LaTeXML) on documents users submit. No distribution fix exists. |
| CVE-2026-86420 | imagemagick, imagemagick-7-common, imagemagick-7.q16, libimage-magick-perl, libimage-magick-q16-perl, libmagickcore-7.q16-10, libmagickwand-7.q16-10 | ImageMagick may process graphics referenced by submitted LaTeX documents. Denial of service only; no distribution fix exists. |
| CVE-2026-86421 | imagemagick, imagemagick-7-common, imagemagick-7.q16, libimage-magick-perl, libimage-magick-q16-perl, libmagickcore-7.q16-10, libmagickwand-7.q16-10 | ImageMagick may process graphics referenced by submitted LaTeX documents. Denial of service only; no distribution fix exists. |

## Not affected

| Advisory | Packages | Justification | Assessment |
| --- | --- | --- | --- |
| CVE-2022-4055 | xdg-utils | `vulnerable_code_not_in_execute_path` | xdg-utils mailto handling with Thunderbird. The image has no mail client and the application never runs xdg-open. |
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
| CVE-2026-66046 | libexpat1 | `vulnerable_code_cannot_be_controlled_by_adversary` | The application Python uses its own bundled Expat 2.8.5, which includes the fixes. The distribution libexpat is used directly only by the unused system Python and is otherwise loaded through fontconfig, which parses image-provided font configuration. |
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
| CVE-2026-76956 | libexpat1 | `vulnerable_code_cannot_be_controlled_by_adversary` | The application Python uses its own bundled Expat 2.8.5, which includes the fixes. The distribution libexpat is used directly only by the unused system Python and is otherwise loaded through fontconfig, which parses image-provided font configuration. |
| CVE-2026-76957 | libexpat1 | `vulnerable_code_cannot_be_controlled_by_adversary` | The application Python uses its own bundled Expat 2.8.5, which includes the fixes. The distribution libexpat is used directly only by the unused system Python and is otherwise loaded through fontconfig, which parses image-provided font configuration. |
| CVE-2026-77214 | libexpat1 | `vulnerable_code_cannot_be_controlled_by_adversary` | The application Python uses its own bundled Expat 2.8.5, which includes the fixes. The distribution libexpat is used directly only by the unused system Python and is otherwise loaded through fontconfig, which parses image-provided font configuration. |
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
| CVE-2026-93990 | libexpat1 | `vulnerable_code_cannot_be_controlled_by_adversary` | The application Python uses its own bundled Expat 2.8.5, which includes the fixes. The distribution libexpat is used directly only by the unused system Python and is otherwise loaded through fontconfig, which parses image-provided font configuration. |
| CVE-2026-9538 | libperl5.40, perl, perl-base, perl-modules-5.40 | `vulnerable_code_not_in_execute_path` | Perl Archive::Tar. LaTeX conversion processes LaTeX sources; nothing in the application reads tar archives with Archive::Tar. |
