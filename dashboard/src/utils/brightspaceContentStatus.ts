export type BrightspaceStatusBadgeVariant =
  | 'accent'
  | 'success'
  | 'warning'
  | 'neutral';

export interface BrightspacePersistedContentStatus {
  writeback_status: string | null;
  has_remediated_version: boolean;
  remediation_origin: 'automatic' | 'manual' | null;
}

export interface BrightspaceContentStatusBadge {
  label: string;
  variant: BrightspaceStatusBadgeVariant;
}

const APPROVABLE_WRITEBACK_STATUSES = new Set<string | null>([
  null,
  'remediated',
  'pending_review',
]);

export function isBrightspaceContentApprovable(
  item: BrightspacePersistedContentStatus
): boolean {
  if (!APPROVABLE_WRITEBACK_STATUSES.has(item.writeback_status)) return false;
  return (
    item.writeback_status !== 'pending_review' || item.has_remediated_version
  );
}

/**
 * Resolve the persisted Brightspace remediation/writeback state shown in the
 * course-content table. Provenance comes only from remediation_origin; content
 * type is intentionally absent from this interface so it cannot be inferred.
 */
export function resolveBrightspaceContentStatus(
  item: BrightspacePersistedContentStatus
): BrightspaceContentStatusBadge | null {
  switch (item.writeback_status) {
    case 'remediated':
      return { label: 'Remediated', variant: 'accent' };
    case 'approved':
      return { label: 'Approved', variant: 'accent' };
    case 'written_back':
      return { label: 'Written back', variant: 'success' };
    case 'stale':
      return { label: 'Stale', variant: 'warning' };
    case 'rolled_back':
      return { label: 'Rolled back', variant: 'neutral' };
    case 'pending_review':
      if (!item.has_remediated_version) return null;
      return {
        label:
          item.remediation_origin === 'automatic'
            ? 'Auto-remediated · pending review'
            : item.remediation_origin === 'manual'
              ? 'Manually remediated · pending review'
              : 'Remediated · pending review',
        variant: 'neutral',
      };
    default:
      return null;
  }
}
