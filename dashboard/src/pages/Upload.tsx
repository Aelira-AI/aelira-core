import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { ScanTypeSelector } from '../components/upload/ScanTypeSelector';
import { FileUploader } from '../components/upload/FileUploader';
import { WebsiteScanner } from '../components/upload/WebsiteScanner';
import { ArrowLeft, Layers, Sparkles } from 'lucide-react';
import { useFeatureAccess } from '../hooks/useFeatureAccess';
import { trackEvent } from '../components/Analytics';

type ScanType = 'pdf' | 'word' | 'excel' | 'powerpoint' | 'latex' | 'image' | 'video' | 'website' | 'code' | null;

interface CompletedScanResult {
  id: string;
  filename: string;
  scanId: string;
  result: unknown;
}

interface ScanResult {
  scanId: string;
}

export function Upload(): React.ReactElement {
  const [scanType, setScanType] = useState<ScanType>(null);
  const [uploadedFiles, setUploadedFiles] = useState<CompletedScanResult[]>([]);
  const navigate = useNavigate();
  const { showBulkUpload } = useFeatureAccess();

  const handleUploadComplete = (result: CompletedScanResult): void => {
    setUploadedFiles(prev => [...prev, result]);
  };

  const handleScanComplete = (result: ScanResult): void => {
    // Navigate to scan detail page with scan ID
    navigate(`/scan/${result.scanId}`);
  };

  const handleReset = (): void => {
    setScanType(null);
    setUploadedFiles([]);
  };

  return (
    <div className="p-8">
      <div className="max-w-6xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-3xl font-bold text-primary">New Scan</h1>
          {scanType && (
            <button
              onClick={handleReset}
              className="flex items-center space-x-2 text-secondary hover:text-primary transition-colors"
            >
              <ArrowLeft className="w-4 h-4" />
              <span>Change Type</span>
            </button>
          )}
        </div>

        {!scanType ? (
          <div>
            <p className="text-secondary mb-6">
              Select what you want to scan for accessibility issues:
            </p>
            <ScanTypeSelector selected={scanType} onSelect={(type) => {
              trackEvent('dash-scan-type-selected', { scan_type: type, is_locked: false });
              setScanType(type as ScanType);
            }} />

            {/* Bulk Upload Link or Upgrade Prompt */}
            <div className="mt-8 pt-6 border-t" style={{ borderColor: 'var(--border-subtle)' }}>
              {showBulkUpload ? (
                <Link
                  to="/bulk-upload"
                  className="flex items-center gap-3 p-4 rounded-lg transition-colors hover:opacity-90"
                  style={{ backgroundColor: 'var(--surface-secondary)' }}
                >
                  <div
                    className="p-2 rounded-lg"
                    style={{ backgroundColor: 'var(--surface-accent-subtle)' }}
                  >
                    <Layers className="w-5 h-5" style={{ color: 'var(--content-accent)' }} />
                  </div>
                  <div className="flex-1">
                    <p className="font-medium text-primary">Need to scan multiple files?</p>
                    <p className="text-sm text-secondary">Use Bulk Upload to process entire folders at once.</p>
                  </div>
                </Link>
              ) : (
                <div
                  className="flex items-center gap-3 p-4 rounded-lg"
                  style={{ backgroundColor: 'var(--surface-secondary)' }}
                >
                  <div
                    className="p-2 rounded-lg"
                    style={{ backgroundColor: 'var(--surface-accent-subtle)' }}
                  >
                    <Sparkles className="w-5 h-5" style={{ color: 'var(--content-accent)' }} />
                  </div>
                  <div className="flex-1">
                    <p className="font-medium text-primary">Need to scan multiple files?</p>
                    <p className="text-sm text-secondary">Upgrade to a Department plan for bulk upload capabilities.</p>
                  </div>
                  <a
                    href="/pricing"
                    className="btn-secondary text-sm"
                  >
                    Upgrade
                  </a>
                </div>
              )}
            </div>
          </div>
        ) : scanType === 'website' ? (
          <div>
            <div
              className="rounded-lg p-4 mb-6"
              style={{
                backgroundColor: 'var(--surface-info-subtle)',
                borderColor: 'var(--content-info)',
                border: '1px solid',
                color: 'var(--content-info)'
              }}
            >
              <p className="text-sm">
                <strong>Note:</strong> Enter a website URL to scan for WCAG 2.1 AA accessibility compliance.
                The scan includes AI-powered analysis and code fixes.
              </p>
            </div>

            <WebsiteScanner onScanComplete={handleScanComplete} />
          </div>
        ) : (
          <div>
            <div
              className="rounded-lg p-4 mb-6"
              style={{
                backgroundColor: 'var(--surface-info-subtle)',
                borderColor: 'var(--content-info)',
                border: '1px solid',
                color: 'var(--content-info)'
              }}
            >
              <p className="text-sm">
                <strong>Note:</strong> You're uploading files for{' '}
                <span className="font-semibold">{scanType}</span> scanning.
                Files will be processed and results will appear in your scan history.
              </p>
            </div>

            <FileUploader
              scanType={scanType}
              onUploadComplete={handleUploadComplete}
            />

            {uploadedFiles.length > 0 && (
              <div className="mt-6 card">
                <h3 className="text-lg font-semibold text-primary mb-4">
                  Upload Complete ({uploadedFiles.length} files)
                </h3>
                <button
                  onClick={() => navigate('/history')}
                  className="btn-primary"
                >
                  View Scan Results
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
