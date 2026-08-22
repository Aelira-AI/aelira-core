import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { extname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const srcRoot = fileURLToPath(new URL('../../src', import.meta.url));

function sourceFiles(directory) {
  return readdirSync(directory, { withFileTypes: true })
    .sort((a, b) => a.name.localeCompare(b.name))
    .flatMap((entry) => {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) return sourceFiles(path);
      return ['.css', '.ts', '.tsx'].includes(extname(entry.name)) ? [path] : [];
    });
}

const dashboardSources = sourceFiles(srcRoot).map((path) => ({
  path: relative(srcRoot, path),
  source: readFileSync(path, 'utf8'),
}));
const cssSource = readFileSync(join(srcRoot, 'index.css'), 'utf8');
const appSource = readFileSync(join(srcRoot, 'App.tsx'), 'utf8');

function accentValues() {
  return [...cssSource.matchAll(/^\s*--accent:\s*(#[0-9A-F]{6});/gim)].map((match) => match[1].toUpperCase());
}

function accentSolidValues() {
  return [...cssSource.matchAll(/^\s*--accent-solid:\s*(#[0-9A-F]{6});/gim)].map((match) => match[1].toUpperCase());
}

function relativeLuminance(hex) {
  const channels = hex.match(/[0-9A-F]{2}/gi).map((channel) => Number.parseInt(channel, 16) / 255);
  const linear = channels.map((channel) => channel <= 0.04045
    ? channel / 12.92
    : ((channel + 0.055) / 1.055) ** 2.4);
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrastRatio(first, second) {
  const [lighter, darker] = [relativeLuminance(first), relativeLuminance(second)].sort((a, b) => b - a);
  return (lighter + 0.05) / (darker + 0.05);
}

const retiredAccentToken = ['--accent', 'primary'].join('-');

describe('Task21A clarity-token lineage', () => {
  it('retires the legacy primary accent custom property from dashboard source', () => {
    const staleFiles = dashboardSources
      .filter(({ source }) => source.includes(retiredAccentToken))
      .map(({ path }) => path);

    assert.deepEqual(staleFiles, [], `retired accent token remains in: ${staleFiles.join(', ')}`);
  });

  it('separates foreground/focus accents from white-text solid backgrounds', () => {
    const accents = accentValues();
    const solids = accentSolidValues();
    assert.deepEqual(accents, ['#2E2963', '#C4B5FD']);
    assert.deepEqual(solids, ['#2E2963', '#6A5EA6']);
    for (const solid of solids) {
      assert.ok(contrastRatio(solid, '#FFFFFF') >= 4.5, `${solid} must retain WCAG AA contrast with white`);
    }
    for (const surface of ['#1A1816', '#252320', '#353230']) {
      assert.ok(contrastRatio('#C4B5FD', surface) >= 4.5, `dark accent must contrast with ${surface}`);
    }
    const unsafeBackgrounds = dashboardSources
      .filter(({ source }) => /backgroundColor\s*:[^,}]*var\(--accent\)|background(?:-color)?\s*:\s*var\(--accent\)|bg-\[var\(--accent\)\](?!\/)/.test(source))
      .map(({ path }) => path);
    assert.deepEqual(unsafeBackgrounds, [], `solid backgrounds still use foreground accent: ${unsafeBackgrounds.join(', ')}`);
  });

  it('preserves visible keyboard focus and the skip-to-content path', () => {
    assert.match(cssSource, /\*:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--accent\)/s);
    assert.match(appSource, /href="#main-content"[\s\S]*focus:not-sr-only[\s\S]*Skip to main content/);
  });
});
