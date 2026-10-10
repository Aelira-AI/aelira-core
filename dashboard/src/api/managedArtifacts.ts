import { apiClient } from './client';

export interface ManagedArtifactMetadata {
  id: string;
  scan_id: string;
  filename: string;
  sha256: string;
  review_status: 'pending' | 'approved' | 'rejected';
  approval_blockers: string[];
  can_approve: boolean;
  writeback_available?: boolean;
  writeback_provider?: 'google' | 'microsoft' | 'blackboard' | null;
  has_manual_edits?: boolean;
  reviewed_rebuild_available?: boolean;
  reviewed_rebuild_blocker?: string | null;
}

export function artifactReviewPath(scanId: string, artifactId: string): string {
  return `/education/scans/${encodeURIComponent(scanId)}/artifacts/${encodeURIComponent(artifactId)}`;
}

export async function getManagedArtifact(scanId: string, artifactId: string, signal?: AbortSignal): Promise<ManagedArtifactMetadata> {
  const { data } = await apiClient.get<ManagedArtifactMetadata>(artifactReviewPath(scanId, artifactId), { signal });
  if (data.id !== artifactId || data.scan_id !== scanId) throw new Error('Artifact response does not match this review.');
  return data;
}

export async function getCurrentWorkingArtifact(scanId: string, cloudFileId: string | null, signal: AbortSignal): Promise<string | null> {
  const { data } = await apiClient.get<{ scan_id: string; cloud_file_id: string | null; artifact_id: string | null }>(`/api/reviews/${encodeURIComponent(scanId)}/working-artifact`, {
    params: cloudFileId ? { cloud_file_id: cloudFileId } : undefined, signal,
  });
  if (data.scan_id !== scanId || data.cloud_file_id !== cloudFileId) throw new Error('Working-file context does not match this review.');
  return data.artifact_id;
}

export async function approveManagedArtifact(scanId: string, artifactId: string): Promise<void> {
  await apiClient.post(`${artifactReviewPath(scanId, artifactId)}/approve`);
}

export async function downloadManagedArtifact(scanId: string, artifactId: string): Promise<Blob> {
  const { data } = await apiClient.get<Blob>(`${artifactReviewPath(scanId, artifactId)}/download`, { responseType: 'blob' });
  return data;
}

export async function writeBackManagedArtifact(scanId: string, artifactId: string): Promise<void> {
  const { data, status } = await apiClient.post<{ status: string; artifact_id: string; job_id: string }>(`${artifactReviewPath(scanId, artifactId)}/writeback`, { create_new_version: true });
  if (status !== 202 || data.status !== 'queued' || data.artifact_id !== artifactId || !data.job_id) throw new Error('Write-back was not confirmed.');
}

export async function rebuildReviewedPDF(scanId: string): Promise<void> {
  const { data, status } = await apiClient.post<{ job_id: string; scan_id: string }>(`/education/pdf/remediate/${encodeURIComponent(scanId)}`, undefined, { headers: { Prefer: 'respond-async' } });
  if (status !== 202 || data.scan_id !== scanId || !data.job_id) throw new Error('Reviewed rebuild was not confirmed.');
}
