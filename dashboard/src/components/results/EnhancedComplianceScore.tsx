import React from 'react';
import { CheckCircle, XCircle, Eye, Keyboard } from 'lucide-react';

// ============================================================================
// Types
// ============================================================================

interface EnhancedComplianceScoreProps {
  overallScore?: number;
  focusOrderScore?: number | null;
  cvdScore?: number | null;
  wcagCompliant?: boolean;
  focusOrderCompliant?: boolean | null;
  cvdCompliant?: boolean | null;
}

interface ScoreColors {
  bg: string;
  border: string;
  text: string;
  ring: string;
}

interface ComplianceIconProps {
  compliant: boolean | null | undefined;
}

// ============================================================================
// Helper Functions
// ============================================================================

const getScoreColor = (score: number): ScoreColors => {
  if (score >= 90)
    return {
      bg: 'bg-[var(--feature-success-surface)]',
      border: 'border-[var(--feature-success-content)]',
      text: 'text-[var(--feature-success-content)]',
      ring: 'ring-[var(--feature-success-content)]',
    };
  if (score >= 70)
    return {
      bg: 'bg-[var(--feature-warning-surface)]',
      border: 'border-[var(--feature-warning-content)]',
      text: 'text-[var(--feature-warning-content)]',
      ring: 'ring-[var(--feature-warning-content)]',
    };
  return {
    bg: 'bg-[var(--feature-danger-surface)]',
    border: 'border-[var(--feature-danger-content)]',
    text: 'text-[var(--feature-danger-content)]',
    ring: 'ring-[var(--feature-danger-content)]',
  };
};

// ComplianceIcon component moved outside to avoid recreation on render
const ComplianceIcon: React.FC<ComplianceIconProps> = ({ compliant }) => {
  if (compliant === null || compliant === undefined) return null;
  return compliant ? (
    <CheckCircle className="w-5 h-5 text-[var(--feature-success-content)]" />
  ) : (
    <XCircle className="w-5 h-5 text-[var(--feature-danger-content)]" />
  );
};

// ============================================================================
// Component
// ============================================================================

/**
 * Enhanced Compliance Score Component with Feature-Type Breakdown
 *
 * Displays overall compliance score plus breakdowns for:
 * - Focus Order (WCAG 2.4.3, 2.4.7)
 * - Color Accessibility (CVD testing)
 * - General WCAG compliance
 */
export function EnhancedComplianceScore({
  overallScore = 0,
  focusOrderScore = null,
  cvdScore = null,
  wcagCompliant = false,
  focusOrderCompliant = null,
  cvdCompliant = null,
}: EnhancedComplianceScoreProps): React.ReactElement {
  const overallColors = getScoreColor(overallScore);
  const focusColors = focusOrderScore !== null ? getScoreColor(focusOrderScore) : null;
  const cvdColors = cvdScore !== null ? getScoreColor(cvdScore) : null;

  return (
    <div className="card">
      <h2 className="text-lg font-semibold text-[var(--content-primary)] mb-4">
        Compliance Score Breakdown
      </h2>

      {/* Overall Score (Large) */}
      <div className={`mb-6 p-6 rounded-lg border-2 ${overallColors.border} ${overallColors.bg}`}>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-lg font-medium text-[var(--content-primary)]">
            Overall Accessibility Score
          </h3>
          <ComplianceIcon compliant={wcagCompliant} />
        </div>
        <div className="flex items-baseline space-x-2">
          <span className={`text-5xl font-bold ${overallColors.text}`}>
            {Math.round(overallScore)}
          </span>
          <span className="text-2xl text-[var(--content-secondary)]">/100</span>
        </div>
        {wcagCompliant && (
          <p className="mt-2 text-sm text-[var(--feature-success-content)]">
            Meets WCAG 2.1 Level AA standards
          </p>
        )}
        {!wcagCompliant && overallScore > 0 && (
          <p className="mt-2 text-sm text-[var(--feature-warning-content)]">
            Does not meet WCAG 2.1 Level AA standards
          </p>
        )}
      </div>

      {/* Feature-Specific Scores */}
      {(focusOrderScore !== null || cvdScore !== null) && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Focus Order Score */}
          {focusOrderScore !== null && focusColors && (
            <div className={`p-4 rounded-lg border ${focusColors.border} ${focusColors.bg}`}>
              <div className="flex items-center space-x-2 mb-3">
                <Keyboard className={`w-5 h-5 ${focusColors.text}`} />
                <h4 className="font-semibold text-[var(--content-primary)]">Focus Order</h4>
                <ComplianceIcon compliant={focusOrderCompliant} />
              </div>
              <div className="flex items-baseline space-x-1 mb-2">
                <span className={`text-3xl font-bold ${focusColors.text}`}>
                  {Math.round(focusOrderScore)}
                </span>
                <span className="text-lg text-[var(--content-secondary)]">/100</span>
              </div>
              <p className="text-xs text-[var(--content-secondary)]">
                WCAG 2.4.3 (Focus Order) & 2.4.7 (Focus Visible)
              </p>
              {focusOrderCompliant && (
                <p className="mt-2 text-xs text-[var(--feature-success-content)]">
                  Keyboard navigation is accessible
                </p>
              )}
            </div>
          )}

          {/* CVD Accessibility Score */}
          {cvdScore !== null && cvdColors && (
            <div className={`p-4 rounded-lg border ${cvdColors.border} ${cvdColors.bg}`}>
              <div className="flex items-center space-x-2 mb-3">
                <Eye className={`w-5 h-5 ${cvdColors.text}`} />
                <h4 className="font-semibold text-[var(--content-primary)]">
                  Color Accessibility
                </h4>
                <ComplianceIcon compliant={cvdCompliant} />
              </div>
              <div className="flex items-baseline space-x-1 mb-2">
                <span className={`text-3xl font-bold ${cvdColors.text}`}>
                  {Math.round(cvdScore)}
                </span>
                <span className="text-lg text-[var(--content-secondary)]">/100</span>
              </div>
              <p className="text-xs text-[var(--content-secondary)]">
                Color blindness simulation (8% of males)
              </p>
              {cvdCompliant && (
                <p className="mt-2 text-xs text-[var(--feature-success-content)]">
                  Accessible for color-blind users
                </p>
              )}
              {!cvdCompliant && cvdScore > 0 && (
                <p className="mt-2 text-xs text-[var(--feature-warning-content)]">
                  Color combinations may fail for some CVD types
                </p>
              )}
            </div>
          )}
        </div>
      )}

      {/* WCAG Criteria Coverage */}
      {focusOrderScore !== null || cvdScore !== null ? (
        <div className="mt-4 p-3 bg-[var(--feature-info-surface)] rounded border border-[var(--border-primary)]">
          <p className="text-xs text-[var(--feature-info-content)]">
            <strong>Additional checks:</strong> This scan includes automated focus order testing
            (NerdeFocus) and color blindness simulation (RGBlind) for comprehensive accessibility
            coverage.
          </p>
        </div>
      ) : null}
    </div>
  );
}
