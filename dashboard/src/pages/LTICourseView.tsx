import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  FileText,
  RefreshCw,
  ExternalLink,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Shield,
} from 'lucide-react';
import { LTILayout } from '../components/LTILayout';
import { useLTISession } from '../hooks/useLTISession';
import { createLTIClient } from '../api/ltiClient';
import type { AxiosInstance } from 'axios';

// ============================================================================
// Type Definitions
// ============================================================================

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
  if (score >= 90) return 'var(--status-success-text)';
  if (score >= 70) return 'var(--status-warning-text)';
  return 'var(--status-error-text)';
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

// ============================================================================
// Component
// ============================================================================

export function LTICourseView(): React.ReactElement {
  const { accessToken, courseId: sessionCourseId, courseName: sessionCourseName, loading: sessionLoading, error: sessionError } = useLTISession();

  // State
  const [files, setFiles] = useState<CourseFile[]>([]);
  const [scanStatuses, setScanStatuses] = useState<FileScanStatus[]>([]);
  const [courseMeta, setCourseMeta] = useState<CourseMeta>({ name: '', compliance_percentage: null });
  const [loadingData, setLoadingData] = useState(true);
  const [dataError, setDataError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  // Action states
  const [scanningFiles, setScanningFiles] = useState<Set<string>>(new Set());
  const [remediatingFiles, setRemediatingFiles] = useState<Set<string>>(new Set());
  const [openingAelira, setOpeningAelira] = useState(false);

  // Polling ref
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const clientRef = useRef<AxiosInstance | null>(null);

  // Memoize the scan status map for quick lookups
  const scanStatusMap = useMemo(() => {
    const map = new Map<string, FileScanStatus>();
    for (const s of scanStatuses) {
      map.set(s.file_id, s);
    }
    return map;
  }, [scanStatuses]);

  // Summary stats
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

  // Overall compliance
  const overallCompliance = useMemo(() => {
    if (courseMeta.compliance_percentage !== null) return courseMeta.compliance_percentage;
    const scores = scanStatuses
      .map((s) => s.compliance_score)
      .filter((s): s is number => s !== null);
    if (scores.length === 0) return null;
    return Math.round(scores.reduce((a, b) => a + b, 0) / scores.length);
  }, [scanStatuses, courseMeta.compliance_percentage]);

  // Create client when accessToken is available
  useEffect(() => {
    if (accessToken) {
      clientRef.current = createLTIClient(accessToken);
    }
  }, [accessToken]);

  // --------------------------------------------------
  // Fetch data
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
      const filesRes = await client.get(`/canvas/courses/${sessionCourseId}/files`);
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
          `/canvas/courses/${sessionCourseId}/scan-status?file_ids=${encodeURIComponent(fileIds)}`
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
  }, [accessToken, sessionCourseId]);

  // Initial data fetch
  useEffect(() => {
    if (accessToken && sessionCourseId) {
      fetchData();
    }
  }, [accessToken, sessionCourseId, fetchData]);

  // --------------------------------------------------
  // Polling
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
          `/canvas/courses/${sessionCourseId}/scan-status?file_ids=${encodeURIComponent(allFileIds)}`
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
  }, [sessionCourseId, stopPolling, files]);

  // Start/stop polling based on action states
  useEffect(() => {
    if (scanningFiles.size > 0 || remediatingFiles.size > 0) {
      startPolling();
    }

    return () => {
      stopPolling();
    };
  }, [scanningFiles.size, remediatingFiles.size, startPolling, stopPolling]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopPolling();
    };
  }, [stopPolling]);

  // --------------------------------------------------
  // Actions
  // --------------------------------------------------
  const handleScanFile = async (fileId: string): Promise<void> => {
    const client = clientRef.current;
    if (!client || !sessionCourseId) return;

    setScanningFiles((prev) => new Set(prev).add(fileId));
    try {
      await client.post('/canvas/scan', {
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
  // Status badge renderer
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
            style={{ backgroundColor: 'rgba(59, 130, 246, 0.1)', color: 'var(--accent-primary)' }}
          >
            <Loader2 className="w-3 h-3 animate-spin" />
            Scanning...
          </span>
        );
      case 'scanned':
        return (
          <span
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
            style={{ backgroundColor: 'rgba(34, 197, 94, 0.1)', color: 'var(--status-success-text)' }}
          >
            <CheckCircle2 className="w-3 h-3" />
            Scanned
          </span>
        );
      case 'remediating':
        return (
          <span
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
            style={{ backgroundColor: 'rgba(168, 85, 247, 0.1)', color: 'var(--accent-primary)' }}
          >
            <Loader2 className="w-3 h-3 animate-spin" />
            Remediating...
          </span>
        );
      case 'remediated':
        return (
          <span
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
            style={{ backgroundColor: 'rgba(34, 197, 94, 0.1)', color: 'var(--status-success-text)' }}
          >
            <CheckCircle2 className="w-3 h-3" />
            Remediated
          </span>
        );
      case 'failed':
        return (
          <span
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
            style={{ backgroundColor: 'rgba(239, 68, 68, 0.1)', color: 'var(--status-error-text)' }}
          >
            <AlertCircle className="w-3 h-3" />
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
  // Action buttons renderer
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
            className="px-3 py-1.5 rounded-lg text-xs font-medium text-white transition-colors hover:opacity-90"
            style={{ backgroundColor: 'var(--accent-primary)' }}
          >
            Scan
          </button>
        )}

        {/* View Results button */}
        {scanInfo?.scan_id && (fileStatus === 'scanned' || fileStatus === 'remediated') && (
          <button
            onClick={() => handleViewResults(scanInfo.scan_id!)}
            className="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors hover:opacity-90"
            style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--border-primary)', color: 'var(--content-primary)' }}
          >
            View Results
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
              className="px-3 py-1.5 rounded-lg text-xs font-medium text-white transition-colors hover:opacity-90"
              style={{ backgroundColor: 'var(--accent-primary)' }}
            >
              Remediate
            </button>
          )}
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
            style={{ backgroundColor: 'rgba(239, 68, 68, 0.1)', color: 'var(--status-error-text)' }}
          >
            <AlertCircle className="w-5 h-5 shrink-0" />
            <span className="text-sm">{dataError}</span>
            <button
              onClick={() => fetchData(true)}
              className="ml-auto text-sm font-medium underline hover:opacity-80"
            >
              Retry
            </button>
          </div>
        )}

        {/* Header */}
        <div className="flex items-start justify-between mb-6">
          <div>
            <div className="flex items-center gap-3 mb-1">
              <Shield className="w-5 h-5" style={{ color: 'var(--accent-primary)' }} />
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
              </p>
            )}
            {overallCompliance === null && stats.total > 0 && (
              <p className="text-sm ml-8" style={{ color: 'var(--content-secondary)' }}>
                No files scanned yet
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => fetchData(true)}
              disabled={refreshing}
              className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
              style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--border-primary)', color: 'var(--content-primary)' }}
            >
              <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
              Refresh
            </button>
            <button
              onClick={handleOpenInAelira}
              disabled={openingAelira}
              className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium text-white transition-colors disabled:opacity-50"
              style={{ backgroundColor: 'var(--accent-primary)' }}
            >
              {openingAelira ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <ExternalLink className="w-4 h-4" />
              )}
              Open in Aelira
            </button>
          </div>
        </div>

        {/* Summary stats */}
        {stats.total > 0 && (
          <div
            className="grid grid-cols-4 gap-4 mb-6"
          >
            <div
              className="p-4 rounded-lg"
              style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--card-border)', boxShadow: 'var(--card-shadow)' }}
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
              style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--card-border)', boxShadow: 'var(--card-shadow)' }}
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
              style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--card-border)', boxShadow: 'var(--card-shadow)' }}
            >
              <p className="text-xs font-medium mb-1" style={{ color: 'var(--content-secondary)' }}>
                Compliant
              </p>
              <p className="text-2xl font-semibold tabular-nums" style={{ color: 'var(--status-success-text)' }}>
                {stats.compliant}
              </p>
            </div>
            <div
              className="p-4 rounded-lg"
              style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--card-border)', boxShadow: 'var(--card-shadow)' }}
            >
              <p className="text-xs font-medium mb-1" style={{ color: 'var(--content-secondary)' }}>
                Needs Attention
              </p>
              <p className="text-2xl font-semibold tabular-nums" style={{ color: 'var(--status-error-text)' }}>
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
            />
            <h3
              className="text-lg font-semibold mb-2"
              style={{ color: 'var(--content-primary)' }}
            >
              No files found
            </h3>
            <p className="text-sm" style={{ color: 'var(--content-secondary)' }}>
              This course does not have any files to scan yet.
            </p>
          </div>
        )}

        {/* File table */}
        {files.length > 0 && (
          <div
            className="rounded-xl overflow-hidden"
            style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--card-border)', boxShadow: 'var(--card-shadow)' }}
          >
            <table className="w-full">
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-primary)' }}>
                  <th
                    className="text-left px-4 py-3 text-xs font-medium uppercase tracking-wider"
                    style={{ color: 'var(--content-secondary)' }}
                  >
                    File Name
                  </th>
                  <th
                    className="text-left px-4 py-3 text-xs font-medium uppercase tracking-wider"
                    style={{ color: 'var(--content-secondary)' }}
                  >
                    Type
                  </th>
                  <th
                    className="text-right px-4 py-3 text-xs font-medium uppercase tracking-wider"
                    style={{ color: 'var(--content-secondary)' }}
                  >
                    Size
                  </th>
                  <th
                    className="text-center px-4 py-3 text-xs font-medium uppercase tracking-wider"
                    style={{ color: 'var(--content-secondary)' }}
                  >
                    Compliance
                  </th>
                  <th
                    className="text-center px-4 py-3 text-xs font-medium uppercase tracking-wider"
                    style={{ color: 'var(--content-secondary)' }}
                  >
                    Status
                  </th>
                  <th
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
                          </span>
                        ) : (
                          <span
                            className="text-sm"
                            style={{ color: 'var(--content-secondary)' }}
                          >
                            --
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
    </LTILayout>
  );
}
