import React, { useEffect, useId, useMemo, useRef, useState } from 'react';
import { isAxiosError } from 'axios';
import { apiClient } from '../../api/client';
import {
  movePDFChild, parsePDFEditSave, parsePDFEditTargets, validPDFEditOperation,
  type PDFEditOperation, type PDFEditTarget, type PDFEditTargets, type PDFSourceKind,
} from '../../utils/pdfStructureEditor';

const disabledButton = 'disabled:cursor-not-allowed disabled:opacity-50';

function targetLabel(target: PDFEditTarget): string {
  const page = target.context.page_numbers.length ? `page ${target.context.page_numbers.join(', ')}` : 'page unavailable';
  const excerpt = target.context.segments.map(item => item.text).join(' ').trim();
  return `${target.role} · ${page}${excerpt ? ` · ${excerpt.slice(0, 100)}` : ''}`;
}

function failureMessage(error: unknown, action: 'load' | 'save'): { text: string; stale: boolean } {
  const status = isAxiosError(error) ? error.response?.status : undefined;
  const detail = isAxiosError(error) ? error.response?.data?.detail : undefined;
  if (status === 409 && action === 'load' && detail === 'Cloud file context required') return {
    text: 'This review link does not identify a specific cloud file. Open a file-specific Review link before editing.', stale: true,
  };
  if (status === 409 && action === 'load' && detail === 'PDF source unavailable') return {
    text: 'The selected PDF source is unavailable. Choose a different source or reload targets.', stale: true,
  };
  if (status === 409 && action === 'load' && detail === 'PDF source required') return {
    text: 'This scan is not an editable PDF.', stale: true,
  };
  if (status === 409) return {
    text: 'The PDF or review state changed, or the selected cloud file is unavailable. Reload targets before editing again.',
    stale: true,
  };
  if (status === 422) return { text: 'This PDF or edit is outside the supported structure. No candidate was saved.', stale: false };
  if (status === 503) return { text: 'The candidate could not be confirmed. Reload targets to verify current state before trying again.', stale: true };
  return { text: action === 'load' ? 'Could not load verified PDF targets. Try reloading.' : 'Could not confirm a new candidate. Reload targets before trying again.', stale: action === 'save' };
}

export function PDFStructureEditor({ scanId, cloudFileId, onSaved, onSavingChange }: {
  scanId: string;
  cloudFileId: string | null;
  onSaved: () => void;
  onSavingChange: (saving: boolean) => void;
}): React.ReactElement {
  const [open, setOpen] = useState(false);
  const [sourceKind, setSourceKind] = useState<PDFSourceKind>('original');
  const [reload, setReload] = useState(0);
  const [loaded, setLoaded] = useState<{ key: string; data: PDFEditTargets } | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [stale, setStale] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState('');
  const [heading, setHeading] = useState(1);
  const [order, setOrder] = useState<string[]>([]);
  const [operationKind, setOperationKind] = useState<'heading' | 'order' | 'table_column_headers'>('heading');
  const [tableConfirmed, setTableConfirmed] = useState(false);
  const [moveAnnouncement, setMoveAnnouncement] = useState('');
  const panelId = useId();
  const saveController = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const busy = useRef(false);
  const key = `${scanId}:${cloudFileId ?? ''}:${sourceKind}:${reload}`;

  useEffect(() => {
    generation.current += 1;
    const attempt = generation.current;
    saveController.current?.abort();
    saveController.current = null;
    busy.current = false;
    onSavingChange(false);
    if (!open) return;
    const controller = new AbortController();
    queueMicrotask(() => {
      if (controller.signal.aborted || generation.current !== attempt) return;
      setLoading(true);
      setError(null);
      setStale(false);
      setLoaded(null);
      setSelectedId('');
      setOrder([]);
    });
    void apiClient.get(`/api/reviews/${encodeURIComponent(scanId)}/pdf-edit-targets`, {
      params: { source_kind: sourceKind, ...(cloudFileId ? { cloud_file_id: cloudFileId } : {}) },
      signal: controller.signal,
    }).then(response => {
      const data = parsePDFEditTargets(response.data, sourceKind, cloudFileId);
      if (!controller.signal.aborted && generation.current === attempt) setLoaded({ key, data });
    }).catch(cause => {
      if (!controller.signal.aborted && generation.current === attempt) {
        setError(failureMessage(cause, 'load').text);
        setStale(true);
      }
    }).finally(() => {
      if (!controller.signal.aborted && generation.current === attempt) setLoading(false);
    });
    return () => { controller.abort(); generation.current += 1; saveController.current?.abort(); };
  }, [scanId, cloudFileId, sourceKind, reload, open, key, onSavingChange]);

  const data = loaded?.key === key ? loaded.data : null;
  const editable = useMemo(() => data?.targets.filter(target =>
    target.context.status === 'available' &&
    (target.can_set_heading || target.can_reorder || target.can_set_column_headers)) ?? [], [data]);
  const target = data?.targets.find(item => item.target_id === selectedId);
  const byId = useMemo(() => new Map(data?.targets.map(item => [item.target_id, item]) ?? []), [data]);
  const operation: PDFEditOperation | null = !target ? null : operationKind === 'heading' ?
    { kind: 'heading', target_id: target.target_id, level: heading } : operationKind === 'order' ?
      { kind: 'order', target_id: target.target_id, children: order } :
      { kind: 'table_column_headers', target_id: target.target_id };
  const canSave = !!data && !!operation && !stale && !saving && !loading &&
    (operation.kind !== 'table_column_headers' || tableConfirmed) && validPDFEditOperation(data, operation);

  function selectTarget(id: string): void {
    setSelectedId(id);
    setNotice(null);
    setMoveAnnouncement('');
    setTableConfirmed(false);
    const next = data?.targets.find(item => item.target_id === id);
    setHeading(/^H[1-6]$/.test(next?.role ?? '') ? Number(next!.role.slice(1)) : 1);
    setOrder(next?.children ?? []);
    setOperationKind(next?.can_set_heading ? 'heading' : next?.can_reorder ? 'order' : 'table_column_headers');
  }

  function moveChild(index: number, direction: -1 | 1, button: HTMLButtonElement): void {
    const nextIndex = index + direction;
    if (nextIndex < 0 || nextIndex >= order.length) return;
    const moved = order[index];
    setOrder(items => movePDFChild(items, index, direction));
    setMoveAnnouncement(`${byId.get(moved)?.role ?? 'Item'} moved to position ${nextIndex + 1} of ${order.length}.`);
    requestAnimationFrame(() => {
      if (!button.disabled) button.focus();
      else {
        const other = button.parentElement?.querySelector<HTMLButtonElement>(direction === -1 ? 'button[data-direction="down"]' : 'button[data-direction="up"]');
        other?.focus();
      }
    });
  }

  async function save(): Promise<void> {
    if (!data || !operation || !canSave || busy.current) return;
    busy.current = true;
    setSaving(true);
    onSavingChange(true);
    setError(null);
    setNotice(null);
    const controller = new AbortController();
    saveController.current = controller;
    const attempt = generation.current;
    try {
      const response = await apiClient.post(`/api/reviews/${encodeURIComponent(scanId)}/pdf-edit-candidates`, {
        source_kind: data.precondition.source_kind,
        cloud_file_id: data.precondition.cloud_file_id,
        expected_artifact_id: data.precondition.expected_artifact_id,
        expected_source_sha256: data.precondition.source_sha256,
        expected_state_digest: data.precondition.state_digest,
        operation,
      }, { signal: controller.signal });
      if (controller.signal.aborted || generation.current !== attempt) return;
      if (response.status !== 201) throw new Error('Unconfirmed candidate');
      parsePDFEditSave(response.data, data.precondition);
      setNotice('A new PDF candidate is saved and pending human review. Refreshing verified targets and reading-order evidence.');
      onSaved();
      setSourceKind('saved');
      setReload(value => value + 1);
    } catch (cause) {
      if (controller.signal.aborted || generation.current !== attempt) return;
      const failure = failureMessage(cause, 'save');
      setError(failure.text);
      setStale(failure.stale);
    } finally {
      if (generation.current === attempt) {
        busy.current = false;
        saveController.current = null;
        setSaving(false);
        onSavingChange(false);
      }
    }
  }

  return <section className="min-w-0 border-b border-[var(--border-primary)] px-4 py-3" aria-label="PDF structure editor">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 className="text-sm font-semibold text-primary">PDF structure editor</h2>
      <button type="button" className={`btn-secondary text-sm ${disabledButton}`} aria-expanded={open} aria-controls={panelId}
        onClick={() => { if (!saving) setOpen(value => !value); }} disabled={saving}>
        {open ? 'Hide editor' : 'Edit PDF structure'}
      </button>
    </div>
    {open && <div id={panelId} className="mt-3 min-w-0 space-y-4">
      <p className="text-sm text-secondary">Choose a verified structure target and one supported change. Each save creates a new candidate requiring human review. Page excerpts identify tag ownership; they are not visual positions or a conformance verdict.</p>
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Edit source PDF">
        {(['original', 'saved'] as const).map(kind => <button key={kind} type="button"
          className={`${sourceKind === kind ? 'btn-primary text-sm' : 'btn-secondary text-sm'} ${disabledButton}`} aria-pressed={sourceKind === kind}
          disabled={saving} onClick={() => { setSourceKind(kind); setNotice(null); }}>
          {kind === 'original' ? 'Original PDF' : 'Current saved PDF'}
        </button>)}
        <button type="button" className={`btn-secondary text-sm ${disabledButton}`} disabled={saving} onClick={() => setReload(value => value + 1)}>Reload targets</button>
      </div>
      {sourceKind === 'original' && <p className="text-sm text-secondary">An edit to the original starts from its original bytes and does not include changes in the current saved PDF.</p>}
      {notice && <p role="status" className="rounded border border-[var(--border-primary)] p-2 text-sm text-primary">{notice}</p>}
      {error && <p role="alert" className="rounded border border-[var(--feature-danger-content)] p-2 text-sm text-[var(--feature-danger-content)]">{error}</p>}
      {(loading || (!data && !error)) && <p role="status" className="text-sm text-secondary">Loading verified PDF targets…</p>}
      {data && <>
        <p className="text-xs text-secondary">Verified {sourceKind} PDF · {editable.length} editable targets. Excerpts are limited; targets without usable context are excluded.</p>
        <label className="block max-w-3xl text-sm text-primary">Structure target
          <select className="mt-1 block w-full rounded border border-[var(--border-primary)] bg-[var(--surface-primary)] p-2 text-primary" value={selectedId}
            disabled={saving || stale} onChange={event => selectTarget(event.target.value)}>
            <option value="">Choose a verified target</option>
            {editable.map(item => <option key={item.target_id} value={item.target_id}>{targetLabel(item)}</option>)}
          </select>
        </label>
        {target && <div className="max-w-3xl space-y-3 rounded border border-[var(--border-primary)] p-3">
          <p className="text-sm font-medium text-primary">{target.role} · {target.context.page_numbers.map(page => `page ${page}`).join(', ')}</p>
          <ol className="list-decimal space-y-1 pl-5 text-sm text-primary">
            {target.context.segments.map((segment, index) => <li key={index} className="whitespace-pre-wrap break-words">
              {segment.text} <span className="text-xs text-secondary">({segment.source}, page {segment.page_number})</span>
            </li>)}
          </ol>
          {target.context.truncated && <p className="text-xs text-secondary">Excerpt shortened. Inspect the PDF before deciding.</p>}
          <div className="flex flex-wrap gap-2" role="group" aria-label="PDF edit operation">
            {target.can_set_heading && <button type="button" className={`${operationKind === 'heading' ? 'btn-primary text-sm' : 'btn-secondary text-sm'} ${disabledButton}`} aria-pressed={operationKind === 'heading'} disabled={saving} onClick={() => setOperationKind('heading')}>Heading level</button>}
            {target.can_reorder && <button type="button" className={`${operationKind === 'order' ? 'btn-primary text-sm' : 'btn-secondary text-sm'} ${disabledButton}`} aria-pressed={operationKind === 'order'} disabled={saving} onClick={() => setOperationKind('order')}>Sibling order</button>}
            {target.can_set_column_headers && <button type="button" className={`${operationKind === 'table_column_headers' ? 'btn-primary text-sm' : 'btn-secondary text-sm'} ${disabledButton}`} aria-pressed={operationKind === 'table_column_headers'} disabled={saving} onClick={() => setOperationKind('table_column_headers')}>Column headers</button>}
          </div>
          {operationKind === 'heading' && target.can_set_heading && <label className="block text-sm text-primary">New heading level
            <select className="ml-2 rounded border border-[var(--border-primary)] bg-[var(--surface-primary)] p-2 text-primary" value={heading} disabled={saving} onChange={event => setHeading(Number(event.target.value))}>
              {Array.from({ length: 6 }, (_, index) => <option key={index + 1} value={index + 1}>H{index + 1}</option>)}
            </select>
          </label>}
          {operationKind === 'order' && target.can_reorder && <>
            <p className="text-sm text-secondary">Move every sibling into the intended reading order. Use the Up and Down buttons with a keyboard or pointer.</p>
            <p role="status" aria-live="polite" className="sr-only">{moveAnnouncement}</p>
            <ol className="space-y-2" aria-label="Proposed sibling order">
              {order.map((id, index) => <li key={id} className="flex min-w-0 items-center gap-2 rounded border border-[var(--border-primary)] p-2 text-sm text-primary">
                <span className="min-w-0 flex-1 break-words">{index + 1}. {byId.has(id) ? targetLabel(byId.get(id)!) : 'Unverified child'}</span>
                <button type="button" data-direction="up" className={`btn-secondary text-sm ${disabledButton}`} disabled={saving || index === 0} aria-label={`Move item ${index + 1} up`} onClick={event => moveChild(index, -1, event.currentTarget)}>Up</button>
                <button type="button" data-direction="down" className={`btn-secondary text-sm ${disabledButton}`} disabled={saving || index === order.length - 1} aria-label={`Move item ${index + 1} down`} onClick={event => moveChild(index, 1, event.currentTarget)}>Down</button>
              </li>)}
            </ol>
          </>}
          {operationKind === 'table_column_headers' && target.can_set_column_headers && <label className="flex items-start gap-2 text-sm text-primary">
            <input type="checkbox" className="mt-1" checked={tableConfirmed} disabled={saving} onChange={event => setTableConfirmed(event.target.checked)} />
            I confirm the first row of this supported simple table contains column headers.
          </label>}
          <div className="flex flex-wrap gap-2">
            <button type="button" className={`btn-primary text-sm ${disabledButton}`} disabled={!canSave} onClick={() => void save()}>{saving ? 'Saving candidate…' : 'Save pending-review candidate'}</button>
            <button type="button" className={`btn-secondary text-sm ${disabledButton}`} disabled={saving} onClick={() => selectTarget('')}>Cancel edit</button>
          </div>
          {!canSave && !saving && !stale && <p className="text-xs text-secondary">Choose a supported change that differs from the inspected structure.</p>}
        </div>}
      </>}
    </div>}
  </section>;
}
