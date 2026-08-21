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
  Wrench,
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
import {
  mergeCourseContent,
  groupContentByType,
  isRemediable,
  isApprovable,
  contentItemState,
  CONTENT_ITEM_STATE_COLOR,
  type LiveCanvasFile,
  type MergedContentItem,
} from '../utils/mergeCourseContent';
import { summarizeBatchOutcome } from '../utils/batchActionResult';

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
  // Live Canvas Files section — merged into the unified content list below.
  // Stays `null` (not []) on fetch failure so the merge helper's
  // graceful-degradation path (render the DB list untouched) is reachable.
  const [liveFiles, setLiveFiles] = useState<LiveCanvasFile[] | null>(null);

  // Scan state
  const [scanning, setScanning] = useState(false);
  const [scanPolling, setScanPolling] = useState(false);

  // Batch action states
  const [approvingAll, setApprovingAll] = useState(false);
  const [writingBackAll, setWritingBackAll] = useState(false);
  const [remediatingAll, setRemediatingAll] = useState(false);
  // { done, total } while Remediate All is running — drives "Remediating
  // 2 of 4…" on the button.
  const [remediateAllProgress, setRemediateAllProgress] = useState<{ done: number; total: number } | null>(null);
  // Per-row remediate in-flight tracking, keyed by composite content identity.
  const [remediatingIds, setRemediatingIds] = useState<Set<string>>(new Set());

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

  // Fetch the live Canvas Files list — merged into the unified content list.
  // Files are course content too: this is what makes them show up here at
  // all before they've ever been scanned (the DB-only /status endpoint has
  // no row for a file until scan_course_content or an individual scan has
  // upserted one).
  const fetchLiveFiles = useCallback(async (): Promise<void> => {
    if (!courseId) return;
    try {
      const res = await apiClient.get(`/canvas/courses/${courseId}/files`);
      const filesData = res.data;
      const filesList: LiveCanvasFile[] = Array.isArray(filesData)
        ? filesData
        : Array.isArray(filesData?.files)
          ? filesData.files
          : [];
      setLiveFiles(filesList);
    } catch (err) {
      console.error('Failed to fetch live Canvas files:', err);
      // Leave liveFiles as null — mergeCourseContent degrades to the DB
      // list untouched rather than blanking the view.
    }
  }, [courseId]);

  useEffect(() => {
    fetchStatus();
    fetchCourseName();
    fetchLiveFiles();
  }, [fetchStatus, fetchCourseName, fetchLiveFiles]);

  const mergedItems = useMemo(
    () =>
      mergeCourseContent(data?.items ?? null, liveFiles, {
        provider: 'canvas',
        parentId: courseId ?? '',
      }),
    [courseId, data, liveFiles]
  );

  // Content-by-type breakdown — derived from mergedItems (not data.by_type,
  // which has no row for a type until something of that type has actually
  // been scanned). Shared with LTICourseView via groupContentByType so the
  // two views' by-type counting rules can't drift from each other.
  const mergedByType = useMemo(() => groupContentByType(mergedItems), [mergedItems]);

  // Items eligible for remediation — scanned, has issues, no remediated
  // version yet, and a scan_id to remediate against. Same condition the
  // per-row Remediate button uses; "Remediate All" fires this same
  // execution path for every one of them.
  const remediableItems = useMemo(() => mergedItems.filter(isRemediable), [mergedItems]);

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
          'No pages, assignments, announcements, quizzes, discussions, or files found in this course.',
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

    // Get all items with a remediation available that aren't already
    // approved/written-back/rejected — see isApprovable's doc comment for
    // why this can't just check `!item.writeback_status` (that excludes
    // 'pending_review', the exact state approval exists to act on).
    const eligibleIds = mergedItems
      .filter(isApprovable)
      .map((item) => item.cloud_file_id)
      .filter((id): id is string => id !== null);

    if (eligibleIds.length === 0) {
      toast.info('No items to approve.', 'Nothing to Do');
      return;
    }

    setApprovingAll(true);
    try {
      const result = await batchApproveContent({ cloud_file_ids: eligibleIds });
      const summary = summarizeBatchOutcome({
        verb: 'Approved',
        succeededCount: result.approved_count,
        buckets: [{ label: 'skipped', count: result.skipped_count ?? 0 }],
        errors: result.errors ?? [],
      });
      if (summary.status === 'success') {
        toast.success(summary.message, 'Batch Approve');
      } else {
        toast.warning(summary.message, 'Batch Approve');
      }
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
      const summary = summarizeBatchOutcome({
        verb: 'Wrote back',
        succeededCount: result.written_count,
        buckets: [
          { label: 'stale', count: result.stale_count ?? 0 },
          { label: 'failed', count: result.failed_count ?? 0 },
          { label: 'skipped', count: result.skipped_count ?? 0 },
        ],
        errors: result.errors ?? [],
      });
      if (summary.status === 'success') {
        toast.success(summary.message, 'Write Back Complete');
      } else {
        toast.warning(summary.message, 'Write Back Complete');
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

  // Per-row remediate — no batch endpoint exists server-side for this yet,
  // so this fires the same synchronous POST /education/remediate/{scan_id}
  // the LTI Files tab already uses successfully. Unlike the CloudJobQueue
  // scan pipeline (c67cb9f), this endpoint does the remediation work
  // in-request and returns only once it's actually done — no queue, no
  // background task needed, and the response is trustworthy immediately.
  // Files are documents and go through the scan-based endpoint, which
  // downloads them. Content items are markup held in our own database and
  // are remediated in place. Sending a content item to the file endpoint
  // makes it try to download a Canvas file that does not exist, and the
  // 404 surfaces as "remediation failed" when nothing was ever attempted.
  const postRemediate = (item: MergedContentItem) =>
    item.content_type === 'file'
      ? apiClient.post(`/education/remediate/${item.scan_id}`)
      : apiClient.post(`/canvas/content/${item.cloud_file_id}/remediate`);

  const handleRemediateItem = async (item: MergedContentItem): Promise<void> => {
    setRemediatingIds((prev) => new Set(prev).add(item.identity_key));
    try {
      const res = await postRemediate(item);
      const fixed = res.data?.fixed_count ?? 0;
      const manual = res.data?.manual_count ?? 0;
      if (res.data?.success === false) {
        toast.error(res.data?.message || 'Remediation failed.', 'Remediate');
      } else if (fixed === 0 && manual === 0) {
        toast.info('No issues to remediate.', 'Remediate');
      } else {
        const fixedPart = `Fixed ${fixed} issue${fixed !== 1 ? 's' : ''}`;
        const manualPart = manual > 0 ? `, ${manual} still need${manual !== 1 ? '' : 's'} manual review` : '';
        toast.success(`${fixedPart}${manualPart}.`, 'Remediate');
      }
      await fetchStatus();
    } catch (err) {
      console.error('Failed to remediate item:', err);
      toast.error('Failed to remediate item.', 'Error');
    } finally {
      setRemediatingIds((prev) => {
        const next = new Set(prev);
        next.delete(item.identity_key);
        return next;
      });
    }
  };

  // Remediate All — principal-requested, client-side only: no batch
  // endpoint exists server-side (grepped for one, there isn't), so this
  // runs the identical per-row execution path (handleRemediateItem's POST)
  // over every eligible row. Sequential, not fire-many-concurrently:
  // /education/remediate/{scan_id} does real remediation work (file
  // download, remediation engine) synchronously in-request rather than
  // queuing a background job, so N concurrent calls would be N concurrent
  // downloads/remediation runs against the same course rather than N
  // cheap enqueues — sequencing keeps this from hammering Canvas or the
  // remediation engine.
  const handleRemediateAll = async (): Promise<void> => {
    if (remediableItems.length === 0) return;

    const scannedCount = mergedItems.filter((item) => item.compliance_score !== null).length;
    const skippedCount = scannedCount - remediableItems.length;

    const total = remediableItems.length;
    setRemediatingAll(true);
    setRemediateAllProgress({ done: 0, total });
    setRemediatingIds((prev) => {
      const next = new Set(prev);
      remediableItems.forEach((item) => next.add(item.identity_key));
      return next;
    });

    let succeeded = 0;
    let failed = 0;
    const errors: string[] = [];

    for (const [index, item] of remediableItems.entries()) {
      try {
        const res = await postRemediate(item);
        if (res.data?.success === false) {
          failed += 1;
          errors.push(`${item.provider_file_id}: ${res.data?.message || 'remediation failed'}`);
        } else {
          succeeded += 1;
        }
      } catch {
        failed += 1;
        errors.push(`${item.provider_file_id}: request failed`);
      } finally {
        setRemediatingIds((prev) => {
          const next = new Set(prev);
          next.delete(item.identity_key);
          return next;
        });
        setRemediateAllProgress({ done: index + 1, total });
      }
    }

    const summary = summarizeBatchOutcome({
      verb: 'Remediated',
      succeededCount: succeeded,
      buckets: [
        { label: 'skipped', count: skippedCount },
        { label: 'failed', count: failed },
      ],
      errors,
    });
    if (summary.status === 'success') {
      toast.success(summary.message, 'Remediate All');
    } else {
      toast.warning(summary.message, 'Remediate All');
    }

    await fetchStatus();
    setRemediatingAll(false);
    setRemediateAllProgress(null);
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

  const SortIcon = ({ field }: { field: SortField }): React.ReactElement => {
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
    // Derived from the unified list (not data.by_type) so an unscanned
    // content type — most commonly "file" before its first course scan —
    // still appears as a filter option instead of being invisible until
    // something scans it.
    const types = new Set(mergedItems.map((item) => item.content_type));
    return Array.from(types);
  }, [mergedItems]);

  const filteredAndSortedItems = useMemo(() => {
    let items = [...mergedItems];

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
  }, [mergedItems, filterType, searchQuery, sortField, sortDir]);

  // --------------------------------------------------
  // Computed stats
  // --------------------------------------------------
  const approvedCount = mergedItems.filter(
    (i) => i.writeback_status === 'approved'
  ).length;

  const writtenBackCount = mergedItems.filter(
    (i) => i.writeback_status === 'written_back'
  ).length;

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
              Content
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
                {mergedItems.length}
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
              disabled={approvingAll || mergedItems.length === 0}
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
              variant="secondary"
              size="sm"
              onClick={handleRemediateAll}
              disabled={remediatingAll || remediableItems.length === 0}
              leftIcon={
                remediatingAll ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Wrench className="w-4 h-4" />
                )
              }
            >
              {remediatingAll && remediateAllProgress
                ? `Remediating ${remediateAllProgress.done} of ${remediateAllProgress.total}…`
                : 'Remediate All'}
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
      {mergedByType.length > 0 && (
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
                {mergedByType.map((typeStatus, idx) => {
                  const badge = getComplianceBadgeVariant(typeStatus.average_compliance);
                  return (
                    <tr
                      key={typeStatus.content_type}
                      style={{
                        borderBottom:
                          idx !== mergedByType.length - 1
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
              {filteredAndSortedItems.length !== mergedItems.length && (
                <span className="ml-2 text-sm font-normal text-[var(--content-secondary)]">
                  ({filteredAndSortedItems.length} of {mergedItems.length})
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
                      <SortIcon field="title" />
                    </span>
                  </th>
                  <th
                    className="text-left px-6 py-3 text-xs font-semibold cursor-pointer select-none text-[var(--content-secondary)]"
                    onClick={() => toggleSort('content_type')}
                  >
                    <span className="inline-flex items-center gap-1">
                      Type
                      <SortIcon field="content_type" />
                    </span>
                  </th>
                  <th
                    className="text-left px-6 py-3 text-xs font-semibold cursor-pointer select-none text-[var(--content-secondary)]"
                    onClick={() => toggleSort('compliance_score')}
                  >
                    <span className="inline-flex items-center gap-1">
                      Score
                      <SortIcon field="compliance_score" />
                    </span>
                  </th>
                  <th
                    className="text-left px-6 py-3 text-xs font-semibold cursor-pointer select-none text-[var(--content-secondary)]"
                    onClick={() => toggleSort('issue_count')}
                  >
                    <span className="inline-flex items-center gap-1">
                      Issues
                      <SortIcon field="issue_count" />
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
                        {mergedItems.length === 0
                          ? 'No pages, assignments, announcements, quizzes, discussions, or files found. Click "Scan Content" to check again.'
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
                  const state = contentItemState(item);

                  return (
                    <tr
                      key={item.identity_key}
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

                      {/* Status — the visible state model: distinguishes
                          "needs remediation" from "already remediated,
                          pending review" from the final approved/written
                          back/rejected states. Previously this column
                          collapsed everything short of a writeback_status
                          into a single "Scanned" badge, which is exactly
                          why a fully-auto-remediated HTML item and a
                          not-yet-touched file looked identical. */}
                      <td className="px-6 py-3">
                        <Badge variant={CONTENT_ITEM_STATE_COLOR[state.key]}>
                          {state.key === 'written_back' && <Check className="w-3 h-3" />}
                          {state.label}
                        </Badge>
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
                          {isRemediable(item) && (
                              <button
                                onClick={() =>
                                  handleRemediateItem(item)
                                }
                                disabled={remediatingIds.has(item.identity_key)}
                                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors disabled:opacity-50 bg-[var(--interactive-accent-bg)] text-[var(--interactive-primary-fg)]"
                              >
                                {remediatingIds.has(item.identity_key) ? (
                                  <Loader2 className="w-3 h-3 animate-spin" />
                                ) : (
                                  <Wrench className="w-3 h-3" />
                                )}
                                Remediate
                              </button>
                            )}
                          {item.compliance_score !== null && item.issue_count > 0 && (
                            <button
                              onClick={() =>
                                navigate(`/canvas/courses/${courseId}/content/${item.cloud_file_id}/review`)
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
