import { useState, useEffect } from 'react';
import { apiClient } from '../api/client';

interface LTISession {
  accessToken: string | null;
  courseId: string | null;
  courseName: string | null;
  loading: boolean;
  error: string | null;
}

export function useLTISession(): LTISession {
  const launchCode = new URLSearchParams(window.location.search).get('code');
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [courseId, setCourseId] = useState<string | null>(null);
  const [courseName, setCourseName] = useState<string | null>(null);
  const [loading, setLoading] = useState(!!launchCode);
  const [error, setError] = useState<string | null>(
    launchCode ? null : 'No launch code provided. Please relaunch from Canvas.'
  );

  useEffect(() => {
    if (!launchCode) return;

    // Remove code from URL immediately (security)
    window.history.replaceState({}, '', window.location.pathname);

    apiClient.post('/lti/exchange', { code: launchCode })
      .then((res) => {
        setAccessToken(res.data.access_token);
        setCourseId(res.data.course_id);
        setCourseName(res.data.course_name || null);
      })
      .catch(() => {
        setError('Session expired or invalid. Please relaunch from Canvas.');
      })
      .finally(() => {
        setLoading(false);
      });
  }, [launchCode]);

  return { accessToken, courseId, courseName, loading, error };
}
