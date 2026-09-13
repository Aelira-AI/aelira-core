import type { RemediationEligibility } from '../types/api';

export interface FindingAutoFix {
  state: 'available' | 'unavailable' | 'unknown';
  reason: 'reported' | 'not_reported' | 'conflicting_flags' | 'invalid_flags';
}

/** Finding-level capability is optional evidence, never document queue eligibility. */
export function normalizeFindingAutoFix(finding: Record<string, unknown>): FindingAutoFix {
  const flags = [finding.can_auto_fix, finding.auto_fix_available].filter((flag) => flag !== undefined);
  if (flags.length === 0) return { state: 'unknown', reason: 'not_reported' };
  if (flags.some((flag) => typeof flag !== 'boolean')) return { state: 'unknown', reason: 'invalid_flags' };
  if (flags.some((flag) => flag !== flags[0])) return { state: 'unknown', reason: 'conflicting_flags' };
  return { state: flags[0] === true ? 'available' : 'unavailable', reason: 'reported' };
}

export function matchesFindingAutoFixFilter(capability: FindingAutoFix, filter: string): boolean {
  return filter === 'all' || capability.state === filter;
}

export function countFindingAutoFix(capabilities: FindingAutoFix[]): Record<FindingAutoFix['state'], number> {
  const counts = { available: 0, unavailable: 0, unknown: 0 };
  for (const capability of capabilities) counts[capability.state]++;
  return counts;
}

/** Use server eligibility only. Missing eligibility fails closed without classifying findings. */
export function selectEligibleIssueScanIds(
  filteredIssues: { scanId: string }[],
  scans: { id: string; remediation_eligibility?: RemediationEligibility }[],
): string[] {
  const eligibleIds = new Set(scans.filter((scan) => scan.remediation_eligibility?.eligible === true).map((scan) => scan.id));
  return [...new Set(filteredIssues.map((issue) => issue.scanId).filter((id) => id && eligibleIds.has(id)))];
}
