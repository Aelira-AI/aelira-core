/** Synthetic, local-only API for the real result/review routes. No provider calls. */
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { Buffer } from 'node:buffer';

const scanId = '11111111-1111-4111-8111-111111111111';
const artifactId = '22222222-2222-4222-8222-222222222222';
const cloudFileId = '55555555-5555-4555-8555-555555555555';
const googleCloudFileId = '66666666-6666-4666-8666-666666666666';
let artifactStatus = 'pending';
let manualEdits = false;
let rebuildRequired = false;
let selectedProvider = 'local';
const tinyPDF = (() => {
  const objects = ['<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R] /Count 1 >>', '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 500] >>'];
  let content = '%PDF-1.4\n'; const offsets = [0];
  objects.forEach((object, index) => { offsets.push(content.length); content += `${index + 1} 0 obj\n${object}\nendobj\n`; });
  const xref = content.length;
  content += `xref\n0 4\n0000000000 65535 f \n${offsets.slice(1).map(offset => `${String(offset).padStart(10, '0')} 00000 n \n`).join('')}trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(content);
})();
const issues = [
  { id: 'finding-0', severity: 'high', page_number: 1, description: 'Image description needs review' },
  { id: 'finding-1', severity: 'medium', page_number: 2, description: 'Figure description needs review' },
  { id: 'finding-2', severity: 'medium', page_number: 3, description: 'Document language was missing' },
  { id: 'finding-3', severity: 'medium', page_number: 4, description: 'Link label was missing' },
  { id: 'finding-4', severity: 'high', page_number: 5, description: 'Decorative image needs manual artifact classification' },
  { id: 'finding-5', severity: 'medium', page_number: 6, description: 'Reading order needs manual work' },
];
const fixes = issues.slice(0, 4).map((issue, index) => ({ ...issue,
  id: `fix-${index}`, category: index < 2 ? 'alt_text' : index === 2 ? 'document_language' : 'link_text', fix_method: index < 2 ? 'ai_vision' : 'rule',
  confidence: null, needs_review: index < 2, review_status: index < 2 ? 'pending' : 'auto_approved',
  original_content: 'No description', fixed_content: index < 2 ? 'Synthetic proposal for visual comparison.' : 'Source-bound deterministic change',
}));
const job = {
  job_id: '33333333-3333-4333-8333-333333333333', scan_id: scanId, status: 'completed', progress: 100,
  status_url: '/education/remediation/jobs/33333333-3333-4333-8333-333333333333',
  fixed_count: 4, manual_count: 2, failed_count: 0, skipped_count: 0, withheld_count: 0, outcome_unreported_count: 0, total_issues: 6, remaining_count: 2,
  original_score: 59.7, remediated_score: null, score_verified: false, score_verification_reason: 'baseline_mismatch',
  fresh_score_comparison: { method_version: 'pdf-strict-v1', source_sha256: 'a'.repeat(64), output_sha256: 'b'.repeat(64), source_score: 57.9, output_score: 83 },
  human_review_required: true, artifact_id: artifactId, download_available: true,
  download_url: '/education/remediation/jobs/33333333-3333-4333-8333-333333333333/download',
  issue_outcomes: issues.map((issue, index) => ({ source_index: index, source_index_scope: 'original_scan', issue_id: issue.id,
    status: index < 4 ? 'fixed' : 'manual',
    ...(index < 4 ? { verification_passed: true, verification_scope: 'saved_file_finding', needs_review: index < 2 } : {
      reason: index === 4 ? 'This image is classified as decorative. An empty description alone does not mark it as an artifact.' : index > 4 ? 'The candidate reading order did not pass its saved-file check and was rolled back.' : 'The source requires manual correction or independent validation.',
      next_step: 'Inspect and correct this finding in the source or a reviewed PDF editor, then rescan.', attempt: 'not_applied',
    }),
  })),
};

export default defineConfig({
  plugins: [react(), { name: 'local-synthetic-review-api', configureServer(server) {
    server.middlewares.use((request, response, next) => {
      const url = new URL(request.url || '/', 'http://localhost');
      if (!url.pathname.startsWith('/fixture-api/')) return next();
      const path = url.pathname.slice('/fixture-api'.length);
      const send = (data: unknown, status = 200) => { response.writeHead(status, { 'content-type': 'application/json' }); response.end(JSON.stringify(data)); };
      if (path === '/qa/reset' && request.method === 'POST') { fixes.forEach((fix,index) => { fix.review_status = index < 2 ? 'pending' : 'auto_approved'; }); artifactStatus = 'pending'; rebuildRequired = false; manualEdits = url.searchParams.get('manual') === 'true'; return send({ status: 'reset' }); }
      if (path === '/auth/session/validate') return send({ valid: true, auth_method: 'session', user: { id: 'fixture-user', email: 'fixture@example.edu', name: 'Synthetic local review', role: 'faculty' }, department: { id: 'fixture-department', name: 'Synthetic local review', tier: 'department' } });
      if (path === '/brightspace/courses') return send([{ org_unit_id: 1, name: 'Synthetic Brightspace course' }]);
      if (path === '/brightspace/content/courses/1/status') return send({ org_unit_id: 1, total_items: 1, scanned_items: 1, average_compliance: 59.7, items: [{ cloud_file_id: cloudFileId, title: 'Synthetic PDF with partial improvements', content_type: 'pdf', compliance_score: 59.7, issue_count: 6, writeback_status: 'approved', has_remediated_version: true, approval_eligible: false, remediation_origin: 'manual', module_path: 'Synthetic module' }] });
      if (path === '/brightspace/content/batch-writeback') return send({ written_count: 0, failed_count: 0, stale_count: 0, skipped_count: 1, errors: ['Managed file write-back unavailable; download and upload manually'] });
      if (path === `/brightspace/content/${cloudFileId}/diff`) return send({ cloud_file_id: cloudFileId, scan_id: scanId, scan_type: 'pdf', content_type: 'pdf', title: 'Synthetic PDF with partial improvements', original_html: '', remediated_html: '', issues_fixed: 4, issues_remaining: 2, issues: [] });
      if (path === `/education/scans/${scanId}`) return send({ id: scanId, file_name: 'Synthetic review example.pdf', scan_type: 'pdf', compliance_score: 59.7, issues });
      if (path.endsWith('/remediation/latest') || path === job.status_url) return send(job);
      if (path === `/api/reviews/${scanId}/working-artifact`) {
        const selectedCloud = url.searchParams.get('cloud_file_id');
        selectedProvider = selectedCloud === googleCloudFileId ? 'google' : selectedCloud === cloudFileId ? 'brightspace' : 'local';
        return send({ scan_id: scanId, cloud_file_id: selectedCloud, artifact_id: artifactId });
      }
      if (path === `/api/reviews/${scanId}`) return send({ scan_id: scanId, file_name: 'Synthetic review example.pdf', scan_type: 'pdf', status: fixes.some(fix => fix.review_status === 'pending') ? 'pending' : 'approved', preview_available: false, fixes, visual_analyses: [], matterhorn_total: 0, matterhorn_passed: 0, matterhorn_failed: 0, matterhorn_warnings: 0, validator_result: 'not_run' });
      if (path === `/api/reviews/${scanId}/batch` && request.method === 'POST') {
        const pending = fixes.filter(fix => fix.review_status === 'pending'); pending.forEach(fix => { fix.review_status = 'approved'; });
        return send({ status: 'ok', affected: pending.length });
      }
      const fixMatch = path.match(/\/fixes\/(fix-\d+)$/);
      if (fixMatch && request.method === 'POST') {
        let body = ''; request.on('data', chunk => { body += chunk; }); request.on('end', () => {
          const decision = JSON.parse(body); const action = decision.action; const fix = fixes.find(item => item.id === fixMatch[1]);
          if (!fix) return send({}, 404);
          fix.review_status = action === 'reject' ? 'rejected' : 'approved';
          if (action === 'edit') fix.fixed_content = decision.edited_content;
          if (action === 'edit' || action === 'reject') rebuildRequired = true;
          artifactStatus = 'pending'; return send({ review_status: fix.review_status });
        }); return;
      }
      if (path === `/education/scans/${scanId}/artifacts/${artifactId}/approve`) {
        if (rebuildRequired || fixes.some(fix => fix.review_status === 'pending')) return send({}, 409);
        artifactStatus = 'approved'; return send({ id: artifactId, review_status: artifactStatus });
      }
      if (path === `/education/pdf/remediate/${scanId}` && request.method === 'POST') {
        if (manualEdits || fixes.some(fix => fix.review_status === 'pending')) return send({}, 409);
        rebuildRequired = false; artifactStatus = 'pending';
        return send({ job_id: job.job_id, scan_id: scanId }, 202);
      }
      if (path.endsWith('/download')) { response.writeHead(200, { 'content-type': 'application/pdf', 'content-disposition': 'attachment; filename="synthetic-working-file.pdf"' }); response.end(tinyPDF); return; }
      if (path === `/education/scans/${scanId}/artifacts/${artifactId}/writeback`) {
        if (artifactStatus !== 'approved' || selectedProvider !== 'google') return send({}, 409);
        return send({ status: 'queued', artifact_id: artifactId, job_id: '44444444-4444-4444-8444-444444444444' }, 202);
      }
      if (path === `/education/scans/${scanId}/artifacts/${artifactId}`) return send({ id: artifactId, scan_id: scanId, filename: 'Synthetic improved working file.pdf', sha256: 'b'.repeat(64), review_status: artifactStatus, approval_blockers: [...(fixes.some(fix => fix.review_status === 'pending') ? ['fixes_pending_review'] : []), ...(rebuildRequired ? ['reviewed_output_required'] : [])], can_approve: artifactStatus === 'pending' && !rebuildRequired && !fixes.some(fix => fix.review_status === 'pending'), writeback_available: artifactStatus === 'approved' && selectedProvider === 'google', writeback_provider: selectedProvider === 'google' ? 'google' : null, has_manual_edits: manualEdits, reviewed_rebuild_available: !manualEdits, reviewed_rebuild_blocker: manualEdits ? 'manual_pdf_edits_require_review' : null });
      return send({});
    });
  } }],
  define: { 'import.meta.env.VITE_API_URL': JSON.stringify('/fixture-api') },
  server: { host: '127.0.0.1', port: 4347, strictPort: true },
});
