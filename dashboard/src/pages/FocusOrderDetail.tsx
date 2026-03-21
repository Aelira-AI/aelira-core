import React, { useState, useEffect, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { Calendar, Globe, Loader, Eye, EyeOff, AlertTriangle, Info } from 'lucide-react';
import { ComplianceScore } from '../components/results/ComplianceScore';
import { Breadcrumbs } from '../components/layout/Breadcrumbs';

interface BoundingBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface FocusElement {
  element_id: number;
  tag_name: string;
  selector: string;
  text_content: string;
  is_visible: boolean;
  is_offscreen: boolean;
  tab_index: number;
  bounding_box: BoundingBox;
}

interface FocusIssueElement {
  element_id: number;
  tag_name: string;
  selector: string;
}

interface FocusIssue {
  issue_type: string;
  severity: 'critical' | 'serious' | 'moderate' | 'minor';
  description: string;
  suggested_fix: string;
  wcag_criterion: string;
  element?: FocusIssueElement;
}

interface FocusScan {
  id: string;
  url: string;
  created_at: string;
  compliance_score: number;
  wcag_compliant: boolean;
  total_focusable_elements: number;
  focus_sequence: FocusElement[];
  issues: FocusIssue[];
}

interface IssuesBySeverity {
  critical: number;
  serious: number;
  moderate: number;
  minor: number;
}

interface SeverityColors {
  bg: string;
  border: string;
  text: string;
}

// Mock data generator (simulates API response)
const getMockScanData = (id: string): FocusScan => ({
  id,
  url: 'https://example.com',
  created_at: new Date().toISOString(),
  compliance_score: 82,
  wcag_compliant: true,
  total_focusable_elements: 45,
  focus_sequence: [
    {
      element_id: 0,
      tag_name: 'a',
      selector: '#skip-link',
      text_content: 'Skip to main content',
      is_visible: false,
      is_offscreen: true,
      tab_index: 0,
      bounding_box: { x: -9999, y: 0, width: 100, height: 20 }
    },
    {
      element_id: 1,
      tag_name: 'a',
      selector: '#logo',
      text_content: 'Home',
      is_visible: true,
      is_offscreen: false,
      tab_index: 0,
      bounding_box: { x: 20, y: 20, width: 150, height: 50 }
    },
  ],
  issues: [
    {
      issue_type: 'invisible_element',
      severity: 'serious',
      description: 'Element is in focus order but not visible: #hidden-input',
      suggested_fix: 'Remove tabindex or set to tabindex="-1" for invisible elements',
      wcag_criterion: '2.4.3',
      element: {
        element_id: 15,
        tag_name: 'input',
        selector: '#hidden-input'
      }
    },
    {
      issue_type: 'illogical_order',
      severity: 'moderate',
      description: 'Large visual jump in focus order (850px)',
      suggested_fix: 'Reorder HTML or use tabindex to create logical focus order',
      wcag_criterion: '2.4.3',
      element: {
        element_id: 22,
        tag_name: 'button',
        selector: '#submit-btn'
      }
    },
    {
      issue_type: 'missing_focus_indicator',
      severity: 'serious',
      description: '28 elements (62%) lack visible focus indicators',
      suggested_fix: 'Add :focus styles with visible outline or box-shadow',
      wcag_criterion: '2.4.7'
    }
  ]
});

export function FocusOrderDetail(): React.ReactElement {
  const { id } = useParams<{ id: string }>();
  const [scan, setScan] = useState<FocusScan | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, _setError] = useState<string | null>(null);

  // Fetch scan data (will be replaced with actual API call)
  const fetchScan = useCallback(async (scanId: string): Promise<FocusScan> => {
    // TODO: Replace with actual API call
    // const response = await apiClient.get(`/focus-order/${scanId}`);
    // return response.data;
    return getMockScanData(scanId);
  }, []);

  useEffect(() => {
    let cancelled = false;

    const loadData = async (): Promise<void> => {
      const data = await fetchScan(id!);
      if (!cancelled) {
        setScan(data);
        setLoading(false);
      }
    };

    loadData();

    return () => {
      cancelled = true;
    };
  }, [id, fetchScan]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64" role="status" aria-label="Loading focus order">
        <Loader className="w-8 h-8 animate-spin text-primary-600" aria-hidden="true" />
        <span className="sr-only">Loading focus order analysis...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-8">
        <div className="max-w-6xl mx-auto">
          <div className="bg-[var(--surface-error-subtle)] border border-[var(--content-error)] rounded-lg p-4 text-[var(--content-error)]" role="alert">
            Error: {error}
          </div>
        </div>
      </div>
    );
  }

  if (!scan) {
    return (
      <div className="p-8">
        <div className="max-w-6xl mx-auto">
          <div className="text-secondary">Scan not found</div>
        </div>
      </div>
    );
  }

  const formatDate = (dateString: string): string => {
    return new Date(dateString).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  const issuesBySeverity: IssuesBySeverity = {
    critical: scan.issues.filter(i => i.severity === 'critical').length,
    serious: scan.issues.filter(i => i.severity === 'serious').length,
    moderate: scan.issues.filter(i => i.severity === 'moderate').length,
    minor: scan.issues.filter(i => i.severity === 'minor').length
  };

  const severityColors: Record<string, SeverityColors> = {
    critical: {
      bg: 'bg-[var(--feature-danger-surface)]',
      border: 'border-[var(--feature-danger-content)]',
      text: 'text-[var(--feature-danger-content)]'
    },
    serious: {
      bg: 'bg-[var(--feature-warning-surface)]',
      border: 'border-[var(--feature-warning-content)]',
      text: 'text-[var(--feature-warning-content)]'
    },
    moderate: {
      bg: 'bg-[var(--feature-info-surface)]',
      border: 'border-[var(--feature-info-content)]',
      text: 'text-[var(--feature-info-content)]'
    },
    minor: {
      bg: 'bg-[var(--surface-tertiary)]',
      border: 'border-[var(--border-primary)]',
      text: 'text-[var(--content-secondary)]'
    }
  };

  return (
    <div className="p-8">
      <div className="max-w-6xl mx-auto">
        {/* Breadcrumbs & Header */}
        <Breadcrumbs items={[
          { label: 'History', href: '/history' },
          { label: 'Focus Order' },
        ]} />

        <div className="mb-6">
          <h1 className="text-3xl font-bold text-primary mb-2">
            Focus Order Analysis
          </h1>
          <div className="flex items-center space-x-6 text-sm text-secondary">
            <div className="flex items-center space-x-2">
              <Calendar className="w-4 h-4" />
              <span>{formatDate(scan.created_at)}</span>
            </div>
            <div className="flex items-center space-x-2">
              <Globe className="w-4 h-4" />
              <span className="break-all">{scan.url}</span>
            </div>
          </div>
        </div>

        {/* Compliance Badge */}
        <div className="mb-6">
          <div className="flex items-center space-x-4">
            <ComplianceScore score={scan.compliance_score} />
            {scan.wcag_compliant && (
              <div className="flex items-center space-x-2 px-4 py-2 bg-[var(--feature-success-surface)] border border-[var(--feature-success-content)] rounded-lg">
                <span className="text-sm font-medium text-[var(--feature-success-content)]">
                  WCAG 2.4.3 & 2.4.7 Compliant
                </span>
              </div>
            )}
          </div>
        </div>

        {/* Statistics */}
        <div className="grid grid-cols-4 gap-4 mb-6">
          <div className="card border-2 border-[var(--feature-info-content)] bg-[var(--feature-info-surface)]">
            <div className="text-sm font-medium text-secondary mb-1">
              Total Focusable
            </div>
            <div className="text-3xl font-bold text-[var(--feature-info-content)]">
              {scan.total_focusable_elements}
            </div>
          </div>
          <div className="card border-2 border-[var(--feature-danger-content)] bg-[var(--feature-danger-surface)]">
            <div className="text-sm font-medium text-secondary mb-1">Critical</div>
            <div className="text-3xl font-bold text-[var(--feature-danger-content)]">
              {issuesBySeverity.critical}
            </div>
          </div>
          <div className="card border-2 border-[var(--feature-warning-content)] bg-[var(--feature-warning-surface)]">
            <div className="text-sm font-medium text-secondary mb-1">Serious</div>
            <div className="text-3xl font-bold text-[var(--feature-warning-content)]">
              {issuesBySeverity.serious}
            </div>
          </div>
          <div className="card border-2 border-[var(--feature-info-content)] bg-[var(--feature-info-surface)]">
            <div className="text-sm font-medium text-secondary mb-1">Moderate</div>
            <div className="text-3xl font-bold text-[var(--feature-info-content)]">
              {issuesBySeverity.moderate}
            </div>
          </div>
        </div>

        {/* Focus Sequence Visualization */}
        <div className="mb-6 card">
          <h2 className="text-xl font-semibold text-primary mb-4">
            Keyboard Navigation Flow (TAB Sequence)
          </h2>
          <p className="text-sm text-secondary mb-4">
            Shows the order in which elements receive focus when pressing the TAB key.
          </p>
          <div className="space-y-2">
            {scan.focus_sequence.map((element, index) => (
              <div
                key={index}
                className={`flex items-center space-x-4 p-3 rounded border ${
                  !element.is_visible
                    ? 'bg-[var(--feature-danger-surface)] border-[var(--feature-danger-content)]'
                    : element.is_offscreen
                    ? 'bg-[var(--feature-warning-surface)] border-[var(--feature-warning-content)]'
                    : 'bg-[var(--surface-tertiary)] border-[var(--border-primary)]'
                }`}
              >
                <div className="flex-shrink-0 w-12 h-12 flex items-center justify-center bg-[var(--feature-info-surface)] text-[var(--feature-info-content)] font-bold rounded-lg">
                  {index + 1}
                </div>
                <div className="flex-1">
                  <div className="flex items-center space-x-2 mb-1">
                    <code className="text-sm font-mono text-primary">
                      {element.selector}
                    </code>
                    {!element.is_visible && (
                      <span className="flex items-center space-x-1 text-xs px-2 py-1 bg-[var(--feature-danger-surface)] text-[var(--feature-danger-content)] rounded">
                        <EyeOff className="w-3 h-3" />
                        <span>Hidden</span>
                      </span>
                    )}
                    {element.is_offscreen && (
                      <span className="flex items-center space-x-1 text-xs px-2 py-1 bg-[var(--feature-warning-surface)] text-[var(--feature-warning-content)] rounded">
                        <Eye className="w-3 h-3" />
                        <span>Off-screen</span>
                      </span>
                    )}
                  </div>
                  {element.text_content && (
                    <p className="text-xs text-secondary">
                      "{element.text_content}"
                    </p>
                  )}
                </div>
                <div className="text-xs text-tertiary">
                  &lt;{element.tag_name}&gt;
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Issues List */}
        <div className="mb-6">
          <h2 className="text-xl font-semibold text-primary mb-4">
            Detected Issues ({scan.issues.length})
          </h2>
          <div className="space-y-3">
            {scan.issues.map((issue, index) => {
              const Icon = issue.severity === 'critical' || issue.severity === 'serious'
                ? AlertTriangle
                : Info;
              const colors = severityColors[issue.severity] || severityColors.minor;

              return (
                <div
                  key={index}
                  className={`card border ${colors.border} ${colors.bg}`}
                >
                  <div className="flex items-start space-x-4">
                    <Icon className={`w-5 h-5 ${colors.text} mt-1`} />
                    <div className="flex-1">
                      <div className="flex items-center justify-between mb-2">
                        <h4 className="font-semibold text-primary">
                          {issue.issue_type.replace(/_/g, ' ').toUpperCase()}
                        </h4>
                        <span className={`text-xs font-medium px-2 py-1 rounded ${colors.bg} ${colors.text}`}>
                          {issue.severity}
                        </span>
                      </div>
                      <p className="text-sm text-secondary mb-2">
                        {issue.description}
                      </p>
                      {issue.wcag_criterion && (
                        <p className="text-xs text-tertiary mb-2">
                          <strong>WCAG Criterion:</strong> {issue.wcag_criterion}
                        </p>
                      )}
                      {issue.suggested_fix && (
                        <div className="mt-3 p-3 bg-[var(--surface-tertiary)] rounded border border-[var(--border-primary)]">
                          <p className="text-xs font-medium text-secondary mb-1">
                            Suggested Fix:
                          </p>
                          <p className="text-xs text-tertiary">
                            {issue.suggested_fix}
                          </p>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
