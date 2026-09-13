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
