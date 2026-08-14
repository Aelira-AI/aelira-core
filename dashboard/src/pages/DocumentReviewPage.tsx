import React, { useState, useEffect, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  CheckCircle2,
  Loader,
  FileText,
  Table2,
  Layers,
} from 'lucide-react';
import { AxiosError } from 'axios';
import { apiClient } from '../api/client';
import { useToast } from '../context/toast-context';
import { FixCard } from '../components/review/FixCard';
import { MatterhornResultsBar } from '../components/review/MatterhornResultsBar';
import { TableStructureEditor } from '../components/review/TableStructureEditor';
import { ReadingOrderOverlay } from '../components/review/ReadingOrderOverlay';
import type { Fix } from '../components/review/FixCard';
import type { TableStructure } from '../components/review/tableStructureUtils';
import type { ReadingOrderData } from '../components/review/readingOrderUtils';

// ============================================================================
// Types
// ============================================================================

interface DocumentReview {
  scan_id: string;
  file_name: string;
  status: string;
  fixes: Fix[];
  matterhorn_total: number;
  matterhorn_passed: number;
  matterhorn_failed: number;
  compliance_level: string;
  total_fixes: number;
  needs_review_count: number;
  auto_approved_count: number;
  reviewed_count: number;
}

type FixFilter = 'all' | 'needs_review' | 'auto_approved' | 'reviewed';
type VisualTab = 'preview' | 'table' | 'reading-order';

// ============================================================================
// Demo data for visual tools (will be replaced with real PDF data)
// ============================================================================

const demoTableStructure: TableStructure = {
  rows: 4,
  cols: 3,
  header_rows: 1,
  header_cols: 0,
  cells: [
    { row: 0, col: 0, is_header: true, scope: 'Column', text: 'Course' },
    { row: 0, col: 1, is_header: true, scope: 'Column', text: 'Instructor' },
    { row: 0, col: 2, is_header: true, scope: 'Column', text: 'Enrollment' },
    { row: 1, col: 0, is_header: false, text: 'CS 101' },
    { row: 1, col: 1, is_header: false, text: 'Dr. Smith' },
    { row: 1, col: 2, is_header: false, text: '150' },
    { row: 2, col: 0, is_header: false, text: 'MATH 200' },
    { row: 2, col: 1, is_header: false, text: 'Dr. Jones' },
    { row: 2, col: 2, is_header: false, text: '85' },
    { row: 3, col: 0, is_header: false, text: 'ENG 101' },
    { row: 3, col: 1, is_header: false, text: 'Prof. Lee' },
    { row: 3, col: 2, is_header: false, text: '120' },
  ],
};

const demoReadingOrderData: ReadingOrderData = {
  pageWidth: 612,
  pageHeight: 792,
  blocks: [
    { index: 0, bbox: [50, 50, 562, 90], text: 'Document Title', pageNum: 1 },
    { index: 1, bbox: [50, 110, 270, 190], text: 'Introduction paragraph with key information...', pageNum: 1 },
    { index: 2, bbox: [300, 110, 562, 190], text: 'Figure 1: Chart', pageNum: 1 },
    { index: 3, bbox: [50, 210, 562, 330], text: 'Table 1: Course Data', pageNum: 1 },
    { index: 4, bbox: [50, 350, 562, 410], text: 'Summary paragraph...', pageNum: 1 },
  ],
  originalOrder: [0, 2, 1, 3, 4],
  newOrder: [0, 1, 2, 3, 4],
};

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
  const [visualTab, setVisualTab] = useState<VisualTab>('preview');

  // Fetch document review data
  useEffect(() => {
    const fetchReview = async (): Promise<void> => {
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
    };

    fetchReview();
  }, [scanId]);

  // Filter fixes
  const filteredFixes = useMemo(() => {
    if (!review) return [];
    switch (fixFilter) {
      case 'needs_review':
        return review.fixes.filter((f) => f.needs_review && f.review_status === 'pending');
      case 'auto_approved':
        return review.fixes.filter((f) => !f.needs_review && f.review_status === 'auto_approved');
      case 'reviewed':
        return review.fixes.filter((f) =>
          f.review_status === 'approved' || f.review_status === 'rejected' || f.review_status === 'edited'
        );
      default:
        return review.fixes;
    }
  }, [review, fixFilter]);

  // Handle individual fix approve
  const handleApprove = async (fixId: string, editedContent?: string, notes?: string): Promise<void> => {
    if (!scanId) return;
    try {
      const action = editedContent ? 'edit' : 'approve';
      await apiClient.post(`/api/reviews/${scanId}/fixes/${fixId}`, {
        action,
        edited_content: editedContent,
        notes,
      });

      // Update local state
      setReview((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          fixes: prev.fixes.map((f) =>
            f.id === fixId
              ? {
                  ...f,
                  review_status: editedContent ? 'edited' : 'approved',
                  needs_review: false,
                  fixed_content: editedContent || f.fixed_content,
                }
              : f
          ),
          needs_review_count: prev.needs_review_count - 1,
          reviewed_count: prev.reviewed_count + 1,
        };
      });

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
      await apiClient.post(`/api/reviews/${scanId}/fixes/${fixId}`, {
        action: 'reject',
        notes,
      });

      // Update local state
      setReview((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          fixes: prev.fixes.map((f) =>
            f.id === fixId
              ? { ...f, review_status: 'rejected', needs_review: false }
              : f
          ),
          needs_review_count: prev.needs_review_count - 1,
          reviewed_count: prev.reviewed_count + 1,
        };
      });

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

  // Handle approve all
  const handleApproveAll = async (): Promise<void> => {
    if (!scanId) return;
    setApproveAllLoading(true);
    try {
      await apiClient.post(`/api/reviews/${scanId}/batch`, { action: 'approve_all' });

      // Update local state - mark all pending as approved
      setReview((prev) => {
        if (!prev) return prev;
        const updatedFixes = prev.fixes.map((f) =>
          f.needs_review && f.review_status === 'pending'
            ? { ...f, review_status: 'approved', needs_review: false }
            : f
        );
        return {
          ...prev,
          fixes: updatedFixes,
          needs_review_count: 0,
          reviewed_count: updatedFixes.filter((f) =>
            f.review_status === 'approved' || f.review_status === 'rejected' || f.review_status === 'edited'
          ).length,
        };
      });

      toast.success('All pending fixes approved', 'Batch Approve');
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

  const needsReviewCount = review.fixes.filter((f) => f.needs_review && f.review_status === 'pending').length;

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)]">
      {/* Top bar */}
      <div
        className="flex items-center justify-between px-6 py-3 shrink-0"
        style={{ backgroundColor: 'var(--surface-secondary)', borderBottom: '1px solid var(--border-primary)' }}
      >
        <div className="flex items-center gap-4 min-w-0">
          <button
            onClick={() => navigate('/review')}
            className="p-1.5 rounded hover:bg-[var(--surface-tertiary)] transition-colors"
            aria-label="Back to review queue"
          >
            <ArrowLeft className="w-5 h-5 text-[var(--content-secondary)]" aria-hidden="true" />
          </button>
          <div className="flex items-center gap-2 min-w-0">
            <FileText className="w-5 h-5 text-[var(--accent-primary)] shrink-0" aria-hidden="true" />
            <h1 className="text-lg font-semibold text-primary truncate">{review.file_name}</h1>
          </div>
          <div className="flex items-center gap-4 text-sm text-secondary shrink-0">
            <span>{review.total_fixes} fixes</span>
            <span className="text-[var(--border-primary)]">|</span>
            {needsReviewCount > 0 ? (
              <span className="text-[var(--feature-warning-content)] font-medium">{needsReviewCount} need review</span>
            ) : (
              <span className="text-[var(--feature-success-content)]">All reviewed</span>
            )}
          </div>
        </div>
        {needsReviewCount > 0 && (
          <button
            onClick={handleApproveAll}
            disabled={approveAllLoading}
            className="btn-primary text-sm py-1.5 px-4 flex items-center gap-2 disabled:opacity-50 shrink-0"
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

      {/* Main content - split view */}
      <div className="flex flex-1 min-h-0">
        {/* Left panel - Visual tools */}
        <div
          className="hidden lg:flex lg:flex-col w-1/2 border-r border-[var(--border-primary)]"
          style={{ backgroundColor: 'var(--surface-tertiary)' }}
        >
          {/* Tab bar */}
          <div
            className="flex items-center gap-1 px-3 py-2 shrink-0"
            style={{ borderBottom: '1px solid var(--border-primary)', backgroundColor: 'var(--surface-secondary)' }}
            role="tablist"
            aria-label="Visual tools"
          >
            {([
              { key: 'preview' as VisualTab, label: 'Preview', Icon: FileText },
              { key: 'table' as VisualTab, label: 'Table Editor', Icon: Table2 },
              { key: 'reading-order' as VisualTab, label: 'Reading Order', Icon: Layers },
            ]).map(({ key, label, Icon }) => (
              <button
                key={key}
                role="tab"
                aria-selected={visualTab === key}
                onClick={() => setVisualTab(key)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                  visualTab === key
                    ? 'bg-[var(--accent-primary)] text-white'
                    : 'text-[var(--content-secondary)] hover:bg-[var(--surface-tertiary)]'
                }`}
              >
                <Icon className="w-4 h-4" aria-hidden="true" />
                {label}
              </button>
            ))}
          </div>

          {/* Tab content */}
          <div className="flex-1 overflow-y-auto">
            {visualTab === 'preview' && (
              <div className="flex items-center justify-center h-full">
                <div className="text-center p-8">
                  <FileText className="w-16 h-16 mx-auto mb-4 text-[var(--content-tertiary)] opacity-40" aria-hidden="true" />
                  <p className="text-lg font-medium text-[var(--content-tertiary)]">PDF Preview</p>
                  <p className="text-sm text-[var(--content-tertiary)] mt-1">Upload a PDF to see a preview here</p>
                </div>
              </div>
            )}

            {visualTab === 'table' && (
              <div className="p-4">
                <TableStructureEditor
                  structure={demoTableStructure}
                  onChange={(updated: TableStructure) => {
                    console.log('Table structure updated:', updated);
                    toast.success('Table structure saved', 'Table Editor');
                  }}
                />
              </div>
            )}

            {visualTab === 'reading-order' && (
              <div className="p-4">
                <ReadingOrderOverlay
                  data={demoReadingOrderData}
                  onChange={(newOrder: number[]) => {
                    console.log('Reading order updated:', newOrder);
                    toast.success('Reading order saved', 'Reading Order');
                  }}
                />
              </div>
            )}
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
                { key: 'all', label: 'All', count: review.total_fixes },
                { key: 'needs_review', label: 'Needs Review', count: needsReviewCount },
                { key: 'auto_approved', label: 'Auto-Approved', count: review.auto_approved_count },
                { key: 'reviewed', label: 'Reviewed', count: review.reviewed_count },
              ] as { key: FixFilter; label: string; count: number }[]
            ).map(({ key, label, count }) => (
              <button
                key={key}
                onClick={() => setFixFilter(key)}
                className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors whitespace-nowrap ${
                  fixFilter === key
                    ? 'bg-[var(--accent-primary)] text-white'
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
          compliance={review.compliance_level}
        />
      </div>
    </div>
  );
}
