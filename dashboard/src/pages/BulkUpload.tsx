import React, { useState, useCallback, useRef, ChangeEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useDropzone, FileRejection } from 'react-dropzone';
import {
  Upload,
  Folder,
  FileText,
  X,
  CheckCircle,
  AlertCircle,
  Play,
  Pause,
  RotateCcw,
  Loader,
  ArrowLeft,
  Download,
  Eye,
  Wrench,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { scansApi } from '../api/scans';
import { useToast } from '../context/toast-context';
import { FeatureGate } from '../components/FeatureGate';
import { trackEvent } from '../utils/analytics';

// Type definitions
type FileStatus = 'pending' | 'queued' | 'uploading' | 'processing' | 'complete' | 'error';
type ScanType = 'pdf' | 'word' | 'excel' | 'powerpoint' | 'latex' | 'image' | 'unknown';

interface ScanResult {
  compliance_score?: number;
  issues?: unknown[];
}

interface FileItem {
  file: File;
  id: string;
  status: FileStatus;
  progress: number;
  scanId: string | null;
  result: ScanResult | null;
  error: string | null;
}

interface StatusConfig {
  icon: LucideIcon;
  color: string;
  bg: string;
  animate?: boolean;
}

interface FileRowProps {
  file: FileItem;
  onRemove: (id: string) => void;
  onView: (scanId: string) => void;
  onRemediate: (scanId: string) => void;
}

interface BatchProgressProps {
  files: FileItem[];
}

interface UploadOptions {
  generateAltText: boolean;
  autoRemediate: boolean;
  concurrency: number;
}

const FILE_EXTENSIONS: Record<string, ScanType> = {
  '.pdf': 'pdf',
  '.docx': 'word',
  '.xlsx': 'excel',
  '.pptx': 'powerpoint',
  '.tex': 'latex',
  '.png': 'image',
  '.jpg': 'image',
  '.jpeg': 'image',
  '.gif': 'image',
  '.webp': 'image',
};

const STATUS_CONFIG: Record<FileStatus, StatusConfig> = {
  pending: { icon: FileText, color: 'text-tertiary', bg: 'bg-[var(--surface-tertiary)]' },
  queued: { icon: Loader, color: 'text-[var(--feature-info-content)]', bg: 'bg-[var(--feature-info-surface)]' },
  uploading: { icon: Loader, color: 'text-[var(--feature-info-content)]', bg: 'bg-[var(--feature-info-surface)]', animate: true },
  processing: { icon: Loader, color: 'text-[var(--feature-info-content)]', bg: 'bg-[var(--feature-info-surface)]', animate: true },
  complete: { icon: CheckCircle, color: 'text-[var(--feature-success-content)]', bg: 'bg-[var(--feature-success-surface)]' },
  error: { icon: AlertCircle, color: 'text-[var(--feature-danger-content)]', bg: 'bg-[var(--feature-danger-surface)]' },
};

function FileRow({ file, onRemove, onView, onRemediate }: FileRowProps): React.ReactElement {
  const config = STATUS_CONFIG[file.status] || STATUS_CONFIG.pending;
  const StatusIcon = config.icon;
  const ext = file.file.name.substring(file.file.name.lastIndexOf('.')).toLowerCase();
  const fileType = FILE_EXTENSIONS[ext] || 'unknown';

  return (
    <div className="flex items-center gap-4 p-3 border-b border-[var(--border-primary)] last:border-b-0">
      <div className={`p-2 rounded ${config.bg}`}>
        <StatusIcon
          className={`w-4 h-4 ${config.color} ${config.animate ? 'animate-spin' : ''}`}
        />
      </div>

      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-primary truncate">{file.file.name}</p>
        <div className="flex items-center gap-2 mt-0.5">
          <span className="text-xs text-tertiary">
            {(file.file.size / 1024 / 1024).toFixed(2)} MB
          </span>
          <span className="text-xs px-1.5 py-0.5 rounded bg-[var(--surface-tertiary)] text-secondary">
            {fileType.toUpperCase()}
          </span>
          {file.progress > 0 && file.progress < 100 && (
            <span className="text-xs text-[var(--feature-info-content)]">
              {file.progress}%
            </span>
          )}
        </div>
      </div>

      {file.result && (
        <div className="flex items-center gap-2">
          <span
            className={`text-sm font-semibold px-2 py-1 rounded ${
              (file.result.compliance_score ?? 0) >= 80
                ? 'bg-[var(--feature-success-surface)] text-[var(--feature-success-content)]'
                : (file.result.compliance_score ?? 0) >= 60
                ? 'bg-[var(--feature-warning-surface)] text-[var(--feature-warning-content)]'
                : 'bg-[var(--feature-danger-surface)] text-[var(--feature-danger-content)]'
            }`}
          >
            {file.result.compliance_score}/100
          </span>
          <span className="text-xs text-tertiary">
            {file.result.issues?.length || 0} issues
          </span>
        </div>
      )}

      <div className="flex items-center gap-1">
        {file.status === 'complete' && file.scanId && (
          <>
            <button
              onClick={() => onView(file.scanId!)}
              className="p-1.5 text-tertiary hover:text-accent transition-colors"
              title="View Details"
            >
              <Eye className="w-4 h-4" />
            </button>
            <button
              onClick={() => onRemediate(file.scanId!)}
              className="p-1.5 text-tertiary hover:text-[var(--feature-success-content)] transition-colors"
              title="Remediate"
            >
              <Wrench className="w-4 h-4" />
            </button>
          </>
        )}
        {(file.status === 'pending' || file.status === 'error') && (
          <button
            onClick={() => onRemove(file.id)}
            className="p-1.5 text-tertiary hover:text-[var(--feature-danger-content)] transition-colors"
            title="Remove"
          >
            <X className="w-4 h-4" />
          </button>
        )}
      </div>
    </div>
  );
}

function BatchProgress({ files }: BatchProgressProps): React.ReactElement {
  const total = files.length;
  const complete = files.filter((f) => f.status === 'complete').length;
  const failed = files.filter((f) => f.status === 'error').length;
  const processing = files.filter((f) => ['uploading', 'processing', 'queued'].includes(f.status)).length;
  const pending = files.filter((f) => f.status === 'pending').length;

  const percentage = total > 0 ? Math.round(((complete + failed) / total) * 100) : 0;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-secondary">Batch Progress</span>
        <span className="text-lg font-bold text-primary">{percentage}%</span>
      </div>

      <div className="h-3 bg-[var(--surface-tertiary)] rounded-full overflow-hidden flex">
        {complete > 0 && (
          <div
            className="h-full bg-[var(--feature-success-content)]"
            style={{ width: `${(complete / total) * 100}%` }}
          />
        )}
        {failed > 0 && (
          <div
            className="h-full bg-[var(--feature-danger-content)]"
            style={{ width: `${(failed / total) * 100}%` }}
          />
        )}
        {processing > 0 && (
          <div
            className="h-full bg-[var(--feature-info-content)] animate-pulse"
            style={{ width: `${(processing / total) * 100}%` }}
          />
        )}
      </div>

      <div className="grid grid-cols-4 gap-4 text-center">
        <div>
          <p className="text-xl font-bold text-primary">{pending}</p>
          <p className="text-xs text-tertiary">Pending</p>
        </div>
        <div>
          <p className="text-xl font-bold text-[var(--feature-info-content)]">{processing}</p>
          <p className="text-xs text-tertiary">Processing</p>
        </div>
        <div>
          <p className="text-xl font-bold text-[var(--feature-success-content)]">{complete}</p>
          <p className="text-xs text-tertiary">Complete</p>
        </div>
        <div>
          <p className="text-xl font-bold text-[var(--feature-danger-content)]">{failed}</p>
          <p className="text-xs text-tertiary">Failed</p>
        </div>
      </div>
    </div>
  );
}

export function BulkUpload(): React.ReactElement {
  const [files, setFiles] = useState<FileItem[]>([]);
  const [isProcessing, setIsProcessing] = useState<boolean>(false);
  const [isPaused, setIsPaused] = useState<boolean>(false);
  const [options, setOptions] = useState<UploadOptions>({
    generateAltText: false,
    autoRemediate: false,
    concurrency: 3,
  });
  const abortRef = useRef<boolean>(false);
  const navigate = useNavigate();
  const toast = useToast();

  const onDrop = useCallback((acceptedFiles: File[], _fileRejections: FileRejection[]) => {
    const newFiles: FileItem[] = acceptedFiles
      .filter((file) => {
        const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
        return FILE_EXTENSIONS[ext];
      })
      .map((file) => ({
        file,
        id: Math.random().toString(36).substr(2, 9),
        status: 'pending' as FileStatus,
        progress: 0,
        scanId: null,
        result: null,
        error: null,
      }));

    if (newFiles.length < acceptedFiles.length) {
      toast.warning(
        `${acceptedFiles.length - newFiles.length} unsupported files were skipped`,
        'Unsupported Files'
      );
    }

    setFiles((prev) => [...prev, ...newFiles]);
  }, [toast]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    noClick: false,
    noKeyboard: false,
    maxSize: 100 * 1024 * 1024,
    multiple: true,
  });

  const removeFile = (id: string): void => {
    setFiles((prev) => prev.filter((f) => f.id !== id));
  };

  const clearAll = (): void => {
    setFiles([]);
    setIsProcessing(false);
    setIsPaused(false);
    abortRef.current = false;
  };

  const processFile = async (fileItem: FileItem): Promise<void> => {
    const ext = fileItem.file.name.substring(fileItem.file.name.lastIndexOf('.')).toLowerCase();
    const fileType = FILE_EXTENSIONS[ext] || 'pdf';

    try {
      setFiles((prev) =>
        prev.map((f) =>
          f.id === fileItem.id ? { ...f, status: 'uploading' as FileStatus, progress: 10 } : f
        )
      );

      const uploadOptions: Record<string, boolean> = {};
      if (options.generateAltText) {
        uploadOptions.generate_alt_text = true;
      }

      const result = await scansApi.uploadFile(fileItem.file, fileType, uploadOptions);

      // Handle async processing
      if (result.status?.toUpperCase() === 'PROCESSING' && result.scan_id) {
        setFiles((prev) =>
          prev.map((f) =>
            f.id === fileItem.id
              ? { ...f, status: 'processing' as FileStatus, scanId: result.scan_id, progress: 30 }
              : f
          )
        );

        // Poll for completion
        let complete = false;
        while (!complete && !abortRef.current) {
          await new Promise((resolve) => setTimeout(resolve, 2000));

          try {
            const progress = await scansApi.getScanProgress(result.scan_id);

            setFiles((prev) =>
              prev.map((f) =>
                f.id === fileItem.id
                  ? { ...f, progress: progress.progress || 50 }
                  : f
              )
            );

            if (progress.status?.toUpperCase() === 'COMPLETED') {
              complete = true;
              const scanDetails = await scansApi.getScan(result.scan_id);

              setFiles((prev) =>
                prev.map((f) =>
                  f.id === fileItem.id
                    ? {
                        ...f,
                        status: 'complete' as FileStatus,
                        progress: 100,
                        result: scanDetails as ScanResult,
                      }
                    : f
                )
              );
            } else if (progress.status?.toUpperCase() === 'FAILED') {
              throw new Error(progress.error_message || 'Processing failed');
            }
          } catch (pollError) {
            console.error('Poll error:', pollError);
          }
        }
      } else {
        // Synchronous result
        setFiles((prev) =>
          prev.map((f) =>
            f.id === fileItem.id
              ? {
                  ...f,
                  status: 'complete' as FileStatus,
                  progress: 100,
                  scanId: result.scan_id || result.id || null,
                  result: result as ScanResult,
                }
              : f
          )
        );
      }

      // Auto-remediate if enabled
      if (options.autoRemediate && fileItem.scanId) {
        try {
          await scansApi.remediateScan(fileItem.scanId, { use_ai: true });
        } catch (remError) {
          console.error('Auto-remediation failed:', remError);
        }
      }
    } catch (error) {
      console.error('File processing failed:', error);
      const err = error as { response?: { data?: { detail?: string } }; message?: string };
      setFiles((prev) =>
        prev.map((f) =>
          f.id === fileItem.id
            ? {
                ...f,
                status: 'error' as FileStatus,
                error: err.response?.data?.detail || err.message || 'Processing failed',
              }
            : f
        )
      );
    }
  };

  const startProcessing = async (): Promise<void> => {
    trackEvent('dash-bulk-upload-started', { file_count: pendingCount, concurrency: options.concurrency });
    setIsProcessing(true);
    setIsPaused(false);
    abortRef.current = false;

    const pendingFiles = files.filter((f) => f.status === 'pending');

    // Process files with concurrency limit
    const queue = [...pendingFiles];
    const running = new Set<string>();

    const processNext = async (): Promise<void> => {
      while (queue.length > 0 && running.size < options.concurrency && !abortRef.current) {
        if (isPaused) {
          await new Promise((resolve) => setTimeout(resolve, 500));
          continue;
        }

        const file = queue.shift();
        if (!file) break;

        setFiles((prev) =>
          prev.map((f) => (f.id === file.id ? { ...f, status: 'queued' as FileStatus } : f))
        );

        running.add(file.id);
        processFile(file).finally(() => {
          running.delete(file.id);
        });
      }
    };

    // Start initial batch
    const workers = Array(Math.min(options.concurrency, pendingFiles.length))
      .fill(null)
      .map(() => processNext());

    await Promise.all(workers);

    // Wait for all running tasks
    while (running.size > 0) {
      await new Promise((resolve) => setTimeout(resolve, 500));
    }

    setIsProcessing(false);

    const complete = files.filter((f) => f.status === 'complete').length;
    const failed = files.filter((f) => f.status === 'error').length;

    toast.success(
      `Processed ${complete + failed} files: ${complete} successful, ${failed} failed`,
      'Batch Complete'
    );
  };

  const pauseProcessing = (): void => {
    setIsPaused(true);
    toast.info('Processing paused', 'Paused');
  };

  const resumeProcessing = (): void => {
    setIsPaused(false);
    toast.info('Processing resumed', 'Resumed');
  };

  const stopProcessing = (): void => {
    abortRef.current = true;
    setIsProcessing(false);
    setIsPaused(false);
    toast.warning('Processing stopped', 'Stopped');
  };

  const retryFailed = (): void => {
    setFiles((prev) =>
      prev.map((f) => (f.status === 'error' ? { ...f, status: 'pending' as FileStatus, error: null } : f))
    );
    startProcessing();
  };

  const downloadReport = async (): Promise<void> => {
    const completeFiles = files.filter((f) => f.status === 'complete' && f.result);
    if (completeFiles.length === 0) {
      toast.warning('No completed scans to export', 'Nothing to Export');
      return;
    }

    const report = {
      generated_at: new Date().toISOString(),
      total_files: files.length,
      summary: {
        complete: files.filter((f) => f.status === 'complete').length,
        failed: files.filter((f) => f.status === 'error').length,
        average_score:
          completeFiles.reduce((sum, f) => sum + (f.result?.compliance_score || 0), 0) /
          completeFiles.length,
      },
      files: completeFiles.map((f) => ({
        filename: f.file.name,
        scan_id: f.scanId,
        compliance_score: f.result?.compliance_score,
        issues_count: f.result?.issues?.length || 0,
      })),
    };

    const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `bulk-scan-report-${new Date().toISOString().split('T')[0]}.json`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);

    toast.success('Report downloaded', 'Success');
  };

  const pendingCount = files.filter((f) => f.status === 'pending').length;
  const failedCount = files.filter((f) => f.status === 'error').length;

  return (
    <FeatureGate
      feature="showBulkUpload"
      featureName="Bulk Upload"
      description="Upload and process multiple documents at once. Batch scan PDFs, Word documents, PowerPoints, and more with a single upload."
    >
    <div className="p-8">
      <div className="max-w-6xl mx-auto">
        <div className="flex items-center gap-4 mb-6">
          <button
            onClick={() => navigate('/upload')}
            className="p-2 rounded-lg hover:bg-[var(--surface-secondary)] transition-colors"
          >
            <ArrowLeft className="w-5 h-5 text-tertiary" />
          </button>
          <div className="flex-1">
            <h1 className="text-2xl font-bold text-primary">Bulk Upload</h1>
            <p className="text-sm text-secondary">
              Upload multiple files or folders for batch accessibility scanning
            </p>
          </div>
          {files.length > 0 && (
            <button onClick={downloadReport} className="btn-secondary flex items-center gap-2">
              <Download className="w-4 h-4" />
              Export Report
            </button>
          )}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Left Column - Upload Area */}
          <div className="lg:col-span-2 space-y-6">
            {/* Dropzone */}
            <div
              {...getRootProps()}
              className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors ${
                isDragActive
                  ? 'border-accent bg-[var(--surface-accent-subtle)]'
                  : 'border-[var(--border-primary)] hover:border-accent'
              }`}
            >
              <input {...getInputProps()} />
              <div className="flex items-center justify-center gap-8">
                <div className="text-center">
                  <Upload className="w-12 h-12 text-tertiary mx-auto mb-2" />
                  <p className="text-sm font-medium text-primary">Drop files here</p>
                  <p className="text-xs text-tertiary">or click to browse</p>
                </div>
                <div className="text-tertiary">or</div>
                <div className="text-center">
                  <Folder className="w-12 h-12 text-tertiary mx-auto mb-2" />
                  <p className="text-sm font-medium text-primary">Drop folder</p>
                  <p className="text-xs text-tertiary">for batch processing</p>
                </div>
              </div>
              <p className="text-xs text-tertiary mt-4">
                Supported: PDF, DOCX, XLSX, PPTX, TEX, PNG, JPG, GIF, WEBP (max 100MB each)
              </p>
            </div>

            {/* File List */}
            {files.length > 0 && (
              <div className="card">
                <div className="flex items-center justify-between mb-4">
                  <h2 className="text-lg font-semibold text-primary">
                    Files ({files.length})
                  </h2>
                  <button
                    onClick={clearAll}
                    className="text-sm text-tertiary hover:text-[var(--feature-danger-content)] transition-colors"
                    disabled={isProcessing}
                  >
                    Clear All
                  </button>
                </div>

                <div className="max-h-96 overflow-y-auto">
                  {files.map((file) => (
                    <FileRow
                      key={file.id}
                      file={file}
                      onRemove={removeFile}
                      onView={(scanId) => navigate(`/scan/${scanId}`)}
                      onRemediate={(scanId) => navigate(`/remediate/${scanId}`)}
                    />
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Right Column - Controls & Progress */}
          <div className="space-y-6">
            {/* Options */}
            <div className="card">
              <h2 className="text-lg font-semibold text-primary mb-4">Options</h2>
              <div className="space-y-4">
                <label className="flex items-center gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={options.generateAltText}
                    onChange={(e: ChangeEvent<HTMLInputElement>) =>
                      setOptions((o) => ({ ...o, generateAltText: e.target.checked }))
                    }
                    disabled={isProcessing}
                    className="w-4 h-4 rounded"
                  />
                  <div>
                    <p className="text-sm font-medium text-primary">Generate Alt Text</p>
                    <p className="text-xs text-tertiary">Use AI to generate image descriptions</p>
                  </div>
                </label>

                <label className="flex items-center gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={options.autoRemediate}
                    onChange={(e: ChangeEvent<HTMLInputElement>) =>
                      setOptions((o) => ({ ...o, autoRemediate: e.target.checked }))
                    }
                    disabled={isProcessing}
                    className="w-4 h-4 rounded"
                  />
                  <div>
                    <p className="text-sm font-medium text-primary">Auto-Remediate</p>
                    <p className="text-xs text-tertiary">Automatically fix issues after scan</p>
                  </div>
                </label>

                <div>
                  <label className="text-sm font-medium text-primary block mb-2">
                    Concurrent Uploads
                  </label>
                  <select
                    value={options.concurrency}
                    onChange={(e: ChangeEvent<HTMLSelectElement>) =>
                      setOptions((o) => ({ ...o, concurrency: parseInt(e.target.value) }))
                    }
                    disabled={isProcessing}
                    className="input w-full"
                  >
                    <option value={1}>1 file at a time</option>
                    <option value={3}>3 files at a time</option>
                    <option value={5}>5 files at a time</option>
                  </select>
                </div>
              </div>
            </div>

            {/* Progress */}
            {files.length > 0 && (
              <div className="card">
                <BatchProgress files={files} />
              </div>
            )}

            {/* Actions */}
            <div className="space-y-3">
              {!isProcessing && pendingCount > 0 && (
                <button
                  onClick={startProcessing}
                  className="btn-primary w-full flex items-center justify-center gap-2"
                >
                  <Play className="w-4 h-4" />
                  Start Processing ({pendingCount} files)
                </button>
              )}

              {isProcessing && !isPaused && (
                <button
                  onClick={pauseProcessing}
                  className="btn-secondary w-full flex items-center justify-center gap-2"
                >
                  <Pause className="w-4 h-4" />
                  Pause
                </button>
              )}

              {isProcessing && isPaused && (
                <button
                  onClick={resumeProcessing}
                  className="btn-primary w-full flex items-center justify-center gap-2"
                >
                  <Play className="w-4 h-4" />
                  Resume
                </button>
              )}

              {isProcessing && (
                <button
                  onClick={stopProcessing}
                  className="btn-secondary w-full flex items-center justify-center gap-2 text-[var(--feature-danger-content)]"
                >
                  <X className="w-4 h-4" />
                  Stop All
                </button>
              )}

              {!isProcessing && failedCount > 0 && (
                <button
                  onClick={retryFailed}
                  className="btn-secondary w-full flex items-center justify-center gap-2"
                >
                  <RotateCcw className="w-4 h-4" />
                  Retry Failed ({failedCount})
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
    </FeatureGate>
  );
}
