import React, { useState, useEffect, useMemo, ChangeEvent } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { RemediationStatusLink } from '../components/results/RemediationStatusLink';
import { batchRemediationReceiptIsConfirmed } from '../utils/batchRemediationReceipt';
import { describeIssueFinding } from '../utils/issueFindingDescription';
import {
  authorizedTrackedIssueScanIds,
  withTrackedIssueScanIds,
  parseTrackedIssueScanIds,
  missingTrackedIssueScanIds,
} from '../utils/issueRemediationTracking';
import {
  normalizeFindingAutoFix,
  selectEligibleIssueScanIds,
  type FindingAutoFix,
} from '../utils/issueRemediationEligibility';
import {
  AlertTriangle,
  AlertCircle,
  Info,
  ChevronDown,
  ChevronRight,
  Filter,
  Search,
  Wrench,
  CheckCircle,
  Loader,
  FileText,
  ExternalLink,
  User,
  MessageSquare,
  Clock,
  LucideIcon,
} from 'lucide-react';
import { scansApi } from '../api/scans';
import type { Issue as ApiIssue, RemediationEligibility } from '../types/api';
import { trackEvent } from '../utils/analytics';
import { useToast } from '../context/toast-context';

type SeverityLevel = 'critical' | 'high' | 'medium' | 'low';
type IssueStatus = 'OPEN' | 'IN_PROGRESS' | 'RESOLVED' | 'WONT_FIX' | 'FALSE_POSITIVE';
type CategoryKey = 'alt_text' | 'heading' | 'contrast' | 'language' | 'table' | 'list' | 'link' | 'navigation' | 'structure' | 'form' | 'media' | 'other';

interface SeverityConfig {
  icon: LucideIcon;
  color: string;
  bg: string;
  label: string;
  priority: number;
}

interface StatusConfig {
  label: string;
  color: string;
  bg: string;
}

interface ScanInfo {
  id: string;
  filename: string;
  type: string;
  remediation_eligibility?: RemediationEligibility;
}

interface Issue {
  id: string;
  description: string;
  severity: SeverityLevel;
  category: CategoryKey | string;
  status?: IssueStatus;
  location?: string;
  wcag_criteria?: string;
  recommendation?: string;
  autoFixCapability: FindingAutoFix;
  assigned_to_name?: string;
  created_at?: string;
  notes?: string;
  tracked_id?: string;
  scanId: string;
  scanInfo: ScanInfo;
}

interface Scan {
  id: string;
  filename: string;
  type: string;
  issues: ApiIssue[];
  remediation_eligibility?: RemediationEligibility;
}

interface Filters {
  severity: string;
  category: string;
  status: string;
  search: string;
}

interface Stats {
  bySeverity: Record<string, number>;
  total: number;
}

interface IssueCardProps {
  issue: Issue;
  scanInfo: ScanInfo;
  onRemediate: (issue: Issue) => void;
  onStatusChange: (issue: Issue, status: string) => void;
  onAddNote: (issue: Issue, note: string) => void;
  isExpanded: boolean;
  onToggle: () => void;
}

const SEVERITY_CONFIG: Record<SeverityLevel, SeverityConfig> = {
  critical: {
    icon: AlertCircle,
    color: 'text-[var(--feature-danger-content)]',
    bg: 'bg-[var(--feature-danger-surface)]',
    label: 'Critical',
    priority: 1,
  },
  high: {
    icon: AlertTriangle,
    color: 'text-[var(--feature-warning-content)]',
    bg: 'bg-[var(--feature-warning-surface)]',
    label: 'High',
    priority: 2,
  },
  medium: {
    icon: Info,
    color: 'text-[var(--feature-info-content)]',
    bg: 'bg-[var(--feature-info-surface)]',
    label: 'Medium',
    priority: 3,
  },
  low: {
    icon: Info,
    color: 'text-[var(--content-secondary)]',
    bg: 'bg-[var(--surface-tertiary)]',
    label: 'Low',
    priority: 4,
  },
};

const CATEGORY_LABELS: Record<string, string> = {
  alt_text: 'Alt Text',
  heading: 'Heading Structure',
  contrast: 'Color Contrast',
  language: 'Language',
  table: 'Tables',
  list: 'Lists',
  link: 'Links',
  navigation: 'Navigation',
  structure: 'Document Structure',
  form: 'Forms',
  media: 'Media',
  title: 'Document Title',
  sheet_name: 'Sheet Name',
  table_header: 'Table Headers',
  other: 'Other',
  // Extended content-type categories
  shadow_dom: 'Shadow DOM',
  aria: 'ARIA Attributes',
  animation: 'Animation',
  pivot_table: 'Pivot Tables',
  conditional_format: 'Conditional Formatting',
  smartart: 'SmartArt',
  embedded_object: 'Embedded Objects',
  flashing: 'Flashing Content',
  color: 'Color Usage',
  reading_order: 'Reading Order',
};

const STATUS_CONFIG: Record<IssueStatus, StatusConfig> = {
  OPEN: {
    label: 'Open',
    color: 'text-[var(--feature-danger-content)]',
    bg: 'bg-[var(--feature-danger-surface)]',
  },
  IN_PROGRESS: {
    label: 'In Progress',
    color: 'text-[var(--feature-warning-content)]',
    bg: 'bg-[var(--feature-warning-surface)]',
  },
  RESOLVED: {
    label: 'Resolved',
    color: 'text-[var(--feature-success-content)]',
    bg: 'bg-[var(--feature-success-surface)]',
  },
  WONT_FIX: {
    label: "Won't Fix",
    color: 'text-[var(--content-secondary)]',
    bg: 'bg-[var(--surface-tertiary)]',
  },
  FALSE_POSITIVE: {
    label: 'False Positive',
    color: 'text-[var(--feature-info-content)]',
    bg: 'bg-[var(--feature-info-surface)]',
  },
};

function IssueCard({ issue, scanInfo, onRemediate, onStatusChange, onAddNote, isExpanded, onToggle }: IssueCardProps): React.ReactElement {
  const severityConfig = SEVERITY_CONFIG[issue.severity] || SEVERITY_CONFIG.medium;
  const SeverityIcon = severityConfig.icon;
  const statusConfig = STATUS_CONFIG[issue.status || 'OPEN'] || STATUS_CONFIG.OPEN;
  const [noteText, setNoteText] = useState<string>('');
  const [showNoteInput, setShowNoteInput] = useState<boolean>(false);

  const handleAddNote = (): void => {
    if (noteText.trim()) {
      onAddNote(issue, noteText.trim());
      setNoteText('');
      setShowNoteInput(false);
    }
  };

  return (
    <div className="border border-[var(--border-primary)] rounded-lg overflow-hidden">
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between p-4 hover:bg-[var(--surface-secondary)] transition-colors text-left"
        aria-expanded={isExpanded}
        aria-label={`${issue.description}, ${severityConfig.label} severity, click to ${isExpanded ? 'collapse' : 'expand'} details`}
      >
        <div className="flex items-center gap-3">
          <div className={`p-2 rounded-lg ${severityConfig.bg}`}>
            <SeverityIcon className={`w-4 h-4 ${severityConfig.color}`} aria-hidden="true" />
          </div>
          <div>
            <p className="font-medium text-primary">{issue.description}</p>
            <div className="flex flex-wrap items-center gap-2 mt-1">
              <span className={`text-xs px-2 py-0.5 rounded ${severityConfig.bg} ${severityConfig.color}`}>
                {severityConfig.label}
              </span>
              <span className={`text-xs px-2 py-0.5 rounded ${statusConfig.bg} ${statusConfig.color}`}>
                {statusConfig.label}
              </span>
              <span className="text-xs text-tertiary">
                {CATEGORY_LABELS[issue.category] || issue.category}
              </span>
              {scanInfo && (
                <span className="text-xs text-tertiary flex items-center gap-1">
                  <FileText className="w-3 h-3" aria-hidden="true" />
                  {scanInfo.filename}
                </span>
              )}
              {issue.assigned_to_name && (
                <span className="text-xs text-tertiary flex items-center gap-1">
                  <User className="w-3 h-3" aria-hidden="true" />
                  {issue.assigned_to_name}
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {isExpanded ? (
            <ChevronDown className="w-5 h-5 text-tertiary" aria-hidden="true" />
          ) : (
            <ChevronRight className="w-5 h-5 text-tertiary" aria-hidden="true" />
          )}
        </div>
      </button>

      {isExpanded && (
        <div className="border-t border-[var(--border-primary)] p-4 bg-[var(--surface-secondary)]">
          <div className="space-y-4">
            {/* Issue Details */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {issue.location && (
                <div>
                  <p className="text-xs font-medium text-tertiary uppercase mb-1">Location</p>
                  <p className="text-sm text-secondary">{issue.location}</p>
                </div>
              )}
              {issue.wcag_criteria && (
                <div>
                  <p className="text-xs font-medium text-tertiary uppercase mb-1">WCAG Criteria</p>
                  <p className="text-sm text-secondary">{issue.wcag_criteria}</p>
                </div>
              )}
            </div>

            {issue.recommendation && (
              <div>
                <p className="text-xs font-medium text-tertiary uppercase mb-1">Recommendation</p>
                <p className="text-sm text-secondary">{issue.recommendation}</p>
              </div>
            )}

            {/* Status & Assignment Controls */}
            <div className="border-t border-[var(--border-secondary)] pt-3">
              <div className="flex flex-wrap items-center gap-3">
                <div>
                  <label id={`status-label-${issue.id}`} className="text-xs font-medium text-tertiary uppercase block mb-1">Status</label>
                  <select
                    value={issue.status || 'OPEN'}
                    onChange={(e: ChangeEvent<HTMLSelectElement>) => {
                      e.stopPropagation();
                      onStatusChange(issue, e.target.value);
                    }}
                    className="input py-1 text-sm"
                    onClick={(e) => e.stopPropagation()}
                    aria-labelledby={`status-label-${issue.id}`}
                  >
                    <option value="OPEN">Open</option>
                    <option value="IN_PROGRESS">In Progress</option>
                    <option value="RESOLVED">Resolved</option>
                    <option value="WONT_FIX">Won't Fix</option>
                    <option value="FALSE_POSITIVE">False Positive</option>
                  </select>
                </div>

                {issue.created_at && (
                  <div>
                    <label className="text-xs font-medium text-tertiary uppercase block mb-1">Created</label>
                    <p className="text-sm text-secondary flex items-center gap-1">
                      <Clock className="w-3 h-3" aria-hidden="true" />
                      {new Date(issue.created_at).toLocaleDateString()}
                    </p>
                  </div>
                )}
              </div>
            </div>

            {/* Notes Section */}
            {issue.notes && (
              <div className="border-t border-[var(--border-secondary)] pt-3">
                <p className="text-xs font-medium text-tertiary uppercase mb-2">Notes</p>
                <div className="bg-[var(--surface-tertiary)] rounded p-3 text-sm text-secondary whitespace-pre-wrap">
                  {issue.notes}
                </div>
              </div>
            )}

            {/* Add Note Form */}
            {showNoteInput ? (
              <div className="border-t border-[var(--border-secondary)] pt-3">
                <label className="text-xs font-medium text-tertiary uppercase block mb-2">Add Note</label>
                <textarea
                  value={noteText}
                  onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setNoteText(e.target.value)}
                  className="input w-full h-20 text-sm"
                  placeholder="Enter your note..."
                  onClick={(e) => e.stopPropagation()}
                />
                <div className="flex gap-2 mt-2">
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleAddNote();
                    }}
                    className="btn-primary text-sm py-1.5 px-3"
                    disabled={!noteText.trim()}
                  >
                    Save Note
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setShowNoteInput(false);
                      setNoteText('');
                    }}
                    className="btn-secondary text-sm py-1.5 px-3"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : null}

            {/* Action Buttons */}
            <div className="flex flex-wrap gap-2 pt-2 border-t border-[var(--border-secondary)]">
              {(['pdf', 'word', 'excel', 'powerpoint', 'latex'].includes(scanInfo.type.toLowerCase())
                || scanInfo.remediation_eligibility?.eligible === true
                || issue.autoFixCapability.state === 'available') && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onRemediate(issue);
                  }}
                  className="btn-primary text-sm py-1.5 px-3 flex items-center gap-1"
                  aria-label={
                    `Open remediation review for the document containing: ${issue.description}`
                  }
                  title="Review the whole document's remediation status and available output"
                >
                  <Wrench className="w-4 h-4" aria-hidden="true" />
                  Review Document Remediation
                </button>
              )}
              {!showNoteInput && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setShowNoteInput(true);
                  }}
                  className="btn-secondary text-sm py-1.5 px-3 flex items-center gap-1"
                  aria-label={`Add note to issue: ${issue.description}`}
                >
                  <MessageSquare className="w-4 h-4" aria-hidden="true" />
                  Add Note
                </button>
              )}
              {scanInfo && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    window.location.href = `/scan/${scanInfo.id}`;
                  }}
                  className="btn-secondary text-sm py-1.5 px-3 flex items-center gap-1"
                  aria-label={`View scan details for ${scanInfo.filename}`}
                >
                  <ExternalLink className="w-4 h-4" aria-hidden="true" />
                  View Scan
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function Issues(): React.ReactElement {
  const [scans, setScans] = useState<Scan[]>([]);
  const [trackedScans, setTrackedScans] = useState<Scan[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedIssues, setExpandedIssues] = useState<Set<string>>(new Set());
  const [filters, setFilters] = useState<Filters>({
    severity: 'all',
    category: 'all',
    status: 'all',
    search: '',
  });
  const [startingBatch, setStartingBatch] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();
  const trackedIdsKey = parseTrackedIssueScanIds(searchParams).join(',');
  const listedIdsKey = scans.map((scan) => scan.id).join(',');
  const knownScans = useMemo(() => [...scans, ...trackedScans], [scans, trackedScans]);
  const batchScanIds = useMemo(() => authorizedTrackedIssueScanIds(searchParams, knownScans), [searchParams, knownScans]);
  const navigate = useNavigate();
  const toast = useToast();

  useEffect(() => {
    const fetchScans = async (): Promise<void> => {
      try {
        setLoading(true);
        const response = await scansApi.listScans();
        const scansList = response.scans || response || [];

        // Fetch issues for each scan
        const scansWithIssues = await Promise.all(
          scansList.map(async (scan: { scan_id?: string; id?: string; file_name?: string; scan_type?: string }) => {
            try {
              const details = await scansApi.getScan(scan.scan_id || scan.id || '');
              const scanResult = details as unknown as { result?: { issues?: ApiIssue[] } };
              return {
                id: scan.scan_id || scan.id || '',
                filename: scan.file_name || 'Unknown',
                type: scan.scan_type?.toLowerCase() || 'unknown',
                issues: scanResult.result?.issues || details.issues || [],
                remediation_eligibility: details.remediation_eligibility,
              };
            } catch {
              return {
                id: scan.scan_id || scan.id || '',
                filename: scan.file_name || 'Unknown',
                type: scan.scan_type?.toLowerCase() || 'unknown',
                issues: [],
              };
            }
          })
        );

        setScans(scansWithIssues);
      } catch (err: unknown) {
        console.error('Failed to fetch scans:', err);
        const fetchError = err as Error;
        setError(fetchError.message || 'Failed to load issues');
      } finally {
        setLoading(false);
      }
    };

    fetchScans();
  }, []);

  useEffect(() => {
    if (loading) return;
    let cancelled = false;
    const missingIds = missingTrackedIssueScanIds(trackedIdsKey.split(','), listedIdsKey.split(','));
    void Promise.all(missingIds.map(async (scanId): Promise<Scan | null> => {
      try {
        const details = await scansApi.getScan(scanId);
        const returnedId = details.scan_id || details.id;
        if (returnedId?.toLowerCase() !== scanId) return null;
        return {
          id: returnedId,
          filename: details.file_name,
          type: details.scan_type.toLowerCase(),
          // Restore document status references without expanding the current findings list.
          issues: [],
          remediation_eligibility: details.remediation_eligibility,
        };
      } catch {
        return null;
      }
    })).then((results) => {
      if (!cancelled) setTrackedScans(results.filter((scan): scan is Scan => scan !== null));
    });
    return () => { cancelled = true; };
  }, [loading, listedIdsKey, trackedIdsKey]);

  // Flatten all issues with scan info, normalizing API fields to frontend shape
  const allIssues = useMemo<Issue[]>(() => {
    const issues: Issue[] = [];
    scans.forEach((scan) => {
      ((scan.issues || []) as unknown as Record<string, unknown>[]).forEach((rawIssue, index) => {
        const suggestedFix = (rawIssue.suggested_fix as string) || (rawIssue.suggested_alt_text as string) || '';
        const description = describeIssueFinding(rawIssue, CATEGORY_LABELS);

        // Map location from various scan-type-specific fields
        const slide = rawIssue.slide_number || rawIssue.slide;
        const page = rawIssue.page_number || rawIssue.page;
        const sheet = rawIssue.sheet_name;
        const paragraph = rawIssue.paragraph_index;
        const location = (rawIssue.location as string)
          || (slide ? `Slide ${slide}` : '')
          || (page ? `Page ${page}` : '')
          || (sheet ? `Sheet: ${sheet}` : '')
          || (paragraph !== undefined ? `Paragraph ${paragraph}` : '')
          || undefined;

        issues.push({
          id: `${scan.id}-${index}`,
          description,
          severity: ((rawIssue.severity as string) || 'medium') as SeverityLevel,
          category: (rawIssue.type as string) || 'other',
          status: (rawIssue.status as IssueStatus) || undefined,
          location: location || undefined,
          wcag_criteria: (rawIssue.criterion as string) || (rawIssue.rule as string) || undefined,
          recommendation: suggestedFix || undefined,
          autoFixCapability: normalizeFindingAutoFix(rawIssue),
          assigned_to_name: (rawIssue.assigned_to as string) || undefined,
          created_at: (rawIssue.created_at as string) || undefined,
          notes: (rawIssue.notes as string) || undefined,
          tracked_id: (rawIssue.tracked_id as string) || (rawIssue.id as string) || undefined,
          scanId: scan.id,
          scanInfo: {
            id: scan.id,
            filename: scan.filename,
            type: scan.type,
            remediation_eligibility: scan.remediation_eligibility,
          },
        });
      });
    });
    return issues;
  }, [scans]);

  // Filter issues
  const filteredIssues = useMemo<Issue[]>(() => {
    return allIssues.filter((issue) => {
      if (filters.severity !== 'all' && issue.severity !== filters.severity) return false;
      if (filters.category !== 'all' && issue.category !== filters.category) return false;
      if (filters.status !== 'all' && (issue.status || 'OPEN') !== filters.status) return false;
      if (filters.search) {
        const search = filters.search.toLowerCase();
        if (
          !issue.description?.toLowerCase().includes(search) &&
          !issue.scanInfo.filename.toLowerCase().includes(search)
        ) {
          return false;
        }
      }
      return true;
    }).sort((a, b) => {
      const priorityA = SEVERITY_CONFIG[a.severity]?.priority || 5;
      const priorityB = SEVERITY_CONFIG[b.severity]?.priority || 5;
      return priorityA - priorityB;
    });
  }, [allIssues, filters]);

  const eligibleScanIds = useMemo(() => selectEligibleIssueScanIds(filteredIssues, scans), [filteredIssues, scans]);

  // Get unique categories from issues
  const categories = useMemo<string[]>(() => {
    const cats = new Set(allIssues.map((i) => i.category).filter(Boolean));
    return Array.from(cats);
  }, [allIssues]);

  // Statistics
  const stats = useMemo<Stats>(() => {
    const bySeverity: Record<string, number> = {};

    allIssues.forEach((issue) => {
      bySeverity[issue.severity] = (bySeverity[issue.severity] || 0) + 1;
    });

    return { bySeverity, total: allIssues.length };
  }, [allIssues]);

  const toggleIssue = (issueId: string): void => {
    setExpandedIssues((prev) => {
      const next = new Set(prev);
      if (next.has(issueId)) {
        next.delete(issueId);
      } else {
        next.add(issueId);
      }
      return next;
    });
  };

  const handleRemediate = (issue: Issue): void => {
    trackEvent('dash-issue-remediation-review', { scope: 'document' });
    navigate(`/remediate/${issue.scanId}`);
  };

  const handleBulkRemediate = async (): Promise<void> => {
    if (startingBatch || batchScanIds.length > 0) return;
    const scanIds = eligibleScanIds;
    if (scanIds.length === 0) return;
    if (scanIds.length > 50) {
      toast.warning('Narrow the filter to at most 50 documents per batch.', 'Batch limit');
      return;
    }
    setStartingBatch(true);
    trackEvent('dash-issue-autofix', { scope: 'bulk' });
    try {
      const receipt = await scansApi.batchRemediate(scanIds, { use_ai: true });
      const queued = receipt.scans_queued;
      if (!batchRemediationReceiptIsConfirmed(receipt, scanIds)) {
        throw new Error('Unconfirmed queue receipt');
      }
      toast.success(`${queued.length} document jobs queued. Review their recorded outcomes below.`, 'Remediation queued');
    } catch {
      toast.warning('The queue request was not confirmed. Check each recorded job before retrying.', 'Status check required');
    } finally {
      setSearchParams((previous) => withTrackedIssueScanIds(previous, scanIds), { replace: true });
      setStartingBatch(false);
    }
  };


  // Handle status change for tracked issues
  const handleStatusChange = async (issue: Issue, newStatus: string): Promise<void> => {
    try {
      // If issue has a tracked ID, update via API
      if (issue.tracked_id) {
        await scansApi.updateIssueStatus(issue.tracked_id, newStatus as IssueStatus);
        toast.success(`Issue status updated to ${STATUS_CONFIG[newStatus as IssueStatus]?.label || newStatus}`, 'Status Updated');
      } else {
        // For local issues not yet tracked, update locally
        setScans((prev) =>
          prev.map((s) => {
            if (s.id === issue.scanId) {
              return {
                ...s,
                issues: s.issues.map((i: ApiIssue, idx: number) =>
                  `${s.id}-${idx}` === issue.id ? { ...i, status: newStatus as IssueStatus } : i
                ),
              };
            }
            return s;
          })
        );
        toast.success(`Issue marked as ${STATUS_CONFIG[newStatus as IssueStatus]?.label || newStatus}`, 'Status Updated');
      }
    } catch (err: unknown) {
      const statusError = err as Error;
      toast.error(statusError.message || 'Failed to update status', 'Error');
    }
  };

  // Handle adding notes to issues
  const handleAddNote = async (issue: Issue, note: string): Promise<void> => {
    try {
      if (issue.tracked_id) {
        await scansApi.addIssueNote(issue.tracked_id, note);
        toast.success('Note added successfully', 'Note Saved');
        // Refresh issue data
        const details = await scansApi.getScan(issue.scanId);
        setScans((prev) =>
          prev.map((s) =>
            s.id === issue.scanId ? { ...s, issues: details.issues || [] } : s
          )
        );
      } else {
        // For local issues, add note locally
        setScans((prev) =>
          prev.map((s) => {
            if (s.id === issue.scanId) {
              return {
                ...s,
                issues: s.issues.map((i: ApiIssue, idx: number) => {
                  if (`${s.id}-${idx}` === issue.id) {
                    const existingNotes = i.notes || '';
                    const timestamp = new Date().toLocaleString();
                    const newNote = `[${timestamp}]: ${note}`;
                    return {
                      ...i,
                      notes: existingNotes ? `${existingNotes}\n\n${newNote}` : newNote,
                    };
                  }
                  return i;
                }),
              };
            }
            return s;
          })
        );
        toast.success('Note added locally', 'Note Saved');
      }
    } catch (err: unknown) {
      const noteError = err as Error;
      toast.error(noteError.message || 'Failed to add note', 'Error');
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64" role="status" aria-label="Loading issues">
        <Loader className="w-8 h-8 animate-spin text-accent" aria-hidden="true" />
        <span className="sr-only">Loading issues...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-8">
        <div className="max-w-7xl mx-auto">
          <div
            className="rounded-lg p-4 bg-[var(--feature-danger-surface)] border border-[var(--feature-danger-content)] text-[var(--feature-danger-content)]"
            role="alert"
          >
            Error: {error}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="p-8">
      <div className="max-w-7xl mx-auto">
        <div className="flex flex-col items-start gap-4 sm:flex-row sm:items-center sm:justify-between mb-6">
          <h1 className="text-3xl font-bold text-primary">Issue Management</h1>
          <button
            onClick={handleBulkRemediate}
            className="btn-primary flex items-center gap-2"
            disabled={startingBatch || batchScanIds.length > 0 || eligibleScanIds.length === 0}
            aria-label="Queue remediation jobs for eligible documents in the filtered findings"
            aria-describedby="issue-queue-eligibility"
          >
            <Wrench className="w-4 h-4" aria-hidden="true" />
            {startingBatch ? 'Queueing document jobs…' : 'Queue Document Remediation'}
          </button>
        </div>
        <p id="issue-queue-eligibility" className="text-sm text-secondary mb-6">
          {eligibleScanIds.length} eligible {eligibleScanIds.length === 1 ? 'document' : 'documents'} in the filtered findings.
          {' '}Queueing processes the whole document; automatic fixes are not guaranteed. Documents without confirmed queue eligibility are excluded.
        </p>

        {/* Stats Overview */}
        {batchScanIds.length > 0 && (
          <section className="card mb-6 space-y-3" aria-label="Document remediation jobs">
            <h2 className="text-lg font-semibold text-primary">Tracked Document Remediation</h2>
            <button type="button" className="btn-secondary" disabled={startingBatch}
              onClick={() => setSearchParams((previous) => withTrackedIssueScanIds(previous, []), { replace: true })}
              aria-describedby="clear-tracked-help">Clear tracked documents</button>
            <p id="clear-tracked-help" className="text-sm text-secondary">Clearing this list does not cancel jobs or delete results. Check recorded outcomes before queueing the same documents again.</p>
            <p className="text-sm text-secondary">Showing each document's latest recorded job. These document references survive reload; they do not confirm a particular batch was accepted. Original findings remain below. Check each recorded outcome before downloading output or retrying.</p>
            {batchScanIds.map((scanId) => (
              <div key={scanId}>
                <h3 className="mb-2 text-sm font-medium text-primary">{knownScans.find((scan) => scan.id === scanId)?.filename || 'Document'}</h3>
                <RemediationStatusLink scanId={scanId} />
              </div>
            ))}
          </section>
        )}

        <div className="grid grid-cols-2 gap-4 mb-6">
          <div className="card">
            <p className="text-sm text-tertiary">Issues Found</p>
            <p className="text-2xl font-bold text-primary">{stats.total}</p>
            <p className="mt-1 text-sm text-secondary">Detected before remediation; includes issues since fixed.</p>
          </div>
          <div className="card">
            <p className="text-sm text-tertiary">Critical/High</p>
            <p className="text-2xl font-bold text-[var(--feature-danger-content)]">
              {(stats.bySeverity.critical || 0) + (stats.bySeverity.high || 0)}
            </p>
          </div>
        </div>

        {/* Filters */}
        <div className="card mb-6" role="search" aria-label="Filter issues">
          <div className="flex flex-wrap items-center gap-4">
            <div className="flex items-center gap-2">
              <Filter className="w-4 h-4 text-tertiary" aria-hidden="true" />
              <span className="text-sm font-medium text-secondary">Filters:</span>
            </div>

            <div className="relative">
              <Search className="w-4 h-4 absolute left-3 top-1/2 transform -translate-y-1/2 text-tertiary" aria-hidden="true" />
              <input
                type="text"
                placeholder="Search issues..."
                value={filters.search}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setFilters((f) => ({ ...f, search: e.target.value }))}
                className="input pl-9 py-1.5 text-sm w-48"
                aria-label="Search issues by description or filename"
              />
            </div>

            <select
              value={filters.severity}
              onChange={(e: ChangeEvent<HTMLSelectElement>) => setFilters((f) => ({ ...f, severity: e.target.value }))}
              className="input py-1.5 text-sm"
              aria-label="Filter by severity"
            >
              <option value="all">All Severities</option>
              <option value="critical">Critical</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>

            <select
              value={filters.category}
              onChange={(e: ChangeEvent<HTMLSelectElement>) => setFilters((f) => ({ ...f, category: e.target.value }))}
              className="input py-1.5 text-sm"
              aria-label="Filter by category"
            >
              <option value="all">All Categories</option>
              {categories.map((cat) => (
                <option key={cat} value={cat}>
                  {CATEGORY_LABELS[cat] || cat}
                </option>
              ))}
            </select>

            <select
              value={filters.status}
              onChange={(e: ChangeEvent<HTMLSelectElement>) => setFilters((f) => ({ ...f, status: e.target.value }))}
              className="input py-1.5 text-sm"
              aria-label="Filter by status"
            >
              <option value="all">All Statuses</option>
              <option value="OPEN">Open</option>
              <option value="IN_PROGRESS">In Progress</option>
              <option value="RESOLVED">Resolved</option>
              <option value="WONT_FIX">Won't Fix</option>
              <option value="FALSE_POSITIVE">False Positive</option>
            </select>
          </div>
        </div>

        {/* Issues List */}
        <div className="space-y-3">
          {filteredIssues.length === 0 ? (
            <div className="card text-center py-12">
              <CheckCircle className="w-12 h-12 mx-auto text-[var(--feature-success-content)] mb-4" aria-hidden="true" />
              <p className="text-lg font-medium text-primary mb-2">
                {allIssues.length === 0 ? 'No issues found!' : 'No matching issues'}
              </p>
              <p className="text-tertiary">
                {allIssues.length === 0
                  ? 'Upload documents to scan for accessibility issues.'
                  : 'Try adjusting your filters to see more issues.'}
              </p>
            </div>
          ) : (
            filteredIssues.map((issue) => (
              <IssueCard
                key={issue.id}
                issue={issue}
                scanInfo={issue.scanInfo}
                isExpanded={expandedIssues.has(issue.id)}
                onToggle={() => toggleIssue(issue.id)}
                onRemediate={handleRemediate}
                onStatusChange={handleStatusChange}
                onAddNote={handleAddNote}
              />
            ))
          )}
        </div>

        {/* Results count */}
        {filteredIssues.length > 0 && (
          <p className="text-sm text-tertiary mt-4 text-center">
            Showing {filteredIssues.length} of {allIssues.length} issues
          </p>
        )}
      </div>
    </div>
  );
}
