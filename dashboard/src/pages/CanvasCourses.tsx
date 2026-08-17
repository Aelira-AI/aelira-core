import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Loader2,
  ChevronDown,
  ChevronRight,
  RefreshCw,
  FileText,
  Upload,
  ExternalLink,
  Check,
  AlertTriangle,
  Search,
  GraduationCap,
} from 'lucide-react';
import { apiClient } from '../api/client';
import { FeatureGate } from '../components/FeatureGate';
import { useToast } from '../context/toast-context';

// ============================================================================
// Type Definitions
// ============================================================================

interface CanvasCourse {
  id: string;
  name: string;
  course_code: string;
  workflow_state: string;
  total_students: number;
  term?: { name: string };
}

interface CanvasFile {
  id: string;
  display_name: string;
  size: number;
  content_type: string;
  url: string;
  created_at: string;
  updated_at: string;
}

interface FileScanStatus {
  provider_file_id: string;
  file_name: string;
  scan_id: string | null;
  compliance_score: number | null;
  issues_count: number;
  status: string; // "not_tracked", "pending", "processing", "completed", "failed"
  has_remediated_version: boolean;
}

interface ScanStatusResponse {
  course_id: string;
  total_files: number;
  scanned_files: number;
  average_compliance: number | null;
  files: FileScanStatus[];
}

interface CanvasStatusResponse {
  connected: boolean;
  canvas_instance_url?: string;
  user_email?: string;
}

// ============================================================================
// Helpers
// ============================================================================

function formatFileSize(bytes: number | null): string {
  if (!bytes) return 'N/A';
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb.toFixed(1)} MB`;
  const gb = mb / 1024;
  return `${gb.toFixed(1)} GB`;
}

function getFileExtension(file: CanvasFile): string {
  const name = file.display_name || '';
  const dotIndex = name.lastIndexOf('.');
  if (dotIndex === -1) {
    // Fall back to content_type
    const typeMap: Record<string, string> = {
      'application/pdf': 'PDF',
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'DOCX',
      'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'PPTX',
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'XLSX',
      'application/msword': 'DOC',
      'application/vnd.ms-powerpoint': 'PPT',
      'application/vnd.ms-excel': 'XLS',
    };
    return typeMap[file.content_type] || 'FILE';
  }
  return name.substring(dotIndex + 1).toUpperCase();
}

function getComplianceColor(score: number | null): string {
  if (score === null) return 'var(--content-secondary)';
  if (score >= 90) return 'var(--status-success-text)';
  if (score >= 70) return 'var(--status-warning-text)';
  return 'var(--status-error-text)';
}

function getFileStatus(
  fileId: string,
  scanStatuses: Record<string, FileScanStatus[]>,
  courseId: string
): FileScanStatus | null {
  const statuses = scanStatuses[courseId];
  if (!statuses) return null;
  return statuses.find((s) => s.provider_file_id === fileId) || null;
}

// ============================================================================
// Component
// ============================================================================

export function CanvasCourses(): React.ReactElement {
  const navigate = useNavigate();
  const toast = useToast();

  // Connection and course state
  const [connected, setConnected] = useState<boolean | null>(null);
  const [courses, setCourses] = useState<CanvasCourse[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [refreshing, setRefreshing] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState<string>('');

  // Expansion and file state
  const [expandedCourse, setExpandedCourse] = useState<string | null>(null);
  const [courseFiles, setCourseFiles] = useState<Record<string, CanvasFile[]>>({});
  const [loadingFiles, setLoadingFiles] = useState<string | null>(null);
  const [scanStatuses, setScanStatuses] = useState<Record<string, FileScanStatus[]>>({});

  // Action states
  const [scanningFiles, setScanningFiles] = useState<Set<string>>(new Set());
  const [remediatingFiles, setRemediatingFiles] = useState<Set<string>>(new Set());
  const [pushingFiles, setPushingFiles] = useState<Set<string>>(new Set());
  const [bulkScanning, setBulkScanning] = useState<string | null>(null);

  // Polling ref
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // --------------------------------------------------
  // Fetch Canvas connection status and courses
  // --------------------------------------------------
  const fetchCourses = useCallback(async (): Promise<void> => {
    try {
      setRefreshing(true);

      // Check connection first
      const statusRes = await apiClient.get<CanvasStatusResponse>('/canvas/status');
      const statusData = statusRes.data;
      const isConnected = statusData?.connected ?? false;
      setConnected(isConnected);

      if (!isConnected) {
        setCourses([]);
        setLoading(false);
        setRefreshing(false);
        return;
      }

      // Fetch courses
      const coursesRes = await apiClient.get('/canvas/courses');
      const coursesData = coursesRes.data;
      // Handle both { courses: [...] } and direct array
      const coursesList: CanvasCourse[] = Array.isArray(coursesData)
        ? coursesData
        : Array.isArray(coursesData?.courses)
          ? coursesData.courses
          : [];
      setCourses(coursesList);
      setError(null);
    } catch (err) {
      console.error('Failed to fetch Canvas courses:', err);
      setError('Failed to load Canvas courses. Please try again.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchCourses();
  }, [fetchCourses]);

  // --------------------------------------------------
  // Expand / collapse course
  // --------------------------------------------------
  const toggleCourse = async (courseId: string): Promise<void> => {
    if (expandedCourse === courseId) {
      // Collapse
      setExpandedCourse(null);
      stopPolling();
      return;
    }

    // Expand
    setExpandedCourse(courseId);

    // Fetch files if not already loaded
    if (!courseFiles[courseId]) {
      setLoadingFiles(courseId);
      try {
        const filesRes = await apiClient.get(`/canvas/courses/${courseId}/files`);
        const filesData = filesRes.data;
        const files: CanvasFile[] = Array.isArray(filesData)
          ? filesData
          : Array.isArray(filesData?.files)
            ? filesData.files
            : [];
        setCourseFiles((prev) => ({ ...prev, [courseId]: files }));

        // Fetch scan statuses for these files
        if (files.length > 0) {
          const fileIds = files.map((f) => f.id).join(',');
          try {
            const statusRes = await apiClient.get<ScanStatusResponse>(
              `/canvas/courses/${courseId}/scan-status?file_ids=${fileIds}`
            );
            const statusData = statusRes.data;
            setScanStatuses((prev) => ({
              ...prev,
              [courseId]: statusData?.files || [],
            }));
          } catch (statusErr) {
            console.error('Failed to fetch scan statuses:', statusErr);
          }
        }
      } catch (err) {
        console.error('Failed to fetch course files:', err);
        toast.error('Failed to load course files.', 'Error');
      } finally {
        setLoadingFiles(null);
      }
    }
  };

  // --------------------------------------------------
  // Polling for in-progress files
  // --------------------------------------------------
  const stopPolling = useCallback((): void => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  const startPolling = useCallback(
    (courseId: string, fileIds: string[]): void => {
      stopPolling();

      pollingRef.current = setInterval(async () => {
        try {
          const ids = fileIds.join(',');
          const res = await apiClient.get<ScanStatusResponse>(
            `/canvas/courses/${courseId}/scan-status?file_ids=${ids}`
          );
          const statusData = res.data;
          const files = statusData?.files || [];

          setScanStatuses((prev) => ({
            ...prev,
            [courseId]: files,
          }));

          // Clear scanning/remediating flags for completed files
          const completedIds = new Set(
            files
              .filter(
                (f) =>
                  f.status === 'completed' || f.status === 'failed' || f.status === 'not_tracked'
              )
              .map((f) => f.provider_file_id)
          );

          // Notify when files finish scanning
          const newlyCompleted = files.filter(
            (f) => f.status === 'completed' && (scanningFiles.has(f.provider_file_id))
          );
          if (newlyCompleted.length === 1) {
            const f = newlyCompleted[0];
            const issueText = f.issues_count === 0
              ? 'No issues found — this file is accessible!'
              : `${f.issues_count} issue${f.issues_count > 1 ? 's' : ''} found. Click Remediate to fix.`;
            toast.success(issueText, 'Scan Complete');
          } else if (newlyCompleted.length > 1) {
            const totalIssues = newlyCompleted.reduce((sum, f) => sum + f.issues_count, 0);
            toast.success(
              `${newlyCompleted.length} files scanned. ${totalIssues} total issue${totalIssues !== 1 ? 's' : ''} found.`,
              'Scan Complete'
            );
          }

          setScanningFiles((prev) => {
            const next = new Set(prev);
            completedIds.forEach((id) => next.delete(id));
            return next;
          });
          setRemediatingFiles((prev) => {
            const next = new Set(prev);
            completedIds.forEach((id) => next.delete(id));
            return next;
          });

          // Stop polling if no files are in progress
          const hasInProgress = files.some(
            (f) => f.status === 'pending' || f.status === 'processing'
          );
          if (!hasInProgress) {
            stopPolling();
            setBulkScanning(null);
          }
        } catch (err) {
          console.error('Polling error:', err);
        }
      }, 2000);
    },
    [stopPolling, scanningFiles, toast]
  );

  // Start polling when files are scanning or remediating
  useEffect(() => {
    if (expandedCourse && (scanningFiles.size > 0 || remediatingFiles.size > 0)) {
      const files = courseFiles[expandedCourse] || [];
      if (files.length > 0) {
        startPolling(
          expandedCourse,
          files.map((f) => f.id)
        );
      }
    }

    return () => {
      stopPolling();
    };
  }, [expandedCourse, scanningFiles.size, remediatingFiles.size, courseFiles, startPolling, stopPolling]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopPolling();
    };
  }, [stopPolling]);

  // --------------------------------------------------
  // Actions
  // --------------------------------------------------
  const handleScanFile = async (fileId: string, courseId: string): Promise<void> => {
    setScanningFiles((prev) => new Set(prev).add(fileId));
    try {
      await apiClient.post('/canvas/scan', { file_id: fileId, course_id: courseId });
      toast.info('Scan started', 'Scanning');
    } catch (err) {
      console.error('Failed to scan file:', err);
      toast.error('Failed to start scan.', 'Scan Error');
      setScanningFiles((prev) => {
        const next = new Set(prev);
        next.delete(fileId);
        return next;
      });
    }
  };

  const handleScanAll = async (courseId: string): Promise<void> => {
    setBulkScanning(courseId);
    try {
      const res = await apiClient.post('/canvas/scan/bulk', { course_id: courseId });
      const data = res.data;
      const jobs = data?.jobs || [];
      // Mark all files as scanning
      const newScanning = new Set(scanningFiles);
      jobs.forEach((job: { cloud_file_id?: string; file_id?: string }) => {
        const id = job.cloud_file_id || job.file_id;
        if (id) newScanning.add(String(id));
      });
      setScanningFiles(newScanning);
      toast.info(`Scanning ${jobs.length} files`, 'Bulk Scan Started');
    } catch (err) {
      console.error('Failed to start bulk scan:', err);
      toast.error('Failed to start bulk scan.', 'Error');
      setBulkScanning(null);
    }
  };

  const handleRemediateFile = async (fileId: string, courseId: string): Promise<void> => {
    const status = getFileStatus(fileId, scanStatuses, courseId);
    if (!status?.scan_id) {
      toast.error('File must be scanned first.', 'Error');
      return;
    }
    // Navigate to the remediation page which handles the full flow
    toast.info('Opening remediation page. Come back here to push the fixed file to Canvas.', 'Remediating');
    navigate(`/remediate/${status.scan_id}`);
  };

  const handleRemediateAll = async (courseId: string): Promise<void> => {
    const statuses = scanStatuses[courseId] || [];
    const toRemediate = statuses.filter(
      (s) => s.status === 'completed' && s.issues_count > 0 && !s.has_remediated_version
    );
    if (toRemediate.length === 0) {
      toast.info('No files need remediation.', 'Nothing to Do');
      return;
    }
    // This used to loop over the files calling the single-file handler,
    // which navigates away: the route change unmounted the page and every
    // file after the first was silently never touched. The course content
    // view remediates the whole course in place, so send the user there.
    toast.info(
      `${toRemediate.length} items need remediation. Opening the course content view.`,
      'Remediate All'
    );
    navigate(`/canvas/courses/${courseId}/content`);
  };

  const handlePushToCanvas = async (scanId: string, courseId: string, fileId: string): Promise<void> => {
    setPushingFiles((prev) => new Set(prev).add(fileId));
    try {
      const res = await apiClient.post('/canvas/upload-remediated', {
        scan_id: scanId,
        course_id: courseId,
      });
      const data = res.data;
      toast.success(
        `"${data?.file_name || 'File'}" pushed to Canvas`,
        'Upload Complete'
      );
    } catch (err) {
      console.error('Failed to push to Canvas:', err);
      toast.error('Failed to upload to Canvas.', 'Upload Error');
    } finally {
      setPushingFiles((prev) => {
        const next = new Set(prev);
        next.delete(fileId);
        return next;
      });
    }
  };

  // --------------------------------------------------
  // Filtered courses
  // --------------------------------------------------
  const filteredCourses = searchQuery
    ? courses.filter(
        (c) =>
          c.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
          c.course_code.toLowerCase().includes(searchQuery.toLowerCase())
      )
    : courses;

  // --------------------------------------------------
  // Course compliance summary
  // --------------------------------------------------
  const getCourseCompliance = (courseId: string): { scanned: number; total: number; avg: number | null } => {
    const files = courseFiles[courseId] || [];
    const statuses = scanStatuses[courseId] || [];
    const scannedStatuses = statuses.filter((s) => s.status === 'completed');
    const scores = scannedStatuses
      .map((s) => s.compliance_score)
      .filter((s): s is number => s !== null);
    const avg = scores.length > 0 ? scores.reduce((a, b) => a + b, 0) / scores.length : null;
    return { scanned: scannedStatuses.length, total: files.length, avg };
  };

  // --------------------------------------------------
  // Render helpers
  // --------------------------------------------------
  const renderStatusBadge = (
    fileId: string,
    courseId: string
  ): React.ReactElement => {
    const isScanning = scanningFiles.has(fileId);
    const isRemediating = remediatingFiles.has(fileId);
    const status = getFileStatus(fileId, scanStatuses, courseId);

    if (isScanning || status?.status === 'pending' || status?.status === 'processing') {
      return (
        <span
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
          style={{ backgroundColor: 'var(--feature-info-surface)', color: 'var(--feature-info-content)' }}
        >
          <Loader2 className="w-3 h-3 animate-spin" />
          Scanning...
        </span>
      );
    }

    if (isRemediating) {
      return (
        <span
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
          style={{ backgroundColor: 'var(--feature-primary-surface)', color: 'var(--feature-primary-content)' }}
        >
          <Loader2 className="w-3 h-3 animate-spin" />
          Remediating...
        </span>
      );
    }

    if (status?.has_remediated_version) {
      return (
        <span
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
          style={{ backgroundColor: 'var(--feature-success-surface)', color: 'var(--feature-success-content)' }}
        >
          <Check className="w-3 h-3" />
          Remediated
        </span>
      );
    }

    if (status?.status === 'completed' && status.issues_count > 0) {
      return (
        <span
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
          style={{ backgroundColor: 'var(--feature-warning-surface)', color: 'var(--feature-warning-content)' }}
        >
          <AlertTriangle className="w-3 h-3" />
          {status.issues_count} issue{status.issues_count !== 1 ? 's' : ''}
        </span>
      );
    }

    if (status?.status === 'completed' && status.issues_count === 0) {
      return (
        <span
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
          style={{ backgroundColor: 'var(--feature-success-surface)', color: 'var(--feature-success-content)' }}
        >
          <Check className="w-3 h-3" />
          Compliant
        </span>
      );
    }

    if (status?.status === 'failed') {
      return (
        <span
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
          style={{ backgroundColor: 'var(--feature-danger-surface)', color: 'var(--feature-danger-content)' }}
        >
          <AlertTriangle className="w-3 h-3" />
          Failed
        </span>
      );
    }

    // Not scanned
    return (
      <span
        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
        style={{ backgroundColor: 'var(--surface-tertiary)', color: 'var(--content-secondary)' }}
      >
        Not scanned
      </span>
    );
  };

  const renderFileActions = (
    file: CanvasFile,
    courseId: string
  ): React.ReactElement => {
    const status = getFileStatus(file.id, scanStatuses, courseId);
    const isScanning = scanningFiles.has(file.id) || status?.status === 'pending' || status?.status === 'processing';
    const isRemediating = remediatingFiles.has(file.id);
    const isPushing = pushingFiles.has(file.id);

    return (
      <div className="flex items-center justify-end gap-2">
        {/* Scan button */}
        {(!status || status.status === 'not_tracked' || status.status === 'failed') && !isScanning && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              handleScanFile(file.id, courseId);
            }}
            className="px-3 py-1.5 rounded-lg text-xs font-medium text-white transition-colors"
            style={{ backgroundColor: 'var(--accent-primary)' }}
          >
            Scan
          </button>
        )}

        {/* View Results button */}
        {status?.scan_id && status.status === 'completed' && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              navigate(`/scan/${status.scan_id}`);
            }}
            className="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
            style={{ backgroundColor: 'var(--surface-tertiary)', color: 'var(--content-primary)' }}
          >
            View Results
          </button>
        )}

        {/* Remediate button */}
        {status?.status === 'completed' &&
          status.issues_count > 0 &&
          !status.has_remediated_version &&
          !isRemediating && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                handleRemediateFile(file.id, courseId);
              }}
              className="px-3 py-1.5 rounded-lg text-xs font-medium text-white transition-colors"
              style={{ backgroundColor: 'var(--interactive-accent-bg)' }}
            >
              Remediate
            </button>
          )}

        {/* Push to Canvas button */}
        {status?.has_remediated_version && status.scan_id && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              handlePushToCanvas(status.scan_id!, courseId, file.id);
            }}
            disabled={isPushing}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-white transition-colors disabled:opacity-50"
            style={{ backgroundColor: 'var(--accent-primary)' }}
          >
            {isPushing ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <Upload className="w-3 h-3" />
            )}
            Push to Canvas
          </button>
        )}
      </div>
    );
  };

  // --------------------------------------------------
  // Loading state
  // --------------------------------------------------
  if (loading) {
    return (
      <FeatureGate
        feature="showIntegrations"
        featureName="Canvas Courses"
        description="Browse and scan files from your Canvas LMS courses for accessibility compliance."
      >
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin" style={{ color: 'var(--accent-primary)' }} />
        </div>
      </FeatureGate>
    );
  }

  // --------------------------------------------------
  // Main render
  // --------------------------------------------------
  return (
    <FeatureGate
      feature="showIntegrations"
      featureName="Canvas Courses"
      description="Browse and scan files from your Canvas LMS courses for accessibility compliance."
    >
      <div className="max-w-7xl mx-auto px-4 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <Link
                to="/integrations"
                className="text-sm font-medium hover:opacity-80 transition-opacity"
                style={{ color: 'var(--content-accent)' }}
              >
                Integrations
              </Link>
              <ChevronRight className="w-4 h-4" style={{ color: 'var(--content-tertiary)' }} />
              <span className="text-sm font-medium" style={{ color: 'var(--content-secondary)' }}>
                Canvas Courses
              </span>
            </div>
            <h1
              className="text-2xl font-bold font-serif"
              style={{ color: 'var(--content-primary)' }}
            >
              Canvas Courses
            </h1>
            <p style={{ color: 'var(--content-secondary)' }}>
              Browse your Canvas LMS courses, scan files for accessibility issues, and remediate
              them.
            </p>
          </div>
          <div className="flex gap-3">
            <button
              onClick={fetchCourses}
              disabled={refreshing}
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
              style={{
                backgroundColor: 'var(--surface-tertiary)',
                color: 'var(--content-primary)',
              }}
            >
              <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
              Refresh
            </button>
            <Link
              to="/integrations"
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
              style={{ backgroundColor: 'var(--surface-tertiary)', color: 'var(--content-primary)' }}
            >
              <ExternalLink className="w-4 h-4" />
              Manage Integrations
            </Link>
          </div>
        </div>

        {/* Error */}
        {error && (
          <div
            className="mb-6 p-4 rounded-lg"
            style={{
              backgroundColor: 'var(--feature-danger-surface)',
              color: 'var(--feature-danger-content)',
            }}
          >
            {error}
          </div>
        )}

        {/* Not connected state */}
        {connected === false && (
          <div
            className="rounded-xl p-12 text-center"
            style={{ backgroundColor: 'var(--surface-secondary)' }}
          >
            <GraduationCap
              className="w-16 h-16 mx-auto mb-4"
              style={{ color: 'var(--content-tertiary)' }}
            />
            <h3
              className="text-lg font-semibold font-serif mb-2"
              style={{ color: 'var(--content-primary)' }}
            >
              Connect Canvas to browse courses
            </h3>
            <p className="mb-6" style={{ color: 'var(--content-secondary)' }}>
              Link your Canvas LMS account to scan course files for accessibility compliance.
            </p>
            <Link
              to="/integrations"
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white"
              style={{ backgroundColor: 'var(--accent-primary)' }}
            >
              <ExternalLink className="w-4 h-4" />
              Connect Canvas
            </Link>
          </div>
        )}

        {/* Connected but no courses */}
        {connected && courses.length === 0 && !error && (
          <div
            className="rounded-xl p-12 text-center"
            style={{ backgroundColor: 'var(--surface-secondary)' }}
          >
            <GraduationCap
              className="w-16 h-16 mx-auto mb-4"
              style={{ color: 'var(--content-tertiary)' }}
            />
            <h3
              className="text-lg font-semibold font-serif mb-2"
              style={{ color: 'var(--content-primary)' }}
            >
              No courses found
            </h3>
            <p className="mb-6" style={{ color: 'var(--content-secondary)' }}>
              No courses were found in your Canvas account. Check your Canvas connection settings.
            </p>
            <Link
              to="/integrations"
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white"
              style={{ backgroundColor: 'var(--accent-primary)' }}
            >
              <ExternalLink className="w-4 h-4" />
              Check Integrations
            </Link>
          </div>
        )}

        {/* Course list */}
        {connected && courses.length > 0 && (
          <>
            {/* Search */}
            <div className="mb-6">
              <div
                className="flex items-center gap-3 px-4 py-2.5 rounded-lg"
                style={{
                  backgroundColor: 'var(--surface-secondary)',
                  border: '1px solid var(--border-primary)',
                }}
              >
                <Search className="w-4 h-4" style={{ color: 'var(--content-tertiary)' }} />
                <input
                  type="text"
                  placeholder="Search courses..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="flex-1 bg-transparent outline-none text-sm"
                  style={{ color: 'var(--content-primary)' }}
                />
                {searchQuery && (
                  <span className="text-xs" style={{ color: 'var(--content-secondary)' }}>
                    {filteredCourses.length} result{filteredCourses.length !== 1 ? 's' : ''}
                  </span>
                )}
              </div>
            </div>

            {/* Course cards */}
            <div className="space-y-3">
              {filteredCourses.map((course) => {
                const isExpanded = expandedCourse === course.id;
                const files = courseFiles[course.id] || [];
                const isLoadingFiles = loadingFiles === course.id;
                const compliance = getCourseCompliance(course.id);
                const isBulkScanning = bulkScanning === course.id;

                return (
                  <div
                    key={course.id}
                    className="rounded-xl overflow-hidden"
                    style={{
                      backgroundColor: 'var(--surface-secondary)',
                      border: '1px solid var(--border-primary)',
                    }}
                  >
                    {/* Course header (clickable) */}
                    <button
                      onClick={() => toggleCourse(course.id)}
                      className="w-full text-left px-6 py-4 flex items-center justify-between transition-colors hover:opacity-90"
                      style={{ backgroundColor: 'var(--surface-secondary)' }}
                    >
                      <div className="flex items-center gap-4">
                        {isExpanded ? (
                          <ChevronDown
                            className="w-5 h-5 shrink-0"
                            style={{ color: 'var(--content-secondary)' }}
                          />
                        ) : (
                          <ChevronRight
                            className="w-5 h-5 shrink-0"
                            style={{ color: 'var(--content-secondary)' }}
                          />
                        )}
                        <div>
                          <h3
                            className="text-lg font-semibold font-serif"
                            style={{ color: 'var(--content-primary)' }}
                          >
                            {course.name}
                          </h3>
                          <div className="flex items-center gap-3 mt-1">
                            <span
                              className="px-2 py-0.5 rounded text-xs font-medium"
                              style={{
                                backgroundColor: 'var(--surface-tertiary)',
                                color: 'var(--content-secondary)',
                              }}
                            >
                              {course.course_code}
                            </span>
                            {course.term?.name && (
                              <span className="text-xs" style={{ color: 'var(--content-tertiary)' }}>
                                {course.term.name}
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-4">
                        {files.length > 0 && (
                          <span className="text-sm" style={{ color: 'var(--content-secondary)' }}>
                            {files.length} file{files.length !== 1 ? 's' : ''}
                          </span>
                        )}
                        {compliance.avg !== null && (
                          <span
                            className="text-sm font-semibold tabular-nums"
                            style={{ color: getComplianceColor(compliance.avg) }}
                          >
                            {compliance.avg.toFixed(0)}%
                          </span>
                        )}
                      </div>
                    </button>

                    {/* Expanded content */}
                    {isExpanded && (
                      <div
                        style={{ borderTop: '1px solid var(--border-primary)' }}
                      >
                        {/* Native course content (pages, assignments, etc.) */}
                        <div
                          className="px-6 py-3 flex items-center justify-between"
                          style={{ borderBottom: '1px solid var(--border-primary)' }}
                        >
                          <span className="text-sm" style={{ color: 'var(--content-secondary)' }}>
                            Files uploaded to this course are listed below.
                          </span>
                          <Link
                            to={`/canvas/courses/${course.id}/content`}
                            className="text-sm font-medium hover:underline"
                            style={{ color: 'var(--accent-primary)' }}
                          >
                            Show Course Content
                          </Link>
                        </div>
                        {/* Loading files */}
                        {isLoadingFiles && (
                          <div className="flex items-center justify-center py-12">
                            <Loader2
                              className="w-6 h-6 animate-spin"
                              style={{ color: 'var(--accent-primary)' }}
                            />
                          </div>
                        )}

                        {/* No files */}
                        {!isLoadingFiles && files.length === 0 && (
                          <div className="py-8 text-center">
                            <FileText
                              className="w-10 h-10 mx-auto mb-2"
                              style={{ color: 'var(--content-tertiary)' }}
                            />
                            <p className="text-sm" style={{ color: 'var(--content-secondary)' }}>
                              No files found in this course.
                            </p>
                          </div>
                        )}

                        {/* File table */}
                        {!isLoadingFiles && files.length > 0 && (
                          <>
                            {/* Course action bar */}
                            <div
                              className="flex items-center justify-between px-6 py-3"
                              style={{ backgroundColor: 'var(--surface-tertiary)' }}
                            >
                              <div className="flex items-center gap-4">
                                <span
                                  className="text-sm font-medium"
                                  style={{ color: 'var(--content-primary)' }}
                                >
                                  {compliance.scanned} of {compliance.total} scanned
                                </span>
                                {compliance.avg !== null && (
                                  <span
                                    className="text-sm font-semibold tabular-nums"
                                    style={{ color: getComplianceColor(compliance.avg) }}
                                  >
                                    Avg: {compliance.avg.toFixed(0)}%
                                  </span>
                                )}
                              </div>
                              <div className="flex items-center gap-2">
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleScanAll(course.id);
                                  }}
                                  disabled={isBulkScanning}
                                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-white transition-colors disabled:opacity-50"
                                  style={{ backgroundColor: 'var(--accent-primary)' }}
                                >
                                  {isBulkScanning ? (
                                    <Loader2 className="w-3 h-3 animate-spin" />
                                  ) : (
                                    <Search className="w-3 h-3" />
                                  )}
                                  Scan All
                                </button>
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleRemediateAll(course.id);
                                  }}
                                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-white transition-colors"
                                  style={{ backgroundColor: 'var(--interactive-accent-bg)' }}
                                >
                                  Remediate All
                                </button>
                              </div>
                            </div>

                            {/* Table */}
                            <div className="overflow-x-auto">
                              <table className="w-full">
                                <thead>
                                  <tr
                                    style={{
                                      borderBottom: '1px solid var(--border-primary)',
                                    }}
                                  >
                                    <th
                                      className="text-left px-6 py-3 text-xs font-semibold"
                                      style={{ color: 'var(--content-secondary)' }}
                                    >
                                      Name
                                    </th>
                                    <th
                                      className="text-left px-6 py-3 text-xs font-semibold"
                                      style={{ color: 'var(--content-secondary)' }}
                                    >
                                      Type
                                    </th>
                                    <th
                                      className="text-left px-6 py-3 text-xs font-semibold"
                                      style={{ color: 'var(--content-secondary)' }}
                                    >
                                      Size
                                    </th>
                                    <th
                                      className="text-left px-6 py-3 text-xs font-semibold"
                                      style={{ color: 'var(--content-secondary)' }}
                                    >
                                      Compliance
                                    </th>
                                    <th
                                      className="text-left px-6 py-3 text-xs font-semibold"
                                      style={{ color: 'var(--content-secondary)' }}
                                    >
                                      Status
                                    </th>
                                    <th
                                      className="text-right px-6 py-3 text-xs font-semibold"
                                      style={{ color: 'var(--content-secondary)' }}
                                    >
                                      Actions
                                    </th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {files.map((file, index) => {
                                    const fileStatus = getFileStatus(
                                      file.id,
                                      scanStatuses,
                                      course.id
                                    );
                                    return (
                                      <tr
                                        key={file.id}
                                        style={{
                                          borderBottom:
                                            index !== files.length - 1
                                              ? '1px solid var(--border-primary)'
                                              : 'none',
                                        }}
                                      >
                                        {/* Name */}
                                        <td className="px-6 py-3">
                                          <div className="flex items-center gap-2">
                                            <FileText
                                              className="w-4 h-4 shrink-0"
                                              style={{ color: 'var(--content-secondary)' }}
                                            />
                                            <span
                                              className="text-sm font-medium truncate max-w-[280px]"
                                              style={{ color: 'var(--content-primary)' }}
                                              title={file.display_name}
                                            >
                                              {file.display_name}
                                            </span>
                                          </div>
                                        </td>
                                        {/* Type */}
                                        <td className="px-6 py-3">
                                          <span
                                            className="px-2 py-0.5 rounded text-xs font-medium uppercase"
                                            style={{
                                              backgroundColor: 'var(--surface-tertiary)',
                                              color: 'var(--content-secondary)',
                                            }}
                                          >
                                            {getFileExtension(file)}
                                          </span>
                                        </td>
                                        {/* Size */}
                                        <td className="px-6 py-3">
                                          <span
                                            className="text-sm"
                                            style={{ color: 'var(--content-secondary)' }}
                                          >
                                            {formatFileSize(file.size)}
                                          </span>
                                        </td>
                                        {/* Compliance */}
                                        <td className="px-6 py-3">
                                          {fileStatus?.compliance_score !== null &&
                                          fileStatus?.compliance_score !== undefined ? (
                                            <span
                                              className="text-sm font-semibold tabular-nums"
                                              style={{
                                                color: getComplianceColor(
                                                  fileStatus.compliance_score
                                                ),
                                              }}
                                            >
                                              {fileStatus.compliance_score.toFixed(0)}%
                                            </span>
                                          ) : (
                                            <span
                                              className="text-sm"
                                              style={{ color: 'var(--content-tertiary)' }}
                                            >
                                              --
                                            </span>
                                          )}
                                        </td>
                                        {/* Status */}
                                        <td className="px-6 py-3">
                                          {renderStatusBadge(file.id, course.id)}
                                        </td>
                                        {/* Actions */}
                                        <td className="px-6 py-3">
                                          {renderFileActions(file, course.id)}
                                        </td>
                                      </tr>
                                    );
                                  })}
                                </tbody>
                              </table>
                            </div>
                          </>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>

            {/* No search results */}
            {searchQuery && filteredCourses.length === 0 && (
              <div
                className="rounded-xl p-8 text-center"
                style={{ backgroundColor: 'var(--surface-secondary)' }}
              >
                <Search
                  className="w-10 h-10 mx-auto mb-3"
                  style={{ color: 'var(--content-tertiary)' }}
                />
                <p className="text-sm" style={{ color: 'var(--content-secondary)' }}>
                  No courses match "{searchQuery}"
                </p>
                <button
                  onClick={() => setSearchQuery('')}
                  className="mt-3 text-sm font-medium hover:opacity-80 transition-opacity"
                  style={{ color: 'var(--content-accent)' }}
                >
                  Clear search
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </FeatureGate>
  );
}

export default CanvasCourses;
