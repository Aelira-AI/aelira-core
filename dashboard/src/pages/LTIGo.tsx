import React, { useEffect, useRef, useState } from 'react';
import { LTILayout } from '../components/LTILayout';
import { apiClient } from '../api/client';

/**
 * Public (no ProtectedRoute) hop page for LTI launches that should land on
 * the main dashboard course content page rather than the LTI mini-UI.
 *
 * The target route (/canvas/courses/:courseId/content) is wrapped in
 * ProtectedRoute and doesn't know how to exchange a one-time launch code —
 * so this page does the exchange (which sets the aelira_access cookie
 * ProtectedRoute's auth check accepts) and then hard-navigates on.
 *
 * Capture code/course from the query string at module load, before the
 * exchange effect runs — mirrors useLTISession's approach so a React
 * StrictMode remount can't lose them.
 */
const initialParams = new URLSearchParams(window.location.search);
const initialCode = initialParams.get('code');
const initialCourse = initialParams.get('course');

/** Only plain id-like tokens may be interpolated into the redirect path. */
function isSafeCourseSegment(value: string | null | undefined): value is string {
  return !!value && /^[A-Za-z0-9_-]+$/.test(value);
}

export function LTIGo(): React.ReactElement {
  const [error, setError] = useState<string | null>(null);
  const exchanged = useRef(false);

  useEffect(() => {
    if (exchanged.current) return;
    exchanged.current = true;

    if (!initialCode) {
      window.location.replace('/lti/overview');
      return;
    }

    // Inside the Canvas iframe the main dashboard is frame-denied by our
    // security headers ("refused to connect"), so the escape-to-dashboard
    // hop is top-level-only. In-iframe launches forward the UNCONSUMED
    // code to the LTI mini-view, which does its own exchange — the exact
    // pre-hop flow.
    if (window.self !== window.top) {
      const codeParam = `code=${encodeURIComponent(initialCode)}`;
      window.location.replace(
        isSafeCourseSegment(initialCourse)
          ? `/lti/course/${initialCourse}?${codeParam}`
          : `/lti/overview?${codeParam}`
      );
      return;
    }

    apiClient
      .post('/lti/exchange', { code: initialCode })
      .then((res) => {
        const token = res.data.access_token;
        if (token) {
          localStorage.setItem('apiKey', token);
          apiClient.defaults.headers.common['Authorization'] = `Bearer ${token}`;
        }
        const course = isSafeCourseSegment(initialCourse)
          ? initialCourse
          : isSafeCourseSegment(res.data.course_id)
            ? res.data.course_id
            : null;
        window.location.replace(course ? `/canvas/courses/${course}/content` : '/lti/overview');
      })
      .catch(() => {
        setError('Session expired or invalid. Please relaunch from Canvas.');
      });
  }, []);

  return (
    <LTILayout loading={!error} error={error}>
      <div />
    </LTILayout>
  );
}
