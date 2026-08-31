import type { BatchRemediateResponse } from '../api/brightspaceContent';

const BRIGHTSPACE_REMEDIATE_BATCH_SIZE = 20;

export interface RemediateAllSummary {
  requestedCount: number;
  processedCount: number;
  completedCount: number;
  fixedCount: number;
  manualCount: number;
  failedCount: number;
  unreportedCount: number;
  chunkFailures: Array<{
    chunkNumber: number;
    requestedCount: number;
    message: string;
  }>;
}

export async function remediateAllInChunks(
  eligibleIds: readonly string[],
  remediateChunk: (ids: string[]) => Promise<BatchRemediateResponse>,
  signal?: AbortSignal
): Promise<RemediateAllSummary> {
  const summary: RemediateAllSummary = {
    requestedCount: eligibleIds.length,
    processedCount: 0,
    completedCount: 0,
    fixedCount: 0,
    manualCount: 0,
    failedCount: 0,
    unreportedCount: eligibleIds.length,
    chunkFailures: [],
  };

  for (
    let offset = 0, chunkNumber = 1;
    offset < eligibleIds.length;
    offset += BRIGHTSPACE_REMEDIATE_BATCH_SIZE, chunkNumber += 1
  ) {
    if (signal?.aborted) {
      const error = new Error('Brightspace remediation aborted');
      error.name = 'AbortError';
      throw error;
    }
    const ids = eligibleIds.slice(
      offset,
      offset + BRIGHTSPACE_REMEDIATE_BATCH_SIZE
    );
    try {
      const response = await remediateChunk([...ids]);
      const requested = new Set(ids);
      const seen = new Set<string>();
      const validOutcomes = response.results.filter((outcome) => {
        if (!requested.has(outcome.cloud_file_id) || seen.has(outcome.cloud_file_id)) {
          return false;
        }
        seen.add(outcome.cloud_file_id);
        return true;
      });
      summary.processedCount += validOutcomes.length;
      for (const outcome of validOutcomes) {
        summary.completedCount += outcome.status === 'completed' ? 1 : 0;
        summary.failedCount += outcome.status === 'failed' ? 1 : 0;
        summary.fixedCount += outcome.fixed_count;
        summary.manualCount += outcome.manual_count;
      }
      if (validOutcomes.length !== ids.length) {
        summary.chunkFailures.push({
          chunkNumber,
          requestedCount: ids.length,
          message: `Chunk returned ${validOutcomes.length} of ${ids.length} valid outcomes`,
        });
      }
    } catch (error) {
      if (signal?.aborted) {
        const abort = new Error('Brightspace remediation aborted');
        abort.name = 'AbortError';
        throw abort;
      }
      summary.chunkFailures.push({
        chunkNumber,
        requestedCount: ids.length,
        message: error instanceof Error ? error.message : 'Chunk request failed',
      });
    }
  }

  summary.unreportedCount = summary.requestedCount - summary.processedCount;
  return summary;
}
