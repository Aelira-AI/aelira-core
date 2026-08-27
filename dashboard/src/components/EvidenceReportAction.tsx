import React, { useState } from 'react';
import { AlertCircle, Download, FileText, Loader } from 'lucide-react';
import { scansApi } from '../api/scans';
import { trackEvent } from '../utils/analytics';

interface EvidenceReportActionProps {
  departmentId: string;
}

/** Download the bounded record of Aelira's checks for a department. */
export function EvidenceReportAction({
  departmentId,
}: EvidenceReportActionProps): React.ReactElement {
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDownload = async (): Promise<void> => {
    trackEvent('dash-evidence-report-download');

    try {
      setDownloading(true);
      setError(null);

      const blob = await scansApi.generateEvidenceReport(departmentId);
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `accessibility-evidence-report-${departmentId}-${new Date().toISOString().split('T')[0]}.pdf`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Failed to download accessibility evidence report:', err);
      setError('Failed to generate the accessibility evidence report. Please try again.');
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="card">
      <h2 className="text-xl font-semibold text-primary mb-4 flex items-center gap-2">
        <FileText className="w-5 h-5" />
        Accessibility Evidence Report
      </h2>

      {error && (
        <div
          className="mb-4 p-3 rounded-lg flex items-center gap-2"
          style={{
            backgroundColor: 'var(--surface-error-subtle)',
            color: 'var(--content-error)',
          }}
        >
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          <span className="text-sm">{error}</span>
        </div>
      )}

      <div className="p-4 rounded-lg border border-primary bg-surface-secondary">
        <div className="flex items-start justify-between gap-4 mb-3">
          <p className="text-sm text-secondary">
            Download a PDF record of Aelira's scanned-content findings, methods, and
            limitations. It does not determine conformance with an accessibility standard
            or legal requirement.
          </p>
          <FileText className="w-8 h-8 text-accent flex-shrink-0" />
        </div>

        <button
          onClick={handleDownload}
          disabled={downloading}
          className="btn-primary flex items-center justify-center gap-2"
        >
          {downloading ? (
            <>
              <Loader className="w-4 h-4 animate-spin" />
              Generating...
            </>
          ) : (
            <>
              <Download className="w-4 h-4" />
              Download Evidence Report
            </>
          )}
        </button>
      </div>
    </div>
  );
}
