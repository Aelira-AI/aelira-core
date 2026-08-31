import { useCallback, useEffect, useState, type ReactElement } from 'react';
import { AlertTriangle, BookOpen, ChevronRight, Loader2, RefreshCw } from 'lucide-react';
import { Link } from 'react-router-dom';

import { apiClient } from '../api/client';

interface BrightspaceCourse {
  orgUnitId: string;
  name: string;
  code: string | null;
}

interface RawBrightspaceCourse {
  OrgUnitId?: unknown;
  Name?: unknown;
  Code?: unknown;
  org_unit_id?: unknown;
  name?: unknown;
  code?: unknown;
}

function normalizeCourses(payload: unknown): BrightspaceCourse[] {
  const rawCourses = Array.isArray(payload)
    ? payload
    : payload && typeof payload === 'object' && 'courses' in payload
      ? (payload as { courses?: unknown }).courses
      : [];

  if (!Array.isArray(rawCourses)) return [];

  return rawCourses.flatMap((rawCourse: RawBrightspaceCourse) => {
    const rawId = rawCourse.org_unit_id ?? rawCourse.OrgUnitId;
    const rawName = rawCourse.name ?? rawCourse.Name;
    const rawCode = rawCourse.code ?? rawCourse.Code;
    const orgUnitId =
      typeof rawId === 'number' || typeof rawId === 'string'
        ? String(rawId).trim()
        : '';
    const name = typeof rawName === 'string' ? rawName.trim() : '';

    if (!orgUnitId || !name) return [];

    return [{
      orgUnitId,
      name,
      code: typeof rawCode === 'string' && rawCode.trim() ? rawCode.trim() : null,
    }];
  });
}

export default function BrightspaceCourses(): ReactElement {
  const [courses, setCourses] = useState<BrightspaceCourse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const loadCourses = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(false);

    try {
      const response = await apiClient.get('/brightspace/courses');
      setCourses(normalizeCourses(response.data));
    } catch {
      setCourses([]);
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount; request lifecycle owns the state transition */
  useEffect(() => {
    void loadCourses();
  }, [loadCourses]);
  /* eslint-enable react-hooks/set-state-in-effect */

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <nav aria-label="Breadcrumb" className="mb-3 flex items-center gap-2 text-sm">
        <Link
          to="/integrations"
          className="font-medium text-[var(--content-accent)] hover:underline"
        >
          Integrations
        </Link>
        <ChevronRight className="h-4 w-4 text-[var(--content-tertiary)]" aria-hidden="true" />
        <span aria-current="page" className="text-[var(--content-secondary)]">
          Brightspace Courses
        </span>
      </nav>

      <div className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="font-heading text-2xl font-bold text-[var(--content-primary)]">
            Browse Brightspace courses
          </h1>
          <p className="mt-1 text-[var(--content-secondary)]">
            Choose a course to review and scan its pages, assignments, and files.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void loadCourses()}
          disabled={loading}
          className="inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-lg border border-[var(--border-primary)] bg-[var(--surface-secondary)] px-4 py-2 text-sm font-medium text-[var(--content-primary)] transition-colors hover:bg-[var(--surface-tertiary)] disabled:cursor-wait disabled:opacity-60 sm:w-auto"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />
          Refresh courses
        </button>
      </div>

      {loading ? (
        <div
          role="status"
          aria-live="polite"
          className="flex min-h-64 flex-col items-center justify-center gap-3 rounded-xl border border-[var(--border-primary)] bg-[var(--surface-secondary)] p-8 text-center"
        >
          <Loader2 className="h-8 w-8 animate-spin text-[var(--content-accent)]" aria-hidden="true" />
          <span className="font-medium text-[var(--content-secondary)]">
            Loading Brightspace courses...
          </span>
        </div>
      ) : error ? (
        <section
          role="alert"
          aria-labelledby="brightspace-course-error-title"
          className="rounded-xl border border-[var(--feature-danger-border)] bg-[var(--feature-danger-surface)] p-6"
        >
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-[var(--feature-danger-content)]" aria-hidden="true" />
            <div>
              <h2 id="brightspace-course-error-title" className="font-semibold text-[var(--content-primary)]">
                Unable to load Brightspace courses
              </h2>
              <p className="mt-1 text-sm text-[var(--content-secondary)]">
                Check the Brightspace connection, then try the request again.
              </p>
              <button
                type="button"
                onClick={() => void loadCourses()}
                className="mt-4 rounded-lg bg-[var(--interactive-primary-bg)] px-4 py-2 text-sm font-medium text-[var(--interactive-primary-fg)]"
              >
                Retry
              </button>
            </div>
          </div>
        </section>
      ) : courses.length === 0 ? (
        <section
          aria-labelledby="brightspace-course-empty-title"
          className="rounded-xl border border-[var(--border-primary)] bg-[var(--surface-secondary)] p-8 text-center"
        >
          <BookOpen className="mx-auto h-10 w-10 text-[var(--content-tertiary)]" aria-hidden="true" />
          <h2 id="brightspace-course-empty-title" className="mt-3 font-semibold text-[var(--content-primary)]">
            No Brightspace courses found
          </h2>
          <p className="mx-auto mt-2 max-w-xl text-sm text-[var(--content-secondary)]">
            Confirm that this Brightspace account is enrolled in an active course, or reconnect the integration.
          </p>
          <Link
            to="/integrations"
            className="mt-4 inline-flex min-h-11 items-center rounded-lg border border-[var(--border-primary)] px-4 py-2 text-sm font-medium text-[var(--content-primary)] hover:bg-[var(--surface-tertiary)]"
          >
            Manage integration
          </Link>
        </section>
      ) : (
        <section aria-labelledby="brightspace-course-list-title">
          <div className="mb-4 flex items-end justify-between gap-4">
            <div>
              <h2 id="brightspace-course-list-title" className="text-lg font-semibold text-[var(--content-primary)]">
                Available courses
              </h2>
              <p className="text-sm text-[var(--content-secondary)]">
                {courses.length} {courses.length === 1 ? 'course' : 'courses'} available
              </p>
            </div>
          </div>

          <ul className="grid gap-4 md:grid-cols-2">
            {courses.map((course) => (
              <li key={course.orgUnitId}>
                <Link
                  to={`/brightspace/courses/${encodeURIComponent(course.orgUnitId)}/content`}
                  className="group flex min-h-28 items-center justify-between gap-4 rounded-xl border border-[var(--border-primary)] bg-[var(--surface-secondary)] p-5 transition-colors hover:border-[var(--content-accent)] hover:bg-[var(--surface-tertiary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]"
                  aria-label={`Review content for ${course.name}`}
                >
                  <span className="min-w-0">
                    <span className="block truncate font-semibold text-[var(--content-primary)]">
                      {course.name}
                    </span>
                    {course.code && (
                      <span className="mt-1 block truncate text-sm text-[var(--content-secondary)]">
                        {course.code}
                      </span>
                    )}
                    <span className="mt-3 block text-sm font-medium text-[var(--content-accent)]">
                      Review content
                    </span>
                  </span>
                  <ChevronRight className="h-5 w-5 shrink-0 text-[var(--content-tertiary)] transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
