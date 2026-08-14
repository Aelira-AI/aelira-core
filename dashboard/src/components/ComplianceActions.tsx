import React, { useState, useEffect } from 'react';
import { Award, FileText, Download, Loader, AlertCircle, Info } from 'lucide-react';
import { scansApi } from '../api/scans';
import { trackEvent } from '../utils/analytics';

// ============================================================================
// Types
// ============================================================================

interface ComplianceActionsProps {
  departmentId: string;
  complianceScore: number;
}

type CertificateLevel = 'platinum' | 'gold' | 'silver' | 'bronze';

interface CertificateEligibility {
  eligible: boolean;
  level?: CertificateLevel;
  score?: number;
  reason?: string;
}

interface LevelStyles {
  bg: string;
  text: string;
  icon: string;
  badge: string;
}

// ============================================================================
// Component
// ============================================================================

/**
 * ComplianceActions Component
 * Provides download buttons for compliance reports and certificates
 */
export function ComplianceActions({
  departmentId,
  complianceScore,
}: ComplianceActionsProps): React.ReactElement {
  const [certificateEligibility, setCertificateEligibility] =
    useState<CertificateEligibility | null>(null);
  const [loadingEligibility, setLoadingEligibility] = useState<boolean>(true);
  const [downloadingReport, setDownloadingReport] = useState<boolean>(false);
  const [downloadingCertificate, setDownloadingCertificate] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Check certificate eligibility on mount
  useEffect(() => {
    const checkEligibility = async (): Promise<void> => {
      try {
        setLoadingEligibility(true);
        const eligibility = await scansApi.checkCertificateEligibility(departmentId);
        setCertificateEligibility(eligibility);
      } catch (err) {
        console.warn('Could not check certificate eligibility:', err);
        // Default to showing based on passed score
        if (complianceScore >= 70) {
          const level: CertificateLevel =
            complianceScore >= 95
              ? 'platinum'
              : complianceScore >= 90
                ? 'gold'
                : complianceScore >= 80
                  ? 'silver'
                  : 'bronze';
          setCertificateEligibility({
            eligible: true,
            level,
            score: complianceScore,
          });
        } else {
          setCertificateEligibility({
            eligible: false,
            score: complianceScore,
            reason: 'Compliance score must be at least 70% to earn a certificate',
          });
        }
      } finally {
        setLoadingEligibility(false);
      }
    };

    if (departmentId) {
      checkEligibility();
    }
  }, [departmentId, complianceScore]);

  // Download compliance report
  const handleDownloadReport = async (): Promise<void> => {
    try {
      setDownloadingReport(true);
      setError(null);

      const blob = await scansApi.generateComplianceReport(departmentId, {
        include_ai_recommendations: true,
        include_trends: true,
        include_issues: true,
      });

      // Create download link
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `compliance-report-${departmentId}-${new Date().toISOString().split('T')[0]}.pdf`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Failed to download report:', err);
      setError('Failed to generate compliance report. Please try again.');
    } finally {
      setDownloadingReport(false);
    }
  };

  // Download compliance certificate
  const handleDownloadCertificate = async (): Promise<void> => {
    trackEvent('dash-certificate-download', { level: certificateEligibility?.level ?? 'unknown' });
    try {
      setDownloadingCertificate(true);
      setError(null);

      const blob = await scansApi.generateCertificate(departmentId);

      // Create download link
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `compliance-certificate-${departmentId}-${new Date().toISOString().split('T')[0]}.pdf`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Failed to download certificate:', err);
      setError('Failed to generate certificate. Please try again.');
    } finally {
      setDownloadingCertificate(false);
    }
  };

  // Certificate level styling
  const getLevelStyles = (level: CertificateLevel | null | undefined): LevelStyles => {
    switch (level?.toLowerCase()) {
      case 'platinum':
        return {
          bg: 'bg-[var(--interactive-primary-bg)]',
          text: 'text-white',
          icon: 'text-white/70',
          badge: 'PLATINUM',
        };
      case 'gold':
        return {
          bg: 'bg-[var(--accent-gold)]',
          text: 'text-[var(--content-secondary)]',
          icon: 'text-[var(--content-secondary)]',
          badge: 'GOLD',
        };
      case 'silver':
        return {
          bg: 'bg-[var(--content-tertiary)]',
          text: 'text-[var(--content-secondary)]',
          icon: 'text-[var(--content-secondary)]',
          badge: 'SILVER',
        };
      case 'bronze':
        return {
          bg: 'bg-[var(--accent-terracotta)]',
          text: 'text-white',
          icon: 'text-white/70',
          badge: 'BRONZE',
        };
      default:
        return {
          bg: 'bg-surface-tertiary',
          text: 'text-secondary',
          icon: 'text-tertiary',
          badge: '--',
        };
    }
  };

  const levelStyles = certificateEligibility?.level
    ? getLevelStyles(certificateEligibility.level)
    : getLevelStyles(null);

  return (
    <div className="card">
      <h2 className="text-xl font-semibold text-primary mb-4 flex items-center gap-2">
        <FileText className="w-5 h-5" />
        Compliance Documents
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

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Compliance Report Card */}
        <div className="p-4 rounded-lg border border-primary bg-surface-secondary">
          <div className="flex items-start justify-between mb-3">
            <div>
              <h3 className="font-medium text-primary">Compliance Report</h3>
              <p className="text-sm text-secondary mt-1">
                Detailed PDF report with AI recommendations, trend analysis, and issue breakdown.
              </p>
            </div>
            <FileText className="w-8 h-8 text-accent flex-shrink-0" />
          </div>

          <button
            onClick={handleDownloadReport}
            disabled={downloadingReport}
            className="btn-primary flex items-center justify-center gap-2"
          >
            {downloadingReport ? (
              <>
                <Loader className="w-4 h-4 animate-spin" />
                Generating...
              </>
            ) : (
              <>
                <Download className="w-4 h-4" />
                Download Report
              </>
            )}
          </button>
        </div>

        {/* Certificate Card */}
        <div className="p-4 rounded-lg border border-primary bg-surface-secondary">
          <div className="flex items-start justify-between mb-3">
            <div>
              <h3 className="font-medium text-primary">Compliance Certificate</h3>
              {loadingEligibility ? (
                <p className="text-sm text-secondary mt-1 flex items-center gap-1">
                  <Loader className="w-3 h-3 animate-spin" />
                  Checking eligibility...
                </p>
              ) : certificateEligibility?.eligible ? (
                <div className="mt-1">
                  <span
                    className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-bold ${levelStyles.bg} ${levelStyles.text}`}
                  >
                    <Award className={`w-3 h-3 mr-1 ${levelStyles.icon}`} />
                    {levelStyles.badge} LEVEL
                  </span>
                  <p className="text-sm text-secondary mt-1">
                    Score: {Math.round(certificateEligibility.score || complianceScore)}%
                  </p>
                </div>
              ) : (
                <p className="text-sm text-secondary mt-1 flex items-center gap-1">
                  <Info className="w-3 h-3" />
                  {certificateEligibility?.reason || 'Score must be 70%+ for certificate'}
                </p>
              )}
            </div>
            <Award
              className={`w-8 h-8 flex-shrink-0 ${certificateEligibility?.eligible ? 'text-accent' : 'text-tertiary'}`}
            />
          </div>

          {certificateEligibility?.eligible ? (
            <button
              onClick={handleDownloadCertificate}
              disabled={downloadingCertificate || !certificateEligibility?.eligible}
              className="btn-primary flex items-center justify-center gap-2"
            >
              {downloadingCertificate ? (
                <>
                  <Loader className="w-4 h-4 animate-spin" />
                  Generating...
                </>
              ) : (
                <>
                  <Award className="w-4 h-4" />
                  Download Certificate
                </>
              )}
            </button>
          ) : (
            <button
              disabled
              className="btn-secondary flex items-center justify-center gap-2 opacity-50 cursor-not-allowed"
            >
              <Award className="w-4 h-4" />
              Certificate Unavailable
            </button>
          )}
        </div>
      </div>

      {/* Certificate Level Guide */}
      {certificateEligibility && !certificateEligibility.eligible && (
        <div className="mt-4 p-3 rounded-lg bg-surface-tertiary border border-secondary">
          <h4 className="text-sm font-medium text-primary mb-2">Certificate Levels</h4>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
            <div className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-full bg-[var(--accent-terracotta)]"></span>
              <span className="text-secondary">Bronze: 70-79%</span>
            </div>
            <div className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-full bg-[var(--content-tertiary)]"></span>
              <span className="text-secondary">Silver: 80-89%</span>
            </div>
            <div className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-full bg-[var(--accent-gold)]"></span>
              <span className="text-secondary">Gold: 90-94%</span>
            </div>
            <div className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-full bg-[var(--interactive-primary-bg)]"></span>
              <span className="text-secondary">Platinum: 95%+</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
