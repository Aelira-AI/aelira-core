/**
 * CSV formatting utilities for scan results export.
 * RFC 4180 compliant with field normalization across scan types.
 */

/**
 * Escape a field value for CSV per RFC 4180.
 */
export function escapeField(value?: unknown): string {
  const str = String(value ?? '')
  if (str.includes(',') || str.includes('"') || str.includes('\n') || str.includes('\r')) {
    return `"${str.replaceAll('"', '""')}"`
  }

  return str
}

/**
 * Normalize issue fields across different scan types.
 * PDF/document scans use `message`, web/axe-core uses `description`.
 */
export function normalizeIssue(item: any): {
  element: string
  issue: string
  rule: string
  severity: string
} {
  return {
    element: item.element || item.html || item.selector || '',
    issue: item.message || item.description || item.help || '',
    rule: item.rule || item.criterion || item.id || '',
    severity: item.severity || item.impact || '',
  }
}

const ISSUE_HEADERS = ['file', 'issue', 'severity', 'rule', 'element']
const HISTORY_HEADERS = ['scan_id', 'date', 'file', 'issue', 'severity', 'rule', 'element']

/**
 * Format individual scan issues to CSV (for scan commands).
 */
export function formatIssuesToCsv(issues: any[], filename: string): string {
  const rows = [ISSUE_HEADERS.join(',')]
  for (const item of issues) {
    const n = normalizeIssue(item)
    rows.push([
      escapeField(filename),
      escapeField(n.issue),
      escapeField(n.severity),
      escapeField(n.rule),
      escapeField(n.element),
    ].join(','))
  }

  return rows.join('\n')
}

/**
 * Format scan history to CSV (for export command).
 * Each scan object should have: scan_id, created_at, filename, issues[]
 */
export function formatScanHistoryToCsv(scans: any[]): string {
  const rows = [HISTORY_HEADERS.join(',')]
  for (const scan of scans) {
    const scanId = scan.scan_id || scan.id || ''
    const date = scan.created_at ? new Date(scan.created_at).toISOString().split('T')[0] : ''
    const filename = scan.filename || scan.file_name || ''
    const issues = scan.issues || []
    for (const item of issues) {
      const n = normalizeIssue(item)
      rows.push([
        escapeField(scanId),
        escapeField(date),
        escapeField(filename),
        escapeField(n.issue),
        escapeField(n.severity),
        escapeField(n.rule),
        escapeField(n.element),
      ].join(','))
    }
  }

  return rows.join('\n')
}
