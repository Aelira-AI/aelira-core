import React, { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { FileText, CheckCircle2, AlertCircle, Download, ExternalLink } from 'lucide-react';
import { LTILayout } from '../components/LTILayout';
import { useLTISession } from '../hooks/useLTISession';
import { createLTIClient } from '../api/ltiClient';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Issue {
  severity?: string;
  impact?: string;
  description?: string;
  wcag_criteria?: string;
  element?: string;
  fix_suggestion?: string;
}

interface ScanResult {
  compliance_score: number;
  issues: Issue[];
}

interface ScanResponse {
  scan_id: string;
  file_name: string;
  scan_type: string;
  status: string;
  result: ScanResult | null;
}

interface RemediatedFormat {
  format: string;
  url: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function normalizeSeverity(issue: Issue): string {
  const raw = issue.severity || issue.impact || 'low';
  switch (raw) {
    case 'critical':
      return 'critical';
    case 'serious':
    case 'high':
      return 'high';
    case 'moderate':
    case 'medium':
      return 'medium';
    default:
      return 'low';
  }
}

function severityColor(severity: string): string {
  switch (severity) {
    case 'critical':
      return 'var(--status-error-text)';
    case 'high':
      return 'var(--status-warning-text)';
    case 'medium':
      return 'var(--accent)';
    default:
      return 'var(--content-secondary)';
  }
}

function scoreColor(score: number): string {
  if (score >= 90) return 'var(--status-success-text)';
  if (score >= 70) return 'var(--status-warning-text)';
  return 'var(--status-error-text)';
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ScoreBadge({ score }: { score: number }): React.ReactElement {
  const color = scoreColor(score);
  return (
    <div
      className="flex items-center justify-center rounded-full shrink-0"
      style={{
        width: 96,
        height: 96,
        border: `4px solid ${color}`,
      }}
    >
      <span className="text-3xl font-bold" style={{ color }}>
        {Math.round(score)}
      </span>
    </div>
  );
}

function SeveritySummary({ counts }: { counts: Record<string, number> }): React.ReactElement {
  const items: Array<{ label: string; key: string }> = [
    { label: 'Critical', key: 'critical' },
    { label: 'High', key: 'high' },
    { label: 'Medium', key: 'medium' },
    { label: 'Low', key: 'low' },
  ];

  return (
    <div className="grid grid-cols-4 gap-3">
      {items.map(({ label, key }) => (
        <div
          key={key}
          className="rounded-lg p-3 text-center"
          style={{
            backgroundColor: 'var(--card-bg)',
            border: '1px solid var(--card-border)',
          }}
        >
          <div className="text-xs font-medium mb-1" style={{ color: 'var(--content-secondary)' }}>
            {label}
          </div>
          <div className="text-2xl font-bold" style={{ color: severityColor(key) }}>
            {counts[key] || 0}
          </div>
        </div>
      ))}
    </div>
  );
}

function SeverityBadge({ severity }: { severity: string }): React.ReactElement {
  return (
    <span
      className="inline-block text-xs font-semibold px-2 py-0.5 rounded-full uppercase"
      style={{
        color: severityColor(severity),
        backgroundColor: `color-mix(in srgb, ${severityColor(severity)} 12%, transparent)`,
      }}
    >
      {severity}
    </span>
  );
}

function IssueCard({ issue }: { issue: Issue }): React.ReactElement {
  const severity = normalizeSeverity(issue);

  return (
    <div
      className="rounded-lg p-4"
      style={{
        backgroundColor: 'var(--card-bg)',
        border: '1px solid var(--card-border)',
      }}
    >
      <div className="flex items-start gap-3">
        <AlertCircle
          className="w-4 h-4 shrink-0 mt-0.5"
          style={{ color: severityColor(severity) }}
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-1">
            <SeverityBadge severity={severity} />
            {issue.wcag_criteria && (
              <span
                className="text-xs font-mono px-1.5 py-0.5 rounded"
                style={{
                  backgroundColor: 'var(--surface-primary)',
                  color: 'var(--content-secondary)',
                  border: '1px solid var(--border-primary)',
                }}
              >
                {issue.wcag_criteria}
              </span>
            )}
          </div>
          <p className="text-sm mb-1" style={{ color: 'var(--content-primary)' }}>
            {issue.description || 'No description available'}
          </p>
          {issue.fix_suggestion && (
            <p className="text-xs mt-2" style={{ color: 'var(--content-secondary)' }}>
              <span className="font-medium">Suggested fix:</span> {issue.fix_suggestion}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function LTIReportView(): React.ReactElement {
  const { scanId } = useParams<{ scanId: string }>();
  const { accessToken, loading: sessionLoading, error: sessionError } = useLTISession();

  const [scan, setScan] = useState<ScanResponse | null>(null);
  const [formats, setFormats] = useState<RemediatedFormat[]>([]);
  const [dataLoading, setDataLoading] = useState(true);
  const [dataError, setDataError] = useState<string | null>(null);

  useEffect(() => {
    if (!accessToken || !scanId) return;

    const client = createLTIClient(accessToken);

    async function fetchData(): Promise<void> {
      try {
        const [scanRes, formatsRes] = await Promise.allSettled([
          client.get<ScanResponse>(`/education/scans/${scanId}`),
          client.get<RemediatedFormat[]>(`/education/scans/${scanId}/remediated/formats`),
        ]);

        if (scanRes.status === 'fulfilled') {
          setScan(scanRes.value.data);
        } else {
          setDataError('Failed to load scan details. The scan may no longer exist.');
          setDataLoading(false);
          return;
        }

        if (formatsRes.status === 'fulfilled') {
          const data = formatsRes.value.data;
          setFormats(Array.isArray(data) ? data : []);
        }

        setDataLoading(false);
      } catch {
        setDataError('An unexpected error occurred while loading the report.');
        setDataLoading(false);
      }
    }

    fetchData();
  }, [accessToken, scanId]);

  // Derive values from scan data
  const score = scan?.result?.compliance_score ?? 0;
  const issues = scan?.result?.issues ?? [];

  const severityCounts: Record<string, number> = { critical: 0, high: 0, medium: 0, low: 0 };
  for (const issue of issues) {
    const sev = normalizeSeverity(issue);
    severityCounts[sev] = (severityCounts[sev] || 0) + 1;
  }

  const hasRemediatedVersion = formats.length > 0;
  const downloadHref = hasRemediatedVersion
    ? `${createLTIClient(accessToken || '').defaults.baseURL}/education/scans/${scanId}/remediated`
    : undefined;

  return (
    <LTILayout loading={sessionLoading || dataLoading} error={sessionError || dataError}>
      <div className="max-w-3xl mx-auto">
        {/* Header: file name + score */}
        <div className="flex items-center gap-6 mb-6">
          <ScoreBadge score={score} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 mb-1">
              <FileText className="w-5 h-5 shrink-0" style={{ color: 'var(--content-secondary)' }} />
              <h1
                className="text-xl font-semibold truncate"
                style={{ color: 'var(--content-primary)' }}
              >
                {scan?.file_name || 'Document'}
              </h1>
            </div>
            <p className="text-sm" style={{ color: 'var(--content-secondary)' }}>
              Compliance Score
            </p>
            <p className="text-sm mt-1" style={{ color: scoreColor(score) }}>
              {score >= 90 ? (
                <span className="flex items-center gap-1">
                  <CheckCircle2 className="w-4 h-4" /> Compliant
                </span>
              ) : score >= 70 ? (
                'Needs improvement'
              ) : (
                <span className="flex items-center gap-1">
                  <AlertCircle className="w-4 h-4" /> Non-compliant
                </span>
              )}
            </p>
          </div>
        </div>

        {/* Severity summary cards */}
        <div className="mb-6">
          <SeveritySummary counts={severityCounts} />
        </div>

        {/* Download button */}
        {hasRemediatedVersion && downloadHref && (
          <div className="mb-6">
            <a
              href={downloadHref}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium"
              style={{
                backgroundColor: 'var(--accent-solid)',
                color: '#fff',
              }}
            >
              <Download className="w-4 h-4" />
              Download Accessible Version
            </a>
          </div>
        )}

        {/* Issues list */}
        <div className="mb-8">
          <h2
            className="text-lg font-semibold mb-3"
            style={{ color: 'var(--content-primary)' }}
          >
            Issues ({issues.length})
          </h2>
          {issues.length === 0 ? (
            <div
              className="rounded-lg p-6 text-center"
              style={{
                backgroundColor: 'var(--card-bg)',
                border: '1px solid var(--card-border)',
              }}
            >
              <CheckCircle2
                className="w-8 h-8 mx-auto mb-2"
                style={{ color: 'var(--status-success-text)' }}
              />
              <p className="text-sm" style={{ color: 'var(--content-secondary)' }}>
                No accessibility issues found.
              </p>
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              {issues.map((issue, idx) => (
                <IssueCard key={idx} issue={issue} />
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div
          className="border-t pt-4 text-center text-xs"
          style={{
            borderColor: 'var(--border-primary)',
            color: 'var(--content-secondary)',
          }}
        >
          Powered by{' '}
          <a
            href="https://example.com"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 font-medium"
            style={{ color: 'var(--accent)' }}
          >
            Aelira
            <ExternalLink className="w-3 h-3" />
          </a>
        </div>
      </div>
    </LTILayout>
  );
}
