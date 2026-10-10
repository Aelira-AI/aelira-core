import React, { useEffect, useState } from 'react';
import DOMPurify from 'dompurify';
import { ArrowLeft } from 'lucide-react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { LTILayout } from '../components/LTILayout';
import { useLTISession } from '../hooks/useLTISession';
import { DocumentReviewPage } from './DocumentReviewPage';
import {
  approveContent,
  getContentDiff,
  rejectContent,
  type ContentDiffResponse,
} from '../api/brightspaceContent';

export function LTIBrightspaceReview(): React.ReactElement {
  const location = useLocation();
  return <BrightspaceContentReview key={location.pathname} />;
}

function BrightspaceContentReview(): React.ReactElement {
  const { courseId, orgUnitId, cloudFileId } = useParams<{
    courseId: string;
    orgUnitId: string;
    cloudFileId: string;
  }>();
  const resolvedCourseId = courseId || orgUnitId;
  const isLTI = useLocation().pathname.startsWith('/lti/');
  const navigate = useNavigate();
  const session = useLTISession(isLTI);
  const [diff, setDiff] = useState<ContentDiffResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [action, setAction] = useState<'approve' | 'reject' | null>(null);
  const coursePath = isLTI
    ? `/lti/course/${resolvedCourseId}${session.accountWide ? '?from=overview' : ''}`
    : `/brightspace/courses/${resolvedCourseId}/content`;

  const scopeError =
    session.accessToken &&
    !session.accountWide &&
    session.courseId &&
    resolvedCourseId &&
    session.courseId !== resolvedCourseId
      ? 'This LTI session is limited to its launch course.'
      : null;

  useEffect(() => {
    if (session.loading || session.error || scopeError || !cloudFileId) return;
    const controller = new AbortController();
    getContentDiff(cloudFileId, controller.signal)
      .then(value => { if (!controller.signal.aborted) setDiff(value); })
      .catch(() => { if (!controller.signal.aborted) setError('Failed to load Brightspace content review.'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [cloudFileId, scopeError, session.error, session.loading]);

  async function decide(decision: 'approve' | 'reject'): Promise<void> {
    if (!cloudFileId) return;
    setAction(decision);
    try {
      if (decision === 'approve') await approveContent(cloudFileId);
      else await rejectContent(cloudFileId);
      navigate(coursePath, { replace: true });
    } catch {
      setError(`Failed to ${decision} Brightspace content.`);
    } finally {
      setAction(null);
    }
  }

  const content = (
    <>
      {diff && diff.cloud_file_id === cloudFileId && (
        <div className="max-w-5xl mx-auto space-y-4">
          <button
            type="button"
            onClick={() => navigate(coursePath)}
            className="inline-flex items-center gap-1 text-sm text-[var(--content-accent)] hover:underline"
          >
            <ArrowLeft className="w-4 h-4" aria-hidden="true" />
            Back to Course
          </button>
          <h1 className="text-2xl font-bold text-[var(--content-primary)]">{diff.title}</h1>
          {!['html', 'topic_html', 'text/html', 'webpage'].includes(diff.content_type.toLowerCase()) ? <>
            <p className="text-sm text-[var(--content-secondary)]">Review and download the improved working file below. Brightspace file write-back is unavailable; upload the reviewed file manually.</p>
            {diff.scan_id ? <DocumentReviewPage scanId={diff.scan_id} cloudFileId={cloudFileId} backPath={coursePath} pdfToolsAvailable={!isLTI} resultPath={isLTI ? `/lti/report/${encodeURIComponent(diff.scan_id)}` : undefined} />
              : <p role="status">No completed scan is available for this file yet. Return to the course and scan it first.</p>}
          </> : <>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <section className="rounded-lg border border-[var(--border-primary)] p-4">
              <h2 className="font-semibold mb-3">Original</h2>
              <div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(diff.original_html || '') }} />
            </section>
            <section className="rounded-lg border border-[var(--border-primary)] p-4">
              <h2 className="font-semibold mb-3">Remediated</h2>
              <div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(diff.remediated_html || '') }} />
            </section>
          </div>
          <p className="text-sm text-[var(--content-secondary)]">
            Issues fixed: {diff.issues_fixed}; issues remaining: {diff.issues_remaining}
          </p>
          <div className="flex gap-3">
            <button
              type="button"
              disabled={action !== null}
              onClick={() => void decide('approve')}
              className="px-4 py-2 rounded bg-[var(--interactive-primary-bg)] text-white disabled:opacity-50"
            >
              {action === 'approve' ? 'Approving…' : 'Approve'}
            </button>
            <button
              type="button"
              disabled={action !== null}
              onClick={() => void decide('reject')}
              className="px-4 py-2 rounded border border-[var(--border-primary)] disabled:opacity-50"
            >
              {action === 'reject' ? 'Rejecting…' : 'Reject'}
            </button>
          </div>
          </>}
        </div>
      )}
    </>
  );
  const pending = session.loading || (!scopeError && loading);
  const failure = session.error || scopeError || error;
  if (isLTI) return <LTILayout loading={pending} error={failure}>{content}</LTILayout>;
  if (pending) return <p role="status" className="p-4 text-secondary">Loading Brightspace content review…</p>;
  if (failure) return <p role="alert" className="p-4 text-[var(--feature-danger-content)]">{failure}</p>;
  return content;
}
