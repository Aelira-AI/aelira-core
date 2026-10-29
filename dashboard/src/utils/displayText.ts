/**
 * Human-readable text for values the API records as internal slugs. Display
 * only: nothing here changes what is recorded, scored or written back.
 */

const SLUG = /^[a-z0-9]+(?:_[a-z0-9]+)*$/;

/** `font_size` becomes "Font Size". Anything that is not a lowercase slug is returned unchanged. */
export function humanizeSlug(value: string): string {
  if (!SLUG.test(value)) return value;
  return value
    .split('_')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

/** Issue categories as scanners record them (`category` or `type`). */
export const CATEGORY_LABELS: Record<string, string> = {
  alt_text: 'Alt Text',
  heading: 'Heading Structure',
  contrast: 'Color Contrast',
  language: 'Language',
  table: 'Tables',
  list: 'Lists',
  link: 'Links',
  navigation: 'Navigation',
  structure: 'Document Structure',
  form: 'Forms',
  media: 'Media',
  title: 'Document Title',
  font_size: 'Font Size',
  image_of_text: 'Images of Text',
  sheet_name: 'Sheet Name',
  table_header: 'Table Headers',
  other: 'Other',
  // Extended content-type categories
  shadow_dom: 'Shadow DOM',
  aria: 'ARIA Attributes',
  animation: 'Animation',
  pivot_table: 'Pivot Tables',
  conditional_format: 'Conditional Formatting',
  smartart: 'SmartArt',
  embedded_object: 'Embedded Objects',
  flashing: 'Flashing Content',
  color: 'Color Usage',
  reading_order: 'Reading Order',
};

export function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] || humanizeSlug(category);
}

function nonemptyString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

/**
 * The category of a recorded finding. Web, code and LaTeX scanners record it as
 * `type`; the document scanners (Word, PDF, Excel, PowerPoint) record `category`.
 */
export function issueCategory(issue: Record<string, unknown>): string {
  return nonemptyString(issue.type) || nonemptyString(issue.category) || 'other';
}

const FIX_METHOD_LABELS: Record<string, string> = {
  rule: 'Rule-based',
  rule_based: 'Rule-based',
  heuristic: 'Heuristic',
  ai: 'AI',
  ai_text: 'AI Text',
  ai_vision: 'AI Vision',
  ai_generated: 'AI-generated',
  template: 'Template',
  reviewed_recovery: 'Reviewed recovery',
};

/** How a fix was produced, for the chip on a review item. */
export function fixMethodLabel(method: string): string {
  return FIX_METHOD_LABELS[method] || humanizeSlug(method);
}

/** "1 issue", "2 issues". */
export function issueCountLabel(count: number): string {
  return `${count} ${count === 1 ? 'issue' : 'issues'}`;
}
