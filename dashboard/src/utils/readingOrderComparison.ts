export interface ReadingOrderBlock {
  index: number;
  text: string;
  source: string;
  bbox: [number, number, number, number] | null;
}
export interface ReadingOrderSnapshot {
  status: 'available' | 'unavailable';
  reason: string | null;
  sha256: string | null;
  page_count: number;
  page_number: number;
  width: number | null;
  height: number | null;
  preview_png_base64: string | null;
  blocks: ReadingOrderBlock[];
  unpositioned_count: number;
}
export interface ReadingOrderComparisonData {
  scan_id: string;
  page_number: number;
  artifact_id: string | null;
  source: ReadingOrderSnapshot;
  saved: ReadingOrderSnapshot;
}

function invalid(): never { throw new Error('Invalid reading-order evidence'); }
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid();
  return value as Record<string, unknown>;
}
function snapshot(value: unknown, page: number): ReadingOrderSnapshot {
  const s = record(value);
  if (!['available', 'unavailable'].includes(String(s.status)) || s.page_number !== page
      || !Number.isSafeInteger(s.page_count) || Number(s.page_count) < 0
      || (s.sha256 !== null && (typeof s.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(s.sha256)))
      || (s.reason !== null && typeof s.reason !== 'string')
      || !Array.isArray(s.blocks) || s.blocks.length > 2000) return invalid();
  const width = s.width, height = s.height;
  const geometry = typeof width === 'number' && Number.isFinite(width) && width > 0 && width <= 20000
    && typeof height === 'number' && Number.isFinite(height) && height > 0 && height <= 20000;
  if (!geometry && (width !== null || height !== null)) return invalid();
  if (s.preview_png_base64 !== null && (!geometry || typeof s.preview_png_base64 !== 'string'
    || s.preview_png_base64.length > 12_000_000 || !/^iVBORw0KGgo[A-Za-z0-9+/=]*$/.test(s.preview_png_base64))) return invalid();
  let textLength = 0;
  for (const [i, raw] of s.blocks.entries()) {
    const b = record(raw);
    if (b.index !== i + 1 || typeof b.text !== 'string' || !b.text.trim() || typeof b.source !== 'string') return invalid();
    textLength += Array.from(b.text).length;
    if (b.bbox !== null) {
      if (!geometry || !Array.isArray(b.bbox) || b.bbox.length !== 4 || !b.bbox.every(n => typeof n === 'number' && Number.isFinite(n))) return invalid();
      const [x0, y0, x1, y1] = b.bbox;
      if (x0 < 0 || y0 < 0 || x1 <= x0 || y1 <= y0 || x1 > Number(width) || y1 > Number(height)) return invalid();
    }
  }
  if (textLength > 200_000 || s.unpositioned_count !== s.blocks.filter(b => record(b).bbox === null).length) return invalid();
  if (s.status === 'available' && (!s.sha256 || !geometry || !s.blocks.length || page > Number(s.page_count))) return invalid();
  if (s.status === 'available' && Number(s.page_count) > 500) return invalid();
  if (s.status === 'unavailable' && s.blocks.length) return invalid();
  return s as unknown as ReadingOrderSnapshot;
}

export function parseReadingOrderComparison(value: unknown, scanId: string, page: number): ReadingOrderComparisonData {
  const data = record(value);
  if (data.scan_id !== scanId || data.page_number !== page || (data.artifact_id !== null && typeof data.artifact_id !== 'string')) return invalid();
  return { scan_id: scanId, page_number: page, artifact_id: data.artifact_id as string | null,
    source: snapshot(data.source, page), saved: snapshot(data.saved, page) };
}

export function readingOrderReason(reason: string | null): string {
  const messages: Record<string, string> = {
    untagged_pdf: 'This PDF has no tagged reading order. Visual placement is not a substitute for tags.',
    unresolved_structure: 'The tagged structure could not be fully resolved. No reading order is asserted.',
    no_tagged_content: 'No tagged text could be resolved on this page.',
    invalid_page: 'This version does not contain the selected page.',
    no_saved_artifact: 'No current saved PDF is available for comparison.',
    saved_artifact_unavailable: 'The current saved PDF is unavailable.',
    saved_artifact_expired: 'The saved PDF has expired. Run remediation again to create a current version.',
    saved_artifact_integrity_failed: 'The saved PDF could not be verified against its recorded checksum.',
    source_metadata_unavailable: 'Verified original-file metadata is unavailable.',
    source_missing_or_unsafe: 'The original PDF is unavailable for a verified comparison.',
    source_integrity_failed: 'The original PDF could not be verified against its recorded checksum.',
    encrypted_pdf: 'This PDF is encrypted and cannot be inspected here.',
    invalid_pdf: 'This file could not be read as a PDF.',
    unsupported_pdf: 'Reading-order comparison is available for supported PDFs only.',
    unsupported_format: 'Reading-order comparison is available for PDFs only.',
    file_too_large: 'This file exceeds the safe comparison size limit.',
    limit_exceeded: 'This page exceeds the safe inspection limits. No partial order is asserted.',
  };
  return messages[reason ?? ''] ?? 'Reading-order evidence is unavailable for this version.';
}
