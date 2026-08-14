import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Calendar, FileText, Eye, Download, Trash2, Loader, FileCode, Upload, X } from 'lucide-react';
import { scansApi } from '../api/scans';
import { trackEvent } from '../utils/analytics';
import { useToast } from '../context/toast-context';

interface Scan {
  id: string;
  filename: string;
  type: string;
  uploaded_at: string;
  status: string;
  compliance_score: number;
  issues_count: number;
}

interface DeleteConfirmModalProps {
  scan: Scan;
  onConfirm: (scan: Scan) => void;
  onCancel: () => void;
  isDeleting: boolean;
}

type FilterType = 'all' | 'website' | 'pdf' | 'word' | 'excel' | 'powerpoint' | 'latex' | 'code' | 'image' | 'video';

// Delete confirmation modal component
function DeleteConfirmModal({ scan, onConfirm, onCancel, isDeleting }: DeleteConfirmModalProps): React.ReactElement {
  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
      role="dialog"
      aria-modal="true"
      aria-labelledby="delete-modal-title"
    >
      <div className="bg-[var(--surface-primary)] rounded-xl p-6 max-w-md w-full mx-4 shadow-xl">
        <div className="flex items-center justify-between mb-4">
          <h3 id="delete-modal-title" className="text-lg font-semibold text-primary">
            Delete Scan
          </h3>
          <button
            onClick={onCancel}
            className="p-1 text-tertiary hover:text-primary transition-colors"
            aria-label="Close dialog"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        <p className="text-secondary mb-6">
          Are you sure you want to delete "<span className="font-medium text-primary">{scan.filename}</span>"?
          This action cannot be undone.
        </p>
        <div className="flex gap-3 justify-end">
          <button
            onClick={onCancel}
            className="px-4 py-2 rounded-lg text-sm font-medium bg-[var(--surface-tertiary)] text-secondary hover:bg-[var(--surface-accent-subtle)] transition-colors"
            disabled={isDeleting}
          >
            Cancel
          </button>
          <button
            onClick={() => onConfirm(scan)}
            disabled={isDeleting}
            className="px-4 py-2 rounded-lg text-sm font-medium bg-[var(--feature-danger-surface)] text-[var(--feature-danger-content)] hover:opacity-90 transition-opacity flex items-center gap-2 disabled:opacity-50"
          >
            {isDeleting ? (
              <>
                <Loader className="w-4 h-4 animate-spin" />
                Deleting...
              </>
            ) : (
              <>
                <Trash2 className="w-4 h-4" />
                Delete
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

export function History(): React.ReactElement {
  const [scans, setScans] = useState<Scan[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterType>('all');
  const [downloadingReport, setDownloadingReport] = useState<string | null>(null);
  const [downloadingFixes, setDownloadingFixes] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<boolean>(false);
  const [deleteConfirm, setDeleteConfirm] = useState<Scan | null>(null);
  const navigate = useNavigate();
  const toast = useToast();

  // Download handlers
  const handleDownloadReport = async (scan: Scan): Promise<void> => {
    trackEvent('dash-download-report', {});
    setDownloadingReport(scan.id);
    try {
      const blob = await scansApi.downloadReport(scan.id);
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `accessibility-report-${scan.filename.replace(/[^a-z0-9]/gi, '-').slice(0, 30)}.pdf`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
      toast.success('Report downloaded successfully', 'Download Complete');
    } catch (err) {
      console.error('Failed to download report:', err);
      toast.error('Failed to download report. Please try again.', 'Download Failed');
    } finally {
      setDownloadingReport(null);
    }
  };

  const handleDownloadFixes = async (scan: Scan): Promise<void> => {
    setDownloadingFixes(scan.id);
    try {
      const blob = await scansApi.downloadRemediated(scan.id);
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `remediated-${scan.filename}`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
      toast.success('Remediated file downloaded', 'Download Complete');
    } catch (err) {
      console.error('Failed to download remediated file:', err);
      toast.error('No remediated file available. Run remediation first from the scan detail page.', 'Download Failed');
    } finally {
      setDownloadingFixes(null);
    }
  };

  const handleDeleteScan = async (scan: Scan): Promise<void> => {
    setDeleting(true);
    try {
      await scansApi.deleteScan(scan.id);
      setScans((prev) => prev.filter((s) => s.id !== scan.id));
      toast.success(`"${scan.filename}" has been deleted`, 'Scan Deleted');
    } catch (err) {
      console.error('Failed to delete scan:', err);
      toast.error('Failed to delete scan. Please try again.', 'Delete Failed');
    } finally {
      setDeleting(false);
      setDeleteConfirm(null);
    }
  };

  useEffect(() => {
    const fetchScans = async (): Promise<void> => {
      try {
        setLoading(true);
        const response = await scansApi.listScans();

        // API returns { success: true, scans: [...] }
        const scansList = response.scans || response || [];

        // Transform API response to component format
        const transformedScans: Scan[] = scansList.map((scan: {
          scan_id?: string;
          id?: string;
          file_name?: string;
          scan_type?: string;
          created_at?: string;
          status?: string;
          compliance_score?: number;
          total_issues?: number;
        }) => ({
          id: scan.scan_id || scan.id || '',
          filename: scan.file_name || 'Unknown',
          type: scan.scan_type?.toLowerCase() || 'unknown',
          uploaded_at: scan.created_at || '',
          status: scan.status?.toLowerCase() || 'unknown',
          compliance_score: scan.compliance_score || 0,
          issues_count: scan.total_issues || 0
        }));

        setScans(transformedScans);
      } catch (err: unknown) {
        console.error('Failed to fetch scans:', err);
        const fetchError = err as Error;
        setError(fetchError.message || 'Failed to load scan history');
      } finally {
        setLoading(false);
      }
    };

    fetchScans();
  }, []);

  const getScoreColor = (score: number): string => {
    if (score >= 90) return 'text-[var(--feature-success-content)] bg-[var(--feature-success-surface)]';
    if (score >= 70) return 'text-[var(--feature-warning-content)] bg-[var(--feature-warning-surface)]';
    return 'text-[var(--feature-danger-content)] bg-[var(--feature-danger-surface)]';
  };

  const getTypeColor = (type: string): string => {
    const colors: Record<string, string> = {
      pdf: 'bg-[var(--feature-danger-surface)] text-[var(--feature-danger-content)]',
      word: 'bg-[var(--feature-info-surface)] text-[var(--feature-info-content)]',
      excel: 'bg-[var(--feature-success-surface)] text-[var(--feature-success-content)]',
      powerpoint: 'bg-[var(--feature-warning-surface)] text-[var(--feature-warning-content)]',
      latex: 'bg-[var(--feature-secondary-surface)] text-[var(--feature-secondary-content)]',
      image: 'bg-[var(--feature-media-surface)] text-[var(--feature-media-content)]',
      video: 'bg-[var(--feature-primary-surface)] text-[var(--feature-primary-content)]',
      website: 'bg-[var(--feature-advanced-surface)] text-[var(--feature-advanced-content)]',
      code: 'bg-[var(--feature-info-surface)] text-[var(--feature-info-content)]'
    };
    return colors[type] || 'bg-[var(--surface-tertiary)] text-[var(--content-secondary)]';
  };

  // Filter scans by type
  const filteredScans = filter === 'all'
    ? scans
    : scans.filter(scan => scan.type === filter);

  const formatDate = (dateString: string): string => {
    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
    const diffDays = Math.floor(diffHours / 24);

    if (diffHours < 1) return 'Just now';
    if (diffHours < 24) return `${diffHours} hour${diffHours > 1 ? 's' : ''} ago`;
    if (diffDays < 7) return `${diffDays} day${diffDays > 1 ? 's' : ''} ago`;

    return date.toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric'
    });
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64" role="status" aria-label="Loading scan history">
        <Loader className="w-8 h-8 animate-spin text-accent" aria-hidden="true" />
        <span className="sr-only">Loading scan history...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-8">
        <div className="max-w-7xl mx-auto">
          <div
            className="rounded-lg p-4 bg-[var(--surface-error-subtle)] border border-[var(--content-error)] text-[var(--content-error)]"
            role="alert"
          >
            <p className="font-medium">Error loading scan history</p>
            <p className="text-sm mt-1">{error}</p>
            <button
              onClick={() => window.location.reload()}
              className="mt-3 text-sm underline hover:no-underline"
            >
              Try again
            </button>
          </div>
        </div>
      </div>
    );
  }

  const filterTypes: FilterType[] = ['all', 'website', 'pdf', 'word', 'excel', 'powerpoint', 'latex', 'code', 'image', 'video'];

  return (
    <div className="p-8">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-3xl font-bold text-primary mb-6">Scan History</h1>

        {/* Filters */}
        <div className="card mb-6">
          <div className="flex items-center space-x-4">
            <span className="text-sm font-medium text-secondary">Filter by:</span>
            <div className="flex flex-wrap gap-2" role="group" aria-label="Filter scans by type">
              {filterTypes.map((type) => (
                <button
                  key={type}
                  onClick={() => {
                    if (type !== filter) {
                      trackEvent('dash-history-filter', { filter: type });
                    }
                    setFilter(type);
                  }}
                  aria-pressed={filter === type}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                    filter === type
                      ? 'btn-primary'
                      : 'bg-[var(--surface-tertiary)] text-[var(--content-secondary)] hover:bg-[var(--surface-accent-subtle)]'
                  }`}
                >
                  {type.charAt(0).toUpperCase() + type.slice(1)}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Screen reader announcement for filter results */}
        <div className="sr-only" aria-live="polite" role="status">
          {filter === 'all'
            ? `Showing all ${filteredScans.length} scans`
            : `Showing ${filteredScans.length} ${filter} scans`}
        </div>

        {/* Scans - Mobile Card View */}
        {filteredScans.length > 0 && (
          <div className="lg:hidden space-y-3">
            {filteredScans.map((scan) => (
              <div key={scan.id} className="card p-4">
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center space-x-3 min-w-0">
                    <FileText className="w-5 h-5 text-tertiary shrink-0" aria-hidden="true" />
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-primary truncate">{scan.filename}</p>
                      <div className="flex items-center gap-2 mt-1">
                        <span className={`px-2 py-0.5 text-xs font-medium rounded ${getTypeColor(scan.type)}`}>
                          {scan.type.toUpperCase()}
                        </span>
                        <span className="text-xs text-tertiary">{formatDate(scan.uploaded_at)}</span>
                      </div>
                    </div>
                  </div>
                  <span className={`px-2.5 py-1 text-xs font-semibold rounded-full shrink-0 ml-2 ${getScoreColor(scan.compliance_score)}`}>
                    {scan.compliance_score}/100
                  </span>
                </div>
                <div className="flex items-center justify-between pt-3 border-t border-[var(--border-primary)]">
                  <span className="text-sm text-secondary">{scan.issues_count} issues</span>
                  <div className="flex items-center space-x-1">
                    <button
                      onClick={() => navigate(`/scan/${scan.id}`)}
                      className="p-2 text-tertiary hover:text-accent transition-colors rounded-lg hover:bg-[var(--surface-tertiary)]"
                      aria-label={`View details for ${scan.filename}`}
                    >
                      <Eye className="w-4 h-4" aria-hidden="true" />
                    </button>
                    <button
                      onClick={() => handleDownloadReport(scan)}
                      disabled={downloadingReport === scan.id}
                      className="p-2 text-tertiary hover:text-accent transition-colors rounded-lg hover:bg-[var(--surface-tertiary)] disabled:opacity-50"
                      aria-label={`Download report for ${scan.filename}`}
                    >
                      {downloadingReport === scan.id ? (
                        <Loader className="w-4 h-4 animate-spin" aria-hidden="true" />
                      ) : (
                        <Download className="w-4 h-4" aria-hidden="true" />
                      )}
                    </button>
                    <button
                      onClick={() => handleDownloadFixes(scan)}
                      disabled={downloadingFixes === scan.id}
                      className="p-2 text-tertiary hover:text-[var(--content-success)] transition-colors rounded-lg hover:bg-[var(--surface-tertiary)] disabled:opacity-50"
                      aria-label={`Download remediated file for ${scan.filename}`}
                    >
                      {downloadingFixes === scan.id ? (
                        <Loader className="w-4 h-4 animate-spin" aria-hidden="true" />
                      ) : (
                        <FileCode className="w-4 h-4" aria-hidden="true" />
                      )}
                    </button>
                    <button
                      onClick={() => setDeleteConfirm(scan)}
                      className="p-2 text-tertiary hover:text-[var(--content-error)] transition-colors rounded-lg hover:bg-[var(--surface-tertiary)]"
                      aria-label={`Delete ${scan.filename}`}
                    >
                      <Trash2 className="w-4 h-4" aria-hidden="true" />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Scans - Desktop Table View */}
        {filteredScans.length > 0 && (
          <div className="hidden lg:block card overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full">
                <caption className="sr-only">
                  Scan history showing {filteredScans.length} {filter === 'all' ? '' : filter + ' '}scans
                </caption>
                <thead>
                  <tr>
                    <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-tertiary uppercase tracking-wider">
                      File Name
                    </th>
                    <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-tertiary uppercase tracking-wider">
                      Type
                    </th>
                    <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-tertiary uppercase tracking-wider">
                      Uploaded
                    </th>
                    <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-tertiary uppercase tracking-wider">
                      Score
                    </th>
                    <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-tertiary uppercase tracking-wider">
                      Issues
                    </th>
                    <th scope="col" className="px-6 py-3 text-right text-xs font-medium text-tertiary uppercase tracking-wider">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {filteredScans.map((scan) => (
                    <tr key={scan.id}>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <div className="flex items-center space-x-3">
                          <FileText className="w-5 h-5 text-tertiary" aria-hidden="true" />
                          <span className="text-sm font-medium text-primary">
                            {scan.filename}
                          </span>
                        </div>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <span className={`px-2 py-1 text-xs font-medium rounded ${getTypeColor(scan.type)}`}>
                          {scan.type.toUpperCase()}
                        </span>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <div className="flex items-center space-x-2 text-sm text-tertiary">
                          <Calendar className="w-4 h-4" aria-hidden="true" />
                          <span>{formatDate(scan.uploaded_at)}</span>
                        </div>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <span className={`px-3 py-1 text-sm font-semibold rounded-full ${getScoreColor(scan.compliance_score)}`}>
                          {scan.compliance_score}/100
                        </span>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <span className="text-sm text-primary">{scan.issues_count} issues</span>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-right">
                        <div className="flex items-center justify-end space-x-2">
                          <button
                            onClick={() => navigate(`/scan/${scan.id}`)}
                            className="p-2 text-tertiary hover:text-accent transition-colors rounded-lg hover:bg-[var(--surface-tertiary)]"
                            aria-label={`View details for ${scan.filename}`}
                          >
                            <Eye className="w-5 h-5" aria-hidden="true" />
                          </button>
                          <button
                            onClick={() => handleDownloadReport(scan)}
                            disabled={downloadingReport === scan.id}
                            className="p-2 text-tertiary hover:text-accent transition-colors rounded-lg hover:bg-[var(--surface-tertiary)] disabled:opacity-50"
                            aria-label={`Download PDF report for ${scan.filename}`}
                          >
                            {downloadingReport === scan.id ? (
                              <Loader className="w-5 h-5 animate-spin" aria-hidden="true" />
                            ) : (
                              <Download className="w-5 h-5" aria-hidden="true" />
                            )}
                          </button>
                          <button
                            onClick={() => handleDownloadFixes(scan)}
                            disabled={downloadingFixes === scan.id}
                            className="p-2 text-tertiary hover:text-[var(--content-success)] transition-colors rounded-lg hover:bg-[var(--surface-tertiary)] disabled:opacity-50"
                            aria-label={`Download remediated file for ${scan.filename}`}
                          >
                            {downloadingFixes === scan.id ? (
                              <Loader className="w-5 h-5 animate-spin" aria-hidden="true" />
                            ) : (
                              <FileCode className="w-5 h-5" aria-hidden="true" />
                            )}
                          </button>
                          <button
                            onClick={() => setDeleteConfirm(scan)}
                            className="p-2 text-tertiary hover:text-[var(--content-error)] transition-colors rounded-lg hover:bg-[var(--surface-tertiary)]"
                            aria-label={`Delete ${scan.filename}`}
                          >
                            <Trash2 className="w-5 h-5" aria-hidden="true" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Empty state - no scans at all */}
        {filteredScans.length === 0 && scans.length === 0 && (
          <div className="card text-center py-12">
            <Upload className="w-12 h-12 mx-auto text-tertiary mb-4" aria-hidden="true" />
            <h2 className="text-xl font-semibold text-primary mb-2">No scans yet</h2>
            <p className="text-tertiary mb-6 max-w-md mx-auto">
              Upload your first document to scan it for accessibility issues.
              We support PDFs, PowerPoint, Word, Excel, LaTeX, images, videos, and websites.
            </p>
            <button
              onClick={() => navigate('/upload')}
              className="btn-primary inline-flex items-center gap-2"
            >
              <Upload className="w-4 h-4" aria-hidden="true" />
              Upload Your First File
            </button>
          </div>
        )}

        {/* Empty state - no scans matching filter */}
        {filteredScans.length === 0 && scans.length > 0 && (
          <div className="card text-center py-12">
            <FileText className="w-12 h-12 mx-auto text-tertiary mb-4" aria-hidden="true" />
            <h2 className="text-xl font-semibold text-primary mb-2">No {filter} scans found</h2>
            <p className="text-tertiary mb-4">
              You have {scans.length} scan{scans.length !== 1 ? 's' : ''} but none match the "{filter}" filter.
            </p>
            <button
              onClick={() => setFilter('all')}
              className="text-accent hover:underline"
            >
              Show all scans
            </button>
          </div>
        )}

        {/* Delete confirmation modal */}
        {deleteConfirm && (
          <DeleteConfirmModal
            scan={deleteConfirm}
            onConfirm={handleDeleteScan}
            onCancel={() => setDeleteConfirm(null)}
            isDeleting={deleting}
          />
        )}
      </div>
    </div>
  );
}
