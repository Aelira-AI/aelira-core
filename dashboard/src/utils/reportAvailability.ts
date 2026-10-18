/** A compliance PDF requires an overall assessment; zero is still a measured score. */
export function canDownloadComplianceReport(score: number | null | undefined): boolean {
  return score != null;
}
