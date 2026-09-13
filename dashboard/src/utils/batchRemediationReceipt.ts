import type { BatchRemediationResult } from '../api/scans';

/** A queue receipt proves accepted jobs, never remediation completion. */
export function batchRemediationReceiptIsConfirmed(receipt: Partial<BatchRemediationResult> | null, scanIds: string[]): boolean {
  if (!receipt || receipt.success !== true || scanIds.length === 0) return false;
  const queued = receipt.scans_queued;
  const jobs = receipt.job_ids;
  return receipt.total_scans === scanIds.length
    && Array.isArray(queued) && queued.length === scanIds.length
    && new Set(queued).size === scanIds.length && scanIds.every((id) => queued.includes(id))
    && Array.isArray(jobs) && jobs.length === scanIds.length
    && jobs.every((id) => typeof id === 'string' && id.length > 0)
    && new Set(jobs).size === scanIds.length;
}
