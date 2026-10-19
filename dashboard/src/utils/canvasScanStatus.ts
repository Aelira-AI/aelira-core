/** Public Canvas scan wording for measured scores and partial findings. */
export interface CompletedCanvasScan {
  compliance_score: number | null;
  issues_count: number | null;
}

export function completedScanBadge(scan: CompletedCanvasScan): {
  label: string;
  intent: 'neutral' | 'warning';
} {
  const count = scan.issues_count;
  if (scan.compliance_score === null) {
    return {
      label: count !== null && count > 0
        ? `Unassessed · ${count} finding${count === 1 ? '' : 's'}`
        : 'Unassessed',
      intent: 'neutral',
    };
  }
  if (count === null) return { label: 'Findings unavailable', intent: 'neutral' };
  if (count > 0) {
    return { label: `${count} issue${count === 1 ? '' : 's'}`, intent: 'warning' };
  }
  return { label: 'Scanned · no findings', intent: 'neutral' };
}

export function completedScanNotice(scans: CompletedCanvasScan[]): string {
  const single = scans.length === 1;
  const prefix = single ? 'Scan complete.' : `${scans.length} files scanned.`;
  const unknownScores = scans.filter((scan) => scan.compliance_score === null).length;
  const assessment = unknownScores === 0
    ? ''
    : single
      ? ' Overall assessment unavailable.'
      : ` ${unknownScores} overall assessment${unknownScores === 1 ? '' : 's'} unavailable.`;
  const counts = scans.map((scan) => scan.issues_count);
  const total = counts.reduce<number>((sum, count) => sum + (count ?? 0), 0);
  const findings = counts.some((count) => count === null)
    ? ' Finding count unavailable for some files.'
    : unknownScores > 0 && total === 0
      ? ' Review the recorded checks in results.'
    : ` ${total} recorded finding${total === 1 ? '' : 's'}.`;
  return `${prefix}${assessment}${findings} View results for scope.`;
}

export function completedCourseAverage(scans: CompletedCanvasScan[]): number | null {
  if (scans.length === 0 || scans.some((scan) => scan.compliance_score === null)) {
    return null;
  }
  return scans.reduce((sum, scan) => sum + (scan.compliance_score ?? 0), 0) / scans.length;
}
