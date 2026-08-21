import React, { useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { LTILayout } from '../components/LTILayout';
import { useLTISession } from '../hooks/useLTISession';

/** Only plain id-like tokens may be interpolated into a redirect path. */
function isSafeCourseSegment(value: string | null | undefined): value is string {
  return !!value && /^[A-Za-z0-9_-]+$/.test(value);
}

/**
 * Public hop for launches that should enter the main dashboard course page.
 * The parent LTISessionProvider owns the one-time exchange; this component
 * only chooses the top-level dashboard or embedded LTI destination.
 */
export function LTIGo(): React.ReactElement {
  const { accessToken, courseId, platform, accountWide, loading, error } = useLTISession();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const requestedCourse = searchParams.get('course');

  useEffect(() => {
    if (loading || error || !accessToken) return;
    const signedCourse = isSafeCourseSegment(courseId) ? courseId : null;
    const course = !accountWide && signedCourse
      ? signedCourse
      : isSafeCourseSegment(requestedCourse)
        ? requestedCourse
        : signedCourse;

    // The main dashboard is frame-denied. Embedded launches remain in the
    // LTI mini-view, under the already-restored shared session boundary.
    if (window.self !== window.top) {
      navigate(course ? `/lti/course/${course}` : '/lti/overview', { replace: true });
      return;
    }

    const destination = course
      ? platform === 'brightspace'
        ? `/brightspace/courses/${course}/content`
        : `/canvas/courses/${course}/content`
      : '/lti/overview';
    // Protected dashboard routes bootstrap from this legacy storage key.
    // Mini-view navigation stays sessionStorage/default-header only so an LTI
    // 401 is not misclassified as a dashboard API-key failure.
    localStorage.setItem('apiKey', accessToken);
    window.location.replace(destination);
  }, [accessToken, accountWide, courseId, error, loading, navigate, platform, requestedCourse]);

  return (
    <LTILayout loading={loading || (!!accessToken && !error)} error={error}>
      <div />
    </LTILayout>
  );
}
