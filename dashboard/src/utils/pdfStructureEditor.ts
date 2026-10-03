export type PDFSourceKind = 'original' | 'saved';
export type PDFCloudContext = { kind: 'local' } | { kind: 'cloud'; id: string } | { kind: 'invalid' };

export function parsePDFCloudContext(search: string): PDFCloudContext {
  const ids = new URLSearchParams(search).getAll('cloud_file_id');
  if (ids.length === 0) return { kind: 'local' };
  if (ids.length !== 1 || !/^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$/.test(ids[0])) return { kind: 'invalid' };
  return { kind: 'cloud', id: ids[0].toLowerCase() };
}
export interface PDFEditContext {
  status: 'available' | 'empty' | 'unavailable';
  page_numbers: number[];
  segments: { page_number: number; text: string; source: 'MCID' | 'ActualText' | 'Alt' }[];
  truncated: boolean;
}
export interface PDFEditTarget {
  target_id: string;
  role: string;
  children: string[];
  can_set_heading: boolean;
  can_reorder: boolean;
  can_set_column_headers: boolean;
  context: PDFEditContext;
}
export interface PDFEditTargets {
  precondition: {
    source_kind: PDFSourceKind;
    source_sha256: string;
    expected_artifact_id: string | null;
    state_digest: string;
    cloud_file_id: string | null;
  };
  targets: PDFEditTarget[];
}
export type PDFEditOperation =
  | { kind: 'heading'; target_id: string; level: number }
  | { kind: 'order'; target_id: string; children: string[] }
  | { kind: 'table_column_headers'; target_id: string };

const HASH = /^[0-9a-f]{64}$/;
const ROLE = /^[A-Za-z][A-Za-z0-9]{0,39}$/;
const ORDER_ROLES = new Set(['StructTreeRoot', 'Document', 'Part', 'Sect', 'Div']);
const fail = (): never => { throw new Error('Invalid PDF edit targets'); };
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return fail();
  return value as Record<string, unknown>;
}
function targetId(id: unknown, hash: string): id is string {
  return typeof id === 'string' && id.length <= 256 &&
    (id === `${hash}:root` || new RegExp(`^${hash}:[0-9]+(?:/[0-9]+)*$`).test(id));
}
function context(value: unknown): PDFEditContext {
  const raw = object(value);
  if (typeof raw.status !== 'string' || !['available', 'empty', 'unavailable'].includes(raw.status) ||
    typeof raw.truncated !== 'boolean' || !Array.isArray(raw.page_numbers) ||
    raw.page_numbers.length > 16 || !Array.isArray(raw.segments) || raw.segments.length > 4) return fail();
  const pages = raw.page_numbers;
  if (new Set(pages).size !== pages.length || !pages.every(page => Number.isSafeInteger(page) && page >= 1 && page <= 500)) return fail();
  let count = 0;
  for (const item of raw.segments) {
    const segment = object(item);
    if (!pages.includes(segment.page_number) || typeof segment.text !== 'string' || !segment.text.trim() ||
      typeof segment.source !== 'string' || !['MCID', 'ActualText', 'Alt'].includes(segment.source)) return fail();
    count += Array.from(segment.text).length;
  }
  if (count > 240 || (raw.status === 'available') !== (raw.segments.length > 0) ||
    (raw.status === 'unavailable' && (pages.length > 0 || raw.segments.length > 0))) return fail();
  return raw as unknown as PDFEditContext;
}

export function parsePDFEditTargets(value: unknown, sourceKind: PDFSourceKind, requestedCloudFileId: string | null): PDFEditTargets {
  const envelope = object(value), pre = object(envelope.precondition);
  if (pre.source_kind !== sourceKind || typeof pre.source_sha256 !== 'string' || !HASH.test(pre.source_sha256) ||
    typeof pre.state_digest !== 'string' || !HASH.test(pre.state_digest) ||
    (pre.expected_artifact_id !== null && (typeof pre.expected_artifact_id !== 'string' || !pre.expected_artifact_id || pre.expected_artifact_id.length > 36)) ||
    (pre.cloud_file_id !== null && (typeof pre.cloud_file_id !== 'string' || !pre.cloud_file_id || pre.cloud_file_id.length > 36)) ||
    pre.cloud_file_id !== requestedCloudFileId ||
    (sourceKind === 'saved' && pre.expected_artifact_id === null) ||
    !Array.isArray(envelope.targets) || envelope.targets.length < 2 || envelope.targets.length > 2000) return fail();
  const hash = pre.source_sha256 as string;
  const targets: PDFEditTarget[] = [];
  const seen = new Set<string>();
  for (const item of envelope.targets) {
    const raw = object(item);
    if (!targetId(raw.target_id, hash) || seen.has(raw.target_id) || typeof raw.role !== 'string' || !ROLE.test(raw.role) ||
      !Array.isArray(raw.children) || raw.children.length > 2000 ||
      typeof raw.can_set_heading !== 'boolean' || typeof raw.can_reorder !== 'boolean' ||
      typeof raw.can_set_column_headers !== 'boolean') return fail();
    seen.add(raw.target_id);
    const children = raw.children;
    if (new Set(children).size !== children.length || !children.every(child => targetId(child, hash))) return fail();
    const role = raw.role as string;
    if ((raw.target_id === `${hash}:root`) !== (role === 'StructTreeRoot')) return fail();
    if (raw.can_set_heading !== (['P', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6'].includes(role) && children.length === 0)) return fail();
    if (raw.can_reorder !== (ORDER_ROLES.has(role) && children.length > 1)) return fail();
    if (raw.can_set_column_headers && role !== 'Table') return fail();
    targets.push({ target_id: raw.target_id, role, children, can_set_heading: raw.can_set_heading,
      can_reorder: raw.can_reorder, can_set_column_headers: raw.can_set_column_headers, context: context(raw.context) });
  }
  if (!seen.has(`${hash}:root`)) return fail();
  const referenced = new Set<string>();
  for (const target of targets) {
    target.children.forEach((child, index) => {
      // Structure paths, rather than excerpts, establish each child's identity.
      const parentPath = target.target_id.slice(65);
      const expected = `${hash}:${parentPath === 'root' ? '' : `${parentPath}/`}${index}`;
      if (child !== expected || !seen.has(child) || referenced.has(child)) fail();
      referenced.add(child);
    });
  }
  if (referenced.size !== targets.length - 1 || referenced.has(`${hash}:root`)) return fail();
  return { precondition: pre as unknown as PDFEditTargets['precondition'], targets };
}

export function movePDFChild(children: string[], index: number, direction: -1 | 1): string[] {
  const other = index + direction;
  if (index < 0 || other < 0 || other >= children.length) return children;
  const result = [...children];
  [result[index], result[other]] = [result[other], result[index]];
  return result;
}

export function validPDFEditOperation(data: PDFEditTargets, operation: PDFEditOperation): boolean {
  const target = data.targets.find(item => item.target_id === operation.target_id);
  if (!target || target.context.status !== 'available') return false;
  if (operation.kind === 'heading') return target.can_set_heading && Number.isInteger(operation.level) &&
    operation.level >= 1 && operation.level <= 6 && target.role !== `H${operation.level}`;
  if (operation.kind === 'table_column_headers') return target.can_set_column_headers;
  return target.can_reorder && operation.children.length === target.children.length &&
    operation.children.some((child, index) => child !== target.children[index]) &&
    new Set(operation.children).size === target.children.length &&
    operation.children.every(child => target.children.includes(child) && data.targets.find(item => item.target_id === child)?.context.status === 'available');
}

export function parsePDFEditSave(value: unknown, previous: PDFEditTargets['precondition']): { artifact_id: string; sha256: string } {
  const raw = object(value);
  if (typeof raw.artifact_id !== 'string' || !raw.artifact_id || raw.artifact_id.length > 36 ||
    raw.artifact_id === previous.expected_artifact_id || typeof raw.sha256 !== 'string' || !HASH.test(raw.sha256) ||
    raw.sha256 === previous.source_sha256 || raw.review_status !== 'pending' || raw.needs_review !== true) return fail();
  return { artifact_id: raw.artifact_id, sha256: raw.sha256 as string };
}
