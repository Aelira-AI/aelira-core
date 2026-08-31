import React, { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Building2, Loader, RefreshCw } from 'lucide-react';

import {
  scansApi,
  type DepartmentComplianceRollup,
  type InstitutionComplianceRollup,
} from '../../api/scans';


function scoreLabel(score: number | null): string {
  return score === null ? 'Not assessed' : `${Math.round(score)}/100`;
}


function CoverageCell({ label, value }: { label: string; value: string | number }): React.ReactElement {
  return (
    <div className="rounded-lg p-3 glass-subtle">
      <dt className="text-xs font-medium uppercase tracking-wide text-tertiary">{label}</dt>
      <dd className="mt-1 text-xl font-bold text-primary">{value}</dd>
    </div>
  );
}


function DepartmentRow({ department }: { department: DepartmentComplianceRollup }): React.ReactElement {
  return (
    <tr className="border-t border-primary">
      <th scope="row" className="px-3 py-3 text-left font-medium text-primary">
        {department.department_name}
      </th>
      <td className="px-3 py-3 text-right text-primary">
        {scoreLabel(department.document_weighted_score)}
      </td>
      <td className="px-3 py-3 text-right text-secondary">
        {department.coverage.verified}/{department.coverage.enrolled}
      </td>
      <td className="px-3 py-3 text-right text-secondary">{department.coverage.scanned}</td>
      <td className="px-3 py-3 text-right text-secondary">{department.coverage.stale}</td>
      <td className="px-3 py-3 text-right text-secondary">{department.coverage.failed}</td>
    </tr>
  );
}


export function InstitutionComplianceCard(): React.ReactElement {
  const [rollup, setRollup] = useState<InstitutionComplianceRollup | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      setRollup(await scansApi.getInstitutionCompliance());
    } catch {
      setError('Unable to load institution compliance.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const initialLoad = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(initialLoad);
  }, [load]);

  if (loading) {
    return (
      <section className="card mb-8" aria-labelledby="institution-compliance-title" aria-busy="true">
        <h2 id="institution-compliance-title" className="text-xl font-semibold text-primary">
          Institution compliance
        </h2>
        <div className="flex min-h-32 items-center justify-center gap-2 text-secondary" role="status">
          <Loader className="h-5 w-5 animate-spin" aria-hidden="true" />
          <span>Loading institution compliance…</span>
        </div>
      </section>
    );
  }

  if (error) {
    return (
      <section className="card mb-8" aria-labelledby="institution-compliance-title">
        <h2 id="institution-compliance-title" className="text-xl font-semibold text-primary">
          Institution compliance
        </h2>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg p-4 glass-subtle" role="alert">
          <span className="flex items-center gap-2 text-secondary">
            <AlertTriangle className="h-5 w-5" aria-hidden="true" />
            {error}
          </span>
          <button type="button" className="btn-secondary flex items-center gap-2" onClick={() => void load()}>
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
            Retry
          </button>
        </div>
      </section>
    );
  }

  if (!rollup || rollup.coverage.enrolled === 0) {
    return (
      <section className="card mb-8" aria-labelledby="institution-compliance-title">
        <h2 id="institution-compliance-title" className="text-xl font-semibold text-primary">
          Institution compliance
        </h2>
        <div className="mt-4 rounded-lg p-5 text-secondary glass-subtle">
          No documents are enrolled in the institution inventory yet.
        </div>
      </section>
    );
  }

  const coverage = rollup.coverage;
  return (
    <section className="card mb-8" aria-labelledby="institution-compliance-title">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Building2 className="h-5 w-5 text-accent" aria-hidden="true" />
            <h2 id="institution-compliance-title" className="text-xl font-semibold text-primary">
              Institution compliance
            </h2>
          </div>
          <p className="mt-1 text-sm text-secondary">{rollup.institution_name}</p>
        </div>
        <p className="max-w-xl text-sm text-secondary">
          Current document state only. Historical trends remain separate and are never recalculated from current membership.
        </p>
      </div>

      <div className="mt-6 grid gap-4 md:grid-cols-2">
        <div className="rounded-lg border-2 border-accent p-5">
          <div className="flex items-center justify-between gap-3">
            <h3 className="font-semibold text-primary">Document-weighted institution score</h3>
            <span className="rounded-full bg-surface-accent-subtle px-2 py-1 text-xs font-semibold text-accent">
              Primary
            </span>
          </div>
          <p className="mt-3 text-4xl font-bold text-primary" aria-label={`Document-weighted institution score ${scoreLabel(rollup.document_weighted_score)}`}>
            {scoreLabel(rollup.document_weighted_score)}
          </p>
          <p className="mt-2 text-sm text-secondary">Each verified document has equal weight.</p>
        </div>
        <div className="rounded-lg p-5 glass-subtle">
          <h3 className="font-semibold text-secondary">Secondary: flat department mean</h3>
          <p className="mt-3 text-3xl font-bold text-primary">
            {scoreLabel(rollup.flat_department_mean)}
          </p>
          <p className="mt-2 text-sm text-secondary">Each assessed department has equal weight.</p>
        </div>
      </div>

      <div className="mt-6">
        <h3 className="font-semibold text-primary">Document coverage</h3>
        <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <CoverageCell label="Enrolled" value={coverage.enrolled} />
          <CoverageCell label="Scanned" value={coverage.scanned} />
          <CoverageCell label="Verified" value={coverage.verified} />
          <CoverageCell label="Stale" value={coverage.stale} />
          <CoverageCell label="Failed" value={coverage.failed} />
          <CoverageCell label="Total coverage" value={`${coverage.total_coverage_percent}%`} />
        </dl>
        <p className="mt-3 text-xs text-tertiary">
          Status counts can overlap: a document may retain its last verified score while a newer scan is stale or failed.
        </p>
      </div>

      <div className="mt-7">
        <h3 className="font-semibold text-primary">Department drill-down</h3>
        <div
          className="mt-3 overflow-x-auto rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          role="region"
          aria-label="Department compliance table"
          tabIndex={0}
        >
          <table className="w-full min-w-[680px] text-sm">
            <caption className="sr-only">Current compliance and coverage by department</caption>
            <thead>
              <tr className="text-secondary">
                <th scope="col" className="px-3 py-2 text-left">Department</th>
                <th scope="col" className="px-3 py-2 text-right">Document-weighted score</th>
                <th scope="col" className="px-3 py-2 text-right">Verified / enrolled</th>
                <th scope="col" className="px-3 py-2 text-right">Scanned</th>
                <th scope="col" className="px-3 py-2 text-right">Stale</th>
                <th scope="col" className="px-3 py-2 text-right">Failed</th>
              </tr>
            </thead>
            <tbody>
              {rollup.departments.map((department) => (
                <DepartmentRow key={department.department_id} department={department} />
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
