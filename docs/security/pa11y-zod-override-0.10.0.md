# Pa11y runtime: zod forced to 4.6.7 for 0.10.0

The Pa11y runtime in the API and development images (`pa11y-runtime/`) pins
pa11y 9.0.1, which brings Puppeteer 24.43.1 and `chromium-bidi` 14.0.0.
`chromium-bidi` 14 depends on zod 3. Every zod before 4.6.6 is affected by
[GHSA-mrhx-c7xc-m3w5](https://github.com/advisories/GHSA-mrhx-c7xc-m3w5)
(CVE-2023-54404, high), and there is no zod 3 fix.

## What ships

`pa11y-runtime/package.json` overrides zod to 4.6.7. The lockfile has that
single zod, and `npm audit --audit-level=high` reports no vulnerabilities.

**The compatibility cost, stated plainly.** `chromium-bidi` 14 was written for
zod 3. Under zod 4:

- it still parses valid WebDriver BiDi commands;
- its validation of invalid input throws a `TypeError` instead of a BiDi
  "invalid argument" error, because it reads zod 3's error shape.

## Why scanning is not affected

Pa11y passes its `chromeLaunchConfig` straight to `puppeteer.launch()`. In
Puppeteer 24.43.1, WebDriver BiDi is the default only for Firefox. For Chrome,
Puppeteer drives the browser over the Chrome DevTools Protocol (CDP). It loads
`chromium-bidi` only when the `webDriverBiDi` protocol is selected. Nothing on
Puppeteer's entry point loads it.

`config/pa11y.json` now pins `"protocol": "cdp"`, so this no longer rests on a
default.

## What the image build checks

The Pa11y smoke gate (`pa11y-verified` build stage, and the quickstart Pa11y
step) runs two checks.

**`scripts/verify_pa11y_protocol.cjs`** requires all of the following:

- the configured protocol is `cdp`;
- a browser launched from that configuration reports `cdp`;
- a real Pa11y scan of the smoke fixture never loads a `chromium-bidi`
  module.

Each of these was shown to fail when the configuration is set to
`webDriverBiDi`.

**`scripts/verify_pa11y_bidi_parser.cjs`** loads `chromium-bidi`'s protocol
parser with the installed zod and requires:

- zod is 4.6.6 or later;
- one valid `browsingContext.navigate` command parses unchanged;
- an invalid one is still rejected (it does not check the error type, for
  the reason above).

It fails if zod cannot be resolved, or if zod 3 is installed.

## The clean fix

pa11y 10 uses Puppeteer 25 and a `chromium-bidi` that depends on zod 4. Moving
to it and dropping the override is tracked for a later release in
[#786](https://github.com/Aelira-AI/aelira-core/issues/786).
