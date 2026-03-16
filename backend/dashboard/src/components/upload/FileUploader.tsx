import React, { useCallback, useState, useRef, ChangeEvent } from 'react';
import { useDropzone, Accept } from 'react-dropzone';
import {
  Upload,
  File,
  X,
  CheckCircle,
  AlertCircle,
  ExternalLink,
  Download,
  Loader2,
  Wrench,
  ShieldAlert,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { scansApi } from '../../api/scans';
import { unwrapResponse } from '../../utils/apiUnwrap';
import { trackEvent } from '../../components/Analytics';

// ============================================================================
// Types
// ============================================================================

interface FileUploaderProps {
  scanType: string;
  onUploadComplete?: (result: CompletedScanResult) => void;
}

interface FileTypeConfig {
  accept: Accept;
  label: string;
  apiType: string;
}

interface ParsedSecurityError {
  type: 'security';
  threatLevel: string;
  message: string;
  findings: string[];
}

interface ParsedGenericError {
  type: 'generic';
  message: string;
}

type ParsedError = ParsedSecurityError | ParsedGenericError;

interface FileItem {
  file: File;
  id: string;
  status: 'pending' | 'uploading' | 'complete' | 'error';
  progress: number;
  progressMessage?: string;
  scanId?: string;
  result?: ScanResult;
  error?: string;
  errorType?: string;
  securityFindings?: string[];
  threatLevel?: string;
  isRemediating?: boolean;
}

interface ScanResult {
  scan_id?: string;
  id?: string;
  status?: string;
  progress?: number;
  progress_message?: string;
  compliance_score?: number;
  issues?: ScanIssue[];
  [key: string]: unknown;
}

interface ScanIssue {
  severity: 'critical' | 'high' | 'medium' | 'low';
  [key: string]: unknown;
}

interface CompletedScanResult {
  id: string;
  filename: string;
  scanId: string;
  result: ScanResult;
  isRemediating?: boolean;
  isRemediated?: boolean;
}

interface UploadOptions {
  generate_alt_text?: boolean;
  validate_alt_text?: boolean;
  comprehensive_analysis?: boolean;
  detect_decorative?: boolean;
  detail_level?: 'brief' | 'standard' | 'detailed';
  generate_audio_descriptions?: boolean;
  generate_spoken_descriptions?: boolean;
  detect_flashing?: boolean;
  generate_transcript?: boolean;
  // Remediation options
  auto_remediate?: boolean;
  latex_formats?: string[];
}

interface ApiError {
  response?: {
    data?: {
      detail?: string | {
        error?: string;
        message?: string;
        findings?: Array<{ description?: string; category?: string }>;
        threat_level?: string;
      };
    };
    status?: number;
  };
  message?: string;
}

// ============================================================================
// Constants
// ============================================================================

const FILE_TYPE_CONFIG: Record<string, FileTypeConfig> = {
  pdf: {
    accept: { 'application/pdf': ['.pdf'] },
    label: 'PDF',
    apiType: 'pdf',
  },
  word: {
    accept: {
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
    },
    label: 'Word Document',
    apiType: 'word',
  },
  excel: {
    accept: {
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'],
    },
    label: 'Excel Spreadsheet',
    apiType: 'excel',
  },
  powerpoint: {
    accept: {
      'application/vnd.openxmlformats-officedocument.presentationml.presentation': ['.pptx'],
    },
    label: 'PowerPoint',
    apiType: 'powerpoint',
  },
  latex: {
    accept: { 'text/x-tex': ['.tex'], 'application/pdf': ['.pdf'] },
    label: 'LaTeX',
    apiType: 'latex',
  },
  image: {
    accept: { 'image/*': ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'] },
    label: 'Image',
    apiType: 'image',
  },
  chart: {
    accept: { 'image/*': ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'] },
    label: 'Chart/Graph',
    apiType: 'chart',
  },
  video: {
    accept: { 'video/*': ['.mp4', '.mov', '.avi'] },
    label: 'Video',
    apiType: 'multimedia',
  },
};

// ============================================================================
// Helper Functions
// ============================================================================

/**
 * Parse error response to extract meaningful error message
 * Handles security threat responses and other structured errors
 */
function parseUploadError(error: ApiError): ParsedError {
  const detail = error.response?.data?.detail;

  // Handle security threat detected error
  if (typeof detail === 'object' && detail?.error === 'security_threat_detected') {
    const findings = detail.findings || [];
    const threatLevel = detail.threat_level || 'unknown';
    return {
      type: 'security',
      threatLevel,
      message: detail.message || 'Security threat detected in file',
      findings: findings.map((f) => f.description || f.category).filter(Boolean) as string[],
    };
  }

  // Handle string detail
  if (typeof detail === 'string') {
    return { type: 'generic', message: detail };
  }

  // Handle object detail with message
  if (typeof detail === 'object' && detail?.message) {
    return { type: 'generic', message: detail.message };
  }

  // Fallback to error message
  return { type: 'generic', message: error.message || 'Upload failed' };
}

// ============================================================================
// Component
// ============================================================================

export function FileUploader({
  scanType,
  onUploadComplete,
}: FileUploaderProps): React.ReactElement {
  const [files, setFiles] = useState<FileItem[]>([]);
  const [uploading, setUploading] = useState<boolean>(false);
  const [completedScans, setCompletedScans] = useState<CompletedScanResult[]>([]);
  const [generateAltText, setGenerateAltText] = useState<boolean>(false);
  const [validateAltText, setValidateAltText] = useState<boolean>(false);
  const [comprehensiveAnalysis, setComprehensiveAnalysis] = useState<boolean>(true);
  const [detectDecorative, setDetectDecorative] = useState<boolean>(true);
  const [chartDetailLevel, setChartDetailLevel] = useState<'brief' | 'standard' | 'detailed'>('standard');
  // Video/Multimedia options
  const [generateAudioDescriptions, setGenerateAudioDescriptions] = useState<boolean>(true);
  const [generateSpokenDescriptions, setGenerateSpokenDescriptions] = useState<boolean>(false);
  const [detectFlashing, setDetectFlashing] = useState<boolean>(true);
  const [generateTranscript, setGenerateTranscript] = useState<boolean>(false);
  // Remediation options
  const [autoRemediate, setAutoRemediate] = useState<boolean>(false);
  // LaTeX output format options
  const [latexOutputFormats, setLatexOutputFormats] = useState<string[]>(['tex', 'pdf', 'html']);
  const activePolls = useRef<Record<string, ReturnType<typeof setTimeout> | boolean>>({});

  const config = FILE_TYPE_CONFIG[scanType];

  const onDrop = useCallback((acceptedFiles: File[]) => {
    const newFiles: FileItem[] = acceptedFiles.map((file) => ({
      file,
      id: Math.random().toString(36).substr(2, 9),
      status: 'pending',
      progress: 0,
    }));
    setFiles((prev) => [...prev, ...newFiles]);
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: config.accept,
    maxSize: 100 * 1024 * 1024, // 100MB
    multiple: true,
  });

  const removeFile = (id: string): void => {
    setFiles((prev) => prev.filter((f) => f.id !== id));
  };

  const cancelScan = async (fileItem: FileItem): Promise<void> => {
    try {
      console.log('[FileUploader] Cancelling scan for file:', fileItem.id);

      // CRITICAL: Stop the polling loop first
      if (activePolls.current[fileItem.id]) {
        if (typeof activePolls.current[fileItem.id] !== 'boolean') {
          clearTimeout(activePolls.current[fileItem.id] as ReturnType<typeof setTimeout>);
        }
        delete activePolls.current[fileItem.id];
        console.log('[FileUploader] Stopped polling for file:', fileItem.id);
      }

      // If scan has started, delete it from backend
      if (fileItem.scanId) {
        console.log('[FileUploader] Deleting scan from backend:', fileItem.scanId);
        await scansApi.deleteScan(fileItem.scanId);
      }

      // Remove from local state
      setFiles((prev) => prev.filter((f) => f.id !== fileItem.id));
      console.log('[FileUploader] Removed file from state:', fileItem.id);
    } catch (error) {
      console.error('[FileUploader] Failed to cancel scan:', error);
      // Still remove from local state even if delete failed
      setFiles((prev) => prev.filter((f) => f.id !== fileItem.id));
    }
  };

  const pollProgress = async (scanId: string, fileId: string): Promise<void> => {
    console.log('[FileUploader] pollProgress called with scanId:', scanId, 'fileId:', fileId);

    // Check if polling was cancelled
    if (!activePolls.current[fileId]) {
      console.log('[FileUploader] Polling cancelled for file:', fileId);
      return;
    }

    try {
      console.log('[FileUploader] About to call getScanProgress...');
      const progressData = await scansApi.getScanProgress(scanId);
      console.log('[FileUploader] Poll progress response:', progressData);

      // Check again if polling was cancelled during API call
      if (!activePolls.current[fileId]) {
        console.log('[FileUploader] Polling cancelled during API call for file:', fileId);
        return;
      }

      // Update file progress
      setFiles((prev) =>
        prev.map((f) =>
          f.id === fileId
            ? {
                ...f,
                progress: progressData.progress || 0,
                progressMessage: progressData.progress_message || 'Processing...',
              }
            : f
        )
      );

      // If still processing, poll again
      if (progressData.status.toUpperCase() === 'PROCESSING' && progressData.progress < 100) {
        console.log('[FileUploader] Still processing, scheduling next poll in 1s...');
        const timeoutId = setTimeout(() => pollProgress(scanId, fileId), 1000);
        activePolls.current[fileId] = timeoutId;
      } else if (progressData.status.toUpperCase() === 'COMPLETED') {
        // Clear timeout tracking
        delete activePolls.current[fileId];
        console.log('[FileUploader] Scan completed!');

        // Fetch full scan details and unwrap API response
        const scanDetails = await scansApi.getScan(scanId);
        // API returns { success, scan: { ..., result: { compliance_score, issues, ... } } }
        // Unwrap to get the result object the UI components expect
        const scanData = unwrapResponse<ScanResult>(scanDetails, 'scan');
        const innerResult = unwrapResponse<ScanResult>(scanData, 'result');
        const resultData: ScanResult = {
          scan_id: scanId,
          compliance_score: innerResult.compliance_score || 0,
          issues: innerResult.issues || [],
        };

        // Update to complete
        setFiles((prev) =>
          prev.map((f) =>
            f.id === fileId
              ? {
                  ...f,
                  status: 'complete',
                  progress: 100,
                  progressMessage: 'Complete',
                  scanId: scanId,
                  result: resultData,
                }
              : f
          )
        );

        // Add to completed scans
        setCompletedScans((prev) => [
          ...prev,
          {
            id: fileId,
            filename: files.find((f) => f.id === fileId)?.file.name || '',
            scanId: scanId,
            result: resultData,
          },
        ]);

        if (onUploadComplete) {
          onUploadComplete({
            id: fileId,
            filename: files.find((f) => f.id === fileId)?.file.name || '',
            scanId: scanId,
            result: resultData,
          });
        }

        // Auto-remediate if enabled
        if (autoRemediate) {
          try {
            console.log('[FileUploader] Auto-remediating scan:', scanId);
            setFiles((prev) =>
              prev.map((f) =>
                f.id === fileId ? { ...f, isRemediating: true, progressMessage: 'Remediating...' } : f
              )
            );
            setCompletedScans((prev) =>
              prev.map((s) =>
                s.scanId === scanId ? { ...s, isRemediating: true } : s
              )
            );
            const remediateOptions: { use_ai: boolean; latex_formats?: string[] } = { use_ai: true };
            // Pass LaTeX output formats if applicable
            if (config.apiType === 'latex' && latexOutputFormats.length > 0) {
              remediateOptions.latex_formats = latexOutputFormats;
            }
            await scansApi.remediateScan(scanId, remediateOptions);
            console.log('[FileUploader] Auto-remediation complete:', scanId);
            setCompletedScans((prev) =>
              prev.map((s) =>
                s.scanId === scanId ? { ...s, isRemediating: false, isRemediated: true } : s
              )
            );
          } catch (remError) {
            console.error('[FileUploader] Auto-remediation failed:', remError);
            setCompletedScans((prev) =>
              prev.map((s) =>
                s.scanId === scanId ? { ...s, isRemediating: false } : s
              )
            );
            // Don't fail the entire upload if remediation fails
          } finally {
            setFiles((prev) =>
              prev.map((f) =>
                f.id === fileId ? { ...f, isRemediating: false, progressMessage: 'Complete' } : f
              )
            );
          }
        }
      } else if (progressData.status.toUpperCase() === 'FAILED') {
        // Clear timeout tracking
        delete activePolls.current[fileId];
        setFiles((prev) =>
          prev.map((f) =>
            f.id === fileId
              ? {
                  ...f,
                  status: 'error',
                  error: progressData.error_message || 'Processing failed',
                }
              : f
          )
        );
      }
    } catch (error) {
      console.error('[FileUploader] ERROR in pollProgress:', error);
      const typedError = error as ApiError;
      console.error('[FileUploader] Error details:', {
        message: typedError.message,
        response: typedError.response,
        status: typedError.response?.status,
        data: typedError.response?.data,
      });

      // Check if polling was cancelled before retrying
      if (!activePolls.current[fileId]) {
        console.log('[FileUploader] Polling cancelled, not retrying for file:', fileId);
        return;
      }

      // Retry on error
      console.log('[FileUploader] Retrying in 2s due to error...');
      const timeoutId = setTimeout(() => pollProgress(scanId, fileId), 2000);
      activePolls.current[fileId] = timeoutId;
    }
  };

  const uploadFiles = async (): Promise<void> => {
    setUploading(true);

    const pendingFiles = files.filter((f) => f.status === 'pending');
    trackEvent('dash-upload-started', { file_count: pendingFiles.length, scan_type: scanType, auto_remediate: !!autoRemediate });

    for (const fileItem of files) {
      if (fileItem.status !== 'pending') continue;

      try {
        // Update status to uploading
        setFiles((prev) =>
          prev.map((f) =>
            f.id === fileItem.id
              ? {
                  ...f,
                  status: 'uploading',
                  progress: 0,
                  progressMessage: 'Uploading file...',
                }
              : f
          )
        );

        // Upload file to backend API for scanning
        const options: UploadOptions = {};
        if (config.apiType === 'pdf' || config.apiType === 'powerpoint' || config.apiType === 'word') {
          if (generateAltText) {
            options.generate_alt_text = true;
          }
          if (validateAltText) {
            options.validate_alt_text = true;
          }
        }
        // Image analysis options
        if (config.apiType === 'image') {
          if (comprehensiveAnalysis) {
            options.comprehensive_analysis = true;
          }
          if (detectDecorative) {
            options.detect_decorative = true;
          }
        }
        // Chart/Graph options
        if (config.apiType === 'chart') {
          options.detail_level = chartDetailLevel;
        }
        // Video/Multimedia options
        if (config.apiType === 'multimedia') {
          options.generate_audio_descriptions = generateAudioDescriptions;
          options.generate_spoken_descriptions = generateSpokenDescriptions;
          options.detect_flashing = detectFlashing;
          options.generate_transcript = generateTranscript;
        }
        // Remediation options (for document types)
        if (['pdf', 'word', 'powerpoint', 'excel', 'latex'].includes(config.apiType)) {
          options.auto_remediate = autoRemediate;
          // LaTeX-specific output formats
          if (config.apiType === 'latex' && autoRemediate) {
            options.latex_formats = latexOutputFormats;
          }
        }
        const result = await scansApi.uploadFile(fileItem.file, config.apiType, options);

        // DEBUG: Log upload result
        console.log('[FileUploader] Upload result:', result);
        console.log('[FileUploader] result.status:', result.status);
        console.log('[FileUploader] result.scan_id:', result.scan_id);

        // Check if backend returns scan_id and status=PROCESSING (async mode)
        if (result.status && result.status.toUpperCase() === 'PROCESSING' && result.scan_id) {
          console.log('[FileUploader] ASYNC MODE DETECTED - Starting polling!');

          // Update with scan_id and start polling
          setFiles((prev) =>
            prev.map((f) =>
              f.id === fileItem.id
                ? {
                    ...f,
                    status: 'uploading',
                    scanId: result.scan_id,
                    progress: result.progress || 0,
                    progressMessage: result.progress_message || 'Processing...',
                  }
                : f
            )
          );

          // Initialize poll tracking (using a placeholder value)
          activePolls.current[fileItem.id] = true;

          // Start polling for progress
          pollProgress(result.scan_id, fileItem.id);
        } else {
          console.log('[FileUploader] SYNC MODE - Not polling');

          // Synchronous response (old behavior) - mark as complete immediately
          setFiles((prev) =>
            prev.map((f) =>
              f.id === fileItem.id
                ? {
                    ...f,
                    status: 'complete',
                    progress: 100,
                    progressMessage: result.progress_message || 'Complete',
                    scanId: result.scan_id || result.id,
                    result: result,
                  }
                : f
            )
          );

          setCompletedScans((prev) => [
            ...prev,
            {
              id: fileItem.id,
              filename: fileItem.file.name,
              scanId: result.scan_id || result.id || '',
              result: result,
            },
          ]);

          if (onUploadComplete) {
            onUploadComplete({
              id: fileItem.id,
              filename: fileItem.file.name,
              scanId: result.scan_id || result.id || '',
              result: result,
            });
          }

          // Auto-remediate if enabled (sync mode)
          if (autoRemediate && (result.scan_id || result.id)) {
            try {
              console.log(
                '[FileUploader] Auto-remediating scan (sync):',
                result.scan_id || result.id
              );
              const remediateOptions: { use_ai: boolean; latex_formats?: string[] } = { use_ai: true };
              // Pass LaTeX output formats if applicable
              if (config.apiType === 'latex' && latexOutputFormats.length > 0) {
                remediateOptions.latex_formats = latexOutputFormats;
              }
              await scansApi.remediateScan(result.scan_id || result.id || '', remediateOptions);
              console.log(
                '[FileUploader] Auto-remediation complete (sync):',
                result.scan_id || result.id
              );
            } catch (remError) {
              console.error('[FileUploader] Auto-remediation failed (sync):', remError);
              // Don't fail the entire upload if remediation fails
            }
          }
        }
      } catch (error) {
        console.error('Upload failed:', error);
        const parsedError = parseUploadError(error as ApiError);
        setFiles((prev) =>
          prev.map((f) =>
            f.id === fileItem.id
              ? {
                  ...f,
                  status: 'error',
                  errorType: parsedError.type,
                  error: parsedError.message,
                  securityFindings:
                    parsedError.type === 'security' ? parsedError.findings : undefined,
                  threatLevel: parsedError.type === 'security' ? parsedError.threatLevel : undefined,
                }
              : f
          )
        );
      }
    }

    setUploading(false);
  };

  return (
    <div className="space-y-6">
      {/* PDF-specific options */}
      {config.apiType === 'pdf' && (
        <div className="card p-4">
          <h2 className="font-medium text-primary mb-3 text-base">Scan Options</h2>
          <div className="space-y-3">
            <label className="flex items-center space-x-3 cursor-pointer">
              <input
                type="checkbox"
                checked={generateAltText}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setGenerateAltText(e.target.checked)}
                className="w-4 h-4 text-[var(--content-accent)] rounded focus:ring-[var(--interactive-primary-bg)]"
                disabled={uploading}
              />
              <div>
                <span className="text-sm font-medium text-primary">
                  Generate AI Alt Text for Images
                </span>
                <p className="text-xs text-secondary mt-0.5">
                  Use llava:7b vision model to automatically generate accessible alt text for all
                  images in the PDF. Adds ~10 seconds per image.
                </p>
              </div>
            </label>
            <label className="flex items-center space-x-3 cursor-pointer">
              <input
                type="checkbox"
                checked={validateAltText}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setValidateAltText(e.target.checked)}
                className="w-4 h-4 text-[var(--content-accent)] rounded focus:ring-[var(--interactive-primary-bg)]"
                disabled={uploading}
              />
              <div>
                <span className="text-sm font-medium text-primary">
                  Validate Existing Alt Text Accuracy
                </span>
                <p className="text-xs text-secondary mt-0.5">
                  Use AI vision to verify if existing alt text accurately describes image content.
                  Identifies misleading or incorrect descriptions.
                </p>
              </div>
            </label>
          </div>
        </div>
      )}

      {/* PowerPoint-specific options */}
      {config.apiType === 'powerpoint' && (
        <div className="card p-4">
          <h2 className="font-medium text-primary mb-3 text-base">Scan Options</h2>
          <div className="space-y-3">
            <label className="flex items-center space-x-3 cursor-pointer">
              <input
                type="checkbox"
                checked={generateAltText}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setGenerateAltText(e.target.checked)}
                className="w-4 h-4 text-[var(--content-accent)] rounded focus:ring-[var(--interactive-primary-bg)]"
                disabled={uploading}
              />
              <div>
                <span className="text-sm font-medium text-primary">
                  Generate AI Alt Text for Images
                </span>
                <p className="text-xs text-secondary mt-0.5">
                  Use AI vision to automatically generate accessible alt text for images missing
                  descriptions. Adds ~10 seconds per image.
                </p>
              </div>
            </label>
            <label className="flex items-center space-x-3 cursor-pointer">
              <input
                type="checkbox"
                checked={validateAltText}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setValidateAltText(e.target.checked)}
                className="w-4 h-4 text-[var(--content-accent)] rounded focus:ring-[var(--interactive-primary-bg)]"
                disabled={uploading}
              />
              <div>
                <span className="text-sm font-medium text-primary">
                  Validate Existing Alt Text Accuracy
                </span>
                <p className="text-xs text-secondary mt-0.5">
                  Use AI vision to verify if existing alt text accurately describes image content.
                  Identifies misleading or incorrect descriptions.
                </p>
              </div>
            </label>
          </div>
        </div>
      )}

      {/* Image-specific options */}
      {config.apiType === 'image' && (
        <div className="card p-4">
          <h2 className="font-medium text-primary mb-3 text-base">Analysis Options</h2>
          <div className="space-y-3">
            <label className="flex items-center space-x-3 cursor-pointer">
              <input
                type="checkbox"
                checked={comprehensiveAnalysis}
                onChange={(e: ChangeEvent<HTMLInputElement>) =>
                  setComprehensiveAnalysis(e.target.checked)
                }
                className="w-4 h-4 text-[var(--content-accent)] rounded focus:ring-[var(--interactive-primary-bg)]"
                disabled={uploading}
              />
              <div>
                <span className="text-sm font-medium text-primary">Comprehensive Analysis</span>
                <p className="text-xs text-secondary mt-0.5">
                  Performs full analysis: type detection, description generation, and
                  recommendations. Best for ensuring complete WCAG compliance.
                </p>
              </div>
            </label>
            <label className="flex items-center space-x-3 cursor-pointer">
              <input
                type="checkbox"
                checked={detectDecorative}
                onChange={(e: ChangeEvent<HTMLInputElement>) =>
                  setDetectDecorative(e.target.checked)
                }
                className="w-4 h-4 text-[var(--content-accent)] rounded focus:ring-[var(--interactive-primary-bg)]"
                disabled={uploading}
              />
              <div>
                <span className="text-sm font-medium text-primary">Detect Decorative Images</span>
                <p className="text-xs text-secondary mt-0.5">
                  Automatically classify images as decorative or informative. Decorative images
                  should use empty alt="" (WCAG 1.1.1).
                </p>
              </div>
            </label>
          </div>
        </div>
      )}

      {/* Chart/Graph-specific options */}
      {config.apiType === 'chart' && (
        <div className="card p-4">
          <h2 className="font-medium text-primary mb-3 text-base">Description Options</h2>
          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium text-primary block mb-2">Detail Level</label>
              <select
                value={chartDetailLevel}
                onChange={(e: ChangeEvent<HTMLSelectElement>) =>
                  setChartDetailLevel(e.target.value as 'brief' | 'standard' | 'detailed')
                }
                className="w-full p-2 border border-[var(--border-primary)] rounded-lg bg-[var(--surface-primary)] text-primary"
                disabled={uploading}
              >
                <option value="brief">Brief - 2-3 sentence summary</option>
                <option value="standard">Standard - Comprehensive with key data points</option>
                <option value="detailed">Detailed - All visible data values and elements</option>
              </select>
              <p className="text-xs text-secondary mt-1">
                Choose how much detail to include in the chart description.
              </p>
            </div>
            <div
              className="rounded-lg p-3"
              style={{
                backgroundColor: 'var(--surface-info-subtle)',
                border: '1px solid var(--content-info)',
                color: 'var(--content-info)',
              }}
            >
              <p className="text-sm">
                <strong>Tip:</strong> Upload charts, graphs, infographics, or diagrams. The AI will
                generate both a short description for alt text and a detailed description for
                figcaption or aria-describedby.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Video/Multimedia-specific options */}
      {config.apiType === 'multimedia' && (
        <div className="card p-4">
          <h2 className="font-medium text-primary mb-3 text-base">Accessibility Options</h2>
          <div className="space-y-3">
            <label className="flex items-center space-x-3 cursor-pointer">
              <input
                type="checkbox"
                checked={generateAudioDescriptions}
                onChange={(e: ChangeEvent<HTMLInputElement>) =>
                  setGenerateAudioDescriptions(e.target.checked)
                }
                className="w-4 h-4 text-[var(--content-accent)] rounded focus:ring-[var(--interactive-primary-bg)]"
                disabled={uploading}
              />
              <div>
                <span className="text-sm font-medium text-primary">
                  Generate Audio Descriptions
                </span>
                <p className="text-xs text-secondary mt-0.5">
                  Use AI vision to describe visual content for blind users (WCAG 1.2.3, 1.2.5).
                  Extracts keyframes and generates descriptions.
                </p>
              </div>
            </label>
            <label className="flex items-center space-x-3 cursor-pointer">
              <input
                type="checkbox"
                checked={generateSpokenDescriptions}
                onChange={(e: ChangeEvent<HTMLInputElement>) =>
                  setGenerateSpokenDescriptions(e.target.checked)
                }
                className="w-4 h-4 text-[var(--content-accent)] rounded focus:ring-[var(--interactive-primary-bg)]"
                disabled={uploading || !generateAudioDescriptions}
              />
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-primary">
                    Convert to Spoken Audio (TTS)
                  </span>
                  <span
                    className="text-xs px-2 py-0.5 rounded-full font-medium"
                    style={{
                      backgroundColor: 'var(--surface-success-subtle, rgba(16, 185, 129, 0.15))',
                      color: 'var(--content-success, #059669)',
                    }}
                  >
                    New
                  </span>
                </div>
                <p className="text-xs text-secondary mt-0.5">
                  Use Piper TTS to convert text descriptions to spoken audio (MP3). Creates an audio
                  file for blind users to listen to.
                </p>
              </div>
            </label>
            <label className="flex items-center space-x-3 cursor-pointer">
              <input
                type="checkbox"
                checked={detectFlashing}
                onChange={(e: ChangeEvent<HTMLInputElement>) =>
                  setDetectFlashing(e.target.checked)
                }
                className="w-4 h-4 text-[var(--content-accent)] rounded focus:ring-[var(--interactive-primary-bg)]"
                disabled={uploading}
              />
              <div>
                <span className="text-sm font-medium text-primary">Detect Flashing Content</span>
                <p className="text-xs text-secondary mt-0.5">
                  Check for seizure-triggering flashing (WCAG 2.3.1). Analyzes frame brightness
                  changes.
                </p>
              </div>
            </label>
            <label className="flex items-center space-x-3 cursor-pointer">
              <input
                type="checkbox"
                checked={generateTranscript}
                onChange={(e: ChangeEvent<HTMLInputElement>) =>
                  setGenerateTranscript(e.target.checked)
                }
                className="w-4 h-4 text-[var(--content-accent)] rounded focus:ring-[var(--interactive-primary-bg)]"
                disabled={uploading}
              />
              <div>
                <span className="text-sm font-medium text-primary">Generate Full Transcript</span>
                <p className="text-xs text-secondary mt-0.5">
                  Combine audio transcription with visual descriptions (WCAG 1.2.8). Creates a
                  complete text alternative.
                </p>
              </div>
            </label>
          </div>
        </div>
      )}

      {/* Auto-Remediation option for document types */}
      {['pdf', 'word', 'powerpoint', 'excel', 'latex'].includes(config.apiType) && (
        <div className="card p-4">
          <h2 className="font-medium text-primary mb-3 text-base">Remediation Options</h2>
          <div className="space-y-3">
            <label className="flex items-center space-x-3 cursor-pointer">
              <input
                type="checkbox"
                checked={autoRemediate}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setAutoRemediate(e.target.checked)}
                className="w-4 h-4 text-[var(--content-accent)] rounded focus:ring-[var(--interactive-primary-bg)]"
                disabled={uploading}
              />
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-primary">
                    Auto-Remediate After Scan
                  </span>
                  <span
                    className="text-xs px-2 py-0.5 rounded-full font-medium"
                    style={{
                      backgroundColor: 'var(--surface-success-subtle, rgba(16, 185, 129, 0.15))',
                      color: 'var(--content-success, #059669)',
                    }}
                  >
                    New
                  </span>
                </div>
                <p className="text-xs text-secondary mt-0.5">
                  Automatically fix accessibility issues after scanning completes. Creates a
                  remediated version with AI-powered fixes applied.
                </p>
              </div>
            </label>

            {/* LaTeX Output Format Options */}
            {config.apiType === 'latex' && autoRemediate && (
              <div className="mt-4 pl-7 border-l-2 border-[var(--border-accent)]">
                <p className="text-sm font-medium text-primary mb-2">Output Formats</p>
                <p className="text-xs text-secondary mb-3">
                  Select which formats to generate for the remediated LaTeX document.
                </p>
                <div className="flex flex-wrap gap-3">
                  {[
                    { id: 'tex', label: 'LaTeX (.tex)', desc: 'Accessible LaTeX source' },
                    { id: 'pdf', label: 'PDF', desc: 'Compiled accessible PDF' },
                    { id: 'html', label: 'HTML', desc: 'Web-accessible HTML' },
                  ].map((format) => (
                    <label
                      key={format.id}
                      className={`flex items-center gap-2 px-3 py-2 rounded-lg border cursor-pointer transition-colors ${
                        latexOutputFormats.includes(format.id)
                          ? 'border-[var(--border-accent)] bg-[var(--surface-accent-subtle)]'
                          : 'border-[var(--border-primary)] hover:border-[var(--border-accent)]'
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={latexOutputFormats.includes(format.id)}
                        onChange={(e) => {
                          if (e.target.checked) {
                            setLatexOutputFormats([...latexOutputFormats, format.id]);
                          } else {
                            // Keep at least one format selected
                            if (latexOutputFormats.length > 1) {
                              setLatexOutputFormats(latexOutputFormats.filter((f) => f !== format.id));
                            }
                          }
                        }}
                        className="w-4 h-4 text-[var(--content-accent)] rounded focus:ring-[var(--interactive-primary-bg)]"
                        disabled={uploading}
                      />
                      <div>
                        <span className="text-sm font-medium text-primary">{format.label}</span>
                        <p className="text-xs text-secondary">{format.desc}</p>
                      </div>
                    </label>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Dropzone */}
      <div
        {...getRootProps({
          role: 'button',
          'aria-label': `Upload ${config.label} files. Drag and drop or press Enter to browse.`,
        })}
        className={`border-2 border-dashed rounded-lg p-12 text-center cursor-pointer transition-colors focus-visible:outline-2 focus-visible:outline-[var(--accent-primary)] focus-visible:outline-offset-2 ${
          isDragActive
            ? 'border-primary-500 bg-primary-50 dark:bg-primary-900/20'
            : 'border-[var(--border-primary)] hover:border-[var(--border-accent)]'
        }`}
      >
        <input {...getInputProps()} />
        <Upload className="w-12 h-12 text-secondary mx-auto mb-4" />
        {isDragActive ? (
          <p className="text-lg text-primary-600 dark:text-primary-400">Drop files here...</p>
        ) : (
          <div>
            <p className="text-lg text-primary mb-2">
              Drag & drop {config.label} files here, or click to select
            </p>
            <p className="text-sm text-secondary">Max file size: 100MB</p>
          </div>
        )}
      </div>

      {/* File List */}
      {files.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-lg font-semibold text-primary">Files ({files.length})</h2>

          {files.map((fileItem) => (
            <div key={fileItem.id} className="card flex items-center justify-between p-4">
              <div className="flex items-center space-x-3 flex-1">
                {fileItem.status === 'uploading' ? (
                  <Loader2 className="w-5 h-5 text-primary-600 dark:text-primary-400 animate-spin" />
                ) : (
                  <File className="w-5 h-5 text-secondary" />
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-primary truncate">{fileItem.file.name}</p>
                  <p className="text-xs text-secondary">
                    {(fileItem.file.size / 1024 / 1024).toFixed(2)} MB
                  </p>
                </div>

                {/* Status */}
                {fileItem.status === 'pending' && (
                  <button
                    onClick={() => removeFile(fileItem.id)}
                    className="text-secondary hover:opacity-70 transition-opacity"
                    aria-label={`Remove ${fileItem.file.name}`}
                  >
                    <X className="w-5 h-5" aria-hidden="true" />
                  </button>
                )}

                {fileItem.status === 'uploading' && (
                  <div className="flex items-center space-x-3">
                    <div className="flex flex-col items-end space-y-1">
                      <div className="flex items-center space-x-2">
                        <div
                          className="w-32 rounded-full h-2"
                          style={{ backgroundColor: 'var(--surface-tertiary)' }}
                          role="progressbar"
                          aria-valuenow={fileItem.progress}
                          aria-valuemin={0}
                          aria-valuemax={100}
                          aria-label={`${fileItem.file.name} upload progress`}
                        >
                          <div
                            className="h-2 rounded-full transition-all"
                            style={{ width: `${fileItem.progress}%`, backgroundColor: 'var(--accent-primary)' }}
                          />
                        </div>
                        <span className="text-sm text-secondary">{fileItem.progress}%</span>
                      </div>
                      {fileItem.progressMessage && (
                        <span className="text-xs text-secondary italic" aria-live="polite">
                          {fileItem.progressMessage}
                        </span>
                      )}
                    </div>
                    <button
                      onClick={() => cancelScan(fileItem)}
                      className="text-[var(--feature-danger-content)] hover:opacity-70 transition-opacity"
                      aria-label={`Cancel scan for ${fileItem.file.name}`}
                    >
                      <X className="w-5 h-5" aria-hidden="true" />
                    </button>
                  </div>
                )}

                {fileItem.status === 'complete' && (
                  <span role="status">
                    <CheckCircle className="w-5 h-5 text-[var(--feature-success-content)]" aria-hidden="true" />
                    <span className="sr-only">{fileItem.file.name} scan complete</span>
                  </span>
                )}

                {fileItem.status === 'error' && fileItem.errorType === 'security' && (
                  <div className="flex items-start space-x-2 max-w-md">
                    <ShieldAlert className="w-5 h-5 text-[var(--feature-danger-content)] shrink-0 mt-0.5" />
                    <div className="text-left">
                      <span className="text-xs font-medium text-[var(--feature-danger-content)] block">
                        Security Threat Detected
                      </span>
                      <span className="text-xs text-secondary block mt-1">{fileItem.error}</span>
                      {fileItem.securityFindings && fileItem.securityFindings.length > 0 && (
                        <ul className="text-xs text-secondary mt-1 list-disc list-inside">
                          {fileItem.securityFindings.slice(0, 3).map((finding, i) => (
                            <li key={i}>{finding}</li>
                          ))}
                        </ul>
                      )}
                    </div>
                  </div>
                )}

                {fileItem.status === 'error' && fileItem.errorType !== 'security' && (
                  <div className="flex items-center space-x-2">
                    <AlertCircle className="w-5 h-5 text-[var(--feature-danger-content)]" />
                    <span className="text-xs text-[var(--feature-danger-content)]">
                      {fileItem.error}
                    </span>
                  </div>
                )}
              </div>
            </div>
          ))}

          {/* Remediate checkbox - shown after files are staged for document types */}
          {['pdf', 'word', 'powerpoint', 'excel'].includes(config.apiType) &&
            files.some((f) => f.status === 'pending') && (
              <div
                className="p-4 rounded-lg"
                style={{ backgroundColor: 'var(--surface-secondary)' }}
              >
                <label className="flex items-center space-x-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={autoRemediate}
                    onChange={(e: ChangeEvent<HTMLInputElement>) =>
                      setAutoRemediate(e.target.checked)
                    }
                    className="w-4 h-4 text-[var(--content-accent)] rounded focus:ring-[var(--interactive-primary-bg)]"
                    disabled={uploading}
                  />
                  <div>
                    <div className="flex items-center gap-2">
                      <Wrench className="w-4 h-4 text-primary" />
                      <span className="text-sm font-medium text-primary">
                        Remediate accessibility issues
                      </span>
                    </div>
                    <p className="text-xs text-secondary mt-0.5">
                      Automatically fix issues and generate a remediated file for download.
                    </p>
                  </div>
                </label>
              </div>
            )}

          {/* Scan Button */}
          <button
            onClick={uploadFiles}
            disabled={uploading || files.every((f) => f.status !== 'pending')}
            className="btn-primary w-full"
          >
            {uploading
              ? 'Scanning...'
              : `Scan ${files.filter((f) => f.status === 'pending').length} file(s)`}
          </button>
        </div>
      )}

      {/* Completed Scans Results */}
      {completedScans.length > 0 && (
        <div className="space-y-4 mt-8">
          <h2 className="text-xl font-bold text-primary">Scan Results</h2>
          {completedScans.map((scan) => (
            <div key={scan.id} className="card p-6 space-y-4">
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="text-lg font-semibold text-primary">{scan.filename}</h3>
                  <p className="text-sm text-secondary mt-1">Scan ID: {scan.scanId}</p>
                </div>
                <div className="flex items-center space-x-2">
                  <CheckCircle className="w-6 h-6 text-[var(--feature-success-content)]" />
                  <span className="text-sm font-medium text-[var(--feature-success-content)]">
                    Complete
                  </span>
                </div>
              </div>

              {/* Compliance Score */}
              {scan.result.compliance_score !== undefined && (
                <div className="flex items-center space-x-4">
                  <div className="flex-1">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium text-primary">Compliance Score</span>
                      <span className="text-2xl font-bold text-primary">
                        {scan.result.compliance_score}%
                      </span>
                    </div>
                    <div className="w-full rounded-full h-3" style={{ backgroundColor: 'var(--surface-tertiary)' }} role="progressbar" aria-valuenow={scan.result.compliance_score} aria-valuemin={0} aria-valuemax={100} aria-label={`Compliance score: ${scan.result.compliance_score}% - ${scan.result.compliance_score >= 80 ? 'Good' : scan.result.compliance_score >= 60 ? 'Needs improvement' : 'Poor'}`}>
                      <div
                        className={`h-3 rounded-full transition-all ${
                          scan.result.compliance_score >= 80
                            ? 'bg-[var(--feature-success-content)]'
                            : scan.result.compliance_score >= 60
                              ? 'bg-[var(--feature-warning-content)]'
                              : 'bg-[var(--feature-danger-content)]'
                        }`}
                        style={{ width: `${scan.result.compliance_score}%` }}
                      />
                    </div>
                    <span className="sr-only">
                      {scan.result.compliance_score >= 80 ? 'Good' : scan.result.compliance_score >= 60 ? 'Needs improvement' : 'Poor'}
                    </span>
                  </div>
                </div>
              )}

              {/* Issues Summary */}
              {scan.result.issues && scan.result.issues.length > 0 && (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <div className="bg-[var(--feature-danger-surface)] border border-[var(--feature-danger-content)] rounded-lg p-3">
                    <p className="text-xs text-[var(--feature-danger-content)] font-medium">
                      Critical
                    </p>
                    <p className="text-2xl font-bold text-[var(--feature-danger-content)]">
                      {scan.result.issues.filter((i) => i.severity === 'critical').length || 0}
                    </p>
                  </div>
                  <div className="bg-[var(--feature-warning-surface)] border border-[var(--feature-warning-content)] rounded-lg p-3">
                    <p className="text-xs text-[var(--feature-warning-content)] font-medium">High</p>
                    <p className="text-2xl font-bold text-[var(--feature-warning-content)]">
                      {scan.result.issues.filter((i) => i.severity === 'high').length || 0}
                    </p>
                  </div>
                  <div className="bg-[var(--feature-info-surface)] border border-[var(--feature-info-content)] rounded-lg p-3">
                    <p className="text-xs text-[var(--feature-info-content)] font-medium">Medium</p>
                    <p className="text-2xl font-bold text-[var(--feature-info-content)]">
                      {scan.result.issues.filter((i) => i.severity === 'medium').length || 0}
                    </p>
                  </div>
                  <div className="bg-[var(--feature-info-surface)] border border-[var(--feature-info-content)] rounded-lg p-3">
                    <p className="text-xs text-[var(--feature-info-content)] font-medium">Low</p>
                    <p className="text-2xl font-bold text-[var(--feature-info-content)]">
                      {scan.result.issues.filter((i) => i.severity === 'low').length || 0}
                    </p>
                  </div>
                </div>
              )}

              {/* Action Buttons */}
              <div className="flex flex-wrap gap-3 pt-2">
                <Link to={`/scan/${scan.scanId}`} className="btn-primary flex items-center space-x-2">
                  <ExternalLink className="w-4 h-4" />
                  <span>View Full Report</span>
                </Link>
                <button
                  onClick={async () => {
                    try {
                      trackEvent('dash-download-report', {});
                      const blob = await scansApi.downloadReport(scan.scanId);
                      const url = window.URL.createObjectURL(blob);
                      const a = document.createElement('a');
                      a.href = url;
                      a.download = `${scan.filename.replace(/\.[^/.]+$/, '')}_report.pdf`;
                      document.body.appendChild(a);
                      a.click();
                      window.URL.revokeObjectURL(url);
                      document.body.removeChild(a);
                    } catch (error) {
                      console.error('Download failed:', error);
                      const typedError = error as ApiError;
                      alert(
                        'Failed to download report: ' +
                          (typedError.response?.data?.detail || typedError.message)
                      );
                    }
                  }}
                  className="btn-secondary flex items-center space-x-2"
                >
                  <Download className="w-4 h-4" />
                  <span>Download Report</span>
                </button>
                {/* Remediate button for document types */}
                {['pdf', 'word', 'powerpoint', 'excel', 'latex'].includes(config.apiType) && (
                  <button
                    onClick={async () => {
                      try {
                        await scansApi.remediateScan(scan.scanId, { use_ai: true });
                        alert('Remediation started! The fixed file will be available shortly.');
                      } catch (error) {
                        console.error('Remediation failed:', error);
                        const typedError = error as ApiError;
                        alert(
                          'Failed to remediate: ' +
                            (typedError.response?.data?.detail || typedError.message)
                        );
                      }
                    }}
                    className="btn-secondary flex items-center space-x-2"
                  >
                    <Wrench className="w-4 h-4" />
                    <span>Remediate</span>
                  </button>
                )}
                {/* Download remediated file button - with format options for LaTeX */}
                {['pdf', 'word', 'powerpoint', 'excel', 'latex'].includes(config.apiType) && (
                  <div className="relative group">
                    <button
                      disabled={scan.isRemediating}
                      onClick={async () => {
                        if (scan.isRemediating) return;
                        try {
                          trackEvent('dash-download-fixed', { scan_type: scanType });
                          // For LaTeX, default to tex format
                          const format = config.apiType === 'latex' ? 'tex' : undefined;
                          const blob = await scansApi.downloadRemediated(scan.scanId, format);
                          const url = window.URL.createObjectURL(blob);
                          const a = document.createElement('a');
                          a.href = url;
                          const ext = config.apiType === 'latex' ? '.tex' : '';
                          a.download = `remediated_${scan.filename}${ext ? '' : ''}`;
                          document.body.appendChild(a);
                          a.click();
                          window.URL.revokeObjectURL(url);
                          document.body.removeChild(a);
                        } catch (error) {
                          console.error('Download remediated file failed:', error);
                          const typedError = error as ApiError;
                          if (typedError.response?.status === 404) {
                            alert('Remediated file not found. Please run remediation first.');
                          } else {
                            alert(
                              'Failed to download remediated file: ' +
                                (typedError.response?.data?.detail || typedError.message)
                            );
                          }
                        }
                      }}
                      className={`btn-secondary flex items-center space-x-2 ${scan.isRemediating ? 'opacity-50 cursor-not-allowed' : ''}`}
                    >
                      {scan.isRemediating ? (
                        <>
                          <Loader2 className="w-4 h-4 animate-spin" />
                          <span>Remediating...</span>
                        </>
                      ) : (
                        <>
                          <Download className="w-4 h-4" />
                          <span>Download Remediated</span>
                        </>
                      )}
                    </button>
                    {/* Format dropdown for LaTeX */}
                    {config.apiType === 'latex' && (
                      <div className="absolute right-0 mt-1 w-40 bg-[var(--surface-primary)] border border-[var(--border-primary)] rounded-lg shadow-lg opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all z-10">
                        <div className="py-1">
                          {[
                            { format: 'tex', label: 'LaTeX (.tex)', icon: '📄' },
                            { format: 'pdf', label: 'PDF', icon: '📕' },
                            { format: 'html', label: 'HTML', icon: '🌐' },
                          ].map((opt) => (
                            <button
                              key={opt.format}
                              onClick={async (e) => {
                                e.stopPropagation();
                                try {
                                  const blob = await scansApi.downloadRemediated(scan.scanId, opt.format);
                                  const url = window.URL.createObjectURL(blob);
                                  const a = document.createElement('a');
                                  a.href = url;
                                  a.download = `remediated_${scan.filename.replace(/\.[^.]+$/, '')}.${opt.format}`;
                                  document.body.appendChild(a);
                                  a.click();
                                  window.URL.revokeObjectURL(url);
                                  document.body.removeChild(a);
                                } catch (error) {
                                  console.error(`Download ${opt.format} failed:`, error);
                                  alert(`Failed to download ${opt.label}. The format may not have been generated.`);
                                }
                              }}
                              className="w-full px-3 py-2 text-left text-sm hover:bg-[var(--surface-tertiary)] flex items-center gap-2"
                            >
                              <span>{opt.icon}</span>
                              <span>{opt.label}</span>
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
