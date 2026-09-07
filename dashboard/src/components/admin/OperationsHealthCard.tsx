import React, { useCallback, useEffect, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Loader,
  RefreshCw,
  Server,
  Wrench,
} from 'lucide-react';

import {
  adminApi,
  type WorkerHealthState,
  type WorkerStatusResponse,
} from '../../api/admin';

const HEALTH_LABELS: Record<WorkerHealthState, string> = {
  worker_unavailable: 'Worker unavailable',
  expired_lease: 'Expired processing lease',
  stuck_processing: 'Stalled processing work',
  healthy_processing: 'Healthy processing',
  stuck_runnable_backlog: 'Stale runnable backlog',
  healthy_advancing: 'Healthy advancing queue',
  healthy_idle: 'Healthy idle',
};

const SCHEDULER_LABELS: Record<WorkerStatusResponse['weekly_summary_scheduler']['state'], string> = {
  not_started: 'Not started',
  healthy: 'Healthy',
  stale: 'Stale',
  failed: 'Failed',
};

function ageLabel(seconds: number | null): string {
  if (seconds === null) return 'Not recorded';
  if (seconds < 60) return `${Math.round(seconds)}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}

function timestampLabel(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? 'time unavailable' : parsed.toLocaleString();
}

function Metric({ label, value }: { label: string; value: React.ReactNode }): React.ReactElement {
  return (
    <div className="rounded-lg p-3 glass-subtle">
      <dt className="text-xs font-medium uppercase tracking-wide text-tertiary">{label}</dt>
      <dd className="mt-1 text-lg font-semibold text-primary">{value}</dd>
    </div>
  );
}

export function OperationsHealthCard(): React.ReactElement {
  const [snapshot, setSnapshot] = useState<WorkerStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(false);
    try {
      setSnapshot(await adminApi.getWorkerStatus());
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const initialLoad = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(initialLoad);
  }, [load]);

  if (loading && !snapshot) {
    return (
      <section className="card mb-8" aria-labelledby="operations-health-title" aria-busy="true">
        <h2 id="operations-health-title" className="text-xl font-semibold text-primary">
          Operations
        </h2>
        <div className="flex min-h-32 items-center justify-center gap-2 text-secondary" role="status">
          <Loader className="h-5 w-5 animate-spin" aria-hidden="true" />
          <span>Loading operations snapshot…</span>
        </div>
      </section>
    );
  }

  if (error || !snapshot) {
    return (
      <section className="card mb-8" aria-labelledby="operations-health-title">
        <h2 id="operations-health-title" className="text-xl font-semibold text-primary">
          Operations
        </h2>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg p-4 glass-subtle" role="alert">
          <span className="flex items-center gap-2 text-secondary">
            <AlertTriangle className="h-5 w-5 text-[var(--feature-danger-content)]" aria-hidden="true" />
            Operations snapshot unavailable.
          </span>
          <button
            type="button"
            className="btn-secondary flex items-center gap-2"
            onClick={() => void load()}
            aria-label="Refresh operations snapshot"
          >
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
            Refresh
          </button>
        </div>
      </section>
    );
  }

  const healthy = snapshot.status === 'healthy';
  const queueIsEmpty = snapshot.queue.pending === 0 && snapshot.queue.processing === 0;

  return (
    <section className="card mb-8" aria-labelledby="operations-health-title" aria-busy={loading}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Activity className="h-5 w-5 text-accent" aria-hidden="true" />
            <h2 id="operations-health-title" className="text-xl font-semibold text-primary">
              Operations
            </h2>
          </div>
          <p className="mt-1 text-sm text-secondary">
            Current snapshot generated {timestampLabel(snapshot.generated_at)}.
          </p>
          <p className="mt-1 text-xs text-tertiary">
            This is a current snapshot, not uptime history or an SLO.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-semibold ${
              healthy
                ? 'bg-[var(--feature-success-surface)] text-[var(--feature-success-content)]'
                : 'bg-[var(--feature-danger-surface)] text-[var(--feature-danger-content)]'
            }`}
          >
            {healthy ? (
              <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
            ) : (
              <AlertTriangle className="h-4 w-4" aria-hidden="true" />
            )}
            {healthy ? 'Healthy' : 'Degraded — needs attention'}
          </span>
          <button
            type="button"
            className="btn-secondary flex items-center gap-2"
            onClick={() => void load()}
            disabled={loading}
            aria-label="Refresh operations snapshot"
          >
            {loading ? (
              <Loader className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <RefreshCw className="h-4 w-4" aria-hidden="true" />
            )}
            Refresh
          </button>
        </div>
      </div>

      <div className="mt-5 rounded-lg border border-primary p-4">
        <div className="flex items-center gap-2">
          <Server className="h-5 w-5 text-accent" aria-hidden="true" />
          <h3 className="font-semibold text-primary">{HEALTH_LABELS[snapshot.health_state]}</h3>
        </div>
        {queueIsEmpty && (
          <p className="mt-2 text-sm text-secondary">No jobs queued or processing.</p>
        )}
      </div>

      <div className="mt-5 grid gap-5 xl:grid-cols-2">
        <section aria-labelledby="worker-queue-title">
          <h3 id="worker-queue-title" className="flex items-center gap-2 font-semibold text-primary">
            <Activity className="h-4 w-4" aria-hidden="true" />
            Queue and workers
          </h3>
          <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Metric label="Pending" value={snapshot.queue.pending} />
            <Metric label="Processing" value={snapshot.queue.processing} />
            <Metric label="Completed" value={snapshot.queue.completed} />
            <Metric label="Failed" value={snapshot.queue.failed} />
            <Metric label="Live workers" value={snapshot.workers.live} />
            <Metric label="Draining" value={snapshot.workers.draining} />
            <Metric label="Heartbeat" value={ageLabel(snapshot.workers.latest_heartbeat_age_seconds)} />
            <Metric label="Runnable" value={snapshot.progress.runnable_pending} />
          </dl>
        </section>

        <section aria-labelledby="worker-attention-title">
          <h3 id="worker-attention-title" className="flex items-center gap-2 font-semibold text-primary">
            <Clock3 className="h-4 w-4" aria-hidden="true" />
            Progress and maintenance
          </h3>
          <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Metric label="Claimed" value={snapshot.progress.jobs_claimed} />
            <Metric label="Completed by workers" value={snapshot.progress.jobs_completed} />
            <Metric label="Worker failures" value={snapshot.progress.jobs_failed} />
            <Metric label="Latest progress" value={ageLabel(snapshot.progress.latest_progress_age_seconds)} />
            <Metric label="Oldest pending" value={ageLabel(snapshot.progress.oldest_pending_age_seconds)} />
            <Metric label="Oldest running" value={ageLabel(snapshot.progress.oldest_running_job_age_seconds)} />
            <Metric label="Expired processing" value={snapshot.progress.expired_processing} />
            <Metric label="Stalled processing" value={snapshot.progress.stalled_processing} />
            <Metric label="Artifact cleanup due" value={snapshot.maintenance.artifact_cleanup_due} />
          </dl>
        </section>

        <section aria-labelledby="scheduler-health-title">
          <h3 id="scheduler-health-title" className="flex items-center gap-2 font-semibold text-primary">
            <Clock3 className="h-4 w-4" aria-hidden="true" />
            Weekly summary scheduler
          </h3>
          <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Metric label="State" value={SCHEDULER_LABELS[snapshot.weekly_summary_scheduler.state]} />
            <Metric label="Last success" value={ageLabel(snapshot.weekly_summary_scheduler.last_success_age_seconds)} />
            <Metric label="Last error" value={snapshot.weekly_summary_scheduler.last_error_code ?? 'None recorded'} />
          </dl>
        </section>

        <section aria-labelledby="integrity-health-title">
          <h3 id="integrity-health-title" className="flex items-center gap-2 font-semibold text-primary">
            <Wrench className="h-4 w-4" aria-hidden="true" />
            Reconciliation and artifacts
          </h3>
          <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Metric label="Reconciliation required" value={snapshot.reconciliation.required} />
            <Metric label="Manual reconciliation" value={snapshot.reconciliation.manual_required} />
            <Metric label="Failed manual reconciliation" value={snapshot.reconciliation.failed_manual} />
            <Metric label="Orphans pending move" value={snapshot.orphans.pending_move} />
            <Metric label="Orphans quarantined" value={snapshot.orphans.quarantined} />
            <Metric label="Restore required" value={snapshot.orphans.restore_required} />
            <Metric label="Orphans reviewed" value={snapshot.orphans.reviewed} />
            <Metric label="Orphans purging" value={snapshot.orphans.purging} />
          </dl>
        </section>
      </div>
    </section>
  );
}
