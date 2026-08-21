import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Link, useParams, useNavigate } from 'react-router-dom';
import {
  Loader2,
  ChevronRight,
  ChevronDown,
  RefreshCw,
  FileText,
  FileCode2,
  Search,
  Check,
  CheckCheck,
  AlertTriangle,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  Upload,
  Eye,
  FolderOpen,
  Wand2,
  RotateCcw,
} from 'lucide-react';
import { useToast } from '../context/toast-context';
import {
  getCourseContentStatus,
  scanCourseContent,
  batchApproveContent,
  batchWriteBack,
  batchRemediateContent,
  batchRollback,
  type CourseContentStatusResponse,
  type ContentItemStatus,
} from '../api/brightspaceContent';
import { remediateAllInChunks } from '../utils/brightspaceRemediateAll';
import { apiClient } from '../api/client';
import { Badge } from '../components/ui/Badge';
import { Button } from '../components/ui/Button';

// ============================================================================
// Helpers
// ============================================================================

function getComplianceBadgeVariant(
  score: number | null
): 'success' | 'warning' | 'danger' | 'neutral' {
  if (score === null) return 'neutral';
  if (score >= 90) return 'success';
  if (score >= 70) return 'warning';
  return 'danger';
}

function getComplianceBadgeLabel(score: number | null): string {
  if (score === null) return 'Not scanned';
  return `${score.toFixed(0)}%`;
}

function getWritebackBadgeVariant(
  status: string | null
): 'accent' | 'success' | 'warning' | 'neutral' | null {
  if (!status) return null;
  switch (status) {
    case 'remediated':
      return 'accent';
    case 'approved':
      return 'accent';
    case 'written_back':
      return 'success';
    case 'stale':
      return 'warning';
    case 'rolled_back':
      return 'neutral';
    default:
      return null;
  }
}

function getWritebackBadgeLabel(status: string | null): string | null {
  if (!status) return null;
  switch (status) {
    case 'remediated':   return 'Remediated';
    case 'approved':     return 'Approved';
    case 'written_back': return 'Written back';
    case 'stale':        return 'Stale';
    case 'rolled_back':  return 'Rolled back';
    default:             return null;
  }
}

function getContentTypeIcon(contentType: string): React.ReactElement {
  if (contentType === 'html' || contentType === 'topic_html') {
    return (
      <FileCode2 className="w-4 h-4 shrink-0 text-[var(--content-secondary)]" />
    );
  }
  return (
    <FileText className="w-4 h-4 shrink-0 text-[var(--content-secondary)]" />
  );
}

type SortField = 'title' | 'compliance_score' | 'issue_count' | 'module_path';
type SortDir = 'asc' | 'desc';

// Cap how long we poll for scan/remediation progress before giving up and
// asking the user to refresh manually. Without this, a course whose items
// are skipped as "empty" (never reported as scanned) polls forever.
const SCAN_POLL_TIMEOUT_MS = 120_000;
const SCAN_POLL_INTERVAL_MS = 3000;

// ============================================================================
// Component
// ============================================================================

interface BrightspaceContentPageProps {
  orgUnitIdOverride?: string;
  isLTI?: boolean;
}

export default function BrightspaceContentPage({
  orgUnitIdOverride,
  isLTI = false,
}: BrightspaceContentPageProps = {}): React.ReactElement {
  const { orgUnitId: routeOrgUnitId } = useParams<{ orgUnitId: string }>();
  const orgUnitId = orgUnitIdOverride || routeOrgUnitId;
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
  const [remediatingAll, setRemediatingAll] = useState(false);
  const remediatingRef = useRef(false);
  const [approvingAll, setApprovingAll] = useState(false);
  const [writingBackAll, setWritingBackAll] = useState(false);
  const [rollingBackAll, setRollingBackAll] = useState(false);

  // Table controls
  const [searchQuery, setSearchQuery] = useState('');
  const [sortField, setSortField] = useState<SortField>('compliance_score');
  const [sortDir, setSortDir] = useState<SortDir>('asc');

  // Collapsed module sections
  const [collapsedModules, setCollapsedModules] = useState<Set<string>>(
    new Set()
  );

  // Polling ref
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollStartRef = useRef<number>(0);

  const orgUnitIdNum = orgUnitId ? Number(orgUnitId) : NaN;

  // --------------------------------------------------
  // Fetch course content status
  // --------------------------------------------------
  const fetchStatus = useCallback(async (): Promise<void> => {
    if (!orgUnitId || isNaN(orgUnitIdNum)) return;
    try {
      const result = await getCourseContentStatus(orgUnitIdNum);
      setData(result);
      setError(null);
    } catch (err) {
      console.error('Failed to fetch course content status:', err);
      setError('Failed to load course content. Please try again.');
    } finally {
      setLoading(false);
    }
  }, [orgUnitId, orgUnitIdNum]);

  // Fetch course name from Brightspace API
  const fetchCourseName = useCallback(async (): Promise<void> => {
    if (!orgUnitId) return;
    try {
      const res = await apiClient.get('/brightspace/courses');
      const coursesData = res.data;
      const courses = Array.isArray(coursesData)
        ? coursesData
        : Array.isArray(coursesData?.courses)
          ? coursesData.courses
          : [];
      const course = courses.find(
        (c: { org_unit_id: number; name: string }) =>
          String(c.org_unit_id) === String(orgUnitId)
      );
      if (course) {
        setCourseName(course.name);
      }
    } catch (err) {
      console.error('Failed to fetch course name:', err);
    }
  }, [orgUnitId]);

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
    if (!orgUnitId || isNaN(orgUnitIdNum)) return;
    stopPolling();
    setScanPolling(true);
    pollStartRef.current = Date.now();

    let lastRemediatedCount = 0;

    pollingRef.current = setInterval(async () => {
      try {
        const result = await getCourseContentStatus(orgUnitIdNum);
        setData(result);

        // Track remediation progress (only when remediating)
        const remediatedCount = result.items.filter(
          (i: ContentItemStatus) => i.writeback_status === 'remediated'
        ).length;
        const isRemediating = remediatingRef.current || lastRemediatedCount > 0;

        if (isRemediating && remediatedCount > lastRemediatedCount) {
          toast.info(
            `${remediatedCount} of ${result.total_items} items remediated...`,
            'Remediation Progress'
          );
          lastRemediatedCount = remediatedCount;
        }

        // Stop conditions
        const hasUnscanned = result.scanned_items < result.total_items;

        if (isRemediating) {
          // Remediation mode: stop when all eligible items are remediated
          const stillRemediating = result.items.some(
            (i: ContentItemStatus) =>
              i.compliance_score !== null &&
              i.compliance_score < 100 &&
              !i.writeback_status
          );
          if (!stillRemediating && !remediatingRef.current) {
            stopPolling();
            setScanning(false);
            setRemediatingAll(false);
            remediatingRef.current = false;
            toast.success(
              `Remediation complete — ${remediatedCount} items fixed.`,
              'Remediation Complete'
            );
            return;
          }
        } else if (!hasUnscanned) {
          // Scan mode: stop when all items are scanned
          stopPolling();
          setScanning(false);
          toast.success('Content scan complete.', 'Scan Finished');
          return;
        }
      } catch (err) {
        console.error('Polling error:', err);
      }

      // Give up after SCAN_POLL_TIMEOUT_MS so a course with items that never
      // report as scanned/remediated (e.g. skipped-as-empty) doesn't poll forever.
      if (Date.now() - pollStartRef.current >= SCAN_POLL_TIMEOUT_MS) {
        stopPolling();
        setScanning(false);
        if (!remediatingRef.current) {
          setRemediatingAll(false);
        }
        toast.warning(
          'Scan is taking longer than expected. Results will appear when ready - refresh to check.',
          'Scan Timeout'
        );
      }
    }, SCAN_POLL_INTERVAL_MS);
  }, [orgUnitId, orgUnitIdNum, stopPolling, toast]);

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
    if (!orgUnitId || isNaN(orgUnitIdNum)) return;
    setScanning(true);
    try {
      const result = await scanCourseContent({ org_unit_id: orgUnitIdNum });
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

  const handleRemediateAll = async (): Promise<void> => {
    if (!orgUnitId || isNaN(orgUnitIdNum) || !data) return;

    const eligibleIds = data.items
      .filter(
        (item) =>
          item.compliance_score !== null &&
          item.compliance_score < 100 &&
          !item.writeback_status
      )
      .map((item) => item.cloud_file_id);
    if (eligibleIds.length === 0) {
      toast.info('No eligible items to remediate.', 'Nothing to Do');
      return;
    }

    setRemediatingAll(true);
    remediatingRef.current = true;
    try {
      const summary = await remediateAllInChunks(eligibleIds, (cloudFileIds) =>
        batchRemediateContent({
          org_unit_id: orgUnitIdNum,
          cloud_file_ids: cloudFileIds,
        })
      );
      const message =
        `Requested: ${summary.requestedCount}, Processed: ${summary.processedCount}, ` +
        `Completed: ${summary.completedCount}, Fixed: ${summary.fixedCount}, ` +
        `Manual: ${summary.manualCount}, Failed: ${summary.failedCount}` +
        (summary.chunkFailures.length > 0
          ? `, Chunk failures: ${summary.chunkFailures
              .slice(0, 3)
              .map((failure) => `#${failure.chunkNumber} ${failure.message}`)
              .join('; ')} (${summary.unreportedCount} outcomes unavailable)`
          : '');
      if (summary.failedCount > 0 || summary.chunkFailures.length > 0) {
        toast.warning(message, 'Remediation Complete');
      } else {
        toast.success(message, 'Remediation Complete');
      }
      await fetchStatus();
    } catch (err) {
      console.error('Failed to batch remediate:', err);
      toast.error('Remediation failed.', 'Error');
    } finally {
      setRemediatingAll(false);
      remediatingRef.current = false;
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
          (!item.writeback_status || item.writeback_status === 'remediated')
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
    if (!orgUnitId || isNaN(orgUnitIdNum)) return;

    setWritingBackAll(true);
    try {
      const result = await batchWriteBack({ org_unit_id: orgUnitIdNum });
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

  const handleRollbackAll = async (): Promise<void> => {
    if (!orgUnitId || isNaN(orgUnitIdNum)) return;

    setRollingBackAll(true);
    try {
      const result = await batchRollback({ org_unit_id: orgUnitIdNum });
      const message = `Rolled back: ${result.rolled_back_count}` +
        (result.failed_count > 0 ? `, Failed: ${result.failed_count}` : '');
      if (result.failed_count > 0) {
        toast.warning(message, 'Rollback Complete');
      } else {
        toast.success(message, 'Rollback Complete');
      }
      await fetchStatus();
    } catch (err) {
      console.error('Failed to batch rollback:', err);
      toast.error('Failed to rollback content.', 'Error');
    } finally {
      setRollingBackAll(false);
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
      setSortDir('asc');
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

  const filteredAndSortedItems = useMemo(() => {
    if (!data) return [];

    let items = [...data.items];

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
        case 'module_path':
          cmp = a.module_path.localeCompare(b.module_path);
          break;
      }
      return sortDir === 'asc' ? cmp : -cmp;
    });

    return items;
  }, [data, searchQuery, sortField, sortDir]);

  // Group items by module_path for collapsible sections
  const groupedItems = useMemo(() => {
    const groups = new Map<string, ContentItemStatus[]>();
    for (const item of filteredAndSortedItems) {
      const path = item.module_path || 'Ungrouped';
      const existing = groups.get(path);
      if (existing) {
        existing.push(item);
      } else {
        groups.set(path, [item]);
      }
    }
    return groups;
  }, [filteredAndSortedItems]);

  const toggleModuleCollapse = (modulePath: string): void => {
    setCollapsedModules((prev) => {
      const next = new Set(prev);
      if (next.has(modulePath)) {
        next.delete(modulePath);
      } else {
        next.add(modulePath);
      }
      return next;
    });
  };

  // --------------------------------------------------
  // Computed stats
  // --------------------------------------------------
  const approvedCount =
    data?.items.filter((i) => i.writeback_status === 'approved').length ?? 0;

  const writtenBackCount =
    data?.items.filter((i) => i.writeback_status === 'written_back').length ??
    0;

  // --------------------------------------------------
  // Navigation helper
  // --------------------------------------------------
  const handleItemClick = (item: ContentItemStatus): void => {
    if (item.content_type === 'html' || item.content_type === 'topic_html') {
      navigate(
        isLTI
          ? `/lti/course/${orgUnitId}/content/${item.cloud_file_id}/review`
          : `/brightspace/courses/${orgUnitId}/content/${item.cloud_file_id}/review`
      );
    }
    // For file items, we could navigate to scan detail if a scan exists
    // but we don't have scan_id on the item — the review page will handle it
  };

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
              to="/integrations/brightspace"
              className="text-sm font-medium hover:opacity-80 transition-opacity text-[var(--content-accent)]"
            >
              Brightspace Courses
            </Link>
            <ChevronRight className="w-4 h-4 text-[var(--content-tertiary)]" />
            <span className="text-sm font-medium text-[var(--content-secondary)]">
              Pages & Assignments
            </span>
          </div>
          <h1 className="text-2xl font-bold font-serif text-[var(--content-primary)]">
            {courseName || `Course ${orgUnitId}`}
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
            <RefreshCw
              className={`w-4 h-4 ${scanPolling ? 'animate-spin' : ''}`}
            />
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
                style={{ color: data.average_compliance !== null && data.average_compliance >= 90
                  ? 'var(--content-success)'
                  : data.average_compliance !== null && data.average_compliance >= 70
                    ? 'var(--content-warning)'
                    : data.average_compliance !== null
                      ? 'var(--content-error)'
                      : 'var(--content-secondary)' }}
              >
                {data.average_compliance !== null
                  ? `${data.average_compliance.toFixed(0)}%`
                  : '--'}
              </p>
            </div>
            <div className="w-px h-12 bg-[var(--border-primary)]" />
            <div>
              <p className="text-sm font-medium mb-1 text-[var(--content-secondary)]">
                Total Items
              </p>
              <p className="text-xl font-bold tabular-nums text-[var(--content-primary)]">
                {data.total_items}
              </p>
            </div>
            <div>
              <p className="text-sm font-medium mb-1 text-[var(--content-secondary)]">
                Scanned
              </p>
              <p className="text-xl font-bold tabular-nums text-[var(--content-primary)]">
                {data.scanned_items} / {data.total_items}
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
              {scanning || scanPolling ? 'Scanning...' : 'Scan All'}
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={handleRemediateAll}
              disabled={remediatingAll || !data || data.scanned_items === 0}
              leftIcon={
                remediatingAll ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Wand2 className="w-4 h-4" />
                )
              }
            >
              {remediatingAll ? 'Remediating All...' : 'Remediate All'}
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
            <Button
              variant="secondary"
              size="sm"
              onClick={handleRollbackAll}
              disabled={rollingBackAll || writtenBackCount === 0}
              leftIcon={
                rollingBackAll ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <RotateCcw className="w-4 h-4" />
                )
              }
            >
              Rollback All
            </Button>
          </div>
        </div>
      )}

      {/* Module Summary */}
      {data && groupedItems.size > 0 && (
        <div className="mb-6 rounded-xl overflow-hidden bg-[var(--surface-secondary)] border border-[var(--border-primary)]">
          <div className="px-6 py-4 border-b border-[var(--border-primary)]">
            <h2 className="text-lg font-semibold font-serif text-[var(--content-primary)]">
              Content by Module
            </h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-[var(--border-primary)]">
                  <th className="text-left px-6 py-3 text-xs font-semibold text-[var(--content-secondary)]">
                    Module
                  </th>
                  <th className="text-right px-6 py-3 text-xs font-semibold text-[var(--content-secondary)]">
                    Items
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
                {Array.from(groupedItems.entries()).map(
                  ([modulePath, items], idx) => {
                    const scannedCount = items.filter(
                      (i) => i.compliance_score !== null
                    ).length;
                    const scannedItems = items.filter(
                      (i) => i.compliance_score !== null
                    );
                    const avgScore =
                      scannedItems.length > 0
                        ? scannedItems.reduce(
                            (sum, i) => sum + (i.compliance_score ?? 0),
                            0
                          ) / scannedItems.length
                        : null;
                    const totalIssues = items.reduce(
                      (sum, i) => sum + i.issue_count,
                      0
                    );

                    return (
                      <tr
                        key={modulePath}
                        style={{
                          borderBottom:
                            idx !== groupedItems.size - 1
                              ? '1px solid var(--border-primary)'
                              : 'none',
                        }}
                      >
                        <td className="px-6 py-3">
                          <div className="flex items-center gap-2">
                            <FolderOpen className="w-4 h-4 shrink-0 text-[var(--content-secondary)]" />
                            <span className="text-sm font-medium text-[var(--content-primary)]">
                              {modulePath}
                            </span>
                          </div>
                        </td>
                        <td className="px-6 py-3 text-right">
                          <span className="text-sm tabular-nums text-[var(--content-primary)]">
                            {items.length}
                          </span>
                        </td>
                        <td className="px-6 py-3 text-right">
                          <span className="text-sm tabular-nums text-[var(--content-secondary)]">
                            {scannedCount} / {items.length}
                          </span>
                        </td>
                        <td className="px-6 py-3 text-right">
                          <Badge variant={getComplianceBadgeVariant(avgScore)}>
                            {getComplianceBadgeLabel(avgScore)}
                          </Badge>
                        </td>
                        <td className="px-6 py-3 text-right">
                          <span
                            className="text-sm tabular-nums font-medium"
                            style={{
                              color:
                                totalIssues > 0
                                  ? 'var(--content-error)'
                                  : 'var(--content-secondary)',
                            }}
                          >
                            {totalIssues}
                          </span>
                        </td>
                      </tr>
                    );
                  }
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Full Item List — grouped by module_path with collapsible sections */}
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
                    onClick={() => toggleSort('module_path')}
                  >
                    <span className="inline-flex items-center gap-1">
                      Module
                      {renderSortIcon('module_path')}
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
                  <th className="text-right px-6 py-3 text-xs font-semibold text-[var(--content-secondary)]">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {filteredAndSortedItems.length === 0 && (
                  <tr>
                    <td colSpan={6} className="text-center py-12">
                      <FileText className="w-10 h-10 mx-auto mb-2 text-[var(--content-tertiary)]" />
                      <p className="text-sm text-[var(--content-secondary)]">
                        {data.items.length === 0
                          ? 'No pages, assignments, announcements, quizzes or discussions found. Click "Scan All" to check again, or see the Files tab for uploaded documents.'
                          : 'No items match your search.'}
                      </p>
                      {searchQuery && (
                        <button
                          onClick={() => setSearchQuery('')}
                          className="mt-3 text-sm font-medium hover:opacity-80 transition-opacity text-[var(--content-accent)]"
                        >
                          Clear search
                        </button>
                      )}
                    </td>
                  </tr>
                )}
                {Array.from(groupedItems.entries()).map(
                  ([modulePath, items]) => {
                    const isCollapsed = collapsedModules.has(modulePath);
                    return (
                      <React.Fragment key={modulePath}>
                        {/* Module group header row */}
                        <tr className="border-b border-[var(--border-primary)] bg-[var(--surface-tertiary)]">
                          <td
                            colSpan={6}
                            className="px-6 py-2"
                          >
                            <button
                              onClick={() =>
                                toggleModuleCollapse(modulePath)
                              }
                              className="flex items-center gap-2 w-full text-left"
                            >
                              {isCollapsed ? (
                                <ChevronRight className="w-4 h-4 text-[var(--content-secondary)]" />
                              ) : (
                                <ChevronDown className="w-4 h-4 text-[var(--content-secondary)]" />
                              )}
                              <FolderOpen className="w-4 h-4 text-[var(--content-secondary)]" />
                              <span className="text-sm font-semibold text-[var(--content-primary)]">
                                {modulePath}
                              </span>
                              <span className="text-xs ml-1 text-[var(--content-secondary)]">
                                ({items.length} item
                                {items.length !== 1 ? 's' : ''})
                              </span>
                            </button>
                          </td>
                        </tr>
                        {/* Module items */}
                        {!isCollapsed &&
                          items.map((item, idx) => {
                            const scoreBadgeVariant = getComplianceBadgeVariant(item.compliance_score);
                            const scoreBadgeLabel = getComplianceBadgeLabel(item.compliance_score);
                            const wbVariant = getWritebackBadgeVariant(item.writeback_status);
                            const wbLabel = getWritebackBadgeLabel(item.writeback_status);

                            return (
                              <tr
                                key={item.cloud_file_id}
                                className="cursor-pointer hover:opacity-90 transition-opacity"
                                onClick={() => handleItemClick(item)}
                                style={{
                                  borderBottom:
                                    idx !== items.length - 1
                                      ? '1px solid var(--border-primary)'
                                      : 'none',
                                }}
                              >
                                {/* Name */}
                                <td className="px-6 py-3">
                                  <div className="flex items-center gap-2 pl-6">
                                    {getContentTypeIcon(item.content_type)}
                                    <span
                                      className="text-sm font-medium truncate max-w-[300px] text-[var(--content-primary)]"
                                      title={item.title}
                                    >
                                      {item.title}
                                    </span>
                                  </div>
                                </td>

                                {/* Module (content type shown since grouped) */}
                                <td className="px-6 py-3">
                                  <span className="px-2 py-0.5 rounded text-xs font-medium bg-[var(--surface-tertiary)] text-[var(--content-secondary)]">
                                    {item.content_type}
                                  </span>
                                </td>

                                {/* Score */}
                                <td className="px-6 py-3">
                                  <Badge variant={scoreBadgeVariant}>
                                    {item.compliance_score !== null &&
                                      item.compliance_score >= 90 && (
                                        <Check className="w-3 h-3" />
                                      )}
                                    {item.compliance_score !== null &&
                                      item.compliance_score < 70 && (
                                        <AlertTriangle className="w-3 h-3" />
                                      )}
                                    {scoreBadgeLabel}
                                  </Badge>
                                </td>

                                {/* Issues */}
                                <td className="px-6 py-3">
                                  <span
                                    className="text-sm tabular-nums font-medium"
                                    style={{
                                      color:
                                        item.issue_count > 0
                                          ? 'var(--content-error)'
                                          : 'var(--content-secondary)',
                                    }}
                                  >
                                    {item.compliance_score !== null
                                      ? item.issue_count
                                      : '--'}
                                  </span>
                                </td>

                                {/* Status */}
                                <td className="px-6 py-3">
                                  {wbVariant && wbLabel ? (
                                    <Badge variant={wbVariant}>
                                      {item.writeback_status === 'written_back' && (
                                        <Check className="w-3 h-3" />
                                      )}
                                      {wbLabel}
                                    </Badge>
                                  ) : item.compliance_score !== null ? (
                                    <Badge variant="neutral">Scanned</Badge>
                                  ) : (
                                    <Badge variant="neutral">Pending</Badge>
                                  )}
                                </td>

                                {/* Actions */}
                                <td className="px-6 py-3">
                                  <div className="flex items-center justify-end gap-2">
                                    {item.compliance_score !== null &&
                                      item.issue_count > 0 && (
                                        <button
                                          onClick={(e) => {
                                            e.stopPropagation();
                                            navigate(
                                              isLTI
                                                ? `/lti/course/${orgUnitId}/content/${item.cloud_file_id}/review`
                                                : `/brightspace/courses/${orgUnitId}/content/${item.cloud_file_id}/review`
                                            );
                                          }}
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
                      </React.Fragment>
                    );
                  }
                )}
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
          <Button
            variant="primary"
            size="sm"
            onClick={handleScanContent}
            disabled={scanning}
            leftIcon={
              scanning ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Search className="w-4 h-4" />
              )
            }
          >
            Scan All
          </Button>
        </div>
      )}
    </div>
  );
}
