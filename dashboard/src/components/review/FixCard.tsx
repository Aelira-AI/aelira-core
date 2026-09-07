import React, { useState, ChangeEvent } from 'react';
import {
  ChevronDown,
  ChevronRight,
  Check,
  X,
  Pencil,
  CheckCircle,
  XCircle,
  CalendarClock,
} from 'lucide-react';
import { ConfidenceBadge } from './ConfidenceBadge';

// ============================================================================
// Types
// ============================================================================

export interface Fix {
  id: string;
  category: string;
  severity: string;
  description: string;
  confidence: number;
  fix_method: string;
  needs_review: boolean;
  review_status: string;
  page_number: number | null;
  original_content?: string;
  fixed_content?: string;
  deferral?: ReviewDeferral | null;
}

export interface ReviewDeferral {
  lifecycle: 'active' | 'expired' | 'revoked' | 'resolved';
  owner: string;
  reason: string;
  expires_at: string;
  created_at: string;
  updated_at: string;
  closed_at?: string | null;
}

interface FixCardProps {
  fix: Fix;
  onApprove: (fixId: string, editedContent?: string, notes?: string) => void;
  onReject: (fixId: string, notes?: string) => void;
  onDefer: (fixId: string, owner: string, reason: string, expiresAt: string) => void;
  onRevokeDeferral: (fixId: string) => void;
}

// ============================================================================
// Constants
// ============================================================================

const CATEGORY_LABELS: Record<string, string> = {
  alt_text: 'Alt Text',
  heading: 'Heading',
  language: 'Language',
  title: 'Title',
  structure: 'Structure',
  navigation: 'Navigation',
  reading_order: 'Reading Order',
  table: 'Table',
  contrast: 'Contrast',
  form: 'Form',
  link: 'Link',
  list: 'List',
  media: 'Media',
};

const METHOD_LABELS: Record<string, string> = {
  rule: 'Rule',
  heuristic: 'Heuristic',
  ai_text: 'AI Text',
  ai_vision: 'AI Vision',
};

function getCategoryColor(category: string): { bg: string; text: string } {
  switch (category) {
    case 'alt_text':
      return { bg: 'bg-purple-500/10', text: 'text-purple-400' };
    case 'heading':
      return { bg: 'bg-blue-500/10', text: 'text-blue-400' };
    case 'language':
      return { bg: 'bg-cyan-500/10', text: 'text-cyan-400' };
    case 'title':
      return { bg: 'bg-indigo-500/10', text: 'text-indigo-400' };
    case 'structure':
      return { bg: 'bg-amber-500/10', text: 'text-amber-400' };
    case 'navigation':
      return { bg: 'bg-teal-500/10', text: 'text-teal-400' };
    case 'reading_order':
      return { bg: 'bg-rose-500/10', text: 'text-rose-400' };
    case 'table':
      return { bg: 'bg-emerald-500/10', text: 'text-emerald-400' };
    default:
      return { bg: 'bg-[var(--surface-tertiary)]', text: 'text-[var(--content-tertiary)]' };
  }
}

function getReviewStatusDisplay(status: string): { label: string; icon: React.ReactNode; color: string } {
  switch (status) {
    case 'approved':
      return {
        label: 'Approved',
        icon: <CheckCircle className="w-4 h-4 text-[var(--feature-success-content)]" aria-hidden="true" />,
        color: 'text-[var(--feature-success-content)]',
      };
    case 'rejected':
      return {
        label: 'Rejected',
        icon: <XCircle className="w-4 h-4 text-[var(--feature-danger-content)]" aria-hidden="true" />,
        color: 'text-[var(--feature-danger-content)]',
      };
    case 'edited':
      return {
        label: 'Edited',
        icon: <Pencil className="w-4 h-4 text-[var(--feature-info-content)]" aria-hidden="true" />,
        color: 'text-[var(--feature-info-content)]',
      };
    default:
      return {
        label: 'Pending',
        icon: null,
        color: 'text-[var(--content-tertiary)]',
      };
  }
}

// ============================================================================
// Component
// ============================================================================

function localDateTimeValue(value?: string): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

export function FixCard({
  fix,
  onApprove,
  onReject,
  onDefer,
  onRevokeDeferral,
}: FixCardProps): React.ReactElement {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editedContent, setEditedContent] = useState(fix.fixed_content || '');
  const [reviewNotes, setReviewNotes] = useState('');
  const [deferring, setDeferring] = useState(false);
  const [deferralOwner, setDeferralOwner] = useState(fix.deferral?.owner ?? '');
  const [deferralReason, setDeferralReason] = useState(fix.deferral?.reason ?? '');
  const [deferralExpiry, setDeferralExpiry] = useState(
    localDateTimeValue(fix.deferral?.expires_at),
  );

  const categoryStyle = getCategoryColor(fix.category);
  const alreadyReviewed = fix.review_status === 'approved' || fix.review_status === 'rejected' || fix.review_status === 'edited';
  const reviewDisplay = getReviewStatusDisplay(fix.review_status);
  const openDeferral = fix.deferral?.lifecycle === 'active' || fix.deferral?.lifecycle === 'expired';

  const handleApprove = (): void => {
    onApprove(fix.id, editing ? editedContent : undefined, reviewNotes || undefined);
    setEditing(false);
    setReviewNotes('');
  };

  const handleReject = (): void => {
    onReject(fix.id, reviewNotes || undefined);
    setReviewNotes('');
  };

  const handleDefer = (): void => {
    if (!deferralOwner.trim() || !deferralReason.trim() || !deferralExpiry) return;
    onDefer(
      fix.id,
      deferralOwner.trim(),
      deferralReason.trim(),
      new Date(deferralExpiry).toISOString(),
    );
    setDeferring(false);
  };

  return (
    <div className="border border-[var(--border-primary)] rounded-lg overflow-hidden">
      {/* Header - always visible */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between p-4 hover:bg-[var(--surface-secondary)] transition-colors text-left"
        aria-expanded={expanded}
        aria-label={`${fix.description}, ${CATEGORY_LABELS[fix.category] || fix.category} fix, click to ${expanded ? 'collapse' : 'expand'} details`}
      >
        <div className="flex items-center gap-3 min-w-0">
          {/* Category badge */}
          <span className={`text-xs font-medium px-2 py-0.5 rounded ${categoryStyle.bg} ${categoryStyle.text} whitespace-nowrap`}>
            {CATEGORY_LABELS[fix.category] || fix.category}
          </span>

          {/* Confidence */}
          <ConfidenceBadge confidence={fix.confidence} size="sm" />

          {/* Method badge */}
          <span className="text-xs px-1.5 py-0.5 rounded bg-[var(--surface-tertiary)] text-[var(--content-tertiary)] whitespace-nowrap">
            {METHOD_LABELS[fix.fix_method] || fix.fix_method}
          </span>

          {/* Description */}
          <span className="text-sm text-[var(--content-primary)] truncate">
            {fix.description}
          </span>

          {/* Page number */}
          {fix.page_number !== null && (
            <span className="text-xs text-[var(--content-tertiary)] whitespace-nowrap">
              p.{fix.page_number}
            </span>
          )}
        </div>

        <div className="flex items-center gap-2 ml-2 shrink-0">
          {/* Review status indicator */}
          {alreadyReviewed && (
            <span className={`flex items-center gap-1 text-xs ${reviewDisplay.color}`}>
              {reviewDisplay.icon}
              {reviewDisplay.label}
            </span>
          )}
          {fix.deferral && (
            <span className="flex items-center gap-1 text-xs text-[var(--feature-warning-content)]">
              <CalendarClock className="w-4 h-4" aria-hidden="true" />
              {fix.deferral.lifecycle.replace('_', ' ')}
            </span>
          )}

          {expanded ? (
            <ChevronDown className="w-4 h-4 text-[var(--content-tertiary)]" aria-hidden="true" />
          ) : (
            <ChevronRight className="w-4 h-4 text-[var(--content-tertiary)]" aria-hidden="true" />
          )}
        </div>
      </button>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-[var(--border-primary)] p-4 bg-[var(--surface-secondary)] space-y-4">
          {/* Before / After content */}
          {(fix.original_content || fix.fixed_content) && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {fix.original_content && (
                <div>
                  <p className="text-xs font-medium text-[var(--content-tertiary)] uppercase mb-1">Original</p>
                  <div className="p-3 rounded bg-[var(--feature-danger-surface)] border border-[var(--feature-danger-content)] border-opacity-20 text-sm text-[var(--content-secondary)] whitespace-pre-wrap break-words">
                    {fix.original_content}
                  </div>
                </div>
              )}
              <div>
                <p className="text-xs font-medium text-[var(--content-tertiary)] uppercase mb-1">
                  {editing ? 'Edited Fix' : 'Fixed'}
                </p>
                {editing ? (
                  <textarea
                    value={editedContent}
                    onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setEditedContent(e.target.value)}
                    className="input w-full h-24 text-sm"
                    aria-label="Edit fix content"
                  />
                ) : (
                  <div className="p-3 rounded bg-[var(--feature-success-surface)] border border-[var(--feature-success-content)] border-opacity-20 text-sm text-[var(--content-secondary)] whitespace-pre-wrap break-words">
                    {fix.fixed_content || 'No content change'}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Review notes */}
          {!alreadyReviewed && (
            <div>
              <label className="text-xs font-medium text-[var(--content-tertiary)] uppercase block mb-1">
                Review Notes (optional)
              </label>
              <textarea
                value={reviewNotes}
                onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setReviewNotes(e.target.value)}
                className="input w-full h-16 text-sm"
                placeholder="Add notes about this fix..."
                aria-label="Review notes for this fix"
              />
            </div>
          )}

          {!alreadyReviewed && (
            <div className="rounded border border-[var(--feature-warning-content)]/40 bg-[var(--feature-warning-surface)] p-3 space-y-3">
              <div>
                <p className="text-sm font-medium text-[var(--feature-warning-content)]">
                  {fix.deferral
                    ? `Deferral: ${fix.deferral.lifecycle.replace('_', ' ')}`
                    : 'Controlled deferral'}
                </p>
                <p className="text-xs text-[var(--content-secondary)] mt-1">
                  A deferral is an operational decision. It leaves the finding unresolved; it is not remediation or conformance evidence.
                </p>
              </div>

              {fix.deferral && !deferring && (
                <dl className="grid grid-cols-1 gap-1 text-xs text-[var(--content-secondary)] sm:grid-cols-2">
                  <div><dt className="font-medium inline">Owner:</dt> <dd className="inline">{fix.deferral.owner}</dd></div>
                  <div><dt className="font-medium inline">Review date:</dt> <dd className="inline">{new Date(fix.deferral.expires_at).toLocaleString()}</dd></div>
                  <div className="sm:col-span-2"><dt className="font-medium inline">Reason:</dt> <dd className="inline">{fix.deferral.reason}</dd></div>
                </dl>
              )}

              {deferring ? (
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <label className="text-xs font-medium text-[var(--content-secondary)]">
                    Owner
                    <input
                      value={deferralOwner}
                      onChange={(event) => setDeferralOwner(event.target.value)}
                      className="input mt-1 w-full text-sm"
                      maxLength={255}
                      required
                    />
                  </label>
                  <label className="text-xs font-medium text-[var(--content-secondary)]">
                    Expiry / review date
                    <input
                      type="datetime-local"
                      value={deferralExpiry}
                      onChange={(event) => setDeferralExpiry(event.target.value)}
                      className="input mt-1 w-full text-sm"
                      required
                    />
                  </label>
                  <label className="text-xs font-medium text-[var(--content-secondary)] sm:col-span-2">
                    Reason
                    <textarea
                      value={deferralReason}
                      onChange={(event) => setDeferralReason(event.target.value)}
                      className="input mt-1 h-16 w-full text-sm"
                      maxLength={4000}
                      required
                    />
                  </label>
                  <div className="flex gap-2 sm:col-span-2">
                    <button
                      onClick={handleDefer}
                      disabled={!deferralOwner.trim() || !deferralReason.trim() || !deferralExpiry}
                      className="btn-primary px-3 py-1.5 text-sm disabled:opacity-50"
                    >
                      Save deferral
                    </button>
                    <button
                      onClick={() => setDeferring(false)}
                      className="btn-secondary px-3 py-1.5 text-sm"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  <button
                    onClick={() => setDeferring(true)}
                    className="btn-secondary flex items-center gap-1 px-3 py-1.5 text-sm"
                  >
                    <CalendarClock className="w-4 h-4" aria-hidden="true" />
                    {openDeferral ? 'Change deferral' : 'Defer'}
                  </button>
                  {openDeferral && (
                    <button
                      onClick={() => onRevokeDeferral(fix.id)}
                      className="rounded border border-[var(--feature-danger-content)] px-3 py-1.5 text-sm text-[var(--feature-danger-content)] hover:bg-[var(--feature-danger-surface)]"
                    >
                      Revoke deferral
                    </button>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Action buttons */}
          {!alreadyReviewed && (
            <div className="flex items-center gap-2 pt-2 border-t border-[var(--border-secondary)]">
              <button
                onClick={handleApprove}
                className="btn-primary text-sm py-1.5 px-3 flex items-center gap-1"
                aria-label={`Approve fix: ${fix.description}`}
              >
                <Check className="w-4 h-4" aria-hidden="true" />
                {editing ? 'Approve Edit' : 'Approve'}
              </button>
              <button
                onClick={handleReject}
                className="text-sm py-1.5 px-3 flex items-center gap-1 rounded border border-[var(--feature-danger-content)] text-[var(--feature-danger-content)] hover:bg-[var(--feature-danger-surface)] transition-colors"
                aria-label={`Reject fix: ${fix.description}`}
              >
                <X className="w-4 h-4" aria-hidden="true" />
                Reject
              </button>
              {!editing && (
                <button
                  onClick={() => {
                    setEditing(true);
                    setEditedContent(fix.fixed_content || '');
                  }}
                  className="btn-secondary text-sm py-1.5 px-3 flex items-center gap-1"
                  aria-label={`Edit fix: ${fix.description}`}
                >
                  <Pencil className="w-4 h-4" aria-hidden="true" />
                  Edit
                </button>
              )}
              {editing && (
                <button
                  onClick={() => {
                    setEditing(false);
                    setEditedContent(fix.fixed_content || '');
                  }}
                  className="btn-secondary text-sm py-1.5 px-3"
                >
                  Cancel Edit
                </button>
              )}
            </div>
          )}

          {/* Already reviewed state */}
          {alreadyReviewed && (
            <div className={`flex items-center gap-2 text-sm ${reviewDisplay.color}`}>
              {reviewDisplay.icon}
              <span>This fix has been {reviewDisplay.label.toLowerCase()}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
