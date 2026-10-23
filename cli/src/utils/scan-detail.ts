/**
 * Read the fields a console or CSV summary needs from the response of
 * GET /education/scans/{scan_id} (src/api/education/scan_history_routes.py).
 *
 * That route nests the file details under `scan` and the findings under
 * `scan.result`, with severity counts in `scan.result.summary`. `result` is
 * null until a result exists, and the score and counts can be null when the
 * API cannot attribute them.
 */

export interface IssueCounts {
  critical: number
  high: number
  low: number
  medium: number
  total: number
}

export interface ScanSummary {
  complianceScore: null | number
  fileName: null | string
  fileSizeBytes: null | number
  issueCounts: IssueCounts | null
  issues: any[]
  ocrUsed: boolean
  pages: null | number
  scanId: null | string
}

function numberOrNull(value: unknown): null | number {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function stringOrNull(value: unknown): null | string {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function readCounts(summary: any): IssueCounts | null {
  if (!summary || typeof summary !== 'object') return null
  const critical = numberOrNull(summary.critical)
  const high = numberOrNull(summary.high)
  const medium = numberOrNull(summary.medium)
  const low = numberOrNull(summary.low)
  if (critical === null || high === null || medium === null || low === null) return null
  const total = numberOrNull(summary.total) ?? critical + high + medium + low
  return { critical, high, low, medium, total }
}

export function summarizeScanDetail(detail: any): ScanSummary {
  const scan = detail?.scan && typeof detail.scan === 'object' ? detail.scan : {}
  const result = scan.result && typeof scan.result === 'object' ? scan.result : {}

  return {
    complianceScore: numberOrNull(result.compliance_score),
    fileName: stringOrNull(scan.file_name),
    fileSizeBytes: numberOrNull(scan.file_size_bytes),
    issueCounts: readCounts(result.summary),
    issues: Array.isArray(result.issues) ? result.issues : [],
    ocrUsed: result.ocr_used === true,
    pages: numberOrNull(scan.pages),
    scanId: stringOrNull(scan.scan_id),
  }
}

export function formatFileSize(bytes: null | number): string {
  if (bytes === null) return 'N/A'
  const mib = 1024 * 1024
  return bytes < mib ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / mib).toFixed(2)} MB`
}
