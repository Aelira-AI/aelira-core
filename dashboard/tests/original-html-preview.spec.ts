import { test, expect } from '@playwright/test';

/**
 * sanitizeOriginalHtml adds the missing-alt notice inside DOMPurify's own pass
 * and returns exactly what DOMPurify produced. The module is imported through
 * the dev server so the real source and the real DOMPurify run in a browser.
 */

const NOTICE = 'Image with no alternative text in the original';

test.describe('sanitizeOriginalHtml', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/login', { waitUntil: 'domcontentloaded' });
  });

  test('adds the notice to images without alt and keeps existing alt text', async ({ page }) => {
    const out = await page.evaluate(async () => {
      const { sanitizeOriginalHtml } = await import('/src/utils/originalHtmlPreview.ts');
      return sanitizeOriginalHtml(
        '<p>x</p><img src="a.png"><img src="b.png" alt="A chart"><img src="c.png" alt="">',
      );
    });
    expect(out).toContain(`<img src="a.png" alt="${NOTICE}">`);
    expect(out).toContain('alt="A chart"');
    expect(out).toContain('<img src="c.png" alt="">');
  });

  test('returns the sanitiser output without a second parse and serialise', async ({ page }) => {
    const result = await page.evaluate(async () => {
      const { sanitizeOriginalHtml } = await import('/src/utils/originalHtmlPreview.ts');
      // Count how often a template element is created: the old implementation
      // re-parsed the sanitised string through one, the single pass does not.
      const original = Document.prototype.createElement;
      let templates = 0;
      Document.prototype.createElement = function (this: Document, name: string, ...rest: unknown[]) {
        if (String(name).toLowerCase() === 'template') templates += 1;
        return (original as (...a: unknown[]) => Element).call(this, name, ...rest);
      } as typeof Document.prototype.createElement;
      try {
        const html = sanitizeOriginalHtml('<table><tr><td><img src="a.png"></td></tr></table><p>ok</p>');
        return { html, templates };
      } finally {
        Document.prototype.createElement = original;
      }
    });
    expect(result.templates).toBe(0);
    expect(result.html).toContain(NOTICE);
  });

  test('still removes scripts and event handlers', async ({ page }) => {
    const out = await page.evaluate(async () => {
      const { sanitizeOriginalHtml } = await import('/src/utils/originalHtmlPreview.ts');
      return sanitizeOriginalHtml('<img src="a.png" onerror="window.__ran=1"><script>window.__ran=2</script><p>kept</p>');
    });
    expect(out).not.toContain('onerror');
    expect(out).not.toContain('<script');
    expect(out).toContain('<p>kept</p>');
    expect(out).toContain(NOTICE);
  });

  test('does not leave the hook registered for later sanitise calls', async ({ page }) => {
    const out = await page.evaluate(async () => {
      const { sanitizeOriginalHtml } = await import('/src/utils/originalHtmlPreview.ts');
      // Resolve the exact DOMPurify instance the module imported: the dev
      // server rewrites the bare specifier in the source it serves.
      const source = await (await fetch('/src/utils/originalHtmlPreview.ts')).text();
      const specifier = /from\s+["']([^"']*dompurify[^"']*)["']/.exec(source)?.[1] ?? '';
      const { default: DOMPurify } = await import(/* @vite-ignore */ specifier);
      sanitizeOriginalHtml('<img src="a.png">');
      const other = DOMPurify.sanitize('<img src="b.png">');
      // The hook is also removed when sanitising throws.
      let threw = false;
      try {
        sanitizeOriginalHtml({ toString() { throw new Error('boom'); } } as unknown as string);
      } catch {
        threw = true;
      }
      return { other, afterThrow: DOMPurify.sanitize('<img src="c.png">'), threw };
    });
    expect(out.threw).toBe(true);
    expect(out.other).toBe('<img src="b.png">');
    expect(out.afterThrow).toBe('<img src="c.png">');
  });
});
