export interface CriterionIssue {
  criterion?: string;
  wcag_criterion?: string;
  wcag_criteria?: string;
  rule?: string;
}

/** Keep other standards out of a chart that specifically claims WCAG criteria. */
export function wcagCriterion(issue: CriterionIssue): string | null {
  const declared = issue.wcag_criterion || issue.wcag_criteria || issue.criterion;
  const value = declared || (/\bWCAG\b/i.test(issue.rule || '') ? issue.rule : undefined);
  if (typeof value !== 'string' || !value || /PDF\s*\/\s*UA|Matterhorn/i.test(value)) return null;
  // A WCAG version (2.1) is not a success criterion (1.1.1).
  return value.match(/\b\d+\.\d+\.\d+\b/)?.[0] || null;
}

/**
 * Text for a finding's criterion badge. Scanners record "WCAG 1.3.1" as often as
 * a bare "1.3.1", so prefix only a bare number; never show "WCAG WCAG 1.3.1".
 * Other standards keep their own name.
 */
export function wcagBadgeLabel(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const text = value.trim();
  if (!text) return null;
  if (/^\d/.test(text)) return `WCAG ${text}`;
  const prefixed = text.match(/^WCAG\s+(.+)$/i);
  return prefixed ? `WCAG ${prefixed[1]}` : text;
}
