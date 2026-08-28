import { useState, useEffect } from 'react';
import { getCourseOverview, CourseOverviewResponse, CourseOverviewItem } from '../api/canvasContent';
import { Link } from 'react-router-dom';
import { useLTISession } from '../hooks/useLTISession';
import { LTILayout } from '../components/LTILayout';
import { hasDatedDeadline } from '../types/deadline';
import { Badge, BadgeProps } from '../components/ui/Badge';
import { ScoreChip } from '../components/ui/ScoreChip';
import { ProgressBar } from '../components/ui/ProgressBar';
import { apiClient } from '../api/client';

interface CourseOverviewProps {
  isLTI?: boolean;  // When true, renders without AppLayout chrome
}

const STATUS_CONFIG: Record<string, { label: string; variant: BadgeProps['variant'] }> = {
  critical:    { label: 'Critical',     variant: 'danger'   },
  at_risk:     { label: 'At Risk',      variant: 'warning'  },
  on_track:    { label: 'On Track',     variant: 'accent'   },
  compliant:   { label: 'Compliant',    variant: 'success'  },
  not_started: { label: 'Not Started',  variant: 'neutral'  },
};

function getProgressTone(score: number): 'success' | 'warning' | 'danger' | 'accent' {
  if (score >= 90) return 'success';
  if (score >= 70) return 'accent';
  if (score >= 50) return 'warning';
  return 'danger';
}

export default function CourseOverview({ isLTI = false }: CourseOverviewProps) {
  const [data, setData] = useState<CourseOverviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>('all');
  const [sortBy, setSortBy] = useState<'compliance' | 'issues' | 'name'>('compliance');


  // LTI session validation — only runs when embedded in Canvas iframe
  const ltiSession = useLTISession(isLTI);
  const ltiScopeError =
    isLTI && ltiSession.accessToken && !ltiSession.accountWide
      ? 'This course-scoped LTI session cannot access the account overview.'
      : null;

  useEffect(() => {
    // Wait for LTI session to finish loading before fetching data
    if (isLTI && ltiSession.loading) return;
    if (isLTI && ltiSession.error) return;
    if (ltiScopeError) return;
    loadOverview();
    // Dependencies below are the complete inputs read by loadOverview.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLTI, ltiSession.loading, ltiSession.error, ltiSession.platform, ltiScopeError]);

  async function loadOverview() {
    try {
      setLoading(true);
      const result = isLTI && ltiSession.platform === 'brightspace'
        ? await loadBrightspaceOverview()
        : await getCourseOverview();
      setData(result);
    } catch {
      setError('Failed to load course overview. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  async function loadBrightspaceOverview(): Promise<CourseOverviewResponse> {
    const [response, statsResponse] = await Promise.all([
      apiClient.get('/brightspace/courses'),
      apiClient.get('/education/stats'),
    ]);
    const rawCourses = Array.isArray(response.data)
      ? response.data
      : Array.isArray(response.data?.courses)
        ? response.data.courses
        : [];
    const courses: CourseOverviewItem[] = await Promise.all(
      rawCourses.map(async (course: {
        org_unit_id: number | string;
        name: string;
        code?: string | null;
      }) => {
        const courseId = String(course.org_unit_id);
        const statusResponse = await apiClient.get(
          `/brightspace/content/courses/${encodeURIComponent(courseId)}/status`
        );
        const statusData = statusResponse.data;
        const items = Array.isArray(statusData.items) ? statusData.items : [];
        const totalItems = Number(statusData.total_items) || 0;
        const scannedItems = Number(statusData.scanned_items) || 0;
        const average = typeof statusData.average_compliance === 'number'
          ? statusData.average_compliance
          : null;
        const totalIssues = items.reduce(
          (sum: number, item: { issue_count?: number }) => sum + (item.issue_count || 0),
          0,
        );
        const writtenBack = items.filter(
          (item: { writeback_status?: string | null }) => item.writeback_status === 'written_back'
        ).length;
        const status: CourseOverviewItem['status'] = scannedItems === 0
          ? 'not_started'
          : average !== null && average >= 95
            ? 'compliant'
            : average !== null && average >= 70
              ? 'on_track'
              : average !== null && average >= 50
                ? 'at_risk'
                : 'critical';
        return {
          course_id: courseId,
          course_name: course.name,
          course_code: course.code ?? null,
          total_items: totalItems,
          scanned_items: scannedItems,
          avg_compliance: average,
          total_issues: totalIssues,
          written_back: writtenBack,
          status,
        };
      })
    );
    const scores = courses
      .map((course) => course.avg_compliance)
      .filter((score): score is number => score !== null);
    return {
      total_courses: courses.length,
      total_items: courses.reduce((sum, course) => sum + course.total_items, 0),
      total_scanned: courses.reduce((sum, course) => sum + course.scanned_items, 0),
      avg_compliance: scores.length
        ? scores.reduce((sum, score) => sum + score, 0) / scores.length
        : null,
      total_issues: courses.reduce((sum, course) => sum + course.total_issues, 0),
      courses,
      deadline: statsResponse.data?.stats?.deadline ?? null,
    };
  }

  function getFilteredCourses(): CourseOverviewItem[] {
    if (!data) return [];
    let courses = data.courses;
    if (filter !== 'all') {
      courses = courses.filter(c => c.status === filter);
    }
    courses = [...courses].sort((a, b) => {
      if (sortBy === 'compliance') return (a.avg_compliance ?? -1) - (b.avg_compliance ?? -1);
      if (sortBy === 'issues') return b.total_issues - a.total_issues;
      return a.course_name.localeCompare(b.course_name);
    });
    return courses;
  }

  const courseHref = (courseId: string) => {
    const encodedCourseId = encodeURIComponent(courseId);
    return isLTI
      ? `/lti/course/${encodedCourseId}?from=overview`
      : `/canvas/courses/${encodedCourseId}/content`;
  };

  // When in LTI mode, delegate loading/session-error states to LTILayout
  const isLoading = isLTI
    ? (ltiSession.loading || (!ltiSession.error && !ltiScopeError && loading))
    : loading;

  const content = renderContent();

  if (isLTI) {
    return (
      <LTILayout loading={isLoading} error={ltiSession.error || ltiScopeError}>
        {content}
      </LTILayout>
    );
  }

  if (loading) {
    return (
      <div className="p-8">
        <div className="animate-pulse space-y-4">
          <div className="h-8 bg-[var(--surface-tertiary)] rounded w-1/3" />
          <div className="grid grid-cols-4 gap-4">
            {[1,2,3,4].map(i => <div key={i} className="h-24 bg-[var(--surface-tertiary)] rounded" />)}
          </div>
          <div className="h-64 bg-[var(--surface-tertiary)] rounded" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-8">
        <div className="bg-[var(--feature-danger-surface)] text-[var(--feature-danger-content)] p-4 rounded-lg flex justify-between items-center">
          <span>{error}</span>
          <button onClick={loadOverview} className="underline">Retry</button>
        </div>
      </div>
    );
  }

  return content;

  function renderContent() {
    if (!data) return null;
    const filtered = getFilteredCourses();
    const avgCompliance = data.avg_compliance ?? 0;
    const deadline = hasDatedDeadline(data.deadline) ? data.deadline : null;
    const configurationRequired = data.deadline?.applicability === 'configuration_required';

    return (
      <div className="p-6 max-w-7xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold font-heading text-[var(--content-primary)]">
              {isLTI && ltiSession.platform === 'brightspace' ? 'Course Overview' : 'Compliance Overview'}
            </h1>
            <p className="text-[var(--content-secondary)]">
              {isLTI && ltiSession.platform === 'brightspace'
                ? 'Select a Brightspace course to review its accessibility status'
                : 'Institution-wide accessibility compliance across all courses'}
            </p>
          </div>
          <button
            onClick={loadOverview}
            className="px-4 py-2 border border-[var(--border-primary)] rounded-lg text-[var(--content-secondary)] hover:bg-[var(--surface-tertiary)]"
          >
            Refresh
          </button>
        </div>

        {configurationRequired && (
          <section className="rounded-lg border p-4" style={{ borderColor: 'var(--feature-warning-border)', backgroundColor: 'var(--feature-warning-surface)' }} aria-labelledby="course-regulatory-configuration-title">
            <h2 id="course-regulatory-configuration-title" className="font-semibold text-[var(--content-primary)]">Regulatory deadline setup required</h2>
            <p className="text-sm text-[var(--content-secondary)] mt-1">Contact an institution administrator to verify the regulatory profile before relying on a legal deadline.</p>
          </section>
        )}

        {/* Summary stats */}
        <div className={`grid grid-cols-2 ${deadline ? 'md:grid-cols-5' : 'md:grid-cols-4'} gap-4`}>
          <div className="bg-[var(--surface-secondary)] p-4 rounded-lg border border-[var(--border-primary)]">
            <div className="text-sm text-[var(--content-secondary)]">Overall Compliance</div>
            <div className="text-2xl font-bold text-[var(--content-primary)]">
              {data.avg_compliance !== null ? `${data.avg_compliance.toFixed(0)}%` : '\u2014'}
            </div>
          </div>
          <div className="bg-[var(--surface-secondary)] p-4 rounded-lg border border-[var(--border-primary)]">
            <div className="text-sm text-[var(--content-secondary)]">Courses</div>
            <div className="text-2xl font-bold text-[var(--content-primary)]">{data.total_courses}</div>
          </div>
          <div className="bg-[var(--surface-secondary)] p-4 rounded-lg border border-[var(--border-primary)]">
            <div className="text-sm text-[var(--content-secondary)]">Content Items</div>
            <div className="text-2xl font-bold text-[var(--content-primary)]">{data.total_items}</div>
          </div>
          <div className="bg-[var(--surface-secondary)] p-4 rounded-lg border border-[var(--border-primary)]">
            <div className="text-sm text-[var(--content-secondary)]">Issues Found</div>
            <div className="text-2xl font-bold text-[var(--content-primary)]">{data.total_issues}</div>
          </div>
          {deadline && (
            <div className="bg-[var(--surface-secondary)] p-4 rounded-lg border border-[var(--border-primary)]">
              <div className="text-sm text-[var(--content-secondary)]">Days to Target Date</div>
              <div
                className="text-2xl font-bold"
                style={{ color: deadline.days_remaining < 30 ? 'var(--feature-danger-content)' : 'var(--content-primary)' }}
              >
                {deadline.days_remaining}
              </div>
              <div className="text-xs text-[var(--content-tertiary)] mt-1">
                {deadline.deadline_label}
              </div>
            </div>
          )}
        </div>

        {/* Overall progress bar */}
        <div className="bg-[var(--surface-secondary)] p-4 rounded-lg border border-[var(--border-primary)]">
          <div className="flex justify-between text-sm mb-2">
            <span className="text-[var(--content-secondary)]">Scanned: {data.total_scanned} / {data.total_items}</span>
            <span className="text-[var(--content-secondary)]">{avgCompliance.toFixed(0)}%</span>
          </div>
          <ProgressBar value={avgCompliance} tone={getProgressTone(avgCompliance)} aria-label="Overall compliance progress" />
        </div>

        {/* Filters */}
        <div className="flex flex-wrap gap-2 items-center">
          <span className="text-sm text-[var(--content-secondary)]">Filter:</span>
          {['all', 'critical', 'at_risk', 'on_track', 'compliant', 'not_started'].map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-3 py-1 rounded-full text-sm border ${
                filter === f
                  ? 'bg-[var(--interactive-primary-bg)] text-white border-transparent'
                  : 'border-[var(--border-primary)] text-[var(--content-secondary)] hover:bg-[var(--surface-tertiary)]'
              }`}
            >
              {f === 'all' ? 'All' : STATUS_CONFIG[f]?.label || f}
            </button>
          ))}
          <span className="text-sm text-[var(--content-secondary)] ml-4">Sort:</span>
          <select
            value={sortBy}
            onChange={e => setSortBy(e.target.value as typeof sortBy)}
            className="px-3 py-1 rounded border border-[var(--border-primary)] bg-[var(--surface-secondary)] text-[var(--content-primary)] text-sm"
          >
            <option value="compliance">Compliance</option>
            <option value="issues">Issues</option>
            <option value="name">Name</option>
          </select>
        </div>

        {/* Course table */}
        <div className="bg-[var(--surface-secondary)] rounded-lg border border-[var(--border-primary)] overflow-hidden">
          <table className="w-full">
            <caption className="sr-only">Course compliance overview showing {filtered.length} courses</caption>
            <thead>
              <tr className="border-b border-[var(--border-primary)] text-left text-sm text-[var(--content-secondary)]">
                <th scope="col" className="px-4 py-3 font-medium">Course</th>
                <th scope="col" className="px-4 py-3 font-medium text-center">Items</th>
                <th scope="col" className="px-4 py-3 font-medium text-center">Scanned</th>
                <th scope="col" className="px-4 py-3 font-medium text-center">Score</th>
                <th scope="col" className="px-4 py-3 font-medium text-center">Issues</th>
                <th scope="col" className="px-4 py-3 font-medium text-center">Written Back</th>
                <th scope="col" className="px-4 py-3 font-medium text-center">Status</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(course => {
                const sc = STATUS_CONFIG[course.status] || STATUS_CONFIG.not_started;
                const scoreValue = course.avg_compliance ?? 0;
                return (
                  <tr
                    key={course.course_id}
                    className="border-b border-[var(--border-primary)] hover:bg-[var(--surface-tertiary)]"
                  >
                    <td className="px-4 py-3">
                      <Link
                        to={courseHref(course.course_id)}
                        className="font-medium text-[var(--content-accent)] hover:underline"
                        aria-label={`${course.course_name}, ${scoreValue.toFixed(0)}% compliant, ${course.total_issues} issues`}
                      >
                        {course.course_name}
                      </Link>
                      {course.course_code && (
                        <div className="text-xs text-[var(--content-tertiary)]">{course.course_code}</div>
                      )}
                    </td>
                    <td className="px-4 py-3 text-center text-[var(--content-secondary)]">{course.total_items}</td>
                    <td className="px-4 py-3 text-center text-[var(--content-secondary)]">{course.scanned_items}</td>
                    <td className="px-4 py-3 text-center">
                      <ScoreChip score={course.avg_compliance ?? null} />
                    </td>
                    <td className="px-4 py-3 text-center text-[var(--content-secondary)]">{course.total_issues}</td>
                    <td className="px-4 py-3 text-center text-[var(--content-secondary)]">{course.written_back}</td>
                    <td className="px-4 py-3 text-center">
                      <Badge variant={sc.variant}>{sc.label}</Badge>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {filtered.length === 0 && (
            <div className="p-8 text-center text-[var(--content-secondary)]">
              No courses match the selected filter.
            </div>
          )}
        </div>
      </div>
    );
  }
}
