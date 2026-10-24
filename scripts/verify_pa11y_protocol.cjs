// Pa11y must drive Chrome over the Chrome DevTools Protocol. Puppeteer loads
// chromium-bidi only for WebDriver BiDi, and chromium-bidi 14 runs on a zod
// override it was not written for (see verify_pa11y_bidi_parser.cjs), so the
// configuration pins "cdp" and this check proves it holds at run time:
// the pinned configuration, the protocol of a browser launched from it, and
// that a real Pa11y scan never loads chromium-bidi.
//
// Usage: node verify_pa11y_protocol.cjs CONFIG URL [RUNTIME]
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const [configPath, url, runtime = '/opt/pa11y-runtime'] = process.argv.slice(2);
assert.ok(configPath && url, 'usage: verify_pa11y_protocol.cjs CONFIG URL [RUNTIME]');
const launch = JSON.parse(fs.readFileSync(configPath, 'utf8')).chromeLaunchConfig;
assert.equal(launch.protocol, 'cdp', `Pa11y's configured protocol is ${launch.protocol}, not cdp`);

const pa11yPath = require.resolve('pa11y', { paths: [runtime] });
const pa11y = require(pa11yPath);
const puppeteer = require(require.resolve('puppeteer', { paths: [path.dirname(pa11yPath)] }));
const bidiModules = () => Object.keys(require.cache)
  .filter((file) => file.includes(`${path.sep}chromium-bidi${path.sep}`));

(async () => {
  const browser = await puppeteer.launch(launch);
  try {
    assert.equal(browser.protocol, 'cdp', `the launched browser uses ${browser.protocol}, not cdp`);
  } finally {
    await browser.close();
  }
  const results = await pa11y(url, { chromeLaunchConfig: launch, timeout: 60000 });
  assert.ok(Array.isArray(results.issues), 'Pa11y returned no issue list');
  const loaded = bidiModules();
  assert.deepEqual(loaded, [], `chromium-bidi was loaded during the Pa11y scan: ${loaded[0]}`);
  console.log(`Pa11y protocol cdp; scan returned ${results.issues.length} issues; chromium-bidi not loaded`);
})().catch((error) => {
  console.error(error && error.message ? error.message : error);
  process.exit(1);
});
