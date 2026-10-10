import type { BatchApproveResponse, BatchWritebackResponse } from '../api/brightspaceContent';
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

export function brightspaceWritebackSummary(result: BatchWritebackResponse): BatchResultSummary {
  return summarizeBatchOutcome({
    verb: 'Wrote back', succeededCount: result.written_count,
    buckets: [{ label: 'stale', count: result.stale_count },
      { label: 'failed', count: result.failed_count },
      { label: 'skipped', count: result.skipped_count ?? 0 }],
    errors: result.errors ?? [],
  });
}
