import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Link, useParams, useNavigate } from 'react-router-dom';
import {
  Loader2,
  ChevronRight,
  RefreshCw,
  FileText,
  Search,
  Check,
  CheckCheck,
  AlertTriangle,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  Filter,
  Upload,
  Eye,
} from 'lucide-react';
import { useToast } from '../context/toast-context';
import { Badge } from '../components/ui/Badge';
import { Button } from '../components/ui/Button';
import {
  getCourseContentStatus,
  scanCourseContent,
  batchApproveContent,
  batchWriteBack,
  type CourseContentStatusResponse,
} from '../api/canvasContent';
import { apiClient } from '../api/client';

// ============================================================================
// Helpers
// ============================================================================

function getComplianceBadgeVariant(score: number | null): {
  label: string;
  variant: 'neutral' | 'success' | 'warning' | 'danger';
} {
  if (score === null) return { label: 'Not scanned', variant: 'neutral' };
  if (score >= 90) return { label: `${score.toFixed(0)}%`, variant: 'success' };
  if (score >= 70) return { label: `${score.toFixed(0)}%`, variant: 'warning' };
  return { label: `${score.toFixed(0)}%`, variant: 'danger' };
}

function formatContentType(type: string): string {
  const labels: Record<string, string> = {
    pages: 'Pages',
    assignments: 'Assignments',
    announcements: 'Announcements',
    discussions: 'Discussions',
    quizzes: 'Quizzes',
    files: 'Files',
    modules: 'Modules',
  };
  return labels[type] || type.charAt(0).toUpperCase() + type.slice(1);
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return '--';
  const d = new Date(dateStr);
  return d.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function getWritebackBadge(status: string | null): {
  label: string;
  variant: 'neutral' | 'accent' | 'success' | 'warning' | 'danger';
} | null {
  if (!status) return null;
  switch (status) {
    case 'approved':      return { label: 'Approved',     variant: 'accent'   };
    case 'written_back':  return { label: 'Written back', variant: 'success'  };
    case 'stale':         return { label: 'Stale',        variant: 'warning'  };
    case 'rolled_back':   return { label: 'Rolled back',  variant: 'neutral'  };
    default:              return null;
  }
}

type SortField = 'title' | 'compliance_score' | 'issue_count' | 'content_type';
type SortDir = 'asc' | 'desc';

// Cap how long we poll for scan progress before giving up and asking the
// user to refresh manually. Without this, a course whose items are skipped
// as "empty" (never reported as scanned) polls forever.
const SCAN_POLL_TIMEOUT_MS = 120_000;
const SCAN_POLL_INTERVAL_MS = 3000;

// ============================================================================
// Component
// ============================================================================

export default function CanvasContentPage(): React.ReactElement {
  const { courseId } = useParams<{ courseId: string }>();
  const navigate = useNavigate();
  const toast = useToast();

  // State
  const [data, setData] = useState<CourseContentStatusResponse | null>(null);
  const [courseName, setCourseName] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Scan state
  const [scanning, setScanning] = useState(false);
  const [scanPolling, setScanPolling] = useState(false);

  // Batch action states
  const [approvingAll, setApprovingAll] = useState(false);
  const [writingBackAll, setWritingBackAll] = useState(false);

  // Table controls
  const [searchQuery, setSearchQuery] = useState('');
  const [filterType, setFilterType] = useState<string>('all');
  const [sortField, setSortField] = useState<SortField>('compliance_score');
  const [sortDir, setSortDir] = useState<SortDir>('asc');

  // Polling ref
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollStartRef = useRef<number>(0);

  // --------------------------------------------------
  // Fetch course content status
  // --------------------------------------------------
  const fetchStatus = useCallback(async (): Promise<void> => {
    if (!courseId) return;
    try {
      const result = await getCourseContentStatus(courseId);
      setData(result);
      setError(null);
    } catch (err) {
      console.error('Failed to fetch course content status:', err);
      setError('Failed to load course content. Please try again.');
    } finally {
      setLoading(false);
    }
  }, [courseId]);

  // Fetch course name from Canvas API
  const fetchCourseName = useCallback(async (): Promise<void> => {
    if (!courseId) return;
    try {
      const res = await apiClient.get(`/canvas/courses`);
      const coursesData = res.data;
      const courses = Array.isArray(coursesData)
        ? coursesData
        : Array.isArray(coursesData?.courses)
          ? coursesData.courses
          : [];
      const course = courses.find(
        (c: { id: string; name: string }) => String(c.id) === String(courseId)
      );
      if (course) {
        setCourseName(course.name);
      }
    } catch (err) {
      console.error('Failed to fetch course name:', err);
    }
  }, [courseId]);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount; setState only happens after the awaited request resolves */
  useEffect(() => {
    fetchStatus();
    fetchCourseName();
  }, [fetchStatus, fetchCourseName]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // --------------------------------------------------
  // Polling for scan progress
  // --------------------------------------------------
  const stopPolling = useCallback((): void => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
    setScanPolling(false);
  }, []);

  const startPolling = useCallback((): void => {
    if (!courseId) return;
    stopPolling();
    setScanPolling(true);
    pollStartRef.current = Date.now();

    pollingRef.current = setInterval(async () => {
      try {
        const result = await getCourseContentStatus(courseId);
        setData(result);

        // Check if any items are still being scanned (score is null but item exists).
        // We stop polling once data stabilizes (all items have scores).
        const hasUnscanned = result.by_type.some(
          (t) => t.scanned < t.total
        );
        if (!hasUnscanned) {
          stopPolling();
          setScanning(false);
          toast.success('Content scan complete.', 'Scan Finished');
          return;
        }
      } catch (err) {
        console.error('Polling error:', err);
      }

      // Give up after SCAN_POLL_TIMEOUT_MS so a course with items that never
      // report as scanned (e.g. skipped-as-empty) doesn't poll forever.
      if (Date.now() - pollStartRef.current >= SCAN_POLL_TIMEOUT_MS) {
        stopPolling();
        setScanning(false);
        toast.warning(
          'Scan is taking longer than expected. Results will appear when ready - refresh to check.',
          'Scan Timeout'
        );
      }
    }, SCAN_POLL_INTERVAL_MS);
  }, [courseId, stopPolling, toast]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopPolling();
    };
  }, [stopPolling]);

  // --------------------------------------------------
  // Actions
  // --------------------------------------------------
  const handleScanContent = async (): Promise<void> => {
    if (!courseId) return;
    setScanning(true);
    try {
      const result = await scanCourseContent({ course_id: courseId });
      if (result.total_items === 0) {
        toast.info(
          'No pages, assignments, announcements, quizzes or discussions found in this course. The Files tab scans uploaded course files.',
          'Nothing to Scan'
        );
        setScanning(false);
        return;
      }
      toast.info(
        `Scanning ${result.total_items} items (${result.skipped} skipped, already scanned).`,
        'Scan Started'
      );
      // Start polling for progress
      startPolling();
    } catch (err) {
      console.error('Failed to start content scan:', err);
      toast.error('Failed to start content scan.', 'Error');
      setScanning(false);
    }
  };

  const handleApproveAll = async (): Promise<void> => {
    if (!data) return;

    // Get all items with scores that have been scanned but not yet approved/written back
    const eligibleIds = data.items
      .filter(
        (item) =>
          item.compliance_score !== null &&
          item.issue_count >= 0 &&
          !item.writeback_status
      )
      .map((item) => item.cloud_file_id);

    if (eligibleIds.length === 0) {
      toast.info('No items to approve.', 'Nothing to Do');
      return;
    }

    setApprovingAll(true);
    try {
      const result = await batchApproveContent({ cloud_file_ids: eligibleIds });
      toast.success(
        `Approved ${result.approved_count} item${result.approved_count !== 1 ? 's' : ''}.`,
        'Batch Approve'
      );
      // Refresh data
      await fetchStatus();
    } catch (err) {
      console.error('Failed to batch approve:', err);
      toast.error('Failed to approve items.', 'Error');
    } finally {
      setApprovingAll(false);
    }
  };

  const handleWriteBackAll = async (): Promise<void> => {
    if (!courseId) return;

    setWritingBackAll(true);
    try {
      const result = await batchWriteBack({ course_id: courseId });
      const message = `Written: ${result.written_count}, Failed: ${result.failed_count}, Stale: ${result.stale_count}`;
      if (result.failed_count > 0) {
        toast.warning(message, 'Write Back Complete');
      } else {
        toast.success(message, 'Write Back Complete');
      }
      // Refresh data
      await fetchStatus();
    } catch (err) {
      console.error('Failed to batch write back:', err);
      toast.error('Failed to write back content.', 'Error');
    } finally {
      setWritingBackAll(false);
    }
  };

  // --------------------------------------------------
  // Sorting & Filtering
  // --------------------------------------------------
  const toggleSort = (field: SortField): void => {
    if (sortField === field) {
      setSortDir((prev) => (prev === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortField(field);
      setSortDir(field === 'compliance_score' ? 'asc' : 'asc');
    }
  };

  // Plain helper (not a component) so it isn't recreated every render —
  // avoids the "component created during render" lint finding.
  const renderSortIcon = (field: SortField): React.ReactElement => {
    if (sortField !== field) {
      return <ArrowUpDown className="w-3 h-3 opacity-40" />;
    }
    return sortDir === 'asc' ? (
      <ArrowUp className="w-3 h-3" />
    ) : (
      <ArrowDown className="w-3 h-3" />
    );
  };

  const contentTypes = useMemo(() => {
    if (!data) return [];
    return data.by_type.map((t) => t.content_type);
  }, [data]);

  const filteredAndSortedItems = useMemo(() => {
    if (!data) return [];

    let items = [...data.items];

    // Filter by type
    if (filterType !== 'all') {
      items = items.filter((item) => item.content_type === filterType);
    }

    // Filter by search
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      items = items.filter((item) => item.title.toLowerCase().includes(q));
    }

    // Sort
    items.sort((a, b) => {
      let cmp = 0;
      switch (sortField) {
        case 'title':
          cmp = a.title.localeCompare(b.title);
          break;
        case 'compliance_score': {
          const aScore = a.compliance_score ?? -1;
          const bScore = b.compliance_score ?? -1;
          cmp = aScore - bScore;
          break;
        }
        case 'issue_count':
          cmp = a.issue_count - b.issue_count;
          break;
        case 'content_type':
          cmp = a.content_type.localeCompare(b.content_type);
          break;
      }
      return sortDir === 'asc' ? cmp : -cmp;
    });

    return items;
  }, [data, filterType, searchQuery, sortField, sortDir]);

  // --------------------------------------------------
  // Computed stats
  // --------------------------------------------------
  const approvedCount = data?.items.filter(
    (i) => i.writeback_status === 'approved'
  ).length ?? 0;

  const writtenBackCount = data?.items.filter(
    (i) => i.writeback_status === 'written_back'
  ).length ?? 0;

  // --------------------------------------------------
  // Loading state
  // --------------------------------------------------
  if (loading) {
    return (
      <div className="max-w-7xl mx-auto px-4 py-8">
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin text-[var(--accent-primary)]" />
        </div>
      </div>
    );
  }

  // --------------------------------------------------
  // Main render
  // --------------------------------------------------
  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      {/* Breadcrumb & Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Link
              to="/integrations"
              className="text-sm font-medium hover:opacity-80 transition-opacity text-[var(--content-accent)]"
            >
              Integrations
            </Link>
            <ChevronRight className="w-4 h-4 text-[var(--content-tertiary)]" />
            <Link
              to="/integrations/canvas"
              className="text-sm font-medium hover:opacity-80 transition-opacity text-[var(--content-accent)]"
            >
              Canvas Courses
            </Link>
            <ChevronRight className="w-4 h-4 text-[var(--content-tertiary)]" />
            <span className="text-sm font-medium text-[var(--content-secondary)]">
              Pages & Assignments
            </span>
          </div>
          <h1 className="text-2xl font-bold font-serif text-[var(--content-primary)]">
            {courseName || `Course ${courseId}`}
          </h1>
          <p className="text-[var(--content-secondary)]">
            Review and remediate accessibility issues across all course content.
          </p>
        </div>
        <div className="flex gap-3">
          <button
            onClick={fetchStatus}
            disabled={scanPolling}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50 bg-[var(--surface-tertiary)] text-[var(--content-primary)]"
          >
            <RefreshCw className={`w-4 h-4 ${scanPolling ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="mb-6 p-4 rounded-lg bg-[var(--feature-danger-surface)] text-[var(--feature-danger-content)]">
          {error}
        </div>
      )}

      {/* Overall compliance banner */}
      {data && (
        <div className="mb-6 rounded-xl p-6 flex items-center justify-between bg-[var(--surface-secondary)] border border-[var(--border-primary)]">
          <div className="flex items-center gap-6">
            <div>
              <p className="text-sm font-medium mb-1 text-[var(--content-secondary)]">
                Overall Compliance
              </p>
              <p
                className="text-3xl font-bold tabular-nums"
                style={{ color: data.overall_compliance !== null && data.overall_compliance >= 90
                  ? 'var(--content-success)'
                  : data.overall_compliance !== null && data.overall_compliance >= 70
                    ? 'var(--content-warning)'
                    : data.overall_compliance !== null
                      ? 'var(--content-error)'
                      : 'var(--content-tertiary)' }}
              >
                {data.overall_compliance !== null
                  ? `${data.overall_compliance.toFixed(0)}%`
                  : '--'}
              </p>
            </div>
            <div className="w-px h-12 bg-[var(--border-primary)]" />
            <div>
              <p className="text-sm font-medium mb-1 text-[var(--content-secondary)]">
                Total Items
              </p>
              <p className="text-xl font-bold tabular-nums text-[var(--content-primary)]">
                {data.items.length}
              </p>
            </div>
            <div>
              <p className="text-sm font-medium mb-1 text-[var(--content-secondary)]">
                Approved
              </p>
              <p className="text-xl font-bold tabular-nums text-[var(--content-success)]">
                {approvedCount + writtenBackCount}
              </p>
            </div>
            <div>
              <p className="text-sm font-medium mb-1 text-[var(--content-secondary)]">
                Written Back
              </p>
              <p className="text-xl font-bold tabular-nums text-[var(--content-success)]">
                {writtenBackCount}
              </p>
            </div>
          </div>

          {/* Action buttons */}
          <div className="flex items-center gap-3">
            <Button
              variant="primary"
              size="sm"
              onClick={handleScanContent}
              disabled={scanning || scanPolling}
              leftIcon={
                scanning || scanPolling ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Search className="w-4 h-4" />
                )
              }
            >
              {scanning || scanPolling ? 'Scanning...' : 'Scan Content'}
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={handleApproveAll}
              disabled={approvingAll || !data || data.items.length === 0}
              leftIcon={
                approvingAll ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <CheckCheck className="w-4 h-4" />
                )
              }
            >
              Approve All
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={handleWriteBackAll}
              disabled={writingBackAll || approvedCount === 0}
              leftIcon={
                writingBackAll ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Upload className="w-4 h-4" />
                )
              }
            >
              Write Back All Approved
            </Button>
          </div>
        </div>
      )}

      {/* Content Type Summary Table */}
      {data && data.by_type.length > 0 && (
        <div className="mb-6 rounded-xl overflow-hidden bg-[var(--surface-secondary)] border border-[var(--border-primary)]">
          <div className="px-6 py-4 border-b border-[var(--border-primary)]">
            <h2 className="text-lg font-semibold font-serif text-[var(--content-primary)]">
              Content by Type
            </h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-[var(--border-primary)]">
                  <th className="text-left px-6 py-3 text-xs font-semibold text-[var(--content-secondary)]">
                    Content Type
                  </th>
                  <th className="text-right px-6 py-3 text-xs font-semibold text-[var(--content-secondary)]">
                    Total
                  </th>
                  <th className="text-right px-6 py-3 text-xs font-semibold text-[var(--content-secondary)]">
                    Scanned
                  </th>
                  <th className="text-right px-6 py-3 text-xs font-semibold text-[var(--content-secondary)]">
                    Avg Score
                  </th>
                  <th className="text-right px-6 py-3 text-xs font-semibold text-[var(--content-secondary)]">
                    Issues
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.by_type.map((typeStatus, idx) => {
                  const badge = getComplianceBadgeVariant(typeStatus.average_compliance);
                  return (
                    <tr
                      key={typeStatus.content_type}
                      style={{
                        borderBottom:
                          idx !== data.by_type.length - 1
                            ? '1px solid var(--border-primary)'
                            : 'none',
                      }}
                    >
                      <td className="px-6 py-3">
                        <button
                          onClick={() =>
                            setFilterType(
                              filterType === typeStatus.content_type
                                ? 'all'
                                : typeStatus.content_type
                            )
                          }
                          className={`text-sm font-medium hover:opacity-80 transition-opacity ${
                            filterType === typeStatus.content_type
                              ? 'text-[var(--content-accent)]'
                              : 'text-[var(--content-primary)]'
                          }`}
                        >
                          {formatContentType(typeStatus.content_type)}
                        </button>
                      </td>
                      <td className="px-6 py-3 text-right">
                        <span className="text-sm tabular-nums text-[var(--content-primary)]">
                          {typeStatus.total}
                        </span>
                      </td>
                      <td className="px-6 py-3 text-right">
                        <span className="text-sm tabular-nums text-[var(--content-secondary)]">
                          {typeStatus.scanned} / {typeStatus.total}
                        </span>
                      </td>
                      <td className="px-6 py-3 text-right">
                        <Badge variant={badge.variant}>{badge.label}</Badge>
                      </td>
                      <td className="px-6 py-3 text-right">
                        <span
                          className={`text-sm tabular-nums font-medium ${
                            typeStatus.issues > 0
                              ? 'text-[var(--content-error)]'
                              : 'text-[var(--content-secondary)]'
                          }`}
                        >
                          {typeStatus.issues}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Full Item List */}
      {data && (
        <div className="rounded-xl overflow-hidden bg-[var(--surface-secondary)] border border-[var(--border-primary)]">
          {/* Table controls */}
          <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--border-primary)]">
            <h2 className="text-lg font-semibold font-serif text-[var(--content-primary)]">
              All Content Items
              {filteredAndSortedItems.length !== data.items.length && (
                <span className="ml-2 text-sm font-normal text-[var(--content-secondary)]">
                  ({filteredAndSortedItems.length} of {data.items.length})
                </span>
              )}
            </h2>
            <div className="flex items-center gap-3">
              {/* Type filter dropdown */}
              <div className="relative">
                <div className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm bg-[var(--surface-tertiary)] border border-[var(--border-primary)]">
                  <Filter className="w-3.5 h-3.5 text-[var(--content-secondary)]" />
                  <select
                    value={filterType}
                    onChange={(e) => setFilterType(e.target.value)}
                    className="bg-transparent outline-none text-sm cursor-pointer appearance-none pr-4 text-[var(--content-primary)]"
                  >
                    <option value="all">All types</option>
                    {contentTypes.map((type) => (
                      <option key={type} value={type}>
                        {formatContentType(type)}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Search */}
              <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-[var(--surface-tertiary)] border border-[var(--border-primary)]">
                <Search className="w-3.5 h-3.5 text-[var(--content-tertiary)]" />
                <input
                  type="text"
                  placeholder="Search items..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="bg-transparent outline-none text-sm w-48 text-[var(--content-primary)]"
                />
              </div>
            </div>
          </div>

          {/* Table */}
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-[var(--border-primary)]">
                  <th
                    className="text-left px-6 py-3 text-xs font-semibold cursor-pointer select-none text-[var(--content-secondary)]"
                    onClick={() => toggleSort('title')}
                  >
                    <span className="inline-flex items-center gap-1">
                      Name
                      {renderSortIcon('title')}
                    </span>
                  </th>
                  <th
                    className="text-left px-6 py-3 text-xs font-semibold cursor-pointer select-none text-[var(--content-secondary)]"
                    onClick={() => toggleSort('content_type')}
                  >
                    <span className="inline-flex items-center gap-1">
                      Type
                      {renderSortIcon('content_type')}
                    </span>
                  </th>
                  <th
                    className="text-left px-6 py-3 text-xs font-semibold cursor-pointer select-none text-[var(--content-secondary)]"
                    onClick={() => toggleSort('compliance_score')}
                  >
                    <span className="inline-flex items-center gap-1">
                      Score
                      {renderSortIcon('compliance_score')}
                    </span>
                  </th>
                  <th
                    className="text-left px-6 py-3 text-xs font-semibold cursor-pointer select-none text-[var(--content-secondary)]"
                    onClick={() => toggleSort('issue_count')}
                  >
                    <span className="inline-flex items-center gap-1">
                      Issues
                      {renderSortIcon('issue_count')}
                    </span>
                  </th>
                  <th className="text-left px-6 py-3 text-xs font-semibold text-[var(--content-secondary)]">
                    Status
                  </th>
                  <th className="text-left px-6 py-3 text-xs font-semibold text-[var(--content-secondary)]">
                    Updated
                  </th>
                  <th className="text-right px-6 py-3 text-xs font-semibold text-[var(--content-secondary)]">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {filteredAndSortedItems.length === 0 && (
                  <tr>
                    <td colSpan={7} className="text-center py-12">
                      <FileText className="w-10 h-10 mx-auto mb-2 text-[var(--content-tertiary)]" />
                      <p className="text-sm text-[var(--content-secondary)]">
                        {data.items.length === 0
                          ? 'No pages, assignments, announcements, quizzes or discussions found. Click "Scan Content" to check again, or see the Files tab for uploaded documents.'
                          : 'No items match your filters.'}
                      </p>
                      {searchQuery && (
                        <button
                          onClick={() => {
                            setSearchQuery('');
                            setFilterType('all');
                          }}
                          className="mt-3 text-sm font-medium hover:opacity-80 transition-opacity text-[var(--content-accent)]"
                        >
                          Clear filters
                        </button>
                      )}
                    </td>
                  </tr>
                )}
                {filteredAndSortedItems.map((item, idx) => {
                  const badge = getComplianceBadgeVariant(item.compliance_score);
                  const wbBadge = getWritebackBadge(item.writeback_status);

                  return (
                    <tr
                      key={item.cloud_file_id}
                      style={{
                        borderBottom:
                          idx !== filteredAndSortedItems.length - 1
                            ? '1px solid var(--border-primary)'
                            : 'none',
                      }}
                    >
                      {/* Name */}
                      <td className="px-6 py-3">
                        <div className="flex items-center gap-2">
                          <FileText className="w-4 h-4 shrink-0 text-[var(--content-secondary)]" />
                          <span
                            className="text-sm font-medium truncate max-w-[300px] text-[var(--content-primary)]"
                            title={item.title}
                          >
                            {item.title}
                          </span>
                        </div>
                      </td>

                      {/* Type */}
                      <td className="px-6 py-3">
                        <span className="px-2 py-0.5 rounded text-xs font-medium bg-[var(--surface-tertiary)] text-[var(--content-secondary)]">
                          {formatContentType(item.content_type)}
                        </span>
                      </td>

                      {/* Score */}
                      <td className="px-6 py-3">
                        <Badge variant={badge.variant}>
                          {item.compliance_score !== null && item.compliance_score >= 90 && (
                            <Check className="w-3 h-3" />
                          )}
                          {item.compliance_score !== null && item.compliance_score < 70 && (
                            <AlertTriangle className="w-3 h-3" />
                          )}
                          {badge.label}
                        </Badge>
                      </td>

                      {/* Issues */}
                      <td className="px-6 py-3">
                        <span
                          className={`text-sm tabular-nums font-medium ${
                            item.issue_count > 0
                              ? 'text-[var(--content-error)]'
                              : 'text-[var(--content-secondary)]'
                          }`}
                        >
                          {item.compliance_score !== null ? item.issue_count : '--'}
                        </span>
                      </td>

                      {/* Status */}
                      <td className="px-6 py-3">
                        {wbBadge ? (
                          <Badge variant={wbBadge.variant}>
                            {item.writeback_status === 'written_back' && (
                              <Check className="w-3 h-3" />
                            )}
                            {wbBadge.label}
                          </Badge>
                        ) : item.compliance_score !== null ? (
                          <Badge variant="neutral">Scanned</Badge>
                        ) : (
                          <Badge variant="neutral">Pending</Badge>
                        )}
                      </td>

                      {/* Updated */}
                      <td className="px-6 py-3">
                        <span className="text-sm text-[var(--content-secondary)]">
                          {formatDate(item.content_updated_at)}
                        </span>
                      </td>

                      {/* Actions */}
                      <td className="px-6 py-3">
                        <div className="flex items-center justify-end gap-2">
                          {item.compliance_score !== null && item.issue_count > 0 && (
                            <button
                              onClick={() =>
                                navigate(`/canvas/content/${item.cloud_file_id}/diff`)
                              }
                              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors bg-[var(--surface-tertiary)] text-[var(--content-primary)]"
                            >
                              <Eye className="w-3 h-3" />
                              Review
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Empty state when no data at all */}
      {!data && !loading && !error && (
        <div className="rounded-xl p-12 text-center bg-[var(--surface-secondary)]">
          <FileText className="w-16 h-16 mx-auto mb-4 text-[var(--content-tertiary)]" />
          <h3 className="text-lg font-semibold font-serif mb-2 text-[var(--content-primary)]">
            No content data yet
          </h3>
          <p className="mb-6 text-[var(--content-secondary)]">
            Scan this course to discover and evaluate all content for
            accessibility compliance.
          </p>
          <button
            onClick={handleScanContent}
            disabled={scanning}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-[var(--interactive-primary-fg)] bg-[var(--interactive-primary-bg)]"
          >
            {scanning ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Search className="w-4 h-4" />
            )}
            Scan Content
          </button>
        </div>
      )}
    </div>
  );
}
