import React, { useState, useEffect, useCallback, ChangeEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ClipboardCheck,
  Filter,
  ChevronRight,
  ChevronLeft,
  CheckCircle2,
  Clock,
  XCircle,
  Loader,
} from 'lucide-react';
import { AxiosError } from 'axios';
import { apiClient } from '../api/client';
import { useToast } from '../context/toast-context';
import { ConfidenceBadge } from '../components/review/ConfidenceBadge';
import type { ReviewQueueStatus } from '../utils/reviewState';

// ============================================================================
// Types
// ============================================================================

interface QueueItem {
  scan_id: string;
  file_name: string;
  department_id: string | null;
  scan_type: string | null;
  total_fixes: number;
  needs_review_count: number;
  lowest_confidence: number;
  status: ReviewQueueStatus;
  created_at: string;
}

interface QueueStats {
  pending: number;
  approved: number;
  rejected: number;
  total: number;
  by_type?: Record<string, number>;
}

interface QueueResponse {
  items: QueueItem[];
  total: number;
  has_more: boolean;
}

interface BatchResponse {
  affected: number;
}

type StatusFilter = 'all' | ReviewQueueStatus;
type ScanTypeFilter = 'all' | 'pdf' | 'word' | 'excel' | 'powerpoint' | 'latex' | 'web' | 'code' | 'multimedia';

const PAGE_SIZE = 20;

// ============================================================================
// Component
// ============================================================================

export function ReviewQueuePage(): React.ReactElement {
  const navigate = useNavigate();
  const toast = useToast();

  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [stats, setStats] = useState<QueueStats>({ pending: 0, approved: 0, rejected: 0, total: 0 });
  const [queueTotal, setQueueTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [scanTypeFilter, setScanTypeFilter] = useState<ScanTypeFilter>('all');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [batchLoading, setBatchLoading] = useState(false);
  const [page, setPage] = useState(1);

  // Shared fetch logic for queue and stats
  const fetchQueue = useCallback(async (): Promise<void> => {
    try {
      setLoading(true);
      const params = new URLSearchParams();
      if (statusFilter !== 'all') {
        params.set('status', statusFilter);
      }
      if (scanTypeFilter !== 'all') {
        params.set('scan_type', scanTypeFilter);
      }
      params.set('offset', String((page - 1) * PAGE_SIZE));
      params.set('limit', String(PAGE_SIZE));
      const queryString = params.toString();

      const [queueRes, statsRes] = await Promise.all([
        apiClient.get<QueueResponse>(`/api/reviews/queue?${queryString}`),
        apiClient.get<QueueStats>('/api/reviews/queue/stats'),
      ]);

      setQueue(queueRes.data.items);
      setQueueTotal(queueRes.data.total);
      setHasMore(queueRes.data.has_more);
      setStats(statsRes.data);
      setError(null);
    } catch (err: unknown) {
      console.error('Failed to fetch review queue:', err);
      const message = err instanceof AxiosError
        ? err.response?.data?.detail || err.message
        : err instanceof Error
          ? err.message
          : 'An unexpected error occurred';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [statusFilter, scanTypeFilter, page]);

  // Fetch queue and stats
  useEffect(() => {
    fetchQueue();
  }, [fetchQueue]);

  // Toggle selection
  const toggleSelect = (scanId: string): void => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(scanId)) {
        next.delete(scanId);
      } else {
        next.add(scanId);
      }
      return next;
    });
  };

  // Toggle select all
  const toggleSelectAll = (): void => {
    if (selectedIds.size === queue.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(queue.map((item) => item.scan_id)));
    }
  };

  // Batch approve with confidence threshold
  const handleBatchApprove = async (minConfidence: number): Promise<void> => {
    if (selectedIds.size === 0) return;
    setBatchLoading(true);
    try {
      const promises = Array.from(selectedIds).map((scanId) =>
        apiClient.post<BatchResponse>(`/api/reviews/${scanId}/batch`, { action: 'approve', min_confidence: minConfidence })
      );
      const responses = await Promise.all(promises);
      const affected = responses.reduce((total, response) => total + response.data.affected, 0);
      const pct = Math.round(minConfidence * 100);
      if (affected > 0) {
        toast.success(`Approved ${affected} fix${affected === 1 ? '' : 'es'} at or above ${pct}% confidence`, 'Batch Approve');
      } else {
        toast.warning(`No pending fixes met the ${pct}% confidence threshold`, 'Batch Approve');
      }
      setSelectedIds(new Set());
      await fetchQueue();
    } catch (err: unknown) {
      const message = err instanceof AxiosError
        ? err.response?.data?.detail || err.message
        : err instanceof Error
          ? err.message
          : 'An unexpected error occurred';
      toast.error(message, 'Error');
    } finally {
      setBatchLoading(false);
    }
  };

  // Format date for display
  const formatDate = (dateString: string): string => {
    return new Date(dateString).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  // Status badge styling
  const getStatusStyle = (status: ReviewQueueStatus): { label: string; color: string; bg: string } => {
    switch (status) {
      case 'pending':
        return { label: 'Pending', color: 'text-[var(--feature-warning-content)]', bg: 'bg-[var(--feature-warning-surface)]' };
      case 'approved':
        return { label: 'Approved', color: 'text-[var(--feature-success-content)]', bg: 'bg-[var(--feature-success-surface)]' };
      case 'rejected':
        return { label: 'Rejected', color: 'text-[var(--feature-danger-content)]', bg: 'bg-[var(--feature-danger-surface)]' };
      default:
        return { label: status, color: 'text-[var(--content-tertiary)]', bg: 'bg-[var(--surface-tertiary)]' };
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64" role="status" aria-label="Loading review queue">
        <Loader className="w-8 h-8 animate-spin text-accent" aria-hidden="true" />
        <span className="sr-only">Loading review queue...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-8">
        <div className="max-w-7xl mx-auto">
          <div
            className="rounded-lg p-4 bg-[var(--feature-danger-surface)] border border-[var(--feature-danger-content)] text-[var(--feature-danger-content)]"
            role="alert"
          >
            Error: {error}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="p-8">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <ClipboardCheck className="w-8 h-8 text-[var(--accent)]" aria-hidden="true" />
            <h1 className="text-3xl font-bold text-primary">Review Queue</h1>
          </div>
        </div>

        {/* Stats Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
          <div className="card">
            <div className="flex items-center gap-2 mb-1">
              <Clock className="w-4 h-4 text-[var(--feature-warning-content)]" aria-hidden="true" />
              <p className="text-sm text-tertiary">Pending</p>
            </div>
            <p className="text-2xl font-bold text-[var(--feature-warning-content)]">{stats.pending}</p>
          </div>
          <div className="card">
            <div className="flex items-center gap-2 mb-1">
              <CheckCircle2 className="w-4 h-4 text-[var(--feature-success-content)]" aria-hidden="true" />
              <p className="text-sm text-tertiary">Approved</p>
            </div>
            <p className="text-2xl font-bold text-[var(--feature-success-content)]">{stats.approved}</p>
          </div>
          <div className="card">
            <div className="flex items-center gap-2 mb-1">
              <XCircle className="w-4 h-4 text-[var(--feature-danger-content)]" aria-hidden="true" />
              <p className="text-sm text-tertiary">Rejected</p>
            </div>
            <p className="text-2xl font-bold text-[var(--feature-danger-content)]">{stats.rejected}</p>
          </div>
        </div>

        {/* Filter */}
        <div className="card mb-6">
          <div className="flex items-center gap-4">
            <Filter className="w-4 h-4 text-tertiary" aria-hidden="true" />
            <label htmlFor="status-filter" className="text-sm font-medium text-secondary">Status:</label>
            <select
              id="status-filter"
              value={statusFilter}
              onChange={(e: ChangeEvent<HTMLSelectElement>) => {
                setStatusFilter(e.target.value as StatusFilter);
                setSelectedIds(new Set());
                setPage(1);
              }}
              className="input py-1.5 text-sm"
              aria-label="Filter by review status"
            >
              <option value="all">All ({stats.total})</option>
              <option value="pending">Pending ({stats.pending})</option>
              <option value="approved">Approved ({stats.approved})</option>
              <option value="rejected">Rejected ({stats.rejected})</option>
            </select>
            <label htmlFor="type-filter" className="text-sm font-medium text-secondary ml-4">Type:</label>
            <select
              id="type-filter"
              value={scanTypeFilter}
              onChange={(e: ChangeEvent<HTMLSelectElement>) => {
                setScanTypeFilter(e.target.value as ScanTypeFilter);
                setSelectedIds(new Set());
                setPage(1);
              }}
              className="input py-1.5 text-sm"
              aria-label="Filter by scan type"
            >
              <option value="all">All Types</option>
              <option value="pdf">PDF</option>
              <option value="word">Word</option>
              <option value="excel">Excel</option>
              <option value="powerpoint">PowerPoint</option>
              <option value="latex">LaTeX</option>
              <option value="web">Web</option>
              <option value="code">Code</option>
              <option value="multimedia">Multimedia</option>
            </select>
          </div>
        </div>

        {/* Queue Table */}
        {queue.length === 0 ? (
          <div className="card text-center py-12">
            <CheckCircle2 className="w-12 h-12 mx-auto text-[var(--feature-success-content)] mb-4" aria-hidden="true" />
            <p className="text-lg font-medium text-primary mb-2">No documents to review</p>
            <p className="text-tertiary">
              {statusFilter !== 'all' || scanTypeFilter !== 'all'
                ? 'Try changing the filters to see more documents.'
                : 'Documents with fixes needing review will appear here.'}
            </p>
          </div>
        ) : (
          <div className="card overflow-hidden p-0">
            {/* Table Header */}
            <div className="flex items-center px-4 py-3 border-b border-[var(--border-primary)] bg-[var(--surface-secondary)]">
              <div className="w-10">
                <input
                  type="checkbox"
                  checked={selectedIds.size === queue.length && queue.length > 0}
                  onChange={toggleSelectAll}
                  className="rounded border-[var(--border-primary)]"
                  aria-label="Select all documents"
                />
              </div>
              <div className="flex-1 text-xs font-medium text-[var(--content-tertiary)] uppercase">Document</div>
              <div className="w-28 text-xs font-medium text-[var(--content-tertiary)] uppercase text-center">Fixes</div>
              <div className="w-28 text-xs font-medium text-[var(--content-tertiary)] uppercase text-center">Review</div>
              <div className="w-32 text-xs font-medium text-[var(--content-tertiary)] uppercase text-center">Confidence</div>
              <div className="w-28 text-xs font-medium text-[var(--content-tertiary)] uppercase text-center">Status</div>
              <div className="w-32 text-xs font-medium text-[var(--content-tertiary)] uppercase text-right">Date</div>
              <div className="w-8" />
            </div>

            {/* Table Rows */}
            {queue.map((item) => {
              const statusStyle = getStatusStyle(item.status);
              return (
                <div
                  key={item.scan_id}
                  className="flex items-center px-4 py-3 border-b border-[var(--border-primary)] last:border-b-0 hover:bg-[var(--surface-secondary)] focus:bg-[var(--surface-secondary)] focus:outline-none focus:ring-2 focus:ring-[var(--accent)] transition-colors cursor-pointer"
                  onClick={() => navigate(`/review/${item.scan_id}`)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      navigate(`/review/${item.scan_id}`);
                    }
                  }}
                  tabIndex={0}
                  role="row"
                  aria-label={`Review ${item.file_name}`}
                >
                  <div className="w-10" onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selectedIds.has(item.scan_id)}
                      onChange={() => toggleSelect(item.scan_id)}
                      className="rounded border-[var(--border-primary)]"
                      aria-label={`Select ${item.file_name}`}
                    />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-primary truncate">{item.file_name}</p>
                    <div className="flex items-center gap-2">
                      {item.department_id && (
                        <p className="text-xs text-tertiary truncate">{item.department_id}</p>
                      )}
                      {item.scan_type && (
                        <span className="text-xs font-medium px-1.5 py-0.5 rounded bg-[var(--surface-tertiary)] text-[var(--content-secondary)]">
                          {item.scan_type.toUpperCase()}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="w-28 text-sm text-center text-secondary">{item.total_fixes}</div>
                  <div className="w-28 text-sm text-center">
                    {item.needs_review_count > 0 ? (
                      <span className="text-[var(--feature-warning-content)] font-medium">{item.needs_review_count}</span>
                    ) : (
                      <span className="text-[var(--feature-success-content)]">0</span>
                    )}
                  </div>
                  <div className="w-32 flex justify-center">
                    <ConfidenceBadge confidence={item.lowest_confidence} size="sm" />
                  </div>
                  <div className="w-28 text-center">
                    <span className={`text-xs font-medium px-2 py-0.5 rounded ${statusStyle.bg} ${statusStyle.color}`}>
                      {statusStyle.label}
                    </span>
                  </div>
                  <div className="w-32 text-xs text-tertiary text-right">{formatDate(item.created_at)}</div>
                  <div className="w-8 flex justify-center">
                    <ChevronRight className="w-4 h-4 text-[var(--content-tertiary)]" aria-hidden="true" />
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Pagination controls */}
        {queue.length > 0 && (
          <div className="flex items-center justify-center gap-4 mt-4">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="btn-secondary text-sm py-1.5 px-3 flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed"
              aria-label="Previous page"
            >
              <ChevronLeft className="w-4 h-4" aria-hidden="true" />
              Previous
            </button>
            <span className="text-sm text-secondary">
              Page {page} of {Math.max(1, Math.ceil(queueTotal / PAGE_SIZE))}
            </span>
            <button
              onClick={() => setPage((p) => p + 1)}
              disabled={!hasMore}
              className="btn-secondary text-sm py-1.5 px-3 flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed"
              aria-label="Next page"
            >
              Next
              <ChevronRight className="w-4 h-4" aria-hidden="true" />
            </button>
          </div>
        )}
      </div>

      {/* Batch Operations Bar */}
      {selectedIds.size > 0 && (
        <div
          className="fixed bottom-0 left-0 right-0 z-30 px-8 py-4"
          style={{ backgroundColor: 'var(--surface-secondary)', borderTop: '1px solid var(--border-primary)' }}
        >
          <div className="max-w-7xl mx-auto flex items-center justify-between">
            <span className="text-sm text-secondary">
              {selectedIds.size} document{selectedIds.size !== 1 ? 's' : ''} selected
            </span>
            <div className="flex items-center gap-3">
              <button
                onClick={() => setSelectedIds(new Set())}
                className="btn-secondary text-sm py-1.5 px-3"
              >
                Clear Selection
              </button>
              <button
                onClick={() => handleBatchApprove(0.85)}
                disabled={batchLoading}
                className="btn-primary text-sm py-1.5 px-4 flex items-center gap-2 disabled:opacity-50"
                aria-label={`Approve fixes with 85% or higher confidence for ${selectedIds.size} selected documents`}
              >
                {batchLoading ? (
                  <Loader className="w-4 h-4 animate-spin" aria-hidden="true" />
                ) : (
                  <CheckCircle2 className="w-4 h-4" aria-hidden="true" />
                )}
                Approve fixes &ge;85%
              </button>
              <button
                onClick={() => handleBatchApprove(0.70)}
                disabled={batchLoading}
                className="btn-secondary text-sm py-1.5 px-4 flex items-center gap-2 disabled:opacity-50"
                aria-label={`Approve fixes with 70% or higher confidence for ${selectedIds.size} selected documents`}
              >
                {batchLoading ? (
                  <Loader className="w-4 h-4 animate-spin" aria-hidden="true" />
                ) : (
                  <CheckCircle2 className="w-4 h-4" aria-hidden="true" />
                )}
                Approve fixes &ge;70%
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
