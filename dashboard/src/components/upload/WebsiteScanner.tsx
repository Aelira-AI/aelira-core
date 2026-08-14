import React, { useState, useCallback, ChangeEvent } from 'react';
import { Globe, AlertCircle, CheckCircle, Loader } from 'lucide-react';
import { apiClient } from '../../api/client';
import { usePolling } from '../../hooks/usePolling';
import { trackEvent } from '../../utils/analytics';

// ============================================================================
// Types
// ============================================================================

interface ScanOptions {
  mode: string;
  scan_images: boolean;
  scan_multimedia: boolean;
  scan_math: boolean;
  validate_alt_text: boolean;
  max_depth: number;
  max_pages: number;
  generate_code_fixes: boolean;
  capture_screenshots: boolean;
}

interface ScanResult {
  url: string;
  scanId: string;
  score: number;
}

interface WebsiteScannerProps {
  onScanComplete: (result: ScanResult) => void;
}

interface ScanResponse {
  scan_id: string;
  root_url: string;
  overall_compliance_score: number;
}

interface ProgressResponse {
  progress: number;
  progress_message: string;
  status: string;
}

// ============================================================================
// Component
// ============================================================================

export function WebsiteScanner({ onScanComplete }: WebsiteScannerProps): React.ReactElement {
  const [url, setUrl] = useState<string>('');
  const [options, setOptions] = useState<ScanOptions>({
    mode: 'deep',
    scan_images: false,
    scan_multimedia: false,
    scan_math: false,
    validate_alt_text: false,
    max_depth: 1,
    max_pages: 10,
    generate_code_fixes: true,
    capture_screenshots: true,
  });
  const [isScanning, setIsScanning] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<number>(0);
  const [progressMessage, setProgressMessage] = useState<string>('');
  const [currentScanId, setCurrentScanId] = useState<string | null>(null);

  // Poll for progress updates
  const pollProgress = useCallback(async (): Promise<void> => {
    if (!currentScanId) return;
    try {
      const response = await apiClient.get<ProgressResponse>(
        `/education/scans/${currentScanId}/progress`
      );
      const data = response.data;

      setProgress(data.progress || 0);
      setProgressMessage(data.progress_message || 'Processing...');

      if (data.status === 'completed' || data.status === 'failed') {
        setIsScanning(false);
      }
    } catch (err) {
      console.error('Error polling progress:', err);
    }
  }, [currentScanId]);

  usePolling(pollProgress, 2000, isScanning && !!currentScanId);

  const handleScan = async (): Promise<void> => {
    if (!url) {
      setError('Please enter a URL');
      return;
    }

    try {
      new URL(url);
    } catch {
      setError('Please enter a valid URL (e.g., https://example.com)');
      return;
    }

    setIsScanning(true);
    setError(null);
    setProgress(0);
    setProgressMessage('Starting scan...');
    setCurrentScanId(null);

    trackEvent('dash-website-scan-started', { crawl_depth: options.max_depth, max_pages: options.max_pages });

    try {
      const requestBody = {
        url: url,
        mode: options.mode,
        scan_images: options.scan_images,
        scan_multimedia: options.scan_multimedia,
        scan_math: options.scan_math,
        validate_alt_text: options.validate_alt_text,
        max_depth: options.max_depth,
        max_pages: options.max_pages,
        generate_code_fixes: options.generate_code_fixes,
        capture_screenshots: options.capture_screenshots,
      };

      const response = await apiClient.post<ScanResponse>(`/education/web/scan`, requestBody, {
        timeout: 300000,
      });
      const result = response.data;

      setCurrentScanId(result.scan_id);

      onScanComplete({
        url: result.root_url,
        scanId: result.scan_id,
        score: result.overall_compliance_score,
      });
    } catch (err) {
      const error = err as { response?: { data?: { detail?: string } }; message?: string };
      setError('Failed to scan website: ' + (error.response?.data?.detail || error.message));
      setIsScanning(false);
      setCurrentScanId(null);
    }
  };

  const handleCheckboxChange =
    (key: keyof ScanOptions) =>
    (e: ChangeEvent<HTMLInputElement>): void => {
      setOptions({ ...options, [key]: e.target.checked });
    };

  const handleSelectChange =
    (key: keyof ScanOptions) =>
    (e: ChangeEvent<HTMLSelectElement>): void => {
      setOptions({ ...options, [key]: parseInt(e.target.value) });
    };

  return (
    <div className="space-y-6">
      {/* URL Input */}
      <div>
        <label className="block text-sm font-medium text-primary mb-2">Website URL</label>
        <div className="flex space-x-2">
          <div className="flex-1 relative">
            <Globe className="absolute left-3 top-1/2 transform -translate-y-1/2 text-secondary w-5 h-5" />
            <input
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com"
              className="input pl-10 pr-4 py-3"
              disabled={isScanning}
            />
          </div>
          <button
            onClick={handleScan}
            disabled={isScanning || !url}
            className="px-6 py-3 bg-[var(--interactive-primary-bg)] text-white rounded-lg font-medium hover:bg-[var(--interactive-primary-hover)] disabled:opacity-50 disabled:cursor-not-allowed flex items-center space-x-2"
          >
            {isScanning ? (
              <>
                <Loader className="w-5 h-5 animate-spin" />
                <span>Scanning...</span>
              </>
            ) : (
              <span>Scan Website</span>
            )}
          </button>
        </div>
        {error && (
          <div className="mt-2 flex items-center space-x-2 text-[var(--feature-danger-content)] text-sm">
            <AlertCircle className="w-4 h-4" />
            <span>{error}</span>
          </div>
        )}

        {/* Progress Bar */}
        {isScanning && (
          <div className="mt-4 p-4 bg-[var(--surface-accent-subtle)] border border-[var(--border-accent)] rounded-lg">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium text-[var(--content-accent)]">
                Scanning...
              </span>
              <span className="text-sm font-semibold text-[var(--content-accent)]">
                {progress}%
              </span>
            </div>
            <div className="w-full bg-[var(--surface-tertiary)] rounded-full h-2.5 mb-2">
              <div
                className="bg-[var(--interactive-primary-bg)] h-2.5 rounded-full transition-all duration-300"
                style={{ width: `${progress}%` }}
              ></div>
            </div>
            {progressMessage && (
              <div className="text-xs text-[var(--content-accent)]">{progressMessage}</div>
            )}
          </div>
        )}
      </div>

      {/* Scan Options */}
      <div className="card">
        <h3 className="font-medium text-primary mb-4">Scan Options</h3>

        <div className="mb-6 p-4 bg-[var(--surface-accent-subtle)] border border-[var(--border-accent)] rounded-lg">
          <div className="flex items-start space-x-2">
            <CheckCircle className="w-5 h-5 text-[var(--content-accent)] flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium text-[var(--content-accent)]">
                Comprehensive WCAG 2.1 AA Scanning
              </p>
              <p className="text-xs text-[var(--content-accent)] mt-1">
                All scans use Playwright + axe-core for comprehensive WCAG 2.1 AA accessibility
                testing. Expect 30-60 seconds per page.
              </p>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
          <label className="flex items-center space-x-3 cursor-pointer">
            <input
              type="checkbox"
              checked={options.scan_images}
              onChange={handleCheckboxChange('scan_images')}
              className="w-4 h-4 text-[var(--content-accent)] rounded focus:ring-[var(--interactive-primary-bg)]"
              disabled={isScanning}
            />
            <span className="text-sm text-secondary">Scan Images (AI alt text)</span>
          </label>

          <label className="flex items-center space-x-3 cursor-pointer">
            <input
              type="checkbox"
              checked={options.scan_multimedia}
              onChange={handleCheckboxChange('scan_multimedia')}
              className="w-4 h-4 text-[var(--content-accent)] rounded focus:ring-[var(--interactive-primary-bg)]"
              disabled={isScanning}
            />
            <span className="text-sm text-secondary">Check Multimedia (Captions)</span>
          </label>

          <label className="flex items-center space-x-3 cursor-pointer">
            <input
              type="checkbox"
              checked={options.scan_math}
              onChange={handleCheckboxChange('scan_math')}
              className="w-4 h-4 text-[var(--content-accent)] rounded focus:ring-[var(--interactive-primary-bg)]"
              disabled={isScanning}
            />
            <span className="text-sm text-secondary">Scan Math/LaTeX</span>
          </label>

          <label className="flex items-center space-x-3 cursor-pointer">
            <input
              type="checkbox"
              checked={options.validate_alt_text}
              onChange={handleCheckboxChange('validate_alt_text')}
              className="w-4 h-4 text-[var(--content-accent)] rounded focus:ring-[var(--interactive-primary-bg)]"
              disabled={isScanning}
            />
            <span className="text-sm text-secondary">Validate Alt Text Accuracy</span>
          </label>

          <label className="flex items-center space-x-3 cursor-pointer">
            <input
              type="checkbox"
              checked={options.generate_code_fixes}
              onChange={handleCheckboxChange('generate_code_fixes')}
              className="w-4 h-4 text-[var(--content-accent)] rounded focus:ring-[var(--interactive-primary-bg)]"
              disabled={isScanning}
            />
            <span className="text-sm text-secondary">Generate AI Code Fixes</span>
          </label>

          <label className="flex items-center space-x-3 cursor-pointer">
            <input
              type="checkbox"
              checked={options.capture_screenshots}
              onChange={handleCheckboxChange('capture_screenshots')}
              className="w-4 h-4 text-[var(--content-accent)] rounded focus:ring-[var(--interactive-primary-bg)]"
              disabled={isScanning}
            />
            <span className="text-sm text-secondary">Capture Element Screenshots</span>
          </label>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-primary mb-1">Crawl Depth</label>
            <select
              value={options.max_depth}
              onChange={handleSelectChange('max_depth')}
              className="input"
              disabled={isScanning}
            >
              <option value="1">1 page (current page only)</option>
              <option value="2">2 levels (+ linked pages)</option>
              <option value="3">3 levels (deep scan)</option>
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-primary mb-1">Max Pages</label>
            <select
              value={options.max_pages}
              onChange={handleSelectChange('max_pages')}
              className="input"
              disabled={isScanning}
            >
              <option value="5">5 pages</option>
              <option value="10">10 pages</option>
              <option value="20">20 pages</option>
              <option value="50">50 pages</option>
              <option value="100">100 pages</option>
              <option value="9999">All pages (unlimited)</option>
            </select>
          </div>
        </div>
      </div>

      {/* Info Banner */}
      <div className="flex items-start space-x-2 p-4 bg-[var(--surface-accent-subtle)] border border-[var(--border-accent)] rounded-lg">
        <AlertCircle className="w-5 h-5 text-[var(--content-accent)] flex-shrink-0 mt-0.5" />
        <div className="text-sm text-[var(--content-accent)]">
          <p className="font-medium">Comprehensive WCAG 2.1 AA Scanning</p>
          <p className="text-[var(--content-accent)] mt-1">
            Our AI-powered scanner checks for accessibility issues, generates alt text, analyzes
            math content, and provides code fixes using Qwen Coder AI.
          </p>
        </div>
      </div>
    </div>
  );
}
