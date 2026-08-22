import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Loader2,
  Check,
  X,
  ChevronLeft,
  ChevronRight,
  Eye,
  Code,
  AlertTriangle,
  CheckCircle2,
  ArrowLeft,
  FileText,
  Download,
} from 'lucide-react';
import DOMPurify from 'dompurify';
import { createPatch } from 'diff';
import { html as diff2html } from 'diff2html';
import 'diff2html/bundles/css/diff2html.min.css';
import {
  getContentDiff,
  approveContent,
  rejectContent,
  getCourseContentStatus,
} from '../api/canvasContent';
import type {
  ContentDiffResponse,
  ContentIssue,
  ContentItemStatus,
} from '../api/canvasContent';
import { scansApi } from '../api/scans';
import type { ManagedArtifactMetadata } from '../api/scans';
import { useToast } from '../context/toast-context';
import { Badge } from '../components/ui/Badge';
import { Button } from '../components/ui/Button';

// ============================================================================
// Types
// ============================================================================

type ViewMode = 'preview' | 'diff';

// ============================================================================
// Helpers
// ============================================================================

/** Map content_type slug to a human label */
function contentTypeLabel(type: string): string {
  const labels: Record<string, string> = {
    page: 'Page',
    assignment: 'Assignment',
    discussion: 'Discussion',
    quiz: 'Quiz',
    announcement: 'Announcement',
    syllabus: 'Syllabus',
    module: 'Module',
  };
  return labels[type] ?? type;
}

/** Badge variant for content type */
function contentTypeBadgeVariant(type: string): 'accent' | 'warning' | 'success' | 'danger' | 'neutral' {
  const variants: Record<string, 'accent' | 'warning' | 'success' | 'danger'> = {
    page:       'accent',
    assignment: 'warning',
    discussion: 'success',
    quiz:       'danger',
  };
  return variants[type] ?? 'neutral';
}

/**
 * Safely render HTML by sanitizing with DOMPurify first.
 * Returns a props object suitable for React's dangerouslySetInnerHTML.
 */
function sanitizedHtml(html: string): { __html: string } {
  return { __html: DOMPurify.sanitize(html) };
}

// ============================================================================
// Component
// ============================================================================

export function CanvasContentDiffPage(): React.ReactElement {
  const { courseId, cloudFileId } = useParams<{ courseId: string; cloudFileId: string }>();
  const navigate = useNavigate();
  const toast = useToast();

  // Data state
  const [diff, setDiff] = useState<ContentDiffResponse | null>(null);
  const [items, setItems] = useState<ContentItemStatus[]>([]);
  const [artifact, setArtifact] = useState<ManagedArtifactMetadata | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // UI state
  const [viewMode, setViewMode] = useState<ViewMode>('preview');
  const [actionLoading, setActionLoading] = useState<'approve' | 'reject' | null>(null);

  // Current item index in the items list
  const currentIndex = useMemo(() => {
    if (!cloudFileId || items.length === 0) return -1;
    return items.findIndex((item) => item.cloud_file_id === cloudFileId);
  }, [cloudFileId, items]);

  const hasPrev = currentIndex > 0;
  const hasNext = currentIndex >= 0 && currentIndex < items.length - 1;

  // Fetch data
  useEffect(() => {
    const fetchData = async (): Promise<void> => {
      if (!courseId || !cloudFileId) return;

      try {
        setLoading(true);
        setError(null);

        const [diffData, statusData] = await Promise.all([
          getContentDiff(cloudFileId),
          getCourseContentStatus(courseId),
        ]);

        setDiff(diffData);
        setItems(statusData.items);
        const current = statusData.items.find(
          (item) => item.cloud_file_id === cloudFileId
        );
        if (current?.scan_id && current.current_remediation_artifact_id) {
          setArtifact(
            await scansApi.getManagedArtifact(
              current.scan_id,
              current.current_remediation_artifact_id
            )
          );
        } else {
          setArtifact(null);
        }
      } catch (err: unknown) {
        console.error('Failed to fetch content diff:', err);
        const message =
          err instanceof Error ? err.message : 'Failed to load content diff';
        setError(message);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, [courseId, cloudFileId]);

  // Navigation callbacks
  const navigateToItem = useCallback(
    (index: number) => {
      if (index < 0 || index >= items.length || !courseId) return;
      const item = items[index];
      navigate(`/canvas/courses/${courseId}/content/${item.cloud_file_id}/review`);
    },
    [items, courseId, navigate]
  );

  const handlePrev = useCallback(() => {
    if (hasPrev) navigateToItem(currentIndex - 1);
  }, [hasPrev, currentIndex, navigateToItem]);

  const handleNext = useCallback(() => {
    if (hasNext) navigateToItem(currentIndex + 1);
  }, [hasNext, currentIndex, navigateToItem]);

  // Approve / Reject handlers
  const handleApprove = useCallback(async () => {
    if (!cloudFileId) return;
    setActionLoading('approve');
    try {
      if (artifact) {
        await scansApi.approveManagedArtifact(artifact.scan_id, artifact.id);
        toast.success('Managed artifact approved', 'Approved');
      } else {
        const result = await approveContent(cloudFileId);
        toast.success(result.message || 'Content approved', 'Approved');
      }
      // Navigate to next item if available, otherwise back to course
      if (hasNext) {
        navigateToItem(currentIndex + 1);
      } else if (courseId) {
        navigate(`/canvas/courses/${courseId}/content`);
      }
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : 'Failed to approve content';
      toast.error(message, 'Error');
    } finally {
      setActionLoading(null);
    }
  }, [artifact, cloudFileId, courseId, hasNext, currentIndex, navigateToItem, navigate, toast]);

  const handleReject = useCallback(async () => {
    if (!cloudFileId) return;
    setActionLoading('reject');
    try {
      if (artifact) {
        await scansApi.rejectManagedArtifact(artifact.scan_id, artifact.id);
        toast.success('Managed artifact rejected', 'Rejected');
      } else {
        const result = await rejectContent(cloudFileId);
        toast.success(result.message || 'Content rejected for manual review', 'Rejected');
      }
      // Navigate to next item if available, otherwise back to course
      if (hasNext) {
        navigateToItem(currentIndex + 1);
      } else if (courseId) {
        navigate(`/canvas/courses/${courseId}/content`);
      }
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : 'Failed to reject content';
      toast.error(message, 'Error');
    } finally {
      setActionLoading(null);
    }
  }, [artifact, cloudFileId, courseId, hasNext, currentIndex, navigateToItem, navigate, toast]);

  const handleDownloadArtifact = useCallback(async () => {
    if (!artifact) return;
    try {
      const blob = await scansApi.downloadManagedArtifact(artifact.scan_id, artifact.id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = artifact.filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Download failed', 'Error');
    }
  }, [artifact, toast]);

  // Files (content_type "file") have no HTML content_body — they're real
  // uploaded documents, not HTML fragments — so original_html/
  // remediated_html come back null from the API even though this response
  // type currently claims non-nullable strings. Guard here rather than
  // let createPatch (which expects real strings) throw on null.
  const hasHtmlContent = !!(diff?.original_html && diff?.remediated_html);

  // Generate diff HTML for the raw diff view (sanitized before rendering)
  const sanitizedDiffHtml = useMemo(() => {
    if (!diff || !diff.original_html || !diff.remediated_html) return '';
    const patch = createPatch(
      'content.html',
      diff.original_html,
      diff.remediated_html,
      'Original',
      'Remediated'
    );
    const rawDiffHtml = diff2html(patch, {
      drawFileList: false,
      matching: 'lines',
      outputFormat: 'side-by-side',
    });
    return DOMPurify.sanitize(rawDiffHtml);
  }, [diff]);

  // Real issues from the last scan — diff.issues (never generated; see
  // the server's ContentIssueDetail comment). No per-issue fixed/remaining
  // split exists in the data, so this renders as ONE honest list rather
  // than fabricating which items were "fixed" vs "remaining".
  const issues: ContentIssue[] = diff?.issues ?? [];

  // ========================================================================
  // Render: Loading
  // ========================================================================

  if (loading) {
    return (
      <div
        className="flex items-center justify-center h-64"
        role="status"
        aria-label="Loading content review"
      >
        <Loader2 className="w-8 h-8 animate-spin text-[var(--accent)]" aria-hidden="true" />
        <span className="sr-only">Loading content review...</span>
      </div>
    );
  }

  // ========================================================================
  // Render: Error
  // ========================================================================

  if (error || !diff) {
    return (
      <div className="p-8">
        <div className="max-w-4xl mx-auto">
          <div
            className="rounded-lg p-4 bg-[var(--feature-danger-surface)] border border-[var(--feature-danger-content)] text-[var(--feature-danger-content)]"
            role="alert"
          >
            {error || 'Content not found'}
          </div>
          <button
            onClick={() => navigate(courseId ? `/canvas/courses/${courseId}/content` : '/integrations/canvas')}
            className="mt-4 flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors bg-[var(--surface-secondary)] text-[var(--content-primary)] border border-[var(--border-primary)]"
          >
            <ArrowLeft className="w-4 h-4" aria-hidden="true" />
            Back to Course
          </button>
        </div>
      </div>
    );
  }

  // ========================================================================
  // Render: Main
  // ========================================================================

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)]">
      {/* ================================================================
          Top Header Bar
          ================================================================ */}
      <div className="flex items-center justify-between px-6 py-3 shrink-0 bg-[var(--surface-secondary)] border-b border-[var(--border-primary)]">
        {/* Left: Back + title */}
        <div className="flex items-center gap-3 min-w-0">
          <button
            onClick={() => navigate(courseId ? `/canvas/courses/${courseId}/content` : '/integrations/canvas')}
            className="p-1.5 rounded transition-colors hover:opacity-80 text-[var(--content-secondary)]"
            aria-label="Back to course content"
          >
            <ArrowLeft className="w-5 h-5" aria-hidden="true" />
          </button>
          <FileText className="w-5 h-5 shrink-0 text-[var(--accent)]" aria-hidden="true" />
          <h1 className="text-lg font-semibold truncate text-[var(--content-primary)]">
            {diff.title}
          </h1>
          <Badge variant={contentTypeBadgeVariant(diff.content_type)}>
            {contentTypeLabel(diff.content_type)}
          </Badge>
        </div>

        {/* Right: Navigation + View toggle */}
        <div className="flex items-center gap-2 shrink-0">
          {/* Item navigation */}
          {items.length > 1 && (
            <div className="flex items-center gap-1 mr-2">
              <button
                onClick={handlePrev}
                disabled={!hasPrev}
                className="p-1.5 rounded transition-colors disabled:opacity-30 text-[var(--content-secondary)]"
                aria-label="Previous item"
              >
                <ChevronLeft className="w-5 h-5" aria-hidden="true" />
              </button>
              <span className="text-sm tabular-nums text-[var(--content-secondary)]">
                {currentIndex + 1} / {items.length}
              </span>
              <button
                onClick={handleNext}
                disabled={!hasNext}
                className="p-1.5 rounded transition-colors disabled:opacity-30 text-[var(--content-secondary)]"
                aria-label="Next item"
              >
                <ChevronRight className="w-5 h-5" aria-hidden="true" />
              </button>
            </div>
          )}

          {/* View mode toggle */}
          <div
            className="flex rounded-lg overflow-hidden border border-[var(--border-primary)]"
            role="tablist"
            aria-label="View mode"
          >
            <button
              role="tab"
              aria-selected={viewMode === 'preview'}
              onClick={() => setViewMode('preview')}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium transition-colors ${
                viewMode === 'preview'
                  ? 'bg-[var(--accent-solid)] text-[var(--interactive-primary-fg)]'
                  : 'bg-[var(--surface-primary)] text-[var(--content-secondary)]'
              }`}
            >
              <Eye className="w-4 h-4" aria-hidden="true" />
              Preview
            </button>
            <button
              role="tab"
              aria-selected={viewMode === 'diff'}
              onClick={() => setViewMode('diff')}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium transition-colors border-l border-[var(--border-primary)] ${
                viewMode === 'diff'
                  ? 'bg-[var(--accent-solid)] text-[var(--interactive-primary-fg)]'
                  : 'bg-[var(--surface-primary)] text-[var(--content-secondary)]'
              }`}
            >
              <Code className="w-4 h-4" aria-hidden="true" />
              HTML Diff
            </button>
          </div>
        </div>
      </div>

      {artifact && (
        <section
          aria-label="Managed remediation artifact"
          className="px-6 py-3 bg-[var(--surface-secondary)] border-b border-[var(--border-primary)]"
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="text-sm text-[var(--content-secondary)]">
              <strong className="text-[var(--content-primary)]">{artifact.filename}</strong>
              {' · '}{artifact.mime_type}{' · '}{artifact.size_bytes.toLocaleString()} bytes
              {' · SHA-256 '}{artifact.sha256.slice(0, 12)}…
              {' · expires '}{new Date(artifact.expires_at).toLocaleString()}
            </div>
            <button
              type="button"
              onClick={handleDownloadArtifact}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm border border-[var(--border-primary)]"
            >
              <Download className="w-4 h-4" aria-hidden="true" />
              Download
            </button>
          </div>
          {artifact.approval_blockers.length > 0 && (
            <p id="artifact-approval-reasons" className="mt-2 text-sm text-[var(--feature-warning-content)]">
              Approval unavailable: {artifact.approval_blockers.join(', ')}
            </p>
          )}
        </section>
      )}

      {/* ================================================================
          Main Content Area
          ================================================================ */}
      <div className="flex flex-1 min-h-0">
        {/* Left: Preview / Diff pane */}
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
          {!hasHtmlContent ? (
            /* ------ No HTML content to preview (e.g. a scanned Canvas
               file — a real document, not an HTML fragment) ------ */
            <div className="flex-1 flex flex-col items-center justify-center gap-2 p-8 text-center bg-[var(--surface-primary)]">
              <FileText className="w-10 h-10 text-[var(--content-tertiary)]" aria-hidden="true" />
              <p className="text-sm font-medium text-[var(--content-primary)]">
                No HTML preview available for this item.
              </p>
              <p className="text-sm max-w-md text-[var(--content-secondary)]">
                This is a scanned file, not an HTML page — there's no
                original/remediated HTML to diff here. Use Approve or
                Reject below based on the scan results, or review the file
                directly in Canvas.
              </p>
            </div>
          ) : viewMode === 'preview' ? (
            /* ------ Side-by-side rendered preview ------ */
            <div className="flex flex-1 min-h-0">
              {/* Original */}
              <div className="flex-1 flex flex-col min-w-0 border-r border-[var(--border-primary)]">
                <div className="px-4 py-2 text-sm font-medium shrink-0 bg-[var(--surface-tertiary)] text-[var(--content-secondary)] border-b border-[var(--border-primary)]">
                  Original
                </div>
                <div
                  className="flex-1 overflow-y-auto p-4 prose prose-sm max-w-none bg-[var(--surface-primary)] text-[var(--content-primary)]"
                  dangerouslySetInnerHTML={sanitizedHtml(diff.original_html)}
                />
              </div>

              {/* Remediated */}
              <div className="flex-1 flex flex-col min-w-0">
                <div className="px-4 py-2 text-sm font-medium shrink-0 bg-[var(--surface-tertiary)] text-[var(--content-secondary)] border-b border-[var(--border-primary)]">
                  Remediated
                </div>
                <div
                  className="flex-1 overflow-y-auto p-4 prose prose-sm max-w-none bg-[var(--surface-primary)] text-[var(--content-primary)]"
                  dangerouslySetInnerHTML={sanitizedHtml(diff.remediated_html)}
                />
              </div>
            </div>
          ) : (
            /* ------ Raw HTML diff view (pre-sanitized in useMemo) ------ */
            <div className="flex-1 overflow-y-auto bg-[var(--surface-primary)]">
              {/* sanitizedDiffHtml is already run through DOMPurify.sanitize() */}
              <div
                className="diff2html-wrapper"
                dangerouslySetInnerHTML={{ __html: sanitizedDiffHtml }}
              />
            </div>
          )}
        </div>

        {/* Right: Issues Panel */}
        <div className="w-80 shrink-0 flex flex-col overflow-hidden border-l border-[var(--border-primary)] bg-[var(--surface-secondary)]">
          <div className="flex-1 overflow-y-auto">
            {/* Aggregate counts — real, but aggregate only. No per-issue
                fixed/remaining attribution is tracked anywhere for Canvas
                content, so this is deliberately NOT split into two lists
                the way it used to be (that split was fabricated). */}
            <div className="p-4 border-b border-[var(--border-primary)] flex items-center gap-4">
              <div className="flex items-center gap-1.5">
                <CheckCircle2
                  className="w-4 h-4 shrink-0 text-[var(--content-success)]"
                  aria-hidden="true"
                />
                <span className="text-sm text-[var(--content-primary)]">
                  {diff.issues_fixed} fixed
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                <AlertTriangle
                  className="w-4 h-4 shrink-0 text-[var(--content-warning)]"
                  aria-hidden="true"
                />
                <span className="text-sm text-[var(--content-primary)]">
                  {diff.issues_remaining} remaining
                </span>
              </div>
              {/* Where the two numbers came from. A count produced by a
                  rescan of the remediated copy is a measurement; without
                  one the split is unknown, and saying so is the whole
                  point of the claim we make about verified fixes. */}
              <span className="text-xs text-[var(--content-secondary)] ml-auto">
                {diff.issues_verified_by_rescan
                  ? 'Verified by rescan'
                  : 'Not verified by rescan yet'}
              </span>
            </div>

            {/* Real findings from the last scan — one honest list. Every
                string here traces back to the stored axe-core violation:
                description/help come straight from scan data, and if a
                violation has no description, the raw rule id is shown
                rather than inventing prose for it. */}
            <div className="p-4">
              <h2 className="text-sm font-semibold mb-3 text-[var(--content-primary)]">
                Issues Found in Last Scan ({issues.length})
              </h2>
              {issues.length === 0 ? (
                <p className="text-sm text-[var(--content-secondary)]">
                  Itemized findings unavailable for this scan.
                </p>
              ) : (
                <ul className="space-y-3">
                  {issues.map((issue, idx) => (
                    <li key={`${issue.id}-${idx}`} className="flex items-start gap-2">
                      <AlertTriangle
                        className="w-3.5 h-3.5 mt-0.5 shrink-0 text-[var(--content-warning)]"
                        aria-hidden="true"
                      />
                      <div>
                        <p className="text-sm text-[var(--content-primary)]">
                          {issue.description || issue.id}
                        </p>
                        {issue.help && issue.help !== issue.description && (
                          <p className="text-xs mt-0.5 text-[var(--content-secondary)]">
                            {issue.help}
                          </p>
                        )}
                        <p className="text-xs mt-0.5 text-[var(--content-tertiary)]">
                          {issue.impact && <span className="capitalize">{issue.impact}</span>}
                          {issue.wcag_tags.length > 0 &&
                            `${issue.impact ? ' · ' : ''}${issue.wcag_tags.join(', ')}`}
                          {issue.nodes_affected > 1 && ` · ${issue.nodes_affected} occurrences`}
                        </p>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          {/* ================================================================
              Bottom: Approve / Reject Buttons
              ================================================================ */}
          <div className="p-4 shrink-0 flex gap-3 border-t border-[var(--border-primary)]">
            <Button
              variant="destructive"
              onClick={handleReject}
              disabled={actionLoading !== null}
              className="flex-1 justify-center"
              aria-label="Reject remediation"
            >
              {actionLoading === 'reject' ? (
                <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
              ) : (
                <X className="w-4 h-4" aria-hidden="true" />
              )}
              Reject
            </Button>
            <Button
              variant="primary"
              onClick={handleApprove}
              disabled={
                actionLoading !== null ||
                (artifact !== null && !artifact.can_approve)
              }
              aria-describedby={
                artifact && !artifact.can_approve ? 'artifact-approval-reasons' : undefined
              }
              title={
                artifact && !artifact.can_approve
                  ? artifact.approval_blockers.join(', ')
                  : undefined
              }
              className="flex-1 justify-center"
              aria-label="Approve remediation"
            >
              {actionLoading === 'approve' ? (
                <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
              ) : (
                <Check className="w-4 h-4" aria-hidden="true" />
              )}
              Approve
            </Button>
          </div>
        </div>
      </div>

      {/* ================================================================
          diff2html style overrides for theme compatibility
          ================================================================ */}
      <style>{`
        .diff2html-wrapper .d2h-wrapper {
          background: var(--surface-primary);
        }
        .diff2html-wrapper .d2h-file-header {
          display: none;
        }
        .diff2html-wrapper .d2h-code-line-ctn {
          font-size: 0.8125rem;
          font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace;
        }
        .diff2html-wrapper .d2h-file-diff {
          overflow-x: auto;
        }
      `}</style>
    </div>
  );
}

export default CanvasContentDiffPage;
