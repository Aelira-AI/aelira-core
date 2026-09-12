import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { scansApi, type RemediationJobStatus } from '../../api/scans';
import { classifyRemediationJob, pollRemediationJob } from '../../utils/remediationJob';

const labels = {
  queued: 'Remediation queued',
  running: 'Remediation in progress',
  partial: 'Manual review required',
  failed: 'Remediation failed',
  timed_out: 'Remediation timed out',
  completed: 'Remediation job completed',
};

/** Entry points display persisted state; execution and downloads live on the review page. */
export function RemediationStatusLink({ scanId, refreshKey = false, startUnconfirmed = false }: {
  scanId: string;
  refreshKey?: boolean;
  startUnconfirmed?: boolean;
}): React.ReactElement {
  const [job, setJob] = useState<RemediationJobStatus | null>(null);
  const [state, setState] = useState('Checking remediation status…');
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const update = (latest: RemediationJobStatus): void => {
      if (controller.signal.aborted) return;
      setJob(latest);
      setState(labels[classifyRemediationJob(latest)]);
    };
    const load = async (): Promise<void> => {
      setJob(null);
      setState('Checking remediation status…');
      try {
        const latest = await scansApi.getLatestRemediationJob(scanId);
        if (controller.signal.aborted) return;
        if (latest === null) {
          setState('No remediation job recorded');
          return;
        }
        update(latest);
        if (['queued', 'running'].includes(classifyRemediationJob(latest))) {
          const result = await pollRemediationJob(
            (signal) => scansApi.getRemediationJobStatus(latest.status_url, signal),
            { signal: controller.signal, onUpdate: update },
          );
          if (!controller.signal.aborted && result.outcome === 'client_timeout') {
            setState('Monitoring paused; the server job may still be running');
          }
        }
      } catch (error) {
        if (controller.signal.aborted) return;
        const status = (error as { response?: { status?: number } }).response?.status;
        setState(status === 404 || status === 410
          ? 'Job record unavailable; open remediation review to check'
          : 'Job status unavailable; check again before starting another job');
      }
    };
    void load();
    return () => controller.abort();
  }, [scanId, refreshKey, retry]);

  const outputAvailable = job?.status === 'completed'
    && job.download_available === true && job.download_url;
  return (
    <div className="basis-full rounded-lg border border-[var(--border-primary)] p-3">
      <p className="text-sm font-medium text-primary" role="status">{state}</p>
      {startUnconfirmed && <p className="mt-1 text-sm text-secondary">
        The automatic remediation request was not confirmed. Check the recorded job before retrying.
      </p>}
      {job && <p className="mt-1 text-sm text-secondary">
        {outputAvailable ? 'Output available for review' : 'No downloadable output is available for this job'}
      </p>}
      <div className="mt-3 flex flex-wrap gap-3">
        <Link to={`/remediate/${scanId}`} className="btn-secondary">
          Open Remediation Review
        </Link>
        <button type="button" onClick={() => setRetry((value) => value + 1)} className="btn-secondary">
          Check Status
        </button>
      </div>
    </div>
  );
}
