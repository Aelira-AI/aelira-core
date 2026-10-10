import React, { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Button } from '../ui/Button';
import { approveManagedArtifact, downloadManagedArtifact, getManagedArtifact, getCurrentWorkingArtifact, rebuildReviewedPDF, writeBackManagedArtifact } from '../../api/managedArtifacts';
import type { ManagedArtifactMetadata } from '../../api/managedArtifacts';
import { useAbortableRequestOwner } from '../../hooks/useAbortableRequestOwner';

const BLOCKERS: Record<string, string> = {
  fixes_pending_review: 'Approve, edit or reject the pending changes first.',
  verification_not_passed: 'The saved-file verification requirements have not passed.',
  no_fixes: 'No changes are available to approve.',
  no_accepted_fix: 'No change has been accepted.',
  fix_approval_digest_invalid: 'A review decision changed. Refresh and review the current changes.',
  reviewed_output_required: 'Rebuild the working file to apply edited changes and remove rejected changes.',
  fix_review_digest_invalid: 'The current changes need a new valid review record.',
  fix_occurrence_identity_invalid: 'The change identity could not be verified. Run remediation again.',
  output_membership_unrecorded: 'The working file does not record which reviewed changes it contains. Rebuild a reviewed PDF or run remediation again before approval.',
  source_hash_unavailable: 'The original scan has no verified source fingerprint. Rescan the source and remediate it before approval.',
  source_changed_since_scan: 'The source changed since its scan. Rescan the current source and remediate it before approval.',
  saved_file_verification_unavailable: 'The working file has no valid saved-file verification. Run remediation again and review its result.',
  applied_fix_membership_unavailable: 'The changes could not be bound to this working file. Run remediation again before approval.',
  reviewed_changes_not_in_output: 'The working file contains a rejected change or different reviewed content. Create a new working file from the accepted changes before approval.',
};

/** Viewing or downloading a candidate never grants permission to publish it. */
export function ArtifactReviewPanel({ scanId, scanType, cloudFileId, resultPath: suppliedResultPath, refreshToken, editing, canRebuild, rebuildSupported = true }: {
  scanId: string; scanType: string; cloudFileId: string | null; resultPath?: string; refreshToken: number; editing: boolean; canRebuild: boolean; rebuildSupported?: boolean;
}): React.ReactElement {
  const navigate = useNavigate();
  const [loaded, setLoaded] = useState<{ key: string; artifact: ManagedArtifactMetadata | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmedKey, setConfirmedKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const requestKey = `${scanId}:${scanType}:${cloudFileId ?? ''}:${refreshToken}:${reload}`;
  const owner = useAbortableRequestOwner(requestKey);
  const resultPath = suppliedResultPath ?? `/remediate/${encodeURIComponent(scanId)}${cloudFileId ? `?cloud_file_id=${encodeURIComponent(cloudFileId)}` : ''}`;
  const artifact = loaded?.key === requestKey ? loaded.artifact : null;
  const confirmed = artifact !== null && confirmedKey === `${requestKey}:${artifact.sha256}`;
  const manualEdits = artifact?.has_manual_edits === true || artifact?.reviewed_rebuild_blocker === 'manual_pdf_edits_require_review';
  const rebuildBlocked = manualEdits || artifact?.reviewed_rebuild_available === false;

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      const id = await getCurrentWorkingArtifact(scanId, cloudFileId, controller.signal);
      if (!id) { if (!controller.signal.aborted) { setLoaded({ key: requestKey, artifact: null }); setError(null); setBusy(false); } return; }
      const current = await getManagedArtifact(scanId, id, controller.signal);
      if (!controller.signal.aborted) { setLoaded({ key: requestKey, artifact: current }); setError(null); setBusy(false); }
    })().catch(() => { if (!controller.signal.aborted) { setBusy(false); setError('The current working file could not be loaded. Refresh before approving or downloading it.'); } });
    return () => controller.abort();
  }, [scanId, scanType, cloudFileId, requestKey]);

  async function download(): Promise<void> {
    if (!artifact || busy) return;
    const attempt = owner.begin();
    setBusy(true); setError(null);
    try {
      const blob = await downloadManagedArtifact(scanId, artifact.id);
      if (!owner.isCurrent(attempt)) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a'); link.href = url; link.download = artifact.filename;
      document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(url);
    } catch { if (owner.isCurrent(attempt)) setError('This working file could not be downloaded. Its availability or review requirements may have changed.'); }
    finally { if (owner.finish(attempt)) setBusy(false); }
  }

  async function approve(): Promise<void> {
    if (!artifact || !artifact.can_approve || !confirmed || busy || editing) return;
    const attempt = owner.begin();
    setBusy(true); setError(null);
    try { await approveManagedArtifact(scanId, artifact.id); if (owner.isCurrent(attempt)) setReload(value => value + 1); }
    catch { if (owner.isCurrent(attempt)) { setError('Approval was refused. Refresh the current file and complete its review requirements before retrying.'); setConfirmedKey(null); } }
    finally { if (owner.finish(attempt)) setBusy(false); }
  }

  async function rebuild(): Promise<void> {
    if (!rebuildSupported || !canRebuild || rebuildBlocked || busy || editing || loaded?.key !== requestKey) return;
    const attempt = owner.begin();
    setBusy(true); setError(null);
    try { await rebuildReviewedPDF(scanId); if (owner.isCurrent(attempt)) navigate(resultPath); }
    catch { if (owner.isCurrent(attempt)) setError('The reviewed rebuild could not be confirmed. Check the remediation status before retrying.'); }
    finally { if (owner.finish(attempt)) setBusy(false); }
  }

  async function writeBack(): Promise<void> {
    if (!artifact || artifact.review_status !== 'approved' || artifact.writeback_available !== true || busy || editing) return;
    const attempt = owner.begin();
    setBusy(true); setError(null); setNotice(null);
    try { await writeBackManagedArtifact(scanId, artifact.id); if (owner.isCurrent(attempt)) { setNotice('Write-back is queued. The server will recheck approval before uploading the remediated copy.'); setReload(value => value + 1); } }
    catch { if (owner.isCurrent(attempt)) setError('Write-back could not be confirmed. Refresh the file and check its approval and connection before retrying.'); }
    finally { if (owner.finish(attempt)) setBusy(false); }
  }

  return <section className="border-b border-[var(--border-primary)] bg-[var(--surface-secondary)] px-4 py-4 sm:px-6" aria-labelledby="working-file-heading">
    <h2 id="working-file-heading" className="text-lg font-semibold text-primary">Working file and publication approval</h2>
    <p className="mt-1 text-sm text-secondary">Download the improved file for review and further manual work. Approving this file is a separate decision; it does not certify accessibility or write it back automatically.</p>
    {error && <p className="mt-3 text-sm text-[var(--feature-danger-content)]" role="alert">{error}</p>}
    {notice && <p className="mt-3 text-sm text-primary" role="status">{notice}</p>}
    {scanType.toLowerCase() === 'pdf' && rebuildSupported && <div className="mt-3">
      <Button variant="secondary" size="sm" disabled={!canRebuild || rebuildBlocked || busy || editing || loaded?.key !== requestKey} onClick={() => void rebuild()}>Rebuild from reviewed changes</Button>
      <p className="mt-1 text-sm text-secondary">{manualEdits
        ? 'This working file contains manual PDF edits. Rebuilding from the original would discard them, so review or continue editing the current file instead.'
        : rebuildBlocked ? 'A reviewed rebuild is unavailable for the current working file. Review its remaining requirements before continuing.'
        : 'Complete the pending decisions first. Rebuilding applies accepted edits and leaves rejected changes out of a new working file, which must pass its saved-file checks.'}</p>
    </div>}
    {artifact ? <>
      <p className="mt-3 text-sm text-primary">{artifact.filename} · {artifact.review_status === 'approved' ? 'Approved for publication' : artifact.review_status === 'rejected' ? 'Rejected for publication' : 'Publication approval pending'}</p>
      <div className="mt-3 flex flex-wrap gap-3">
        <Button variant="secondary" size="sm" onClick={() => void download()} disabled={busy || editing}>Download improved working file</Button>
        <Link className="btn-secondary text-sm" to={resultPath}>{suppliedResultPath ? 'View scan findings' : 'View remaining findings'}</Link>
        <Button variant="secondary" size="sm" onClick={() => setReload(value => value + 1)} disabled={busy || editing}>Refresh file status</Button>
        {artifact.review_status === 'approved' && artifact.writeback_available === true && <Button variant="primary" size="sm" onClick={() => void writeBack()} disabled={busy || editing}>Write back approved file</Button>}
      </div>
      {artifact.writeback_available === true && <p className="mt-2 text-sm text-secondary">This action uploads the approved file using a remediated-copy filename. Same-name handling depends on the connected provider.</p>}
      {artifact.review_status === 'pending' && <>
        {artifact.approval_blockers.length > 0 && <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-secondary">{artifact.approval_blockers.map(code => <li key={code}>{BLOCKERS[code] ?? 'A saved-file or specialist review requirement remains. Complete the review before publication.'}</li>)}</ul>}
        <label className="mt-4 flex items-start gap-2 text-sm text-primary"><input type="checkbox" checked={confirmed} disabled={busy || editing || !artifact.can_approve} onChange={event => setConfirmedKey(event.target.checked ? `${requestKey}:${artifact.sha256}` : null)} className="mt-1" />I have reviewed this working file, its changes and the remaining findings.</label>
        <Button variant="primary" size="sm" className="mt-3" disabled={busy || editing || !confirmed || !artifact.can_approve} onClick={() => void approve()}>{busy ? 'Please wait' : 'Approve this file for publication'}</Button>
      </>}
    </> : !error && <p className="mt-3 text-sm text-secondary" role="status">{loaded?.key === requestKey ? 'No current working file is available here yet. Review the remediation result for its status.' : 'Loading the current working file…'}</p>}
  </section>;
}
