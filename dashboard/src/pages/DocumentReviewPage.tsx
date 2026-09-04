import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  CheckCircle2,
  Loader,
  FileText,
  Download,
} from 'lucide-react';
import { AxiosError } from 'axios';
import { apiClient } from '../api/client';
import { useToast } from '../context/toast-context';
import { FixCard } from '../components/review/FixCard';
import { MatterhornResultsBar } from '../components/review/MatterhornResultsBar';
import type { Fix } from '../components/review/FixCard';
import {
  getDeferralLifecycle,
  isAttentionRequired,
  isHumanReviewedStatus,
  summarizeReviewFixes,
} from '../utils/reviewState';
import type { ReviewQueueStatus } from '../utils/reviewState';
import {
  evidenceContentType,
  evidenceFilename,
} from '../utils/reviewEvidenceDownload';
import type { ReviewEvidenceFormat } from '../utils/reviewEvidenceDownload';

// ============================================================================
// Types
// ============================================================================

interface DocumentReview {
  scan_id: string;
  file_name: string;
  status: ReviewQueueStatus;
  fixes: Fix[];
  matterhorn_total: number;
  matterhorn_passed: number;
  matterhorn_failed: number;
  validator_result: string;
  total_fixes: number;
  needs_review_count: number;
  auto_approved_count: number;
  reviewed_count: number;
}

interface ReviewResponse {
  review_status: string;
}

interface BatchResponse {
  affected: number;
}

type FixFilter =
  | 'all'
  | 'needs_review'
  | 'auto_approved'
  | 'reviewed'
  | 'deferred_active'
  | 'deferred_expired'
  | 'deferred_revoked'
  | 'deferred_resolved';

const EVIDENCE_FORMATS: { value: ReviewEvidenceFormat; label: string }[] = [
  { value: 'json', label: 'JSON' },
  { value: 'csv', label: 'CSV' },
  { value: 'pdf', label: 'PDF' },
];

// ============================================================================
// Component
// ============================================================================

export function DocumentReviewPage(): React.ReactElement {
  const { scanId } = useParams<{ scanId: string }>();
  const navigate = useNavigate();
  const toast = useToast();

  const [review, setReview] = useState<DocumentReview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [fixFilter, setFixFilter] = useState<FixFilter>('all');
  const [approveAllLoading, setApproveAllLoading] = useState(false);
  const [downloadingFormat, setDownloadingFormat] = useState<ReviewEvidenceFormat | null>(null);

  // Fetch document review data
  const fetchReview = useCallback(async (): Promise<void> => {
    if (!scanId) return;
    try {
      setLoading(true);
      const response = await apiClient.get<DocumentReview>(`/api/reviews/${scanId}`);
      setReview(response.data);
      setError(null);
    } catch (err: unknown) {
      console.error('Failed to fetch review:', err);
      const message = err instanceof AxiosError
        ? err.response?.data?.detail || err.message
        : err instanceof Error
          ? err.message
          : 'An unexpected error occurred';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [scanId]);

  useEffect(() => {
    fetchReview();
  }, [fetchReview]);

  const summary = useMemo(
    () => summarizeReviewFixes(review?.fixes ?? []),
    [review?.fixes],
  );

  // Filter fixes
  const filteredFixes = useMemo(() => {
    if (!review) return [];
    switch (fixFilter) {
      case 'needs_review':
        return review.fixes.filter((f) => isAttentionRequired(f));
      case 'auto_approved':
        return review.fixes.filter((f) => f.review_status === 'auto_approved');
      case 'reviewed':
        return review.fixes.filter((f) => isHumanReviewedStatus(f.review_status));
      case 'deferred_active':
        return review.fixes.filter((f) => getDeferralLifecycle(f.deferral) === 'active');
      case 'deferred_expired':
        return review.fixes.filter((f) => getDeferralLifecycle(f.deferral) === 'expired');
      case 'deferred_revoked':
        return review.fixes.filter((f) => getDeferralLifecycle(f.deferral) === 'revoked');
      case 'deferred_resolved':
        return review.fixes.filter((f) => getDeferralLifecycle(f.deferral) === 'resolved');
      default:
        return review.fixes;
    }
  }, [review, fixFilter]);

  // Handle individual fix approve
  const handleApprove = async (fixId: string, editedContent?: string, notes?: string): Promise<void> => {
    if (!scanId) return;
    try {
      const action = editedContent ? 'edit' : 'approve';
      await apiClient.post<ReviewResponse>(`/api/reviews/${scanId}/fixes/${fixId}`, {
        action,
        edited_content: editedContent,
        notes,
      });

      await fetchReview();

      toast.success(editedContent ? 'Fix edited and approved' : 'Fix approved', 'Review Updated');
    } catch (err: unknown) {
      const message = err instanceof AxiosError
        ? err.response?.data?.detail || err.message
        : err instanceof Error
          ? err.message
          : 'An unexpected error occurred';
      toast.error(message, 'Error');
    }
  };

  // Handle individual fix reject
  const handleReject = async (fixId: string, notes?: string): Promise<void> => {
    if (!scanId) return;
    try {
      await apiClient.post<ReviewResponse>(`/api/reviews/${scanId}/fixes/${fixId}`, {
        action: 'reject',
        notes,
      });

      await fetchReview();

      toast.success('Fix rejected', 'Review Updated');
    } catch (err: unknown) {
      const message = err instanceof AxiosError
        ? err.response?.data?.detail || err.message
        : err instanceof Error
          ? err.message
          : 'An unexpected error occurred';
      toast.error(message, 'Error');
    }
  };

  const handleDefer = async (
    fixId: string,
    owner: string,
    reason: string,
    expiresAt: string,
  ): Promise<void> => {
    if (!scanId) return;
    try {
      await apiClient.put(`/api/reviews/${scanId}/fixes/${fixId}/deferral`, {
        owner,
        reason,
        expires_at: expiresAt,
      });
      await fetchReview();
      toast.success('Deferral recorded; the finding remains unresolved', 'Review Deferred');
    } catch (err: unknown) {
      const message = err instanceof AxiosError
        ? err.response?.data?.detail || err.message
        : err instanceof Error
          ? err.message
          : 'An unexpected error occurred';
      toast.error(message, 'Deferral');
    }
  };

  const handleRevokeDeferral = async (fixId: string): Promise<void> => {
    if (!scanId) return;
    try {
      await apiClient.post(`/api/reviews/${scanId}/fixes/${fixId}/deferral/revoke`);
      await fetchReview();
      toast.success('Deferral revoked; the finding requires attention', 'Deferral Revoked');
    } catch (err: unknown) {
      const message = err instanceof AxiosError
        ? err.response?.data?.detail || err.message
        : err instanceof Error
          ? err.message
          : 'An unexpected error occurred';
      toast.error(message, 'Deferral');
    }
  };

  // Handle approve all
  const handleApproveAll = async (): Promise<void> => {
    if (!scanId) return;
    const pendingFixIds = review?.fixes
      .filter((fix) => isAttentionRequired(fix))
      .map((fix) => fix.id) ?? [];
    if (pendingFixIds.length === 0) return;
    setApproveAllLoading(true);
    try {
      const response = await apiClient.post<BatchResponse>(`/api/reviews/${scanId}/batch`, {
        action: 'approve',
        fix_ids: pendingFixIds,
      });
      await fetchReview();

      if (response.data.affected === pendingFixIds.length) {
        toast.success('All pending fixes approved', 'Batch Approve');
      } else {
        toast.warning(
          `Approved ${response.data.affected} of ${pendingFixIds.length} pending fixes. Refreshing the review.`,
          'Batch Approve',
        );
      }
    } catch (err: unknown) {
      const message = err instanceof AxiosError
        ? err.response?.data?.detail || err.message
        : err instanceof Error
          ? err.message
          : 'An unexpected error occurred';
      toast.error(message, 'Error');
    } finally {
      setApproveAllLoading(false);
    }
  };

  const handleEvidenceDownload = async (format: ReviewEvidenceFormat): Promise<void> => {
    if (!scanId || downloadingFormat) return;
    setDownloadingFormat(format);
    try {
      const response = await apiClient.get<Blob>(`/api/reviews/${scanId}/audit/export`, {
        params: { format },
        responseType: 'blob',
      });
      const contentTypeHeader = response.headers['content-type'];
      const dispositionHeader = response.headers['content-disposition'];
      const blob = new Blob([response.data], {
        type: evidenceContentType(
          typeof contentTypeHeader === 'string' ? contentTypeHeader : undefined,
          format,
        ),
      });
      const filename = evidenceFilename(
        typeof dispositionHeader === 'string' ? dispositionHeader : undefined,
        scanId,
        format,
      );
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = objectUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(objectUrl);
      toast.success(`${format.toUpperCase()} evidence downloaded`, 'Evidence Download');
    } catch (err: unknown) {
      const message = err instanceof AxiosError
        ? err.response?.data?.detail || err.message
        : err instanceof Error
          ? err.message
          : 'An unexpected error occurred';
      toast.error(message, 'Evidence Download');
    } finally {
      setDownloadingFormat(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64" role="status" aria-label="Loading document review">
        <Loader className="w-8 h-8 animate-spin text-accent" aria-hidden="true" />
        <span className="sr-only">Loading document review...</span>
      </div>
    );
  }

  if (error || !review) {
    return (
      <div className="p-8">
        <div className="max-w-6xl mx-auto">
          <div
            className="rounded-lg p-4 bg-[var(--feature-danger-surface)] border border-[var(--feature-danger-content)] text-[var(--feature-danger-content)]"
            role="alert"
          >
            {error || 'Document not found'}
          </div>
          <button onClick={() => navigate('/review')} className="btn-secondary mt-4 flex items-center gap-2">
            <ArrowLeft className="w-4 h-4" aria-hidden="true" />
            Back to Queue
          </button>
        </div>
      </div>
    );
  }

  const needsReviewCount = summary.needs_review_count;

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)]">
      {/* Top bar */}
      <div
        className="flex flex-col gap-3 px-4 py-3 shrink-0 sm:flex-row sm:items-center sm:justify-between sm:px-6"
        style={{ backgroundColor: 'var(--surface-secondary)', borderBottom: '1px solid var(--border-primary)' }}
      >
        <div className="flex items-center gap-3 min-w-0 w-full sm:w-auto sm:gap-4">
          <button
            onClick={() => navigate('/review')}
            className="p-1.5 rounded hover:bg-[var(--surface-tertiary)] transition-colors"
            aria-label="Back to review queue"
          >
            <ArrowLeft className="w-5 h-5 text-[var(--content-secondary)]" aria-hidden="true" />
          </button>
          <div className="flex items-center gap-2 min-w-0">
            <FileText className="w-5 h-5 text-[var(--accent)] shrink-0" aria-hidden="true" />
            <h1 className="text-lg font-semibold text-primary truncate">{review.file_name}</h1>
          </div>
          <div className="hidden items-center gap-4 text-sm text-secondary shrink-0 md:flex">
            <span>{summary.total_fixes} fixes</span>
            <span className="text-[var(--border-primary)]">|</span>
            {needsReviewCount > 0 ? (
              <span className="text-[var(--feature-warning-content)] font-medium">{needsReviewCount} need review</span>
            ) : (
              <span className="text-[var(--feature-success-content)]">All reviewed</span>
            )}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 w-full sm:w-auto sm:justify-end">
          <div className="flex items-center gap-1" role="group" aria-label="Download review evidence">
            {EVIDENCE_FORMATS.map(({ value, label }) => (
              <button
                key={value}
                onClick={() => handleEvidenceDownload(value)}
                disabled={downloadingFormat !== null}
                className="btn-secondary text-sm py-1.5 px-3 flex items-center gap-1.5 disabled:opacity-50"
                aria-label={`Download ${label} review evidence`}
              >
                {downloadingFormat === value ? (
                  <Loader className="w-4 h-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Download className="w-4 h-4" aria-hidden="true" />
                )}
                {downloadingFormat === value ? 'Downloading' : label}
              </button>
            ))}
          </div>
          {needsReviewCount > 0 && (
            <button
              onClick={handleApproveAll}
              disabled={approveAllLoading}
              className="btn-primary text-sm py-1.5 px-4 flex items-center gap-2 disabled:opacity-50"
              aria-label={`Approve all ${needsReviewCount} pending fixes`}
            >
              {approveAllLoading ? (
                <Loader className="w-4 h-4 animate-spin" aria-hidden="true" />
              ) : (
                <CheckCircle2 className="w-4 h-4" aria-hidden="true" />
              )}
              Approve All ({needsReviewCount})
            </button>
          )}
        </div>
      </div>

      {/* Main content - split view */}
      <div className="flex flex-1 min-h-0">
        {/* Left panel - source preview status */}
        <div
          className="hidden lg:flex lg:flex-col w-1/2 border-r border-[var(--border-primary)]"
          style={{ backgroundColor: 'var(--surface-tertiary)' }}
        >
          <div className="flex items-center justify-center h-full">
            <div className="text-center p-8 max-w-md">
              <FileText className="w-16 h-16 mx-auto mb-4 text-[var(--content-tertiary)] opacity-40" aria-hidden="true" />
              <p className="text-lg font-medium text-primary">Document preview unavailable</p>
              <p className="text-sm text-tertiary mt-2">
                This review record does not include document-bound preview, table structure, or reading-order data. Review the sourced fixes on the right.
              </p>
            </div>
          </div>
        </div>

        {/* Right panel - fix list */}
        <div className="flex-1 flex flex-col min-w-0">
          {/* Filter bar */}
          <div
            className="flex items-center gap-2 px-4 py-2 shrink-0 overflow-x-auto"
            style={{ borderBottom: '1px solid var(--border-primary)' }}
          >
            {(
              [
                { key: 'all', label: 'All', count: summary.total_fixes },
                { key: 'needs_review', label: 'Needs Review', count: needsReviewCount },
                { key: 'auto_approved', label: 'Auto-Approved', count: summary.auto_approved_count },
                { key: 'reviewed', label: 'Reviewed', count: summary.reviewed_count },
                { key: 'deferred_active', label: 'Deferred: Active', count: review.fixes.filter((fix) => getDeferralLifecycle(fix.deferral) === 'active').length },
                { key: 'deferred_expired', label: 'Deferred: Expired', count: review.fixes.filter((fix) => getDeferralLifecycle(fix.deferral) === 'expired').length },
                { key: 'deferred_revoked', label: 'Deferred: Revoked', count: review.fixes.filter((fix) => getDeferralLifecycle(fix.deferral) === 'revoked').length },
                { key: 'deferred_resolved', label: 'Deferred: Resolved', count: review.fixes.filter((fix) => getDeferralLifecycle(fix.deferral) === 'resolved').length },
              ] as { key: FixFilter; label: string; count: number }[]
            ).map(({ key, label, count }) => (
              <button
                key={key}
                onClick={() => setFixFilter(key)}
                className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors whitespace-nowrap ${
                  fixFilter === key
                    ? 'bg-[var(--accent-solid)] text-white'
                    : 'text-[var(--content-secondary)] hover:bg-[var(--surface-secondary)]'
                }`}
                aria-pressed={fixFilter === key}
              >
                {label} ({count})
              </button>
            ))}
          </div>

          {/* Fix cards */}
          <div className="flex-1 overflow-y-auto p-4 space-y-3">
            {filteredFixes.length === 0 ? (
              <div className="text-center py-12">
                <CheckCircle2 className="w-10 h-10 mx-auto text-[var(--feature-success-content)] mb-3" aria-hidden="true" />
                <p className="text-sm font-medium text-primary">
                  {fixFilter === 'needs_review'
                    ? 'No fixes need review'
                    : fixFilter === 'reviewed'
                    ? 'No fixes have been reviewed yet'
                    : 'No fixes found'}
                </p>
              </div>
            ) : (
              filteredFixes.map((fix) => (
                <FixCard
                  key={fix.id}
                  fix={fix}
                  onApprove={handleApprove}
                  onReject={handleReject}
                  onDefer={handleDefer}
                  onRevokeDeferral={handleRevokeDeferral}
                />
              ))
            )}
          </div>
        </div>
      </div>

      {/* Bottom bar - Matterhorn results */}
      <div className="shrink-0">
        <MatterhornResultsBar
          total={review.matterhorn_total}
          passed={review.matterhorn_passed}
          failed={review.matterhorn_failed}
          result={review.validator_result}
        />
      </div>
    </div>
  );
}
