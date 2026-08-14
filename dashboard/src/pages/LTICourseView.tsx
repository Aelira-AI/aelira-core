import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  FileText,
  RefreshCw,
  ExternalLink,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Shield,
  Search,
  Upload,
  BookOpen,
  ClipboardList,
  Megaphone,
  MessageSquare,
  HelpCircle,
  Settings2,
  ArrowLeft,
} from 'lucide-react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { LTILayout } from '../components/LTILayout';
import { useLTISession } from '../hooks/useLTISession';
import { apiClient } from '../api/client';
import type { AxiosInstance } from 'axios';

// ============================================================================
// Type Definitions
// ============================================================================

interface ScanOptions {
  generate_alt_text: boolean;
  auto_remediate: boolean;
  detect_decorative: boolean;
}

interface CourseFile {
  id: string;
  display_name: string;
  filename: string;
  content_type: string;
  size: number;
  url: string;
}

interface FileScanStatus {
  file_id: string;
  scan_id: string | null;
  compliance_score: number | null;
  has_remediated_version: boolean;
  status: string; // "not_scanned", "scanning", "scanned", "remediating", "remediated", "failed"
}

interface CourseMeta {
  name: string;
  compliance_percentage: number | null;
}

type FileStatus = 'not_scanned' | 'scanning' | 'scanned' | 'remediating' | 'remediated' | 'failed';

type ActiveTab = 'content' | 'files';

// Content tab types
interface ContentTypeStatus {
  content_type: string;
  total: number;
  scanned: number;
  average_compliance: number | null;
  issues: number;
}

interface ContentItemStatus {
  cloud_file_id: string;
  title: string;
  content_type: string;
  compliance_score: number | null;
  issue_count: number;
  writeback_status: string | null;
  content_updated_at: string | null;
}

interface CourseContentStatusResponse {
  course_id: string;
  overall_compliance: number | null;
  by_type: ContentTypeStatus[];
  items: ContentItemStatus[];
}

// ============================================================================
// Helpers
// ============================================================================

function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 B';
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  const mb = kb / 1024;
  return `${mb.toFixed(1)} MB`;
}

function getFileExtension(file: CourseFile): string {
  const name = file.display_name || file.filename || '';
  const dotIndex = name.lastIndexOf('.');
  if (dotIndex !== -1) {
    return name.substring(dotIndex + 1).toUpperCase();
  }
  const typeMap: Record<string, string> = {
    'application/pdf': 'PDF',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'DOCX',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'PPTX',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'XLSX',
    'application/msword': 'DOC',
    'application/vnd.ms-powerpoint': 'PPT',
    'application/vnd.ms-excel': 'XLS',
    'image/png': 'PNG',
    'image/jpeg': 'JPG',
    'text/html': 'HTML',
  };
  return typeMap[file.content_type] || 'FILE';
}

function getComplianceColor(score: number | null): string {
  if (score === null) return 'var(--content-secondary)';
  if (score >= 90) return 'var(--content-success)';
  if (score >= 70) return 'var(--content-warning)';
  return 'var(--content-error)';
}

function resolveFileStatus(
  fileId: string,
  scanStatusMap: Map<string, FileScanStatus>,
  actionStates: { scanning: Set<string>; remediating: Set<string> }
): FileStatus {
  if (actionStates.scanning.has(fileId)) return 'scanning';
  if (actionStates.remediating.has(fileId)) return 'remediating';
  const status = scanStatusMap.get(fileId);
  if (!status) return 'not_scanned';
  if (status.status === 'scanning' || status.status === 'pending' || status.status === 'processing') return 'scanning';
  if (status.status === 'remediating') return 'remediating';
  if (status.has_remediated_version || status.status === 'remediated') return 'remediated';
  if (status.status === 'scanned' || status.status === 'completed') return 'scanned';
  if (status.status === 'failed') return 'failed';
  return 'not_scanned';
}

const CONTENT_TYPE_ICONS: Record<string, React.ElementType> = {
  page: BookOpen,
  assignment: ClipboardList,
  announcement: Megaphone,
  discussion: MessageSquare,
  quiz: HelpCircle,
};

function getContentTypeIcon(contentType: string): React.ElementType {
  return CONTENT_TYPE_ICONS[contentType.toLowerCase()] || FileText;
}

function getContentTypeBadgeColor(contentType: string): { bg: string; text: string } {
  const colors: Record<string, { bg: string; text: string }> = {
    page:         { bg: 'var(--surface-accent-subtle)',              text: 'var(--content-accent)' },
    assignment:   { bg: 'var(--surface-secondary)',        text: 'var(--content-secondary)' },
    announcement: { bg: 'var(--surface-warning-subtle)',   text: 'var(--content-warning)' },
    discussion:   { bg: 'var(--surface-success-subtle)',   text: 'var(--content-success)' },
    quiz:         { bg: 'var(--surface-secondary)',        text: 'var(--content-secondary)' },
  };
  return colors[contentType.toLowerCase()] || { bg: 'var(--surface-primary)', text: 'var(--content-secondary)' };
}

function getWritebackBadge(status: string | null): { label: string; bg: string; color: string } {
  if (!status) return { label: 'Pending', bg: 'transparent', color: 'var(--content-secondary)' };
  switch (status.toLowerCase()) {
    case 'approved':
      return { label: 'Approved', bg: 'var(--surface-accent-subtle)', color: 'var(--content-accent)' };
    case 'written_back':
    case 'writtenback':
      return { label: 'Written Back', bg: 'var(--surface-success-subtle)', color: 'var(--content-success)' };
    case 'failed':
      return { label: 'Failed', bg: 'var(--surface-error-subtle)', color: 'var(--content-error)' };
    case 'writing':
      return { label: 'Writing...', bg: 'var(--surface-warning-subtle)', color: 'var(--content-warning)' };
    default:
      return { label: status, bg: 'transparent', color: 'var(--content-secondary)' };
  }
}

// ============================================================================
// Component
// ============================================================================

export function LTICourseView(): React.ReactElement {
  const { accessToken, courseId: sessionCourseId, courseName: sessionCourseName, platform, loading: sessionLoading, error: sessionError } = useLTISession();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const fromOverview = searchParams.get('from') === 'overview';

  // Provider-aware API prefix for Canvas vs Brightspace
  const apiPrefix = platform === 'brightspace' ? '/brightspace' : '/canvas';

  // Tab state — default to content (demo focus)
  const [activeTab, setActiveTab] = useState<ActiveTab>('content');

  // Files tab state
  const [files, setFiles] = useState<CourseFile[]>([]);
  const [scanStatuses, setScanStatuses] = useState<FileScanStatus[]>([]);
  const [courseMeta, setCourseMeta] = useState<CourseMeta>({ name: '', compliance_percentage: null });
  const [loadingData, setLoadingData] = useState(true);
  const [dataError, setDataError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  // Files action states
  const [scanningFiles, setScanningFiles] = useState<Set<string>>(new Set());
  const [remediatingFiles, setRemediatingFiles] = useState<Set<string>>(new Set());
  const [openingAelira, setOpeningAelira] = useState(false);

  // Content tab state
  const [contentData, setContentData] = useState<CourseContentStatusResponse | null>(null);
  const [contentLoading, setContentLoading] = useState(false);
  const [contentError, setContentError] = useState<string | null>(null);
  const [contentScanning, setContentScanning] = useState(false);
  const [batchApproving, setBatchApproving] = useState(false);
  const [batchWritingBack, setBatchWritingBack] = useState(false);
  const [showScanOptions, setShowScanOptions] = useState(false);
  const [scanOptions, setScanOptions] = useState<ScanOptions>({
    generate_alt_text: true,
    auto_remediate: true,
    detect_decorative: true,
  });

  // Scan options panel ref (for outside-click and Escape dismissal)
  const scanOptionsRef = useRef<HTMLDivElement>(null);

  // Polling refs
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const contentPollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const clientRef = useRef<AxiosInstance | null>(null);

  // Memoize the scan status map for quick lookups
  const scanStatusMap = useMemo(() => {
    const map = new Map<string, FileScanStatus>();
    for (const s of scanStatuses) {
      map.set(s.file_id, s);
    }
    return map;
  }, [scanStatuses]);

  // Files summary stats
  const stats = useMemo(() => {
    const total = files.length;
    let scanned = 0;
    let compliant = 0;
    let needsAttention = 0;

    for (const file of files) {
      const status = scanStatusMap.get(file.id);
      if (!status) continue;
      const fileStatus = status.status;
      if (fileStatus === 'scanned' || fileStatus === 'completed' || fileStatus === 'remediated' || status.has_remediated_version) {
        scanned++;
        if (status.compliance_score !== null && status.compliance_score >= 90) {
          compliant++;
        } else if (status.compliance_score !== null) {
          needsAttention++;
        }
      }
    }

    return { total, scanned, compliant, needsAttention };
  }, [files, scanStatusMap]);

  // Content summary stats
  const contentStats = useMemo(() => {
    if (!contentData) return { total: 0, scanned: 0, issues: 0, writtenBack: 0 };

    const items = contentData.items;
    const total = items.length;
    const scanned = items.filter((i) => i.compliance_score !== null).length;
    const issues = items.reduce((sum, i) => sum + (i.issue_count || 0), 0);
    const writtenBack = items.filter(
      (i) => i.writeback_status === 'written_back' || i.writeback_status === 'writtenback'
    ).length;

    return { total, scanned, issues, writtenBack };
  }, [contentData]);

  // Overall compliance
  const overallCompliance = useMemo(() => {
    if (courseMeta.compliance_percentage !== null) return courseMeta.compliance_percentage;
    const scores = scanStatuses
      .map((s) => s.compliance_score)
      .filter((s): s is number => s !== null);
    if (scores.length === 0) return null;
    return Math.round(scores.reduce((a, b) => a + b, 0) / scores.length);
  }, [scanStatuses, courseMeta.compliance_percentage]);

  // Use the generic apiClient — token is stored in localStorage by useLTISession
  // and the apiClient interceptor attaches it automatically
  useEffect(() => {
    if (accessToken) {
      clientRef.current = apiClient;
    }
  }, [accessToken]);

  // Close scan options panel on outside click or Escape key
  useEffect(() => {
    function handleMouseDown(event: MouseEvent): void {
      if (scanOptionsRef.current && !scanOptionsRef.current.contains(event.target as Node)) {
        setShowScanOptions(false);
      }
    }

    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key === 'Escape' && showScanOptions) {
        setShowScanOptions(false);
      }
    }

    document.addEventListener('mousedown', handleMouseDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handleMouseDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [showScanOptions]);

  // --------------------------------------------------
  // Fetch files data
  // --------------------------------------------------
  const fetchData = useCallback(async (isRefresh = false): Promise<void> => {
    if (!accessToken || !sessionCourseId) return;

    const client = clientRef.current;
    if (!client) return;

    try {
      if (isRefresh) {
        setRefreshing(true);
      } else {
        setLoadingData(true);
      }
      setDataError(null);

      // Fetch files first
      const filesRes = await client.get(`${apiPrefix}/courses/${sessionCourseId}/files`);
      const filesData = filesRes.data;
      const filesList: CourseFile[] = Array.isArray(filesData)
        ? filesData
        : Array.isArray(filesData?.files)
          ? filesData.files
          : [];
      setFiles(filesList);

      // Then fetch scan status with file IDs
      const fileIds = filesList.map((f) => f.id).join(',');
      if (fileIds) {
        const scanRes = await client.get(
          `${apiPrefix}/courses/${sessionCourseId}/scan-status?file_ids=${encodeURIComponent(fileIds)}`
        );
        const scanData = scanRes.data;
        const scanList: FileScanStatus[] = Array.isArray(scanData?.files) ? scanData.files : [];
        setScanStatuses(scanList);

        if (scanData?.course_name) {
          setCourseMeta((prev) => ({ ...prev, name: scanData.course_name }));
        }
        if (scanData?.average_compliance !== undefined) {
          setCourseMeta((prev) => ({ ...prev, compliance_percentage: scanData.average_compliance }));
        }
      }
    } catch {
      setDataError('Failed to load course data. Please try refreshing.');
    } finally {
      setLoadingData(false);
      setRefreshing(false);
    }
  }, [accessToken, sessionCourseId, apiPrefix]);

  // --------------------------------------------------
  // Fetch content data
  // --------------------------------------------------
  const fetchContentData = useCallback(async (): Promise<void> => {
    if (!accessToken || !sessionCourseId) return;

    const client = clientRef.current;
    if (!client) return;

    try {
      setContentLoading(true);
      setContentError(null);

      const res = await client.get(`${apiPrefix}/content/courses/${sessionCourseId}/status`);
      const data: CourseContentStatusResponse = res.data;
      setContentData(data);
    } catch {
      setContentError('Failed to load content data. Please try refreshing.');
    } finally {
      setContentLoading(false);
    }
  }, [accessToken, sessionCourseId, apiPrefix]);

  // Fetch course name if not available from LTI session
  useEffect(() => {
    if (!sessionCourseName && sessionCourseId && clientRef.current) {
      clientRef.current.get(`${apiPrefix}/courses`)
        .then((res) => {
          const courses = Array.isArray(res.data) ? res.data : res.data?.courses || [];
          const course = courses.find((c: { id: string }) => String(c.id) === String(sessionCourseId));
          if (course?.name) {
            setCourseMeta((prev) => ({ ...prev, name: course.name }));
          }
        })
        .catch(() => { /* ignore — fallback to Course ID */ });
    }
  }, [sessionCourseName, sessionCourseId, apiPrefix]);

  // Initial data fetch
  useEffect(() => {
    if (accessToken && sessionCourseId) {
      clientRef.current = apiClient;
      fetchData();
      fetchContentData();
    }
  }, [accessToken, sessionCourseId, fetchData, fetchContentData]);

  // --------------------------------------------------
  // Files Polling
  // --------------------------------------------------
  const stopPolling = useCallback((): void => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  const startPolling = useCallback((): void => {
    if (!clientRef.current || !sessionCourseId) return;
    stopPolling();

    pollingRef.current = setInterval(async () => {
      const client = clientRef.current;
      if (!client) return;

      try {
        const allFileIds = files.map((f) => f.id).join(',');
        if (!allFileIds) return;
        const res = await client.get(
          `${apiPrefix}/courses/${sessionCourseId}/scan-status?file_ids=${encodeURIComponent(allFileIds)}`
        );
        const scanData = res.data;
        const scanList: FileScanStatus[] = Array.isArray(scanData?.files) ? scanData.files : [];
        setScanStatuses(scanList);

        if (scanData?.average_compliance !== undefined) {
          setCourseMeta((prev) => ({ ...prev, compliance_percentage: scanData.average_compliance }));
        }

        // Clear action states for completed files
        const completedIds = new Set(
          scanList
            .filter(
              (f) =>
                f.status === 'scanned' ||
                f.status === 'completed' ||
                f.status === 'remediated' ||
                f.status === 'failed' ||
                f.has_remediated_version
            )
            .map((f) => f.file_id)
        );

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

        // Stop polling if nothing is in progress
        const hasInProgress = scanList.some(
          (f) =>
            f.status === 'scanning' ||
            f.status === 'pending' ||
            f.status === 'processing' ||
            f.status === 'remediating'
        );
        if (!hasInProgress) {
          stopPolling();
        }
      } catch {
        // Silently handle polling errors
      }
    }, 3000);
  }, [sessionCourseId, stopPolling, files, apiPrefix]);

  // Start/stop files polling based on action states
  useEffect(() => {
    if (scanningFiles.size > 0 || remediatingFiles.size > 0) {
      startPolling();
    }

    return () => {
      stopPolling();
    };
  }, [scanningFiles.size, remediatingFiles.size, startPolling, stopPolling]);

  // --------------------------------------------------
  // Content Polling
  // --------------------------------------------------
  const stopContentPolling = useCallback((): void => {
    if (contentPollingRef.current) {
      clearInterval(contentPollingRef.current);
      contentPollingRef.current = null;
    }
  }, []);

  const startContentPolling = useCallback((): void => {
    if (!clientRef.current || !sessionCourseId) return;
    stopContentPolling();
    const startedAt = Date.now();

    contentPollingRef.current = setInterval(async () => {
      const client = clientRef.current;
      if (!client) return;

      // Hard cap: never poll forever. Items skipped as empty may never be
      // marked scanned, and a course with no content has no terminal event.
      if (Date.now() - startedAt > 120_000) {
        setContentScanning(false);
        stopContentPolling();
        return;
      }

      try {
        const res = await client.get(`${apiPrefix}/content/courses/${sessionCourseId}/status`);
        const data: CourseContentStatusResponse = res.data;
        setContentData(data);

        // Terminal states: nothing was discovered, or everything has a score.
        const allScanned =
          data.items.length === 0 ||
          data.items.every((item) => item.compliance_score !== null);
        if (allScanned) {
          setContentScanning(false);
          stopContentPolling();
        }
      } catch {
        // Silently handle polling errors
      }
    }, 3000);
  }, [sessionCourseId, stopContentPolling, apiPrefix]);

  // Start/stop content polling when scanning
  useEffect(() => {
    if (contentScanning) {
      startContentPolling();
    }

    return () => {
      stopContentPolling();
    };
  }, [contentScanning, startContentPolling, stopContentPolling]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopPolling();
      stopContentPolling();
    };
  }, [stopPolling, stopContentPolling]);

  // --------------------------------------------------
  // Files Actions
  // --------------------------------------------------
  const handleScanFile = async (fileId: string): Promise<void> => {
    const client = clientRef.current;
    if (!client || !sessionCourseId) return;

    setScanningFiles((prev) => new Set(prev).add(fileId));
    try {
      await client.post(`${apiPrefix}/scan`, {
        file_id: fileId,
        course_id: sessionCourseId,
        credential_id: 'lti-session',
      });
    } catch {
      setScanningFiles((prev) => {
        const next = new Set(prev);
        next.delete(fileId);
        return next;
      });
    }
  };

  const handleRemediateFile = async (scanId: string, fileId: string): Promise<void> => {
    const client = clientRef.current;
    if (!client) return;

    setRemediatingFiles((prev) => new Set(prev).add(fileId));
    try {
      await client.post(`/education/remediate/${scanId}`);
    } catch {
      setRemediatingFiles((prev) => {
        const next = new Set(prev);
        next.delete(fileId);
        return next;
      });
    }
  };

  const handleOpenInAelira = async (): Promise<void> => {
    const client = clientRef.current;
    if (!client) return;

    setOpeningAelira(true);
    try {
      const res = await client.post('/lti/bridge');
      const data = res.data;
      if (data?.url) {
        window.open(data.url, '_blank');
      }
    } catch {
      // Failed silently — user can try again
    } finally {
      setOpeningAelira(false);
    }
  };

  const handleViewResults = (scanId: string): void => {
    const client = clientRef.current;
    if (!client) return;

    // Build the URL to the scan detail page
    const baseUrl = window.location.origin;
    window.open(`${baseUrl}/scan/${scanId}`, '_blank');
  };

  // --------------------------------------------------
  // Content Actions
  // --------------------------------------------------
  const handleScanContent = async (): Promise<void> => {
    const client = clientRef.current;
    if (!client || !sessionCourseId) return;

    setContentScanning(true);
    setShowScanOptions(false);
    try {
      const res = await client.post(`${apiPrefix}/content/scan`, {
        course_id: sessionCourseId,
        ...scanOptions,
      });
      // A course with no native content queues zero jobs; there is nothing
      // to poll for, and polling would never reach a terminal state.
      if (res.data && res.data.total_items === 0) {
        setContentScanning(false);
      }
    } catch {
      setContentScanning(false);
    }
  };

  const handleBatchApprove = async (): Promise<void> => {
    const client = clientRef.current;
    if (!client || !contentData) return;

    const pendingIds = contentData.items
      .filter((item) => item.compliance_score !== null && item.compliance_score < 100 && (!item.writeback_status || item.writeback_status === 'pending_review'))
      .map((item) => item.cloud_file_id);

    if (pendingIds.length === 0) return;

    setBatchApproving(true);
    try {
      await client.post(`${apiPrefix}/content/batch-approve`, { cloud_file_ids: pendingIds });
      // Refresh content data to reflect approval
      await fetchContentData();
    } catch {
      // Failed silently — user can try again
    } finally {
      setBatchApproving(false);
    }
  };

  const handleBatchWriteback = async (): Promise<void> => {
    const client = clientRef.current;
    if (!client || !sessionCourseId) return;

    setBatchWritingBack(true);
    try {
      await client.post(`${apiPrefix}/content/batch-writeback`, { course_id: sessionCourseId });
      // Refresh content data to reflect writeback
      await fetchContentData();
    } catch {
      // Failed silently — user can try again
    } finally {
      setBatchWritingBack(false);
    }
  };

  const handleOpenContentInAelira = (cloudFileId?: string): void => {
    const dashboardBase = window.location.origin;
    if (cloudFileId) {
      window.open(`${dashboardBase}${apiPrefix}/courses/${sessionCourseId}/content/${cloudFileId}/review`, '_blank');
    } else {
      window.open(`${dashboardBase}${apiPrefix}/courses/${sessionCourseId}/content`, '_blank');
    }
  };

  const handleRefreshAll = async (): Promise<void> => {
    await Promise.all([fetchData(true), fetchContentData()]);
  };

  // --------------------------------------------------
  // Files Status badge renderer
  // --------------------------------------------------
  const renderStatusBadge = (fileId: string): React.ReactElement => {
    const status = resolveFileStatus(fileId, scanStatusMap, {
      scanning: scanningFiles,
      remediating: remediatingFiles,
    });

    switch (status) {
      case 'scanning':
        return (
          <span
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
            style={{ backgroundColor: 'var(--surface-accent-subtle)', color: 'var(--content-accent)' }}
          >
            <Loader2 className="w-3 h-3 animate-spin" aria-hidden="true" />
            Scanning...
          </span>
        );
      case 'scanned':
        return (
          <span
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
            style={{ backgroundColor: 'var(--surface-success-subtle)', color: 'var(--content-success)' }}
          >
            <CheckCircle2 className="w-3 h-3" aria-hidden="true" />
            Scanned
          </span>
        );
      case 'remediating':
        return (
          <span
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
            style={{ backgroundColor: 'var(--surface-warning-subtle)', color: 'var(--content-warning)' }}
          >
            <Loader2 className="w-3 h-3 animate-spin" aria-hidden="true" />
            Remediating...
          </span>
        );
      case 'remediated':
        return (
          <span
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
            style={{ backgroundColor: 'var(--surface-success-subtle)', color: 'var(--content-success)' }}
          >
            <CheckCircle2 className="w-3 h-3" aria-hidden="true" />
            Remediated
          </span>
        );
      case 'failed':
        return (
          <span
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
            style={{ backgroundColor: 'var(--surface-error-subtle)', color: 'var(--content-error)' }}
          >
            <AlertCircle className="w-3 h-3" aria-hidden="true" />
            Failed
          </span>
        );
      default:
        return (
          <span
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
            style={{ color: 'var(--content-secondary)' }}
          >
            Not Scanned
          </span>
        );
    }
  };

  // --------------------------------------------------
  // Files Action buttons renderer
  // --------------------------------------------------
  const renderFileActions = (file: CourseFile): React.ReactElement => {
    const fileStatus = resolveFileStatus(file.id, scanStatusMap, {
      scanning: scanningFiles,
      remediating: remediatingFiles,
    });
    const scanInfo = scanStatusMap.get(file.id);

    return (
      <div className="flex items-center justify-end gap-2">
        {/* Scan button */}
        {(fileStatus === 'not_scanned' || fileStatus === 'failed') && (
          <button
            onClick={() => handleScanFile(file.id)}
            className="px-3 py-2.5 rounded-lg text-xs font-medium text-white transition-colors hover:opacity-90 min-h-[44px] min-w-[44px]"
            style={{ backgroundColor: 'var(--accent-primary)' }}
          >
            Scan
          </button>
        )}

        {/* View Results button */}
        {scanInfo?.scan_id && (fileStatus === 'scanned' || fileStatus === 'remediated') && (
          <button
            onClick={() => handleViewResults(scanInfo.scan_id!)}
            className="px-3 py-2.5 rounded-lg text-xs font-medium transition-colors hover:opacity-90 min-h-[44px]"
            style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--border-primary)', color: 'var(--content-primary)' }}
          >
            View Results<span className="sr-only"> (opens in new tab)</span>
          </button>
        )}

        {/* Remediate button */}
        {fileStatus === 'scanned' &&
          scanInfo?.scan_id &&
          scanInfo.compliance_score !== null &&
          scanInfo.compliance_score < 100 &&
          !scanInfo.has_remediated_version && (
            <button
              onClick={() => handleRemediateFile(scanInfo.scan_id!, file.id)}
              className="px-3 py-2.5 rounded-lg text-xs font-medium text-white transition-colors hover:opacity-90 min-h-[44px]"
              style={{ backgroundColor: 'var(--accent-primary)' }}
            >
              Remediate
            </button>
          )}
      </div>
    );
  };

  // --------------------------------------------------
  // Content Tab Renderers
  // --------------------------------------------------
  const renderContentStats = (): React.ReactElement => (
    <div className="grid grid-cols-4 gap-4 mb-6">
      {[
        { label: 'Total Content Items', value: contentStats.total, color: 'var(--content-primary)' },
        { label: 'Scanned', value: contentStats.scanned, color: 'var(--content-primary)' },
        { label: 'Issues Found', value: contentStats.issues, color: contentStats.issues > 0 ? 'var(--content-error)' : 'var(--content-primary)' },
        { label: 'Written Back', value: contentStats.writtenBack, color: contentStats.writtenBack > 0 ? 'var(--content-success)' : 'var(--content-primary)' },
      ].map((stat) => (
        <div
          key={stat.label}
          className="p-4 rounded-lg"
          style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--card-border)' }}
        >
          <p className="text-xs font-medium mb-1" style={{ color: 'var(--content-secondary)' }}>
            {stat.label}
          </p>
          <p className="text-2xl font-semibold tabular-nums" style={{ color: stat.color }}>
            {stat.value}
          </p>
        </div>
      ))}
    </div>
  );

  const renderContentTypeSummary = (): React.ReactElement | null => {
    if (!contentData || contentData.by_type.length === 0) return null;

    return (
      <div
        className="rounded-xl overflow-hidden mb-6"
        style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--card-border)' }}
      >
        <div className="px-4 py-3" style={{ borderBottom: '1px solid var(--border-primary)' }}>
          <h2 className="text-sm font-semibold" style={{ color: 'var(--content-primary)' }}>
            Content by Type
          </h2>
        </div>
        <table className="w-full" aria-label="Content compliance by type">
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border-primary)' }}>
              <th scope="col" className="text-left px-4 py-2.5 text-xs font-medium uppercase tracking-wider" style={{ color: 'var(--content-secondary)' }}>
                Type
              </th>
              <th scope="col" className="text-right px-4 py-2.5 text-xs font-medium uppercase tracking-wider" style={{ color: 'var(--content-secondary)' }}>
                Count
              </th>
              <th scope="col" className="text-right px-4 py-2.5 text-xs font-medium uppercase tracking-wider" style={{ color: 'var(--content-secondary)' }}>
                Scanned
              </th>
              <th scope="col" className="text-right px-4 py-2.5 text-xs font-medium uppercase tracking-wider" style={{ color: 'var(--content-secondary)' }}>
                Avg Score
              </th>
              <th scope="col" className="text-right px-4 py-2.5 text-xs font-medium uppercase tracking-wider" style={{ color: 'var(--content-secondary)' }}>
                Issues
              </th>
            </tr>
          </thead>
          <tbody>
            {contentData.by_type.map((typeInfo) => {
              const Icon = getContentTypeIcon(typeInfo.content_type);
              const badgeColor = getContentTypeBadgeColor(typeInfo.content_type);
              return (
                <tr
                  key={typeInfo.content_type}
                  style={{ borderBottom: '1px solid var(--border-primary)' }}
                >
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-2">
                      <Icon className="w-4 h-4" style={{ color: badgeColor.text }} aria-hidden="true" />
                      <span className="text-sm font-medium capitalize" style={{ color: 'var(--content-primary)' }}>
                        {typeInfo.content_type}s
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-2.5 text-right text-sm tabular-nums" style={{ color: 'var(--content-primary)' }}>
                    {typeInfo.total}
                  </td>
                  <td className="px-4 py-2.5 text-right text-sm tabular-nums" style={{ color: 'var(--content-primary)' }}>
                    {typeInfo.scanned}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    {typeInfo.average_compliance !== null ? (
                      <span className="text-sm font-semibold tabular-nums" style={{ color: getComplianceColor(typeInfo.average_compliance) }}>
                        {Math.round(typeInfo.average_compliance)}%
                        <span className="sr-only">
                          {typeInfo.average_compliance >= 90 ? ' compliant' : typeInfo.average_compliance >= 70 ? ' needs improvement' : ' non-compliant'}
                        </span>
                      </span>
                    ) : (
                      <span className="text-sm" style={{ color: 'var(--content-secondary)' }}>
                        --<span className="sr-only"> not scanned</span>
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-right text-sm tabular-nums" style={{ color: typeInfo.issues > 0 ? 'var(--content-error)' : 'var(--content-primary)' }}>
                    {typeInfo.issues}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  };

  const renderContentItemsTable = (): React.ReactElement | null => {
    if (!contentData || contentData.items.length === 0) return null;

    return (
      <div
        className="rounded-xl overflow-hidden"
        style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--card-border)' }}
      >
        <table className="w-full" aria-label="Content items compliance details">
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border-primary)' }}>
              <th scope="col" className="text-left px-4 py-3 text-xs font-medium uppercase tracking-wider" style={{ color: 'var(--content-secondary)' }}>
                Title
              </th>
              <th scope="col" className="text-left px-4 py-3 text-xs font-medium uppercase tracking-wider" style={{ color: 'var(--content-secondary)' }}>
                Type
              </th>
              <th scope="col" className="text-center px-4 py-3 text-xs font-medium uppercase tracking-wider" style={{ color: 'var(--content-secondary)' }}>
                Compliance
              </th>
              <th scope="col" className="text-center px-4 py-3 text-xs font-medium uppercase tracking-wider" style={{ color: 'var(--content-secondary)' }}>
                Issues
              </th>
              <th scope="col" className="text-center px-4 py-3 text-xs font-medium uppercase tracking-wider" style={{ color: 'var(--content-secondary)' }}>
                Writeback
              </th>
              <th scope="col" className="text-right px-4 py-3 text-xs font-medium uppercase tracking-wider" style={{ color: 'var(--content-secondary)' }}>
                Actions
              </th>
            </tr>
          </thead>
          <tbody>
            {contentData.items.map((item) => {
              const Icon = getContentTypeIcon(item.content_type);
              const badgeColor = getContentTypeBadgeColor(item.content_type);
              const wb = getWritebackBadge(item.writeback_status);

              return (
                <tr
                  key={item.cloud_file_id}
                  className="transition-colors"
                  style={{ borderBottom: '1px solid var(--border-primary)' }}
                >
                  {/* Title */}
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <Icon className="w-4 h-4 shrink-0" style={{ color: badgeColor.text }} aria-hidden="true" />
                      <span
                        className="text-sm font-medium truncate max-w-xs"
                        style={{ color: 'var(--content-primary)' }}
                        title={item.title}
                      >
                        {item.title}
                      </span>
                    </div>
                  </td>

                  {/* Type badge */}
                  <td className="px-4 py-3">
                    <span
                      className="inline-block px-2 py-0.5 rounded text-xs font-semibold capitalize"
                      style={{ backgroundColor: badgeColor.bg, color: badgeColor.text }}
                    >
                      {item.content_type}
                    </span>
                  </td>

                  {/* Compliance Score */}
                  <td className="px-4 py-3 text-center">
                    {item.compliance_score !== null ? (
                      <span
                        className="text-sm font-semibold tabular-nums"
                        style={{ color: getComplianceColor(item.compliance_score) }}
                      >
                        {item.compliance_score}%
                        <span className="sr-only">
                          {item.compliance_score >= 90 ? ' compliant' : item.compliance_score >= 70 ? ' needs improvement' : ' non-compliant'}
                        </span>
                      </span>
                    ) : (
                      <span className="text-sm" style={{ color: 'var(--content-secondary)' }}>
                        --
                        <span className="sr-only"> not scanned</span>
                      </span>
                    )}
                  </td>

                  {/* Issue count */}
                  <td className="px-4 py-3 text-center">
                    <span
                      className="text-sm tabular-nums"
                      style={{ color: item.issue_count > 0 ? 'var(--content-error)' : 'var(--content-secondary)' }}
                    >
                      {item.issue_count}
                    </span>
                  </td>

                  {/* Writeback status */}
                  <td className="px-4 py-3 text-center">
                    <span
                      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
                      style={{ backgroundColor: wb.bg, color: wb.color }}
                    >
                      {wb.label === 'Writing...' && <Loader2 className="w-3 h-3 animate-spin" aria-hidden="true" />}
                      {wb.label === 'Written Back' && <CheckCircle2 className="w-3 h-3" aria-hidden="true" />}
                      {wb.label}
                    </span>
                  </td>

                  {/* Actions */}
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end">
                      <button
                        onClick={() => handleOpenContentInAelira(item.cloud_file_id)}
                        className="px-3 py-2.5 rounded-lg text-xs font-medium transition-colors hover:opacity-90 min-h-[44px]"
                        style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--border-primary)', color: 'var(--content-primary)' }}
                      >
                        Open in Aelira<span className="sr-only"> (opens in new tab)</span>
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  };

  // --------------------------------------------------
  // Render
  // --------------------------------------------------
  const isLoading = sessionLoading || (loadingData && !dataError);

  return (
    <LTILayout loading={isLoading} error={sessionError}>
      <div className="max-w-6xl mx-auto">
        {/* Data error (distinct from session error which LTILayout handles) */}
        {dataError && !sessionError && (
          <div
            className="mb-6 p-4 rounded-lg flex items-center gap-3"
            style={{ backgroundColor: 'var(--surface-error-subtle)', color: 'var(--content-error)' }}
            role="alert"
          >
            <AlertCircle className="w-5 h-5 shrink-0" aria-hidden="true" />
            <span className="text-sm">{dataError}</span>
            <button
              onClick={() => fetchData(true)}
              className="ml-auto text-sm font-medium underline hover:opacity-80 min-h-[44px] min-w-[44px] flex items-center justify-center"
            >
              Retry
            </button>
          </div>
        )}

        {/* Header */}
        {fromOverview && (
          <button
            onClick={() => navigate('/lti/overview')}
            className="flex items-center gap-1 text-sm text-[var(--content-accent)] hover:underline mb-2"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to Overview
          </button>
        )}
        <div className="flex items-start justify-between mb-6">
          <div>
            <div className="flex items-center gap-3 mb-1">
              <Shield className="w-5 h-5" style={{ color: 'var(--accent-primary)' }} aria-hidden="true" />
              <h1
                className="text-xl font-semibold"
                style={{ color: 'var(--content-primary)' }}
              >
                {courseMeta.name || sessionCourseName || `Course ${sessionCourseId || ''}`}
              </h1>
            </div>
            {overallCompliance !== null && (
              <p className="text-sm ml-8" style={{ color: getComplianceColor(overallCompliance) }}>
                <span className="font-semibold tabular-nums">{overallCompliance}%</span>
                {' '}overall compliance
                <span className="sr-only">
                  {overallCompliance >= 90 ? ' — compliant' : overallCompliance >= 70 ? ' — needs improvement' : ' — non-compliant'}
                </span>
              </p>
            )}
            {overallCompliance === null && stats.total > 0 && (
              <p className="text-sm ml-8" style={{ color: 'var(--content-secondary)' }}>
                No files scanned yet
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            {/* Scan Content button + options */}
            {activeTab === 'content' && (
              <div className="relative" ref={scanOptionsRef}>
                <div className="flex gap-2">
                  <button
                    onClick={handleScanContent}
                    disabled={contentScanning}
                    className="flex items-center gap-2 px-4 py-3 rounded-lg text-sm font-semibold text-white transition-colors disabled:opacity-50 min-h-[44px]"
                    style={{ backgroundColor: 'var(--accent-primary)' }}
                  >
                    {contentScanning ? (
                      <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                    ) : (
                      <Search className="w-4 h-4" aria-hidden="true" />
                    )}
                    {contentScanning ? 'Scanning Content...' : 'Scan Content'}
                  </button>
                  <button
                    onClick={() => setShowScanOptions(!showScanOptions)}
                    className="flex items-center px-3 py-2.5 rounded-lg text-sm font-medium transition-colors min-h-[44px]"
                    style={{ border: '1px solid var(--border-primary)', color: 'var(--content-primary)' }}
                    aria-label="Scan options"
                    aria-expanded={showScanOptions}
                  >
                    <Settings2 className="w-4 h-4" aria-hidden="true" />
                  </button>
                </div>
                {showScanOptions && (
                  <div
                    role="group"
                    aria-label="Scan options"
                    className="absolute right-0 mt-2 w-72 rounded-lg p-4 z-10 space-y-3"
                    style={{ backgroundColor: 'var(--surface-secondary)', border: '1px solid var(--border-primary)' }}
                  >
                    <h3 className="font-medium text-sm" style={{ color: 'var(--content-primary)' }}>Scan Options</h3>
                    <label className="flex items-center gap-3 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={scanOptions.generate_alt_text}
                        onChange={e => setScanOptions(prev => ({ ...prev, generate_alt_text: e.target.checked }))}
                        className="rounded"
                      />
                      <div>
                        <div className="text-sm" style={{ color: 'var(--content-primary)' }}>Generate Alt Text</div>
                        <div className="text-xs" style={{ color: 'var(--content-tertiary)' }}>Use AI to describe images</div>
                      </div>
                    </label>
                    <label className="flex items-center gap-3 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={scanOptions.auto_remediate}
                        onChange={e => setScanOptions(prev => ({ ...prev, auto_remediate: e.target.checked }))}
                        className="rounded"
                      />
                      <div>
                        <div className="text-sm" style={{ color: 'var(--content-primary)' }}>Auto-Remediate</div>
                        <div className="text-xs" style={{ color: 'var(--content-tertiary)' }}>Automatically fix issues after scan</div>
                      </div>
                    </label>
                    <label className="flex items-center gap-3 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={scanOptions.detect_decorative}
                        onChange={e => setScanOptions(prev => ({ ...prev, detect_decorative: e.target.checked }))}
                        className="rounded"
                      />
                      <div>
                        <div className="text-sm" style={{ color: 'var(--content-primary)' }}>Detect Decorative Images</div>
                        <div className="text-xs" style={{ color: 'var(--content-tertiary)' }}>Identify images that don't need alt text</div>
                      </div>
                    </label>
                  </div>
                )}
              </div>
            )}
            <button
              onClick={handleRefreshAll}
              disabled={refreshing}
              className="flex items-center gap-2 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors disabled:opacity-50 min-h-[44px]"
              style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--border-primary)', color: 'var(--content-primary)' }}
            >
              <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} aria-hidden="true" />
              Refresh
            </button>
            <button
              onClick={handleOpenInAelira}
              disabled={openingAelira}
              className="flex items-center gap-2 px-3 py-2.5 rounded-lg text-sm font-medium text-white transition-colors disabled:opacity-50 min-h-[44px]"
              style={{ backgroundColor: 'var(--accent-primary)' }}
            >
              {openingAelira ? (
                <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
              ) : (
                <ExternalLink className="w-4 h-4" aria-hidden="true" />
              )}
              Open in Aelira<span className="sr-only"> (opens in new tab)</span>
            </button>
          </div>
        </div>

        {/* Tab Switcher — WAI-ARIA Tabs pattern with arrow-key navigation */}
        <div
          className="flex gap-1 mb-6 p-1 rounded-lg"
          style={{ backgroundColor: 'var(--surface-secondary)' }}
          role="tablist"
          aria-label="Course data views"
          onKeyDown={(e) => {
            const tabs = ['content', 'files'] as const;
            const currentIndex = tabs.indexOf(activeTab);
            let nextIndex = -1;
            if (e.key === 'ArrowRight') {
              nextIndex = (currentIndex + 1) % tabs.length;
            } else if (e.key === 'ArrowLeft') {
              nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
            }
            if (nextIndex >= 0) {
              e.preventDefault();
              setActiveTab(tabs[nextIndex]);
              document.getElementById(`tab-${tabs[nextIndex]}`)?.focus();
            }
          }}
        >
          <button
            onClick={() => setActiveTab('content')}
            role="tab"
            aria-selected={activeTab === 'content'}
            aria-controls="tabpanel-content"
            id="tab-content"
            tabIndex={activeTab === 'content' ? 0 : -1}
            className={`flex-1 px-4 py-2.5 rounded-md text-sm font-medium transition-colors min-h-[44px] ${
              activeTab === 'content' ? 'text-white' : ''
            }`}
            style={activeTab === 'content' ? { backgroundColor: 'var(--accent-primary)' } : { color: 'var(--content-secondary)' }}
          >
            Pages & Assignments ({contentStats.total})
          </button>
          <button
            onClick={() => setActiveTab('files')}
            role="tab"
            aria-selected={activeTab === 'files'}
            aria-controls="tabpanel-files"
            id="tab-files"
            tabIndex={activeTab === 'files' ? 0 : -1}
            className={`flex-1 px-4 py-2.5 rounded-md text-sm font-medium transition-colors min-h-[44px] ${
              activeTab === 'files' ? 'text-white' : ''
            }`}
            style={activeTab === 'files' ? { backgroundColor: 'var(--accent-primary)' } : { color: 'var(--content-secondary)' }}
          >
            Files ({stats.total})
          </button>
        </div>

        {/* ============================================================ */}
        {/* Content Tab */}
        {/* ============================================================ */}
        <div role="tabpanel" id="tabpanel-content" aria-labelledby="tab-content" aria-busy={contentLoading || contentScanning} hidden={activeTab !== 'content'}>
            {/* Content error */}
            {contentError && (
              <div
                className="mb-6 p-4 rounded-lg flex items-center gap-3"
                style={{ backgroundColor: 'var(--surface-error-subtle)', color: 'var(--content-error)' }}
                role="alert"
              >
                <AlertCircle className="w-5 h-5 shrink-0" aria-hidden="true" />
                <span className="text-sm">{contentError}</span>
                <button
                  onClick={fetchContentData}
                  className="ml-auto text-sm font-medium underline hover:opacity-80 min-h-[44px] min-w-[44px] flex items-center justify-center"
                >
                  Retry
                </button>
              </div>
            )}

            {/* Content loading */}
            {contentLoading && !contentData && (
              <div
                className="rounded-xl p-12 text-center"
                style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--card-border)' }}
                role="status"
                aria-label="Loading content data"
              >
                <Loader2
                  className="w-8 h-8 mx-auto mb-3 animate-spin"
                  style={{ color: 'var(--accent-primary)' }}
                  aria-hidden="true"
                />
                <p className="text-sm" style={{ color: 'var(--content-secondary)' }}>
                  Loading content data...
                </p>
              </div>
            )}

            {/* Content empty state */}
            {!contentLoading && contentData && contentData.items.length === 0 && !contentError && (
              <div
                className="rounded-xl p-12 text-center"
                style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--card-border)' }}
              >
                <BookOpen
                  className="w-12 h-12 mx-auto mb-3"
                  style={{ color: 'var(--content-secondary)' }}
                  aria-hidden="true"
                />
                <h2
                  className="text-lg font-semibold mb-2"
                  style={{ color: 'var(--content-primary)' }}
                >
                  No content found
                </h2>
                <p className="text-sm mb-4" style={{ color: 'var(--content-secondary)' }}>
                  Click &ldquo;Scan Content&rdquo; to discover and scan pages, assignments, announcements, and more.
                </p>
                <button
                  onClick={handleScanContent}
                  disabled={contentScanning}
                  className="inline-flex items-center gap-2 px-5 py-3 rounded-lg text-sm font-semibold text-white transition-colors disabled:opacity-50 min-h-[44px]"
                  style={{ backgroundColor: 'var(--accent-primary)' }}
                >
                  {contentScanning ? (
                    <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <Search className="w-4 h-4" aria-hidden="true" />
                  )}
                  Scan Content
                </button>
              </div>
            )}

            {/* Content data */}
            {contentData && contentData.items.length > 0 && (
              <>
                {/* Content summary stats */}
                {renderContentStats()}

                {/* Batch actions */}
                <div className="flex items-center gap-3 mb-6">
                  <button
                    onClick={handleBatchApprove}
                    disabled={batchApproving || contentStats.scanned === 0}
                    className="flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-colors disabled:opacity-50 min-h-[44px]"
                    style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--border-primary)', color: 'var(--content-primary)' }}
                  >
                    {batchApproving ? (
                      <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                    ) : (
                      <CheckCircle2 className="w-4 h-4" aria-hidden="true" />
                    )}
                    Approve All
                  </button>
                  <button
                    onClick={handleBatchWriteback}
                    disabled={batchWritingBack || contentStats.scanned === 0}
                    className="flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium text-white transition-colors disabled:opacity-50 min-h-[44px]"
                    style={{ backgroundColor: 'var(--accent-primary)' }}
                  >
                    {batchWritingBack ? (
                      <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                    ) : (
                      <Upload className="w-4 h-4" aria-hidden="true" />
                    )}
                    Write Back All
                  </button>
                  <button
                    onClick={() => handleOpenContentInAelira()}
                    className="flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-colors hover:opacity-90 min-h-[44px]"
                    style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--border-primary)', color: 'var(--content-primary)' }}
                  >
                    <ExternalLink className="w-4 h-4" aria-hidden="true" />
                    Full Review<span className="sr-only"> (opens in new tab)</span>
                  </button>
                </div>

                {/* Content type summary */}
                {renderContentTypeSummary()}

                {/* Content items table */}
                {renderContentItemsTable()}
              </>
            )}
        </div>

        {/* ============================================================ */}
        {/* Files Tab */}
        {/* ============================================================ */}
        <div role="tabpanel" id="tabpanel-files" aria-labelledby="tab-files" aria-busy={scanningFiles.size > 0 || remediatingFiles.size > 0} hidden={activeTab !== 'files'}>
            {/* Summary stats */}
            {stats.total > 0 && (
              <div
                className="grid grid-cols-4 gap-4 mb-6"
              >
                <div
                  className="p-4 rounded-lg"
                  style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--card-border)' }}
                >
                  <p className="text-xs font-medium mb-1" style={{ color: 'var(--content-secondary)' }}>
                    Total Files
                  </p>
                  <p className="text-2xl font-semibold tabular-nums" style={{ color: 'var(--content-primary)' }}>
                    {stats.total}
                  </p>
                </div>
                <div
                  className="p-4 rounded-lg"
                  style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--card-border)' }}
                >
                  <p className="text-xs font-medium mb-1" style={{ color: 'var(--content-secondary)' }}>
                    Scanned
                  </p>
                  <p className="text-2xl font-semibold tabular-nums" style={{ color: 'var(--content-primary)' }}>
                    {stats.scanned}
                  </p>
                </div>
                <div
                  className="p-4 rounded-lg"
                  style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--card-border)' }}
                >
                  <p className="text-xs font-medium mb-1" style={{ color: 'var(--content-secondary)' }}>
                    Compliant
                  </p>
                  <p className="text-2xl font-semibold tabular-nums" style={{ color: 'var(--content-success)' }}>
                    {stats.compliant}
                  </p>
                </div>
                <div
                  className="p-4 rounded-lg"
                  style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--card-border)' }}
                >
                  <p className="text-xs font-medium mb-1" style={{ color: 'var(--content-secondary)' }}>
                    Needs Attention
                  </p>
                  <p className="text-2xl font-semibold tabular-nums" style={{ color: 'var(--content-error)' }}>
                    {stats.needsAttention}
                  </p>
                </div>
              </div>
            )}

            {/* Empty state */}
            {!loadingData && files.length === 0 && !dataError && (
              <div
                className="rounded-xl p-12 text-center"
                style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--card-border)' }}
              >
                <FileText
                  className="w-12 h-12 mx-auto mb-3"
                  style={{ color: 'var(--content-secondary)' }}
                  aria-hidden="true"
                />
                <h2
                  className="text-lg font-semibold mb-2"
                  style={{ color: 'var(--content-primary)' }}
                >
                  No files found
                </h2>
                <p className="text-sm" style={{ color: 'var(--content-secondary)' }}>
                  This course does not have any files to scan yet.
                </p>
              </div>
            )}

            {/* File table */}
            {files.length > 0 && (
              <div
                className="rounded-xl overflow-hidden"
                style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--card-border)' }}
              >
                <table className="w-full" aria-label="Course files compliance details">
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border-primary)' }}>
                      <th
                        scope="col"
                        className="text-left px-4 py-3 text-xs font-medium uppercase tracking-wider"
                        style={{ color: 'var(--content-secondary)' }}
                      >
                        File Name
                      </th>
                      <th
                        scope="col"
                        className="text-left px-4 py-3 text-xs font-medium uppercase tracking-wider"
                        style={{ color: 'var(--content-secondary)' }}
                      >
                        Type
                      </th>
                      <th
                        scope="col"
                        className="text-right px-4 py-3 text-xs font-medium uppercase tracking-wider"
                        style={{ color: 'var(--content-secondary)' }}
                      >
                        Size
                      </th>
                      <th
                        scope="col"
                        className="text-center px-4 py-3 text-xs font-medium uppercase tracking-wider"
                        style={{ color: 'var(--content-secondary)' }}
                      >
                        Compliance
                      </th>
                      <th
                        scope="col"
                        className="text-center px-4 py-3 text-xs font-medium uppercase tracking-wider"
                        style={{ color: 'var(--content-secondary)' }}
                      >
                        Status
                      </th>
                      <th
                        scope="col"
                        className="text-right px-4 py-3 text-xs font-medium uppercase tracking-wider"
                        style={{ color: 'var(--content-secondary)' }}
                      >
                        Actions
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {files.map((file) => {
                      const scanInfo = scanStatusMap.get(file.id);
                      const score = scanInfo?.compliance_score ?? null;
                      const ext = getFileExtension(file);

                      return (
                        <tr
                          key={file.id}
                          className="transition-colors"
                          style={{ borderBottom: '1px solid var(--border-primary)' }}
                        >
                          {/* File Name */}
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-2">
                              <FileText
                                className="w-4 h-4 shrink-0"
                                style={{ color: 'var(--content-secondary)' }}
                                aria-hidden="true"
                              />
                              <span
                                className="text-sm font-medium truncate max-w-xs"
                                style={{ color: 'var(--content-primary)' }}
                                title={file.display_name}
                              >
                                {file.display_name}
                              </span>
                            </div>
                          </td>

                          {/* Type */}
                          <td className="px-4 py-3">
                            <span
                              className="inline-block px-2 py-0.5 rounded text-xs font-semibold uppercase"
                              style={{
                                backgroundColor: 'var(--surface-primary)',
                                border: '1px solid var(--border-primary)',
                                color: 'var(--content-secondary)',
                              }}
                            >
                              {ext}
                            </span>
                          </td>

                          {/* Size */}
                          <td
                            className="px-4 py-3 text-right text-sm tabular-nums"
                            style={{ color: 'var(--content-secondary)' }}
                          >
                            {formatFileSize(file.size)}
                          </td>

                          {/* Compliance Score */}
                          <td className="px-4 py-3 text-center">
                            {score !== null ? (
                              <span
                                className="text-sm font-semibold tabular-nums"
                                style={{ color: getComplianceColor(score) }}
                              >
                                {score}%
                                <span className="sr-only">
                                  {score >= 90 ? ' compliant' : score >= 70 ? ' needs improvement' : ' non-compliant'}
                                </span>
                              </span>
                            ) : (
                              <span
                                className="text-sm"
                                style={{ color: 'var(--content-secondary)' }}
                              >
                                --<span className="sr-only"> not scanned</span>
                              </span>
                            )}
                          </td>

                          {/* Status */}
                          <td className="px-4 py-3 text-center">
                            {renderStatusBadge(file.id)}
                          </td>

                          {/* Actions */}
                          <td className="px-4 py-3">
                            {renderFileActions(file)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
        </div>
      </div>
    </LTILayout>
  );
}
