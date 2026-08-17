import { useState, useEffect, useRef } from 'react';
import { apiClient } from '../api/client';

interface LTISession {
  accessToken: string | null;
  courseId: string | null;
  courseName: string | null;
  platform: string | null;
  loading: boolean;
  error: string | null;
}

/**
 * Extract course ID from URL path like /lti/course/123 or /lti/course/abc-def.
 * Widened from digits-only so non-numeric/opaque course ids (or a course id
 * that failed to resolve server-side) don't silently fall through to the
 * dashboard home — an empty/missing id is handled by the caller instead.
 */
function getCourseIdFromPath(): string | null {
  const match = window.location.pathname.match(/\/lti\/course\/([^/?#]+)/);
  return match ? match[1] : null;
}

/**
 * Capture the launch code once on initial page load.
 * This survives React StrictMode double-mounting because it lives
 * outside the component lifecycle — replaceState in the first mount
 * can't erase it before the second mount reads it.
 */
const initialLaunchCode = new URLSearchParams(window.location.search).get('code');

export function useLTISession(enabled: boolean = true): LTISession {
  const pathCourseId = getCourseIdFromPath();

  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [courseId, setCourseId] = useState<string | null>(null);
  const [courseName, setCourseName] = useState<string | null>(null);
  const [platform, setPlatform] = useState<string | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const exchanged = useRef(false);

  // Dev-mode fallback (Path 2 below) sets course ID synchronously from the URL
  // path — not a fetch, so there's no await boundary before setState.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    // Skip all side effects when not in LTI context
    if (!enabled) return;

    // Path 1: LTI 1.3 launch code exchange (production)
    // Use the module-level code so StrictMode remount still sees it.
    // Guard with a ref so the exchange only fires once.
    if (initialLaunchCode && !exchanged.current) {
      exchanged.current = true;
      window.history.replaceState({}, '', window.location.pathname);

      apiClient.post('/lti/exchange', { code: initialLaunchCode })
        .then((res) => {
          const token = res.data.access_token;
          setAccessToken(token);
          // Normalize an empty course id (account-level placement, or a
          // launch missing course custom params) to null so callers can
          // tell "no course context" apart from a real, if unusual, id.
          setCourseId(res.data.course_id || null);
          setCourseName(res.data.course_name || null);
          setPlatform(res.data.platform || 'canvas');
          // Store token and set on apiClient so all requests include it
          if (token) {
            localStorage.setItem('apiKey', token);
            apiClient.defaults.headers.common['Authorization'] = `Bearer ${token}`;
          }
        })
        .catch(() => {
          setError('Session expired or invalid. Please relaunch from Canvas.');
        })
        .finally(() => {
          setLoading(false);
        });
      return;
    }

    // If exchange already happened (StrictMode remount), stay in loading
    // until the promise from the first mount resolves.
    if (exchanged.current) return;

    // Path 2: Direct URL with course ID (dev mode / LTI 1.1 fallback)
    // When ALLOW_MOCK_AUTH is enabled on the API, requests without auth
    // are handled by mock auth. The course ID comes from the URL path.
    if (pathCourseId) {
      setCourseId(pathCourseId);
      // No access token needed — mock auth on the API handles it
      setAccessToken('dev-mock-session');
      setLoading(false);
      return;
    }

    // No launch code and no course ID in path
    setError('No launch code provided. Please relaunch from Canvas.');
    setLoading(false);
  }, [enabled, pathCourseId]);
  /* eslint-enable react-hooks/set-state-in-effect */

  return { accessToken, courseId, courseName, platform, loading, error };
}
