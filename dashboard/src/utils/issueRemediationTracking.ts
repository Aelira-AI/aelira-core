const TRACKED_SCAN_PARAM = 'batch_scan_id';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function validUniqueIds(ids: string[]): string[] {
  return [...new Set(ids.filter((id) => UUID.test(id)).map((id) => id.toLowerCase()))].slice(0, 50);
}

/** URL parameters select documents to inspect, never prove a queue receipt or job outcome. */
export function parseTrackedIssueScanIds(params: URLSearchParams): string[] {
  return validUniqueIds(params.getAll(TRACKED_SCAN_PARAM));
}

export function missingTrackedIssueScanIds(trackedIds: string[], listedIds: string[]): string[] {
  const listed = new Set(listedIds.map((id) => id.toLowerCase()));
  return validUniqueIds(trackedIds).filter((id) => !listed.has(id));
}

export function authorizedTrackedIssueScanIds(params: URLSearchParams, scans: { id: string }[]): string[] {
  const authorizedIds = new Map(scans.map((scan) => [scan.id.toLowerCase(), scan.id]));
  return parseTrackedIssueScanIds(params).flatMap((id) => {
    const authorizedId = authorizedIds.get(id);
    return authorizedId ? [authorizedId] : [];
  });
}

export function withTrackedIssueScanIds(params: URLSearchParams, ids: string[]): URLSearchParams {
  const updated = new URLSearchParams(params);
  updated.delete(TRACKED_SCAN_PARAM);
  for (const id of validUniqueIds(ids)) updated.append(TRACKED_SCAN_PARAM, id);
  return updated;
}
