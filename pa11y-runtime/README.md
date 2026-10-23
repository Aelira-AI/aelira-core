<h1 align="center">Pa11y image runtime</h1>

<p align="center"><strong>The pinned Pa11y build manifest that the Aelira Core API images install.</strong></p>

<p align="center">
  <a href="../scripts/smoke_pa11y_runtime.py">Smoke check</a> ·
  <a href="../README.md">Aelira Core README</a>
</p>

> **Not a published package.** The production and development API images install this private build manifest with `npm ci`. It is not a separately published package.

## Pinned versions

| Component | Version |
|---|---|
| Pa11y | 9.0.1 |
| HTML_CodeSniffer | 2.5.1 |
| `@puppeteer/browsers` (exact override) | 3.2.4 |
| Node | 22.12 or newer required by the override; the images use pinned Node 24 |

## Why the override exists

The exact `@puppeteer/browsers` 3.2.4 override removes the older FTP and ZIP downloader dependency chain. It is outside Puppeteer 24's declared version range and requires Node 22.12 or newer.

The API uses its installed Chromium with browser downloads disabled. Optional downloader peers are intentionally absent from the lock.

## Changing the override

A change to this override requires all of the following:

- a clean locked installation
- an advisory audit
- successful native builds on both supported architectures

Both Dockerfiles run `scripts/smoke_pa11y_runtime.py` as the final non-root user. It exercises the real Pa11y CLI and the guarded HTML_CodeSniffer consumer against Chromium, and it also rejects the excluded FTP and ZIP packages.

These checks prove bounded runtime compatibility, not accessibility conformance.
