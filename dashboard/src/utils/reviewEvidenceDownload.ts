export type ReviewEvidenceFormat = 'json' | 'csv' | 'pdf';

const CONTENT_TYPES: Record<ReviewEvidenceFormat, string> = {
  json: 'application/json',
  csv: 'text/csv',
  pdf: 'application/pdf',
};

function safeServerFilename(disposition: string | null | undefined): string | null {
  if (!disposition) return null;

  const encodedMatch = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  const plainMatch = disposition.match(/filename="?([^";]+)"?/i);
  let candidate = plainMatch?.[1];
  if (encodedMatch?.[1]) {
    try {
      candidate = decodeURIComponent(encodedMatch[1]);
    } catch {
      return null;
    }
  }

  if (!candidate || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(candidate)) {
    return null;
  }
  return candidate;
}

export function evidenceFilename(
  disposition: string | null | undefined,
  scanId: string,
  format: ReviewEvidenceFormat,
): string {
  const serverFilename = safeServerFilename(disposition);
  if (serverFilename) return serverFilename;

  const safeScanId = scanId.replace(/[^A-Za-z0-9_-]/g, '').slice(0, 64) || 'scan';
  const prefix = format === 'pdf' ? 'accessibility-review-evidence' : 'audit';
  return `${prefix}-${safeScanId}.${format}`;
}

export function evidenceContentType(
  contentType: string | null | undefined,
  format: ReviewEvidenceFormat,
): string {
  return contentType || CONTENT_TYPES[format];
}
