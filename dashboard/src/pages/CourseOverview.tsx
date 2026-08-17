import { useState, useEffect } from 'react';
import { getCourseOverview, CourseOverviewResponse, CourseOverviewItem } from '../api/canvasContent';
import { useNavigate } from 'react-router-dom';
import { useLTISession } from '../hooks/useLTISession';
import { LTILayout } from '../components/LTILayout';
import { daysUntilAdaTitleIIDeadline } from '../utils/deadlines';
import { Badge, BadgeProps } from '../components/ui/Badge';
import { ScoreChip } from '../components/ui/ScoreChip';
import { ProgressBar } from '../components/ui/ProgressBar';

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
  const navigate = useNavigate();

  // LTI session validation — only runs when embedded in Canvas iframe
  const ltiSession = useLTISession(isLTI);

  useEffect(() => {
    // Wait for LTI session to finish loading before fetching data
    if (isLTI && ltiSession.loading) return;
    if (isLTI && ltiSession.error) return;
    loadOverview();
  }, [isLTI, ltiSession.loading, ltiSession.error]);

  async function loadOverview() {
    try {
      setLoading(true);
      const result = await getCourseOverview();
      setData(result);
    } catch {
      setError('Failed to load course overview. Please try again.');
    } finally {
      setLoading(false);
    }
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

  function handleCourseClick(courseId: string) {
    if (isLTI) {
      navigate(`/lti/course/${courseId}?from=overview`);
    } else {
      navigate(`/canvas/courses/${courseId}/content`);
    }
  }

  // When in LTI mode, delegate loading/session-error states to LTILayout
  const isLoading = isLTI
    ? (ltiSession.loading || loading)
    : loading;

  const content = renderContent();

  if (isLTI) {
    return (
      <LTILayout loading={isLoading} error={ltiSession.error}>
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
    const daysLeft = daysUntilAdaTitleIIDeadline();
    const avgCompliance = data.avg_compliance ?? 0;

    return (
      <div className="p-6 max-w-7xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold font-heading text-[var(--content-primary)]">
              Compliance Overview
            </h1>
            <p className="text-[var(--content-secondary)]">
              Institution-wide accessibility compliance across all courses
            </p>
          </div>
          <button
            onClick={loadOverview}
            className="px-4 py-2 border border-[var(--border-primary)] rounded-lg text-[var(--content-secondary)] hover:bg-[var(--surface-tertiary)]"
          >
            Refresh
          </button>
        </div>

        {/* Summary stats */}
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
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
          <div className="bg-[var(--surface-secondary)] p-4 rounded-lg border border-[var(--border-primary)]">
            <div className="text-sm text-[var(--content-secondary)]">Days to Deadline</div>
            <div
              className="text-2xl font-bold"
              style={{ color: daysLeft < 30 ? 'var(--feature-danger-content)' : 'var(--content-primary)' }}
            >
              {daysLeft}
            </div>
          </div>
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
                    onClick={() => handleCourseClick(course.course_id)}
                    className="border-b border-[var(--border-primary)] hover:bg-[var(--surface-tertiary)] cursor-pointer"
                    role="link"
                    tabIndex={0}
                    onKeyDown={e => (e.key === 'Enter' || e.key === ' ') && handleCourseClick(course.course_id)}
                    aria-label={`${course.course_name}, ${scoreValue.toFixed(0)}% compliant, ${course.total_issues} issues`}
                  >
                    <td className="px-4 py-3">
                      <div className="font-medium text-[var(--content-primary)]">{course.course_name}</div>
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
