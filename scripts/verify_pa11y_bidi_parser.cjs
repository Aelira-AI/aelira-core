// Pa11y drives Chrome through Puppeteer over the Chrome DevTools Protocol.
// Puppeteer loads chromium-bidi only for WebDriver BiDi, which our Pa11y
// configuration does not select. chromium-bidi 14 was written against zod 3,
// and the runtime overrides zod to a patched 4.x release (GHSA-mrhx-c7xc-m3w5),
// so this check makes sure the BiDi protocol parser still loads and validates
// with that zod, in case BiDi ever becomes the path.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const runtime = process.argv[2] || '/opt/pa11y-runtime';
const resolve = (request) => require.resolve(request, { paths: [runtime] });

const zodVersion = require(resolve('zod/package.json')).version;
const [major, minor, patch] = zodVersion.split('.').map(Number);
assert.ok(major > 4 || (major === 4 && (minor > 6 || (minor === 6 && patch >= 6))),
  `zod ${zodVersion} is older than the patched 4.6.6`);

const parserPath = resolve('chromium-bidi/lib/cjs/protocol-parser/protocol-parser.js');
assert.ok(parserPath.startsWith(path.join(fs.realpathSync(runtime), 'node_modules')), `unexpected parser path ${parserPath}`);
const parser = require(parserPath);

const command = { context: 'context-1', url: 'https://example.com/', wait: 'complete' };
assert.deepEqual(parser.BrowsingContext.parseNavigateParams(command), command);
// Invalid input must still be rejected. chromium-bidi 14 reads zod 3's error
// shape, so under zod 4 its validation throws a TypeError instead of a BiDi
// "invalid argument" error. Pa11y uses CDP and never loads this module (see
// verify_pa11y_protocol.cjs), so only rejection itself is asserted here.
assert.throws(() => parser.BrowsingContext.parseNavigateParams({ context: 'context-1' }),
  'a navigate command without a url was accepted');

console.log(`chromium-bidi protocol parser ok with zod ${zodVersion}`);
