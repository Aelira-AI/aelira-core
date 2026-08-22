import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
} from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { apiClient } from '../api/client';

const SESSION_STORAGE_KEY = 'aelira.lti.session.v1';

interface StoredLTISession {
  accessToken: string;
  courseId: string | null;
  courseName: string | null;
  platform: string;
  accountWide: boolean;
  expiresAt: number;
}

export interface LTISession {
  accessToken: string | null;
  courseId: string | null;
  courseName: string | null;
  platform: string | null;
  accountWide: boolean;
  loading: boolean;
  error: string | null;
}

const EMPTY_SESSION: LTISession = {
  accessToken: null,
  courseId: null,
  courseName: null,
  platform: null,
  accountWide: false,
  loading: false,
  error: null,
};

const LTISessionContext = createContext<LTISession | null>(null);

function decodeTokenPayload(token: string): Record<string, unknown> | null {
  try {
    const payload = token.split('.')[1];
    if (!payload) return null;
    const normalized = payload.replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=');
    return JSON.parse(window.atob(padded)) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function tokenExpiry(token: string): number | null {
  const exp = decodeTokenPayload(token)?.exp;
  return typeof exp === 'number' && Number.isFinite(exp) ? exp * 1000 : null;
}

function tokenAccountWide(token: string): boolean {
  return decodeTokenPayload(token)?.lti_account_wide === true;
}

function tokenCourseId(token: string): string | null {
  const courseId = decodeTokenPayload(token)?.course_id;
  return typeof courseId === 'string' && courseId ? courseId : null;
}

function tokenPlatform(token: string): string | null {
  const platform = decodeTokenPayload(token)?.lti_platform;
  return platform === 'canvas' || platform === 'brightspace' || platform === 'blackboard'
    ? platform
    : null;
}

function readStoredSession(): StoredLTISession | null {
  try {
    const raw = sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<StoredLTISession>;
    if (
      typeof value.accessToken !== 'string' ||
      !value.accessToken ||
      typeof value.platform !== 'string' ||
      typeof value.expiresAt !== 'number' ||
      typeof value.accountWide !== 'boolean' ||
      (value.courseId !== null && typeof value.courseId !== 'string') ||
      (value.courseName !== null && typeof value.courseName !== 'string')
    ) {
      return null;
    }
    const session = value as StoredLTISession;
    const actualExpiry = tokenExpiry(session.accessToken);
    const actualPlatform = tokenPlatform(session.accessToken);
    if (
      actualExpiry !== session.expiresAt ||
      actualPlatform !== session.platform ||
      tokenCourseId(session.accessToken) !== session.courseId ||
      tokenAccountWide(session.accessToken) !== session.accountWide
    ) {
      return null;
    }
    return session;
  } catch {
    return null;
  }
}

function peekStoredToken(): string | undefined {
  try {
    const value = JSON.parse(sessionStorage.getItem(SESSION_STORAGE_KEY) || '{}');
    return typeof value.accessToken === 'string' ? value.accessToken : undefined;
  } catch {
    return undefined;
  }
}

function clearSession(token?: string): void {
  sessionStorage.removeItem(SESSION_STORAGE_KEY);
  if (token && localStorage.getItem('apiKey') === token) {
    localStorage.removeItem('apiKey');
  }
  const authorization = apiClient.defaults.headers.common.Authorization;
  if (token && authorization === `Bearer ${token}`) {
    delete apiClient.defaults.headers.common.Authorization;
  }
}

function activateSession(session: StoredLTISession): void {
  sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session));
  apiClient.defaults.headers.common.Authorization = `Bearer ${session.accessToken}`;
}

function sessionState(session: StoredLTISession): LTISession {
  return {
    accessToken: session.accessToken,
    courseId: session.courseId,
    courseName: session.courseName,
    platform: session.platform,
    accountWide: session.accountWide,
    loading: false,
    error: null,
  };
}

export function LTISessionProvider(): ReactElement {
  const location = useLocation();
  const navigate = useNavigate();
  const started = useRef(false);
  const [session, setSession] = useState<LTISession>({ ...EMPTY_SESSION, loading: true });

  /* eslint-disable react-hooks/set-state-in-effect -- bootstrap restores an external session snapshot */
  useEffect(() => {
    if (started.current) return;
    started.current = true;

    const params = new URLSearchParams(location.search);
    const code = params.get('code');
    if (code) {
      params.delete('code');
      const search = params.toString();
      navigate(
        { pathname: location.pathname, search: search ? `?${search}` : '' },
        { replace: true },
      );

      apiClient
        .post('/lti/exchange', { code }, { _skipApiKeyAuth: true })
        .then((response) => {
          const token = response.data.access_token;
          const expiresAt = typeof token === 'string' ? tokenExpiry(token) : null;
          const platform = typeof token === 'string' ? tokenPlatform(token) : null;
          const courseId = typeof token === 'string' ? tokenCourseId(token) : null;
          const responsePlatform = response.data.platform || 'canvas';
          const responseCourseId = response.data.course_id || null;
          if (
            !token ||
            expiresAt === null ||
            expiresAt <= Date.now() ||
            !platform ||
            platform !== responsePlatform ||
            courseId !== responseCourseId
          ) {
            clearSession(typeof token === 'string' ? token : undefined);
            setSession({
              ...EMPTY_SESSION,
              error: 'Your LTI session has expired. Please relaunch Aelira from your LMS.',
            });
            return;
          }

          const stored: StoredLTISession = {
            accessToken: token,
            courseId,
            courseName: response.data.course_name || null,
            platform,
            accountWide: tokenAccountWide(token),
            expiresAt,
          };
          activateSession(stored);
          setSession(sessionState(stored));
        })
        .catch(() => {
          clearSession(peekStoredToken());
          setSession({
            ...EMPTY_SESSION,
            error: 'Your LTI launch is invalid or expired. Please relaunch Aelira from your LMS.',
          });
        });
      return;
    }

    const stored = readStoredSession();
    if (stored && stored.expiresAt > Date.now()) {
      activateSession(stored);
      setSession(sessionState(stored));
      return;
    }

    clearSession(stored?.accessToken || peekStoredToken());
    setSession({
      ...EMPTY_SESSION,
      error: stored
        ? 'Your LTI session has expired. Please relaunch Aelira from your LMS.'
        : 'No active LTI session. Please launch Aelira from your LMS.',
    });
  }, [location.pathname, location.search, navigate]);
  /* eslint-enable react-hooks/set-state-in-effect */

  useEffect(() => {
    if (!session.accessToken) return;
    const expiresAt = tokenExpiry(session.accessToken);
    if (expiresAt === null) return;
    const delay = expiresAt - Date.now();
    if (delay <= 0) return;
    const timer = window.setTimeout(() => {
      clearSession(session.accessToken || undefined);
      setSession({
        ...EMPTY_SESSION,
        error: 'Your LTI session has expired. Please relaunch Aelira from your LMS.',
      });
    }, delay);
    return () => window.clearTimeout(timer);
  }, [session.accessToken]);

  const value = useMemo(() => session, [session]);
  return (
    <LTISessionContext.Provider value={value}>
      <Outlet />
    </LTISessionContext.Provider>
  );
}

// Provider and hook intentionally share this private context contract.
// eslint-disable-next-line react-refresh/only-export-components
export function useLTISession(enabled: boolean = true): LTISession {
  const session = useContext(LTISessionContext);
  if (!enabled) return EMPTY_SESSION;
  return session ?? {
    ...EMPTY_SESSION,
    error: 'No active LTI session. Please launch Aelira from your LMS.',
  };
}