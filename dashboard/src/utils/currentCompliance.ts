import type { DeadlineInfo } from '../types/deadline';

export interface RawCurrentComplianceStats {
  total_scans?: number;
  historical_scan_count?: number;
  enrolled_document_count?: number;
  verified_document_count?: number;
  unverified_document_count?: number;
  avg_compliance_score?: number | null;
  total_issues?: number;
  scans_this_month?: number;
  deadline?: DeadlineInfo;
}

export interface CurrentDashboardStats {
  historicalScanCount: number;
  enrolledDocuments: number;
  verifiedDocuments: number;
  unverifiedDocuments: number;
  avgCompliance: number | null;
  issuesFound: number;
  scansThisMonth: number;
  deadline: DeadlineInfo | null;
}

/** Preserve current-stock coverage and never turn an absent score into zero. */
export function normalizeCurrentComplianceStats(
  raw: RawCurrentComplianceStats,
): CurrentDashboardStats {
  return {
    historicalScanCount: raw.historical_scan_count ?? raw.total_scans ?? 0,
    enrolledDocuments: raw.enrolled_document_count ?? 0,
    verifiedDocuments: raw.verified_document_count ?? 0,
    unverifiedDocuments: raw.unverified_document_count ?? 0,
    avgCompliance: raw.avg_compliance_score ?? null,
    issuesFound: raw.total_issues ?? 0,
    scansThisMonth: raw.scans_this_month ?? 0,
    deadline: raw.deadline ?? null,
  };
}
