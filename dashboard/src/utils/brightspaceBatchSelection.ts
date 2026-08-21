import type { BatchApproveResponse } from '../api/brightspaceContent';
import { summarizeBatchOutcome, type BatchResultSummary } from './batchActionResult.ts';

interface BrightspaceApprovalCandidate {
  cloud_file_id: string;
  approval_eligible: boolean;
}

export function brightspaceApprovalIds(items: BrightspaceApprovalCandidate[]): string[] {
  return items
    .filter((item) => item.approval_eligible)
    .map((item) => item.cloud_file_id);
}

export function brightspaceApprovalSummary(
  result: BatchApproveResponse
): BatchResultSummary {
  return summarizeBatchOutcome({
    verb: 'Approved',
    succeededCount: result.approved_count,
    buckets: [
      { label: 'skipped', count: result.skipped_count },
      { label: 'failed', count: result.failed_count },
    ],
    errors: result.errors,
  });
}
