# Pa11y image runtime

The production and development API images install this private build manifest
with `npm ci`. It is not a separately published package. Pa11y remains at 9.0.1
and HTML_CodeSniffer at 2.5.1.

The exact `@puppeteer/browsers` 3.2.4 override removes the older FTP and ZIP
downloader dependency chain. It is outside Puppeteer 24's declared version
range and requires Node 22.12 or newer; the images use pinned Node 24. The API
uses its installed Chromium with browser downloads disabled. Optional downloader
peers are intentionally absent from the lock.

Changing this override requires a clean locked installation, an advisory audit
and successful native builds on both supported architectures. Both Dockerfiles
run `scripts/smoke_pa11y_runtime.py` as the final non-root user, exercising the
real Pa11y CLI and the guarded HTML_CodeSniffer consumer against Chromium. The
smoke check also rejects the excluded FTP and ZIP packages. These checks prove
bounded runtime compatibility, not accessibility conformance.
