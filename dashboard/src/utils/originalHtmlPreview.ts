import DOMPurify from 'dompurify';

/** Text announced for an image whose source markup has no alt attribute. */
export const MISSING_ALT_NOTICE = 'Image with no alternative text in the original';

/**
 * Sanitize the ORIGINAL markup of a document under review for display.
 *
 * The original pane intentionally shows the inaccessible source, so it can
 * contain images with no alt attribute. Left alone, a screen reader announces
 * such an image by its file name. This adds a factual notice (never an invented
 * description of the picture) so the pane stays readable while the defect it
 * demonstrates is still stated. The remediated pane is sanitized separately and
 * is not touched, so a remediation that still lacks alt text remains visible.
 */
export function sanitizeOriginalHtml(html: string): string {
  // The notice is added inside DOMPurify's own pass, so the sanitized output is
  // returned exactly as DOMPurify produced it and is never parsed or serialized
  // a second time. The hook is registered only for this call and always removed
  // so it cannot run for other callers of the shared DOMPurify instance.
  const addMissingAltNotice = (node: Element): void => {
    if (node.nodeName === 'IMG' && !node.hasAttribute('alt')) {
      node.setAttribute('alt', MISSING_ALT_NOTICE);
    }
  };
  DOMPurify.addHook('afterSanitizeAttributes', addMissingAltNotice);
  try {
    return DOMPurify.sanitize(html);
  } finally {
    DOMPurify.removeHook('afterSanitizeAttributes', addMissingAltNotice);
  }
}
