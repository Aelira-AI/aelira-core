import React, { useState, useEffect, useRef } from 'react';
import { Download, ChevronDown, Loader } from 'lucide-react';
import { scansApi, AvailableFormat } from '../../api/scans';
import { useToast } from '../../context/ToastContext';

// ============================================================================
// Types
// ============================================================================

interface FormatDownloadButtonProps {
  scanId: string;
  scanType: string;
  filename?: string;
}

// ============================================================================
// Constants
// ============================================================================

const FORMAT_LABELS: Record<string, string> = {
  tex: 'LaTeX Source (.tex)',
  pdf: 'PDF Document (.pdf)',
  html: 'HTML Page (.html)',
  zip: 'Accessible Package (.zip)',
  vtt: 'Captions (.vtt)',
  srt: 'SRT Captions (.srt)',
  transcript: 'Transcript (.txt)',
  audio_descriptions: 'Audio Descriptions (.txt)',
};

const MULTI_FORMAT_SCAN_TYPES = ['LATEX', 'MULTIMEDIA', 'latex', 'multimedia'];

// ============================================================================
// Helpers
// ============================================================================

function getFormatLabel(format: string): string {
  return FORMAT_LABELS[format] || format.toUpperCase();
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function isMultiFormatScanType(scanType: string): boolean {
  return MULTI_FORMAT_SCAN_TYPES.includes(scanType);
}

// ============================================================================
// Component
// ============================================================================

export function FormatDownloadButton({
  scanId,
  scanType,
  filename = 'document',
}: FormatDownloadButtonProps): React.ReactElement {
  const toast = useToast();
  const dropdownRef = useRef<HTMLDivElement>(null);

  const [formats, setFormats] = useState<AvailableFormat[]>([]);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);

  // Fetch available formats for multi-format scan types
  useEffect(() => {
    if (!isMultiFormatScanType(scanType)) {
      return;
    }

    const fetchFormats = async (): Promise<void> => {
      setLoading(true);
      try {
        const response = await scansApi.getRemediatedFormats(scanId);
        setFormats(response.available_formats || []);
      } catch (err) {
        console.error('Failed to fetch available formats:', err);
        // Don't show error toast - formats will be empty and we'll fall back to simple download
      } finally {
        setLoading(false);
      }
    };

    fetchFormats();
  }, [scanId, scanType]);

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent): void {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setShowDropdown(false);
      }
    }

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleDownload = async (format?: string): Promise<void> => {
    setDownloading(true);
    setShowDropdown(false);

    try {
      const blob = await scansApi.downloadRemediated(scanId, format);
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;

      // Build filename with format extension
      const extension = format || 'file';
      const baseFilename = filename.replace(/\.[^/.]+$/, ''); // Remove existing extension
      link.download = `remediated-${baseFilename}.${extension}`;

      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);

      toast.success('Remediated file downloaded', 'Download Complete');
    } catch (err) {
      console.error('Failed to download remediated file:', err);
      toast.error('No remediated file available. Run remediation first.', 'Download Failed');
    } finally {
      setDownloading(false);
    }
  };

  // Simple download button for non-multi-format types
  if (!isMultiFormatScanType(scanType)) {
    return (
      <button
        onClick={() => handleDownload()}
        disabled={downloading}
        className="btn-secondary flex items-center gap-2 disabled:opacity-50"
        aria-label="Download remediated file"
      >
        {downloading ? (
          <Loader className="w-4 h-4 animate-spin" aria-hidden="true" />
        ) : (
          <Download className="w-4 h-4" aria-hidden="true" />
        )}
        Download Fixed
      </button>
    );
  }

  // Multi-format dropdown button
  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setShowDropdown(!showDropdown)}
        disabled={loading || downloading}
        className="btn-secondary flex items-center gap-2 disabled:opacity-50"
        aria-haspopup="listbox"
        aria-expanded={showDropdown}
        aria-label="Download remediated file, select format"
      >
        {loading || downloading ? (
          <Loader className="w-4 h-4 animate-spin" aria-hidden="true" />
        ) : (
          <Download className="w-4 h-4" aria-hidden="true" />
        )}
        {loading ? 'Loading...' : 'Download Fixed'}
        <ChevronDown className="w-4 h-4" aria-hidden="true" />
      </button>

      {showDropdown && (
        <div
          className="absolute right-0 mt-2 w-64 bg-[var(--surface-primary)] rounded-lg shadow-lg border border-[var(--border-primary)] z-20"
          role="listbox"
          aria-label="Available download formats"
        >
          {formats.length > 0 ? (
            <div className="py-1">
              {formats.map((fmt) => (
                <button
                  key={fmt.format}
                  onClick={() => handleDownload(fmt.format)}
                  className="w-full px-4 py-2 text-left hover:bg-[var(--surface-secondary)] flex justify-between items-center text-[var(--content-primary)]"
                  role="option"
                  aria-label={`Download ${getFormatLabel(fmt.format)}, ${formatFileSize(fmt.size_bytes)}`}
                >
                  <span className="text-sm">{getFormatLabel(fmt.format)}</span>
                  <span className="text-xs text-[var(--content-tertiary)]">
                    {formatFileSize(fmt.size_bytes)}
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <div className="px-4 py-3 text-sm text-[var(--content-secondary)]">
              No remediated files available.
              <br />
              Run remediation first.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default FormatDownloadButton;
