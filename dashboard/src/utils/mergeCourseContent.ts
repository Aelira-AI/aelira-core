/**
 * Merges the DB-backed course content status (from
 * GET /canvas/content/courses/{id}/status) with the live Canvas Files list
 * (from GET /canvas/courses/{id}/files) into ONE list — files are course
 * content too, not a separate class of thing.
 *
 * Keyed by provider + course/parent + content source + native id, with the
 * DB row winning whenever both sources describe the same item: it carries
 * scan state the live Canvas API doesn't know about. A live file with no
 * matching DB row (never scanned yet) is included as an `unscanned` entry
 * so it still shows up in the unified list.
 *
 * Pure function — no I/O, no React. Both callers (CanvasContentPage,
 * LTICourseView) fetch the two sources themselves and pass them in.
 */

export interface StatusContentItem {
  cloud_file_id: string;
  provider_file_id: string | null;
  provider?: string | null;
  provider_parent_id?: string | null;
  content_type: string | null;
  title: string;
  compliance_score: number | null;
  issue_count: number;
  writeback_status: string | null;
  has_remediated_version: boolean;
  last_scanned_at: string | null;
  content_updated_at?: string | null;
  /** The scan whose results are current for this item — needed to call
   * POST /education/remediate/{scan_id} for a per-item remediate action. */
  scan_id?: string | null;
}

/** Shape of one item from GET /canvas/courses/{id}/files (live Canvas API). */
export interface LiveCanvasFile {
  id: string;
  display_name: string;
  filename: string;
  content_type: string;
  size: number;
  url?: string;
}

export type ScanStatus = 'scanned' | 'unscanned';

export interface MergedContentItem {
  /** Stable composite identity used for dedupe and UI state. */
  identity_key: string;
  provider: string;
  provider_parent_id: string;
  /** Canvas's native id for this item. */
  provider_file_id: string;
  /** null when the item has never been scanned (live-only, no DB row). */
  cloud_file_id: string | null;
  title: string;
  /** 'page' | 'assignment' | 'announcement' | 'quiz' | 'discussion' | 'file' | ... */
  content_type: string;
  compliance_score: number | null;
  issue_count: number;
  writeback_status: string | null;
  has_remediated_version: boolean;
  last_scanned_at: string | null;
  content_updated_at: string | null;
  scan_id: string | null;
  scan_status: ScanStatus;
}

export interface CourseContentIdentityContext {
  provider: string;
  parentId: string;
}

function contentIdentity(
  provider: string,
  parentId: string,
  contentSource: string,
  nativeId: string
): string {
  return JSON.stringify([provider, parentId, contentSource, nativeId]);
}

function fromStatusItem(
  item: StatusContentItem,
  context: CourseContentIdentityContext
): MergedContentItem {
  const provider = item.provider ?? context.provider;
  const providerParentId = item.provider_parent_id ?? context.parentId;
  const contentType = item.content_type ?? 'file';
  const providerFileId = item.provider_file_id ?? item.cloud_file_id;
  return {
    identity_key: contentIdentity(provider, providerParentId, contentType, providerFileId),
    provider,
    provider_parent_id: providerParentId,
    provider_file_id: providerFileId,
    cloud_file_id: item.cloud_file_id,
    title: item.title,
    content_type: contentType,
    compliance_score: item.compliance_score,
    issue_count: item.issue_count,
    writeback_status: item.writeback_status,
    has_remediated_version: item.has_remediated_version,
    last_scanned_at: item.last_scanned_at,
    content_updated_at: item.content_updated_at ?? null,
    scan_id: item.scan_id ?? null,
    scan_status: item.last_scanned_at ? 'scanned' : 'unscanned',
  };
}

function fromLiveFile(
  file: LiveCanvasFile,
  context: CourseContentIdentityContext
): MergedContentItem {
  return {
    identity_key: contentIdentity(context.provider, context.parentId, 'file', file.id),
    provider: context.provider,
    provider_parent_id: context.parentId,
    provider_file_id: file.id,
    cloud_file_id: null,
    title: file.display_name || file.filename,
    content_type: 'file',
    compliance_score: null,
    issue_count: 0,
    writeback_status: null,
    has_remediated_version: false,
    last_scanned_at: null,
    content_updated_at: null,
    scan_id: null,
    scan_status: 'unscanned',
  };
}

/**
 * Merge DB status items with the live files list into one deduped,
 * composite-identity-keyed list. Degrades gracefully: if the live files call
 * failed (pass `null` or `undefined`), the DB list is returned untouched —
 * never blank the view because Canvas's live API had a bad moment.
 */
export function mergeCourseContent(
  statusItems: StatusContentItem[] | null | undefined,
  liveFiles: LiveCanvasFile[] | null | undefined,
  context: CourseContentIdentityContext = { provider: 'canvas', parentId: '' }
): MergedContentItem[] {
  const items = statusItems ?? [];
  const files = liveFiles ?? [];

  const merged = new Map<string, MergedContentItem>();

  for (const item of items) {
    const converted = fromStatusItem(item, context);
    merged.set(converted.identity_key, converted);
  }

  for (const file of files) {
    if (!file || !file.id) continue;
    const converted = fromLiveFile(file, context);
    if (merged.has(converted.identity_key)) continue; // DB row wins — already has scan state
    merged.set(converted.identity_key, converted);
  }

  return Array.from(merged.values());
}

/**
 * Whether a merged item is eligible for the remediate action (per-row
 * button in both views, and "Remediate All"'s eligibility filter):
 * scanned, has issues, no remediated version yet, and there's a scan_id
 * to remediate against (unscanned rows have scan_id: null by construction
 * — nothing to remediate). Shared so CanvasContentPage's per-row button,
 * its "Remediate All" batch, and LTICourseView's per-row button can't
 * disagree on which rows qualify.
 */
export function isRemediable(item: MergedContentItem): boolean {
  return (
    item.compliance_score !== null &&
    item.issue_count > 0 &&
    !item.has_remediated_version &&
    !!item.scan_id
  );
}

// Terminal writeback states — an item in one of these is done, one way or
// another, and shouldn't be offered for approval again. Includes
// 'rolled_back': rollback_content restores the original content_body but
// never clears has_remediated_version, so without this a rolled-back item
// would still pass isApprovable() below and be silently re-offered to
// "Approve All" — a deliberate rollback demands fresh review, not a
// re-approve.
const TERMINAL_WRITEBACK_STATUSES = new Set([
  'approved',
  'written_back',
  'writtenback',
  'rejected',
  'rolled_back',
]);

/**
 * Whether a merged item is eligible for the approve action (per-item and
 * "Approve All" in both views): a remediation exists (has_remediated_version
 * — true for both HTML items with a remediated_body and file items
 * remediated via POST /education/remediate/{scan_id}, which never gets an
 * HTML body) AND writeback_status isn't already a terminal state.
 *
 * The bug this replaced checked `!item.writeback_status`, which excluded
 * 'pending_review' — the exact status approval exists to act on. Only
 * items that had never been touched (writeback_status: null) were ever
 * sent; everything already queued for review was silently skipped.
 */
export function isApprovable(item: MergedContentItem): boolean {
  return (
    item.has_remediated_version &&
    !(item.writeback_status && TERMINAL_WRITEBACK_STATUSES.has(item.writeback_status))
  );
}

export type ContentItemStateKey =
  | 'unscanned'
  | 'needs_remediation'
  | 'auto_remediated_pending_review'
  | 'remediated_pending_review'
  | 'approved'
  | 'written_back'
  | 'rejected'
  | 'rolled_back'
  | 'compliant';

export interface ContentItemState {
  key: ContentItemStateKey;
  label: string;
}

/**
 * The visible per-item state: the actual reason a demo watching "pending
 * review" on every single row looks broken. HTML content is
 * auto-remediated at scan time (CanvasContentScanner.remediate_content_item,
 * called during the same scan that finds the issues); files only get
 * remediated via the explicit POST /education/remediate/{scan_id} action
 * — which is exactly why "Remediate All" correctly targets only files.
 * Nothing on screen said so; every remediated-but-not-yet-approved row
 * just read "pending review" regardless of how it got there.
 *
 * Priority order (checked top to bottom — terminal writeback states win
 * over remediation state, which wins over the raw issue count):
 *   1. compliance_score === null           -> unscanned
 *   2. writeback_status === 'approved'     -> approved
 *   3. writeback_status is a written_back  -> written_back
 *      variant ('written_back'/'writtenback')
 *   4. writeback_status === 'rejected'     -> rejected
 *   5. writeback_status === 'rolled_back'  -> rolled_back
 *   6. has_remediated_version              -> auto_remediated_pending_review
 *                                              (content_type !== 'file')
 *                                           -> remediated_pending_review
 *                                              (content_type === 'file')
 *   7. issue_count === 0                   -> compliant
 *   8. otherwise                           -> needs_remediation
 *
 * 'rolled_back' isn't in the state set the brief named — found while
 * verifying against every real writeback_status value the backend sets
 * (rollback_content, canvas_content_scanner.py:823 / brightspace_routes.py
 * ~1920/1991). A rolled-back row's has_remediated_version stays true
 * (rollback restores original content_body but never clears the flag),
 * so without this check it would misread as still pending review —
 * exactly the kind of state-legibility gap this task exists to close.
 *
 * Auto vs manual remediation can't be read off has_remediated_version
 * alone — both remediation paths set the exact same boolean, with no
 * field recording which one ran. content_type is the discriminator: HTML
 * types (page/assignment/announcement/quiz/discussion) only ever reach
 * has_remediated_version via the scan-time auto-remediation step; 'file'
 * rows only ever reach it via the explicit per-item/batch remediate
 * action. If a future content type breaks that assumption (e.g. an
 * HTML-bearing type gets a manual remediation path too), this inference
 * needs revisiting.
 */
export function contentItemState(item: MergedContentItem): ContentItemState {
  if (item.compliance_score === null) {
    return { key: 'unscanned', label: 'Unscanned' };
  }
  if (item.writeback_status === 'approved') {
    return { key: 'approved', label: 'Approved' };
  }
  if (item.writeback_status === 'written_back' || item.writeback_status === 'writtenback') {
    return { key: 'written_back', label: 'Written back' };
  }
  if (item.writeback_status === 'rejected') {
    return { key: 'rejected', label: 'Rejected' };
  }
  if (item.writeback_status === 'rolled_back') {
    return { key: 'rolled_back', label: 'Rolled back' };
  }
  if (item.has_remediated_version) {
    return item.content_type === 'file'
      ? { key: 'remediated_pending_review', label: 'Remediated · pending review' }
      : { key: 'auto_remediated_pending_review', label: 'Auto-remediated · pending review' };
  }
  if (item.issue_count === 0) {
    return { key: 'compliant', label: 'Compliant' };
  }
  return { key: 'needs_remediation', label: 'Scanned · needs remediation' };
}

/**
 * Color intent per state, as a plain string so each view can map it into
 * whatever badge API it already uses (CanvasContentPage's shared <Badge>
 * component vs LTICourseView's inline {bg, color} idiom — the two views
 * use genuinely different styling systems with no shared component
 * today, so unifying rendering into one new component would have meant
 * introducing a dependency neither view currently has, not reusing one
 * that exists). Matches this codebase's existing convention exactly:
 * approved=accent, written_back=success (see getWritebackBadge in both
 * files).
 */
export const CONTENT_ITEM_STATE_COLOR: Record<
  ContentItemStateKey,
  'neutral' | 'accent' | 'success' | 'warning' | 'danger'
> = {
  unscanned: 'neutral',
  needs_remediation: 'warning',
  auto_remediated_pending_review: 'warning',
  remediated_pending_review: 'warning',
  approved: 'accent',
  written_back: 'success',
  rejected: 'danger',
  rolled_back: 'neutral',
  compliant: 'success',
};

export interface ContentTypeSummary {
  content_type: string;
  total: number;
  scanned: number;
  average_compliance: number | null;
  issues: number;
}

/**
 * Aggregate a merged list into per-content-type stats (total/scanned/
 * average compliance/issues). Shared by both consuming views so their
 * "by type" breakdown tables can never drift from each other's counting
 * rules — used to be computed inline, separately, in each component.
 *
 * Unscanned rows (compliance_score: null) count toward `total` but are
 * excluded from `scanned` and from the average_compliance calculation —
 * they have no score to average in.
 */
export function groupContentByType(items: MergedContentItem[]): ContentTypeSummary[] {
  const groups = new Map<string, MergedContentItem[]>();
  for (const item of items) {
    const list = groups.get(item.content_type);
    if (list) {
      list.push(item);
    } else {
      groups.set(item.content_type, [item]);
    }
  }

  return Array.from(groups.entries()).map(([content_type, groupItems]) => {
    const scannedItems = groupItems.filter((i) => i.compliance_score !== null);
    const average_compliance =
      scannedItems.length > 0
        ? scannedItems.reduce((sum, i) => sum + (i.compliance_score ?? 0), 0) /
          scannedItems.length
        : null;
    return {
      content_type,
      total: groupItems.length,
      scanned: scannedItems.length,
      average_compliance,
      issues: groupItems.reduce((sum, i) => sum + i.issue_count, 0),
    };
  });
}
