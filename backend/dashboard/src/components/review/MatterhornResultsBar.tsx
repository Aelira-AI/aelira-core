import React from 'react';
import { CheckCircle, XCircle, AlertTriangle } from 'lucide-react';

interface MatterhornResultsBarProps {
  total: number;
  passed: number;
  failed: number;
  compliance: string;
}

function getComplianceLevelStyle(level: string): { label: string; color: string; bg: string } {
  switch (level.toLowerCase()) {
    case 'fully_compliant':
      return {
        label: 'Fully Compliant',
        color: 'text-[var(--feature-success-content)]',
        bg: 'bg-[var(--feature-success-surface)]',
      };
    case 'largely_compliant':
      return {
        label: 'Largely Compliant',
        color: 'text-[var(--feature-info-content)]',
        bg: 'bg-[var(--feature-info-surface)]',
      };
    case 'partially_compliant':
      return {
        label: 'Partially Compliant',
        color: 'text-[var(--feature-warning-content)]',
        bg: 'bg-[var(--feature-warning-surface)]',
      };
    case 'non_compliant':
      return {
        label: 'Non-Compliant',
        color: 'text-[var(--feature-danger-content)]',
        bg: 'bg-[var(--feature-danger-surface)]',
      };
    default:
      return {
        label: level.replace(/_/g, ' '),
        color: 'text-[var(--content-tertiary)]',
        bg: 'bg-[var(--surface-tertiary)]',
      };
  }
}

export function MatterhornResultsBar({ total, passed, failed, compliance }: MatterhornResultsBarProps): React.ReactElement {
  const warnings = total - passed - failed;
  const levelStyle = getComplianceLevelStyle(compliance);

  return (
    <div
      className="flex items-center justify-between px-6 py-3"
      style={{ backgroundColor: 'var(--surface-secondary)', borderTop: '1px solid var(--border-primary)' }}
      role="status"
      aria-label="Matterhorn Protocol validation results"
    >
      <div className="flex items-center gap-6">
        <span className="text-sm font-medium text-[var(--content-secondary)]">Matterhorn Protocol</span>

        <div className="flex items-center gap-1.5">
          <CheckCircle className="w-4 h-4 text-[var(--feature-success-content)]" aria-hidden="true" />
          <span className="text-sm text-[var(--feature-success-content)]">{passed} passed</span>
        </div>

        <div className="flex items-center gap-1.5">
          <XCircle className="w-4 h-4 text-[var(--feature-danger-content)]" aria-hidden="true" />
          <span className="text-sm text-[var(--feature-danger-content)]">{failed} failed</span>
        </div>

        <div className="flex items-center gap-1.5">
          <AlertTriangle className="w-4 h-4 text-[var(--feature-warning-content)]" aria-hidden="true" />
          <span className="text-sm text-[var(--feature-warning-content)]">{warnings} warnings</span>
        </div>
      </div>

      <span className={`text-xs font-medium px-2.5 py-1 rounded ${levelStyle.bg} ${levelStyle.color}`}>
        {levelStyle.label}
      </span>
    </div>
  );
}
