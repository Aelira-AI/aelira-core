import React, { useEffect, useId, useState } from 'react';
import { apiClient } from '../../api/client';
import { parseReadingOrderComparison, readingOrderReason } from '../../utils/readingOrderComparison';
import type { ReadingOrderComparisonData, ReadingOrderSnapshot } from '../../utils/readingOrderComparison';

function SnapshotView({ snapshot, label }: { snapshot: ReadingOrderSnapshot; label: string }): React.ReactElement {
  const [selected, setSelected] = useState<number | null>(null);
  return <div className="min-w-0 space-y-3">
    <p className="text-sm text-secondary">{label} · Page {snapshot.page_number}{snapshot.page_count > 0 ? ` of ${snapshot.page_count}` : ''}</p>
    {snapshot.status === 'unavailable' && <p role="status" className="text-sm text-secondary">{readingOrderReason(snapshot.reason)}</p>}
    <div className="grid min-w-0 gap-4 xl:grid-cols-2">
      <div className="min-w-0">
        {snapshot.preview_png_base64 && snapshot.width && snapshot.height ?
          <div className="relative mx-auto w-full max-w-[600px] border border-[var(--border-primary)] bg-white" style={{ aspectRatio: `${snapshot.width} / ${snapshot.height}` }}>
            <img className="block h-auto w-full" src={`data:image/png;base64,${snapshot.preview_png_base64}`} alt={`${label}, page ${snapshot.page_number}. Tagged text is listed in order beside or below the image.`} />
            {snapshot.blocks.map(block => block.bbox && <div key={block.index} aria-hidden="true" className="pointer-events-none absolute border-2 border-blue-700" style={{
              left: `${block.bbox[0] / snapshot.width! * 100}%`, top: `${block.bbox[1] / snapshot.height! * 100}%`,
              width: `${(block.bbox[2] - block.bbox[0]) / snapshot.width! * 100}%`, height: `${(block.bbox[3] - block.bbox[1]) / snapshot.height! * 100}%`,
              backgroundColor: selected === block.index ? 'rgba(29,78,216,0.22)' : 'transparent',
            }}><span className="absolute bottom-full left-0 mb-0.5 bg-blue-800 px-1 text-xs text-white">{block.index}</span></div>)}
          </div> : <p className="text-sm text-secondary">Page image unavailable.</p>}
      </div>
      <div className="min-w-0">
        {snapshot.status === 'available' && <>
          <h3 className="text-base font-semibold text-primary">Tagged reading order</h3>
          <p className="mt-1 text-sm text-secondary">{snapshot.blocks.length} tagged text {snapshot.blocks.length === 1 ? 'segment' : 'segments'}, including any text alternatives. Assistive technology may announce alternatives differently. This is evidence for review, not a conformance verdict.</p>
          {snapshot.unpositioned_count > 0 && <p className="mt-2 text-sm text-secondary">{snapshot.unpositioned_count} {snapshot.unpositioned_count === 1 ? 'segment has' : 'segments have'} no unambiguous visual location. The text remains in the ordered list.</p>}
          <ol className="mt-3 list-decimal space-y-2 pl-6" aria-label={`${label} tagged reading order`}>
            {snapshot.blocks.map(block => <li key={block.index} className="pl-1 text-sm text-primary">
              <button type="button" aria-pressed={selected === block.index} onClick={() => setSelected(selected === block.index ? null : block.index)} className="block w-full min-w-0 rounded border border-[var(--border-primary)] p-2 text-left hover:bg-[var(--surface-tertiary)]" style={{ overflowWrap: 'anywhere' }}>
                <span className="whitespace-pre-wrap">{block.text}</span>
                <span className="mt-1 block text-xs text-secondary">{block.source}{block.bbox === null ? ' · Visual location unresolved' : ' · Highlight location'}</span>
              </button>
            </li>)}
          </ol>
        </>}
      </div>
    </div>
    {snapshot.sha256 && <details className="text-xs text-secondary"><summary className="cursor-pointer">File checksum (SHA-256)</summary><code className="mt-1 block break-all">{snapshot.sha256}</code></details>}
  </div>;
}

export function ReadingOrderComparison({ scanId }: { scanId: string }): React.ReactElement {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState(1);
  const [version, setVersion] = useState<'source' | 'saved'>('source');
  const [retry, setRetry] = useState(0);
  const [result, setResult] = useState<{ key: string; data?: ReadingOrderComparisonData; error?: boolean } | null>(null);
  const panelId = useId();
  const requestKey = `${scanId}:${page}:${retry}`;
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    let active = true;
    void apiClient.get(`/api/reviews/${encodeURIComponent(scanId)}/reading-order`, { params: { page }, signal: controller.signal })
      .then(response => {
        const data = parseReadingOrderComparison(response.data, scanId, page);
        if (active) setResult({ key: requestKey, data });
      }).catch(() => { if (active) setResult({ key: requestKey, error: true }); });
    return () => { active = false; controller.abort(); };
  }, [open, page, requestKey, scanId]);
  const current = result?.key === requestKey ? result : null;
  const data = current?.data;
  const pageCount = Math.min(500, Math.max(data?.source.page_count ?? page, data?.saved.page_count ?? page, 1));
  return <section className="min-w-0 border-b border-[var(--border-primary)] px-4 py-3" aria-label="Reading order comparison">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 className="text-sm font-semibold text-primary">Reading order comparison</h2>
      <button type="button" className="btn-secondary text-sm" aria-expanded={open} aria-controls={panelId} onClick={() => { setOpen(!open); setRetry(value => value + 1); }}>{open ? 'Hide comparison' : 'Show comparison'}</button>
    </div>
    {open && <div id={panelId} className="mt-3 min-w-0 space-y-3">
      <p className="text-sm text-secondary">Compare the original and current saved PDF. Text follows each file’s actual tags, not a proposed reorder. Table structure editing is not included.</p>
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex flex-wrap gap-2" role="group" aria-label="PDF version">
          {(['source', 'saved'] as const).map(value => <button key={value} type="button" className={version === value ? 'btn-primary text-sm' : 'btn-secondary text-sm'} aria-pressed={version === value} onClick={() => setVersion(value)}>{value === 'source' ? 'Original PDF' : 'Saved PDF'}</button>)}
        </div>
        <label className="flex items-center gap-2 text-sm text-primary">Page
          <select aria-label="Comparison page" className="rounded border border-[var(--border-primary)] bg-[var(--surface-primary)] p-2 text-primary" value={page} onChange={event => setPage(Number(event.target.value))} disabled={!data}>
            {Array.from({ length: pageCount }, (_, i) => <option key={i + 1} value={i + 1}>{i + 1}</option>)}
          </select>
        </label>
        <button type="button" className="btn-secondary text-sm" onClick={() => setRetry(value => value + 1)}>Refresh comparison</button>
      </div>
      {!current && <p role="status" className="text-sm text-secondary">Loading verified PDF evidence…</p>}
      {current?.error && <p role="alert" className="text-sm text-secondary">Could not load verified comparison evidence. Refresh to try again.</p>}
      {data && <SnapshotView key={`${requestKey}:${version}:${data[version].sha256}`} snapshot={data[version]} label={version === 'source' ? 'Original PDF' : 'Saved PDF'} />}
    </div>}
  </section>;
}
