import React, { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Calendar, FileText, Loader, Wrench, Download } from 'lucide-react';
import { trackEvent } from '../utils/analytics';
import { ComplianceScore } from '../components/results/ComplianceScore';
import { IssueList } from '../components/results/IssueList';
import { IssuesByTypeChart } from '../components/charts/IssuesByTypeChart';
import { WCAGCriteriaChart } from '../components/charts/WCAGCriteriaChart';
import { FormatDownloadButton } from '../components/results/FormatDownloadButton';
import { scansApi } from '../api/scans';
import { Breadcrumbs } from '../components/layout/Breadcrumbs';
import { useToast } from '../context/toast-context';

interface Issue {
  severity?: string;
  impact?: string;
  description?: string;
  wcag_criteria?: string;
  element?: string;
  fix_suggestion?: string;
}

interface Scan {
  id: string;
  filename: string;
  type: string;
  uploaded_at: string;
  status: string;
  compliance_score: number;
  issues: Issue[];
}

interface IssuesBySeverity {
  critical: number;
  high: number;
  medium: number;
  low: number;
}

export function ScanDetail(): React.ReactElement {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const toast = useToast();
  const [scan, setScan] = useState<Scan | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<number>(0);
  const [progressMessage, setProgressMessage] = useState<string>('Loading scan...');
  const [isProcessing, setIsProcessing] = useState<boolean>(false);
  const [downloadingReport, setDownloadingReport] = useState<boolean>(false);
  const hasTrackedView = useRef<boolean>(false);

  useEffect(() => {
    let pollingInterval: ReturnType<typeof setInterval> | null = null;

    const fetchScan = async (): Promise<void> => {
      try {
        // getScan() unwraps the { scan: ... } envelope
        const data = await scansApi.getScan(id!);

        // Check if scan is still processing
        const scanStatus = data.status?.toUpperCase();
        const stillProcessing = scanStatus === 'PROCESSING' || scanStatus === 'PENDING';

        setIsProcessing(stillProcessing);

        // Transform API response to component format
        const transformedScan: Scan = {
          id: data.scan_id || data.id,
          filename: data.file_name || 'Unknown',
          type: data.scan_type?.toLowerCase() || 'unknown',
          uploaded_at: data.created_at,
          status: scanStatus?.toLowerCase(),
          compliance_score: data.result?.compliance_score || 0,
          // Backend returns issues as flat array (with counts in summary)
          issues: Array.isArray(data.result?.issues)
            ? data.result.issues
            : []
        };


        setScan(transformedScan);
        setLoading(false);

        // Track scan viewed once when results first load
        if (!stillProcessing && !hasTrackedView.current) {
          hasTrackedView.current = true;
          trackEvent('dash-scan-viewed', {
            scan_type: data.scan_type || transformedScan.type,
            score: Math.round(transformedScan.compliance_score || 0),
            issue_count: transformedScan.issues?.length || 0,
          });
        }

        // If completed, stop polling
        if (!stillProcessing && pollingInterval) {
          clearInterval(pollingInterval);
          pollingInterval = null;
        }
      } catch (err: unknown) {
        console.error('Failed to fetch scan:', err);
        const fetchError = err as Error;
        setError(fetchError.message || 'Failed to load scan details');
        setLoading(false);
        if (pollingInterval) {
          clearInterval(pollingInterval);
          pollingInterval = null;
        }
      }
    };

    const fetchProgress = async (): Promise<void> => {
      try {
        const progressData = await scansApi.getScanProgress(id!);
        setProgress(progressData.progress || 0);
        setProgressMessage(progressData.progress_message || 'Processing...');

        // If completed, fetch full scan details
        if (progressData.status === 'COMPLETED' || progressData.status === 'FAILED') {
          await fetchScan();
        }
      } catch (err) {
        console.error('Failed to fetch progress:', err);
      }
    };

    // Initial fetch
    fetchScan();

    // Poll for updates every 2 seconds
    pollingInterval = setInterval(() => {
      fetchProgress();
    }, 2000);

    // Cleanup on unmount
    return () => {
      if (pollingInterval) {
        clearInterval(pollingInterval);
      }
    };
  }, [id]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64" role="status" aria-label="Loading scan details">
        <Loader className="w-8 h-8 animate-spin text-primary-600" aria-hidden="true" />
        <span className="sr-only">Loading scan details...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-8">
        <div className="max-w-6xl mx-auto">
          <div className="bg-[var(--surface-error-subtle)] border border-[var(--content-error)] rounded-lg p-4 text-[var(--content-error)]" role="alert">
            Error: {error}
          </div>
        </div>
      </div>
    );
  }

  if (!scan) {
    return (
      <div className="p-8">
        <div className="max-w-6xl mx-auto">
          <div className="text-secondary">Scan not found</div>
        </div>
      </div>
    );
  }

  const handleDownloadReport = async (): Promise<void> => {
    trackEvent('dash-download-report', {});
    setDownloadingReport(true);
    try {
      const blob = await scansApi.downloadReport(id!);
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `accessibility-report-${scan?.filename.replace(/[^a-z0-9]/gi, '-').slice(0, 30) || 'document'}.pdf`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
      toast.success('Report downloaded successfully', 'Download Complete');
    } catch (err) {
      console.error('Failed to download report:', err);
      toast.error('Failed to download report. Please try again.', 'Download Failed');
    } finally {
      setDownloadingReport(false);
    }
  };

  const formatDate = (dateString: string): string => {
    return new Date(dateString).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  // Backend uses 'impact' field, map to severity for display
  const issuesBySeverity: IssuesBySeverity = {
    critical: scan.issues.filter(i => (i.severity || i.impact) === 'critical').length,
    high: scan.issues.filter(i => (i.severity || i.impact) === 'high' || (i.severity || i.impact) === 'serious').length,
    medium: scan.issues.filter(i => (i.severity || i.impact) === 'medium' || (i.severity || i.impact) === 'moderate').length,
    low: scan.issues.filter(i => (i.severity || i.impact) === 'low' || (i.severity || i.impact) === 'minor').length
  };

  return (
    <div className="p-8">
      <div className="max-w-6xl mx-auto">
        {/* Breadcrumbs */}
        <Breadcrumbs items={[
          { label: 'History', href: '/history' },
          { label: scan.filename },
        ]} />

        <div className="mb-6">
          <div className="flex items-start justify-between">
            <div>
              <h1 className="text-3xl font-bold text-primary mb-2">{scan.filename}</h1>
              <div className="flex items-center space-x-6 text-sm text-secondary">
                <div className="flex items-center space-x-2">
                  <Calendar className="w-4 h-4" />
                  <span>{formatDate(scan.uploaded_at)}</span>
                </div>
                <div className="flex items-center space-x-2">
                  <FileText className="w-4 h-4" />
                  <span className="uppercase">{scan.type}</span>
                </div>
              </div>
            </div>

            {/* Action Buttons */}
            {!isProcessing && scan.issues.length > 0 && (
              <div className="flex items-center gap-3 shrink-0">
                <button
                  onClick={() => navigate(`/remediate/${scan.id}`)}
                  className="btn-primary flex items-center gap-2"
                >
                  <Wrench className="w-4 h-4" />
                  Remediate
                </button>
                <FormatDownloadButton
                  scanId={scan.id}
                  scanType={scan.type}
                  filename={scan.filename}
                />
                <button
                  onClick={handleDownloadReport}
                  disabled={downloadingReport}
                  className="btn-secondary flex items-center gap-2 disabled:opacity-50"
                >
                  {downloadingReport ? (
                    <Loader className="w-4 h-4 animate-spin" />
                  ) : (
                    <Download className="w-4 h-4" />
                  )}
                  Report
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Processing Indicator */}
        {isProcessing && (
          <div className="mb-6 p-6 bg-[var(--feature-info-surface)] border-2 border-[var(--feature-info-content)] rounded-lg">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center space-x-3">
                <Loader className="w-6 h-6 animate-spin text-[var(--feature-info-content)]" />
                <span className="text-lg font-semibold text-primary">
                  Scanning in progress...
                </span>
              </div>
              <span className="text-lg font-bold text-[var(--feature-info-content)]">{progress}%</span>
            </div>
            <div className="w-full bg-[var(--surface-tertiary)] rounded-full h-3 mb-2">
              <div
                className="bg-[var(--feature-info-content)] h-3 rounded-full transition-all duration-500"
                style={{ width: `${progress}%` }}
              ></div>
            </div>
            <p className="text-sm text-[var(--feature-info-content)] mt-2">{progressMessage}</p>
          </div>
        )}

        {/* Compliance Score */}
        {!isProcessing && (
          <div className="mb-6">
            <ComplianceScore score={scan.compliance_score} />
          </div>
        )}

        {/* Issue Summary */}
        <div className="grid grid-cols-4 gap-4 mb-6">
          <div className="card border-2 border-[var(--feature-danger-content)] bg-[var(--feature-danger-surface)]">
            <div className="text-sm font-medium text-secondary mb-1">Critical</div>
            <div className="text-3xl font-bold text-[var(--feature-danger-content)]">{issuesBySeverity.critical}</div>
          </div>
          <div className="card border-2 border-[var(--feature-warning-content)] bg-[var(--feature-warning-surface)]">
            <div className="text-sm font-medium text-secondary mb-1">High</div>
            <div className="text-3xl font-bold text-[var(--feature-warning-content)]">{issuesBySeverity.high}</div>
          </div>
          <div className="card border-2 border-[var(--feature-info-content)] bg-[var(--feature-info-surface)]">
            <div className="text-sm font-medium text-secondary mb-1">Medium</div>
            <div className="text-3xl font-bold text-[var(--feature-info-content)]">{issuesBySeverity.medium}</div>
          </div>
          <div className="card border-2 border-[var(--border-primary)] bg-[var(--surface-tertiary)]">
            <div className="text-sm font-medium text-secondary mb-1">Low</div>
            <div className="text-3xl font-bold text-secondary">{issuesBySeverity.low}</div>
          </div>
        </div>

        {/* Data Visualizations */}
        {!isProcessing && scan.issues.length > 0 && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
            <IssuesByTypeChart issues={scan.issues} />
            <WCAGCriteriaChart issues={scan.issues} />
          </div>
        )}

        {/* Issues List */}
        <div className="mb-6">
          <h2 className="text-xl font-semibold text-primary mb-4">
            Issues Found ({scan.issues.length})
          </h2>
          <IssueList issues={scan.issues} scanType={scan.type} />
        </div>
      </div>
    </div>
  );
}
