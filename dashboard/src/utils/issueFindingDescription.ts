function nonemptyText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

/** Preserve scanner text; never infer a finding from its severity or location. */
export function describeIssueFinding(
  issue: Record<string, unknown>,
  categoryLabels: Record<string, string> = {},
): string {
  for (const field of ['description', 'message', 'title', 'suggested_fix', 'suggested_alt_text']) {
    const value = nonemptyText(issue[field]);
    if (value) return value;
  }

  const type = nonemptyText(issue.type);
  const issueType = nonemptyText(issue.issue_type) || type;
  const text = nonemptyText(issue.text) || nonemptyText(issue.shape_name);
  const detail = [issueType.replace(/_/g, ' '), text].filter(Boolean).join(' — ');
  if (!detail) return 'Finding description unavailable';
  const category = nonemptyText(categoryLabels[type]) || type || 'Issue';
  return `${category}: ${detail}`;
}
