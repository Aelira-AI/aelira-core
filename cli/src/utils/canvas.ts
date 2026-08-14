import { getConfigValue } from './config.js'

/**
 * Department id for a Canvas request: the explicit flag wins, otherwise the
 * configured default, otherwise undefined (the API treats it as optional).
 */
export async function resolveDepartment(flagValue?: string): Promise<string | undefined> {
  if (flagValue) return flagValue
  return getConfigValue('department')
}

/**
 * Canvas file ids out of a bulk-scan response.
 *
 * `CanvasBulkScanResponse` is `{jobs: [{file_id, file_name, job_id}], total,
 * skipped}` (backend/src/api/canvas_scan_routes.py:88-93). There is no flat
 * `file_ids` key — reading one yields an empty list on every successful scan,
 * which silently disables `--wait`.
 */
export function extractFileIds(data: unknown): string[] {
  const jobs = (data as { jobs?: unknown })?.jobs
  if (!Array.isArray(jobs)) return []
  return jobs
    .map((job) => (job as { file_id?: unknown })?.file_id)
    .filter((id): id is string => typeof id === 'string' && id.length > 0)
}

/**
 * Query for `GET /canvas/courses/{course_id}/scan-status`.
 *
 * `file_ids` is a single comma-separated string, not a repeated parameter:
 * the endpoint declares `file_ids: str = Query(...)` and splits on comma
 * server-side (backend/src/api/canvas_scan_routes.py:478, 495). Sending it
 * repeated makes Starlette's MultiDict resolve to the last value alone, so the
 * poll would track exactly one file.
 */
export function buildScanStatusQuery(fileIds: string[]): Record<string, string> {
  return { file_ids: fileIds.join(',') }
}
