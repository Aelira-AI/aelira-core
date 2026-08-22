import React from 'react';
import { ArrowLeft } from 'lucide-react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { LTILayout } from '../components/LTILayout';
import { useLTISession } from '../hooks/useLTISession';
import BrightspaceContentPage from './BrightspaceContentPage';
import { LTICourseView } from './LTICourseView';

export function LTICourseRoute(): React.ReactElement {
  const { courseId } = useParams<{ courseId: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const session = useLTISession();

  if (session.platform !== 'brightspace') {
    return <LTICourseView />;
  }

  const scopeError =
    session.accessToken &&
    !session.accountWide &&
    session.courseId &&
    courseId &&
    session.courseId !== courseId
      ? 'This LTI session is limited to its launch course.'
      : null;

  return (
    <LTILayout
      loading={session.loading}
      error={session.error || scopeError || (!courseId ? 'No Brightspace course was selected.' : null)}
    >
      <div className="max-w-7xl mx-auto">
        {searchParams.get('from') === 'overview' && (
          <button
            type="button"
            onClick={() => navigate('/lti/overview')}
            className="flex items-center gap-1 text-sm text-[var(--content-accent)] hover:underline mb-2"
          >
            <ArrowLeft className="w-4 h-4" aria-hidden="true" />
            Back to Overview
          </button>
        )}
        {courseId && <BrightspaceContentPage orgUnitIdOverride={courseId} isLTI />}
      </div>
    </LTILayout>
  );
}
