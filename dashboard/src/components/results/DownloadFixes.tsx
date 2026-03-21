import React from 'react';
import { Download, FileText, FileCode, LucideIcon } from 'lucide-react';

// ============================================================================
// Types
// ============================================================================

interface DownloadItem {
  label: string;
  icon: LucideIcon;
  type: string;
}

interface DownloadFixesProps {
  scanType: string;
}

// ============================================================================
// Constants
// ============================================================================

const downloads: Record<string, DownloadItem[]> = {
  pdf: [
    { label: 'Remediated PDF', icon: FileText, type: 'pdf' },
    { label: 'Alt Text CSV', icon: FileCode, type: 'csv' },
  ],
  powerpoint: [
    { label: 'Fixed PowerPoint', icon: FileText, type: 'pptx' },
    { label: 'Alt Text Report', icon: FileCode, type: 'csv' },
  ],
  latex: [
    { label: 'Accessible LaTeX', icon: FileText, type: 'tex' },
    { label: 'MathML Output', icon: FileCode, type: 'xml' },
  ],
  image: [{ label: 'Alt Text JSON', icon: FileCode, type: 'json' }],
  video: [
    { label: 'WebVTT Captions', icon: FileCode, type: 'vtt' },
    { label: 'SRT Captions', icon: FileCode, type: 'srt' },
  ],
};

// ============================================================================
// Component
// ============================================================================

export function DownloadFixes({ scanType }: DownloadFixesProps): React.ReactElement {
  const availableDownloads = downloads[scanType] || [];

  return (
    <div className="card">
      <h3 className="text-lg font-semibold text-[var(--content-primary)] mb-4">Download Fixed Files</h3>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {availableDownloads.map((download) => {
          const Icon = download.icon;
          return (
            <button
              key={download.type}
              className="flex items-center justify-between p-4 border border-[var(--border-primary)] rounded-lg hover:border-primary-500 hover:bg-primary-50 transition-colors"
            >
              <div className="flex items-center space-x-3">
                <Icon className="w-5 h-5 text-[var(--content-secondary)]" />
                <span className="font-medium text-[var(--content-primary)]">{download.label}</span>
              </div>
              <Download className="w-5 h-5 text-[var(--content-tertiary)]" />
            </button>
          );
        })}
      </div>
    </div>
  );
}
