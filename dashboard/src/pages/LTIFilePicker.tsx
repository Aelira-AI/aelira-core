import React, { useState, useEffect, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { FileText, CheckCircle2, AlertCircle, ExternalLink } from 'lucide-react';
import { LTILayout } from '../components/LTILayout';
import { useLTISession } from '../hooks/useLTISession';
import { createLTIClient } from '../api/ltiClient';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface CanvasFile {
  id: string;
  display_name: string;
  size: number;
  content_type: string;
}

interface FileScanStatus {
  provider_file_id: string;
  file_name: string;
  scan_id: string | null;
  compliance_score: number | null;
  issues_count: number;
  status: string; // "not_tracked" | "pending" | "processing" | "completed" | "failed"
  has_remediated_version: boolean;
}

interface ScanStatusResponse {
  files: FileScanStatus[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatFileSize(bytes: number | null): string {
  if (!bytes) return 'N/A';
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb.toFixed(1)} MB`;
  const gb = mb / 1024;
  return `${gb.toFixed(1)} GB`;
}

function scoreColor(score: number): string {
  if (score >= 90) return 'var(--status-success-text)';
  if (score >= 70) return 'var(--status-warning-text)';
  return 'var(--status-error-text)';
}

function statusLabel(status: string): string {
  switch (status) {
    case 'completed':
      return 'Scanned';
    case 'processing':
      return 'Scanning...';
    case 'pending':
      return 'Pending';
    case 'failed':
      return 'Failed';
    default:
      return 'Not scanned';
  }
}

function statusColor(status: string): string {
  switch (status) {
    case 'completed':
      return 'var(--status-success-text)';
    case 'processing':
    case 'pending':
      return 'var(--status-warning-text)';
    case 'failed':
      return 'var(--status-error-text)';
    default:
      return 'var(--content-secondary)';
  }
}

// ---------------------------------------------------------------------------
// Merged file row type
// ---------------------------------------------------------------------------

interface FileRow {
  fileId: string;
  displayName: string;
  size: number;
  contentType: string;
  scanId: string | null;
  complianceScore: number | null;
  issuesCount: number;
  status: string;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function FileRowItem({
  file,
  onSelect,
  selecting,
}: {
  file: FileRow;
  onSelect: (file: FileRow) => void;
  selecting: string | null;
}): React.ReactElement {
  const isScanned = file.status === 'completed' && file.scanId !== null;
  const isSelecting = selecting === file.fileId;

  return (
    <div
      className="flex items-center gap-4 rounded-lg p-3"
      style={{
        backgroundColor: 'var(--card-bg)',
        border: '1px solid var(--card-border)',
      }}
    >
      {/* Icon */}
      <FileText className="w-5 h-5 shrink-0" style={{ color: 'var(--content-secondary)' }} />

      {/* File info */}
      <div className="flex-1 min-w-0">
        <p
          className="text-sm font-medium truncate"
          style={{ color: 'var(--content-primary)' }}
        >
          {file.displayName}
        </p>
        <p className="text-xs" style={{ color: 'var(--content-secondary)' }}>
          {formatFileSize(file.size)}
        </p>
      </div>

      {/* Score */}
      <div className="text-right shrink-0 w-20">
        {isScanned && file.complianceScore !== null ? (
          <span className="text-sm font-semibold" style={{ color: scoreColor(file.complianceScore) }}>
            {file.complianceScore.toFixed(0)}%
          </span>
        ) : (
          <span className="text-xs" style={{ color: statusColor(file.status) }}>
            {statusLabel(file.status)}
          </span>
        )}
      </div>

      {/* Action */}
      <div className="shrink-0">
        {isScanned ? (
          <button
            onClick={() => onSelect(file)}
            disabled={isSelecting}
            className="text-xs font-medium px-3 py-1.5 rounded-md disabled:opacity-50"
            style={{
              backgroundColor: 'var(--accent-primary)',
              color: '#fff',
            }}
          >
            {isSelecting ? 'Selecting...' : 'Select'}
          </button>
        ) : (
          <span
            className="text-xs px-3 py-1.5 rounded-md inline-block"
            style={{
              backgroundColor: 'var(--surface-primary)',
              color: 'var(--content-secondary)',
              border: '1px solid var(--border-primary)',
            }}
          >
            Scan first
          </span>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function LTIFilePicker(): React.ReactElement {
  const { courseId: paramCourseId } = useParams<{ courseId: string }>();
  const { accessToken, courseId: sessionCourseId, loading: sessionLoading, error: sessionError } = useLTISession();

  const resolvedCourseId = paramCourseId || sessionCourseId;

  const [files, setFiles] = useState<FileRow[]>([]);
  const [dataLoading, setDataLoading] = useState(true);
  const [dataError, setDataError] = useState<string | null>(null);
  const [selecting, setSelecting] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => {
    if (!accessToken || !resolvedCourseId) return;

    const client = createLTIClient(accessToken);

    async function fetchFiles(): Promise<void> {
      try {
        const [filesRes, statusRes] = await Promise.all([
          client.get<CanvasFile[]>(`/canvas/courses/${resolvedCourseId}/files`),
          client.get<ScanStatusResponse>(`/canvas/courses/${resolvedCourseId}/scan-status`),
        ]);

        const canvasFiles = filesRes.data;
        const scanStatuses = statusRes.data.files || [];

        // Build a lookup by provider_file_id
        const statusMap = new Map<string, FileScanStatus>();
        for (const s of scanStatuses) {
          statusMap.set(s.provider_file_id, s);
        }

        const merged: FileRow[] = canvasFiles.map((f) => {
          const status = statusMap.get(f.id);
          return {
            fileId: f.id,
            displayName: f.display_name,
            size: f.size,
            contentType: f.content_type,
            scanId: status?.scan_id ?? null,
            complianceScore: status?.compliance_score ?? null,
            issuesCount: status?.issues_count ?? 0,
            status: status?.status ?? 'not_tracked',
          };
        });

        // Sort: scanned files first, then by name
        merged.sort((a, b) => {
          const aScanned = a.status === 'completed' ? 0 : 1;
          const bScanned = b.status === 'completed' ? 0 : 1;
          if (aScanned !== bScanned) return aScanned - bScanned;
          return a.displayName.localeCompare(b.displayName);
        });

        setFiles(merged);
        setDataLoading(false);
      } catch {
        setDataError('Failed to load course files. Please relaunch from Canvas.');
        setDataLoading(false);
      }
    }

    fetchFiles();
  }, [accessToken, resolvedCourseId]);

  const handleSelect = useCallback(
    async (file: FileRow) => {
      if (!accessToken || !resolvedCourseId || !file.scanId) return;

      const client = createLTIClient(accessToken);
      setSelecting(file.fileId);

      try {
        await client.post('/lti/deep-link/submit', {
          scan_id: file.scanId,
          course_id: resolvedCourseId,
        });
        setSubmitted(true);
      } catch {
        setDataError('Failed to submit selection. Please try again.');
      } finally {
        setSelecting(null);
      }
    },
    [accessToken, resolvedCourseId],
  );

  // Success state after deep-link submission
  if (submitted) {
    return (
      <LTILayout loading={false} error={null}>
        <div className="max-w-2xl mx-auto text-center py-12">
          <CheckCircle2
            className="w-12 h-12 mx-auto mb-4"
            style={{ color: 'var(--status-success-text)' }}
          />
          <h1
            className="text-xl font-semibold mb-2"
            style={{ color: 'var(--content-primary)' }}
          >
            Content Added
          </h1>
          <p className="text-sm" style={{ color: 'var(--content-secondary)' }}>
            The accessibility report has been embedded in your Canvas module.
            You can close this window.
          </p>
        </div>
      </LTILayout>
    );
  }

  const scannedCount = files.filter((f) => f.status === 'completed').length;

  return (
    <LTILayout loading={sessionLoading || dataLoading} error={sessionError || dataError}>
      <div className="max-w-2xl mx-auto">
        {/* Header */}
        <div className="mb-6">
          <h1
            className="text-lg font-semibold mb-1"
            style={{ color: 'var(--content-primary)' }}
          >
            Select a file to embed its accessibility report
          </h1>
          <p className="text-sm" style={{ color: 'var(--content-secondary)' }}>
            {files.length} file{files.length !== 1 ? 's' : ''} in course
            {scannedCount > 0 && ` \u2022 ${scannedCount} scanned`}
          </p>
        </div>

        {/* File list */}
        {files.length === 0 ? (
          <div
            className="rounded-lg p-8 text-center"
            style={{
              backgroundColor: 'var(--card-bg)',
              border: '1px solid var(--card-border)',
            }}
          >
            <AlertCircle
              className="w-8 h-8 mx-auto mb-2"
              style={{ color: 'var(--content-secondary)' }}
            />
            <p className="text-sm" style={{ color: 'var(--content-secondary)' }}>
              No files found in this course.
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {files.map((file) => (
              <FileRowItem
                key={file.fileId}
                file={file}
                onSelect={handleSelect}
                selecting={selecting}
              />
            ))}
          </div>
        )}

        {/* Footer */}
        <div
          className="border-t mt-8 pt-4 text-center text-xs"
          style={{
            borderColor: 'var(--border-primary)',
            color: 'var(--content-secondary)',
          }}
        >
          Powered by{' '}
          <a
            href="https://aelira.ai"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 font-medium"
            style={{ color: 'var(--accent-primary)' }}
          >
            Aelira
            <ExternalLink className="w-3 h-3" />
          </a>
        </div>
      </div>
    </LTILayout>
  );
}
