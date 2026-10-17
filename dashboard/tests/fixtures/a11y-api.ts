/**
 * Synthetic, local-only API for the accessibility audit specs.
 *
 * Every response is fixture data routed through Playwright; no backend, network
 * or provider is contacted. Scenarios let one route be audited in its default,
 * empty, loading and failure states.
 */
import type { Page, Route } from '@playwright/test';
import { readFileSync } from 'node:fs';

const workspace = JSON.parse(
  readFileSync(new URL('./workspace-api.json', import.meta.url), 'utf8'),
) as Record<string, unknown>;

export const SCAN_ID = '11111111-1111-4111-8111-111111111111';
export const ARTIFACT_ID = '22222222-2222-4222-8222-222222222222';
export const CLOUD_FILE_ID = '55555555-5555-4555-8555-555555555555';
export const JOB_ID = '33333333-3333-4333-8333-333333333333';

export type Scenario = 'default' | 'empty' | 'error' | 'loading' | 'processing';

export interface MockOptions {
  scenario?: Scenario;
  role?: 'faculty' | 'admin';
  /** false answers the session check with 401 so sign-in routes render. */
  authenticated?: boolean;
}

const issues = [
  {
    id: 'finding-0', severity: 'high', category: 'alt_text', page_number: 1,
    description: 'Image is missing alternative text', wcag_criteria: '1.1.1',
    location: 'Page 1', fix_suggestion: 'Add a description of the chart.', can_auto_fix: true,
  },
  {
    id: 'finding-1', severity: 'critical', category: 'contrast', page_number: 2,
    description: 'Text contrast ratio is 2.1:1 and below the 4.5:1 minimum', wcag_criteria: '1.4.3',
    location: 'Page 2', fix_suggestion: 'Darken the text colour.', can_auto_fix: false,
  },
  {
    id: 'finding-2', severity: 'medium', category: 'language', page_number: 3,
    description: 'Document language is not set', wcag_criteria: '3.1.1',
    location: 'Document', fix_suggestion: 'Set the document language to English.', can_auto_fix: true,
  },
  {
    id: 'finding-3', severity: 'low', category: 'link', page_number: 4,
    description: 'Link text is not descriptive', wcag_criteria: '2.4.4',
    location: 'Page 4', fix_suggestion: 'Replace "click here" with the link destination.', can_auto_fix: true,
  },
];

const reviewFixes = issues.slice(0, 3).map((issue, index) => ({
  ...issue,
  id: `fix-${index}`,
  category: index === 0 ? 'alt_text' : index === 1 ? 'heading_structure' : 'document_language',
  fix_method: index === 0 ? 'ai_vision' : 'rule',
  confidence: index === 0 ? 0.62 : null,
  needs_review: index === 0,
  review_status: index === 0 ? 'pending' : 'auto_approved',
  original_content: index === 0 ? 'No description' : 'Original value',
  fixed_content: index === 0 ? 'A bar chart of quarterly enrolment.' : 'Source-bound deterministic change',
}));

const job = {
  job_id: JOB_ID, scan_id: SCAN_ID, status: 'completed', progress: 100,
  status_url: `/education/remediation/jobs/${JOB_ID}`,
  fixed_count: 3, manual_count: 1, failed_count: 0, skipped_count: 0, withheld_count: 0,
  outcome_unreported_count: 0, total_issues: 4, remaining_count: 1,
  original_score: 59.7, remediated_score: null, score_verified: false,
  score_verification_reason: 'baseline_mismatch',
  fresh_score_comparison: {
    method_version: 'pdf-strict-v1', source_sha256: 'a'.repeat(64),
    output_sha256: 'b'.repeat(64), source_score: 57.9, output_score: 83,
  },
  human_review_required: true, artifact_id: ARTIFACT_ID, download_available: true,
  download_url: `/education/remediation/jobs/${JOB_ID}/download`,
  issue_outcomes: issues.map((issue, index) => ({
    source_index: index, source_index_scope: 'original_scan', issue_id: issue.id,
    status: index < 3 ? 'fixed' : 'manual',
    ...(index < 3
      ? { verification_passed: true, verification_scope: 'saved_file_finding', needs_review: index === 0 }
      : {
        reason: 'The source requires manual correction or independent validation.',
        next_step: 'Inspect and correct this finding in the source, then rescan.',
        attempt: 'not_applied',
      }),
  })),
};

const scanList = [
  { id: SCAN_ID, scan_id: SCAN_ID, scan_type: 'pdf', status: 'completed', compliance_score: 59.7, total_issues: 4,
    file_name: 'Quarterly enrolment report.pdf', created_at: '2026-10-08T10:00:00Z' },
  { id: 'scan-2', scan_id: 'scan-2', scan_type: 'word', status: 'completed', compliance_score: 91, total_issues: 1,
    file_name: 'Course handbook.docx', created_at: '2026-10-07T10:00:00Z' },
  { id: 'scan-3', scan_id: 'scan-3', scan_type: 'powerpoint', status: 'failed', compliance_score: null, total_issues: null,
    file_name: 'Week 3 teaching slides.pptx', created_at: '2026-10-06T10:00:00Z' },
];

const scanDetail = {
  id: SCAN_ID, scan_id: SCAN_ID, scan_type: 'pdf', status: 'completed',
  file_name: 'Quarterly enrolment report.pdf', created_at: '2026-10-08T10:00:00Z',
  compliance_score: 59.7,
  remediation_eligibility: { eligible: true, reason: null },
  result: {
    compliance_score: 59.7, wcag_level: 'A', issues,
    summary: { total_issues: 4, critical: 1, high: 1, medium: 1, low: 1, auto_fixable: 3 },
  },
  issues,
};

type Responder = { status?: number; body?: unknown; delayForever?: boolean };

function withRole(role: 'faculty' | 'admin'): Record<string, unknown> {
  const session = workspace['/auth/session/validate'] as { user: Record<string, unknown>; department: Record<string, unknown> };
  return { ...session, user: { ...session.user, role } };
}

export function resolveRequest(
  method: string,
  path: string,
  options: MockOptions,
): Responder {
  const scenario = options.scenario ?? 'default';
  const role = options.role ?? 'admin';
  const empty = scenario === 'empty';

  if (path === '/auth/session/validate') {
    return options.authenticated === false ? { status: 401, body: { detail: 'Not authenticated' } } : { body: withRole(role) };
  }
  if (path === '/auth/oauth/status') return { body: { oauth_allowed: true, google_available: true, microsoft_available: true } };
  if (path === '/auth/security-notices/pending') return { body: { notices: [] } };
  if (path === '/auth/quota') return { body: workspace['/auth/quota'] };
  if (path === '/auth/profile' || path === '/auth/profile/email-preferences') {
    return { body: path === '/auth/profile'
      ? { ...(workspace['/auth/profile'] as object), timezone: 'Australia/Sydney' }
      : { email_scan_complete: true, email_remediation_complete: false, email_critical_alerts: true, email_weekly_summary: true, weekly_summary_day: 0, weekly_summary_hour: 9 } };
  }
  if (path === '/auth/sessions') {
    return { body: { sessions: [
      { id: 'session-current', device: 'Chrome on macOS', ip_address: '203.0.113.7', created_at: '2026-10-08T10:00:00Z',
        last_active: '2026-10-08T10:30:00Z', is_current: true },
      { id: 'session-2', device: 'Firefox on Windows', ip_address: '198.51.100.9', created_at: '2026-10-05T10:00:00Z',
        last_active: '2026-10-06T10:30:00Z', is_current: false },
    ] } };
  }
  if (path === '/auth/keys') return { body: empty ? [] : [{
    id: 'key-1', name: 'CI integration', key_prefix: 'ak_live_1234', rate_limit_per_hour: 100,
    created_at: '2026-09-01T10:00:00Z', last_used_at: null, expires_at: null, is_active: true }] };
  if (path === '/account/deletion/status') return { body: { has_pending_deletion: false, status: 'none' } };

  if (path === '/education/stats') {
    return empty
      ? { body: { ...(workspace['/education/stats'] as object), historical_scan_count: 0, enrolled_document_count: 0,
          verified_document_count: 0, unverified_document_count: 0, avg_compliance_score: null, total_issues: 0, scans_this_month: 0 } }
      : { body: workspace['/education/stats'] };
  }
  if (path === '/education/scans') {
    if (scenario === 'error') return { status: 500, body: { detail: 'Synthetic failure' } };
    if (scenario === 'loading') return { delayForever: true };
    return { body: { scans: empty ? [] : scanList, total: empty ? 0 : scanList.length } };
  }
  if (method === 'DELETE' && path.startsWith('/education/scans/')) return { body: {} };
  if (method === 'PATCH' && path === '/auth/profile/email-preferences') {
    return { body: { email_scan_complete: true, email_remediation_complete: true, email_critical_alerts: true,
      email_weekly_summary: true, weekly_summary_day: 0, weekly_summary_hour: 9 } };
  }
  if (path === `/education/scans/${SCAN_ID}`) {
    if (scenario === 'error') return { status: 500, body: { detail: 'Synthetic failure' } };
    if (scenario === 'loading') return { delayForever: true };
    if (scenario === 'processing') {
      return { body: { success: true, scan: { ...scanDetail, status: 'processing', result: null, issues: [], compliance_score: null } } };
    }
    return { body: { success: true, scan: scanDetail } };
  }
  if (/^\/education\/scans\/scan-\d$/.test(path)) {
    return { body: { success: true, scan: { ...scanDetail, id: path.split('/').pop(), scan_id: path.split('/').pop() } } };
  }
  if (path === `/education/scans/${SCAN_ID}/progress`) {
    if (scenario === 'processing') {
      return { body: { scan_id: SCAN_ID, status: 'PROCESSING', progress: 40, progress_message: 'Checking images for alternative text' } };
    }
    return { body: { scan_id: SCAN_ID, status: 'COMPLETED', progress: 100 } };
  }
  if (path === `/education/scans/${SCAN_ID}/remediation/latest` || path === job.status_url) return { body: job };
  if (path.endsWith('/remediation/latest')) return { body: null };
  if (path === `/api/reviews/${SCAN_ID}`) {
    return { body: { scan_id: SCAN_ID, file_name: 'Quarterly enrolment report.pdf', scan_type: 'pdf',
      status: 'pending', preview_available: false, fixes: reviewFixes, visual_analyses: [],
      matterhorn_total: 0, matterhorn_passed: 0, matterhorn_failed: 0, matterhorn_warnings: 0, validator_result: 'not_run' } };
  }
  if (path === `/api/reviews/${SCAN_ID}/working-artifact`) {
    return { body: { scan_id: SCAN_ID, cloud_file_id: null, artifact_id: ARTIFACT_ID } };
  }
  if (path === `/education/scans/${SCAN_ID}/artifacts/${ARTIFACT_ID}`) {
    return { body: { id: ARTIFACT_ID, scan_id: SCAN_ID, filename: 'Improved working file.pdf', sha256: 'b'.repeat(64),
      review_status: 'pending', approval_blockers: ['fixes_pending_review'], can_approve: false,
      writeback_available: false, writeback_provider: null, has_manual_edits: false,
      reviewed_rebuild_available: true, reviewed_rebuild_blocker: null } };
  }
  if (path === '/api/reviews/department-summary') return { body: workspace['/api/reviews/department-summary'] };
  if (path === '/api/reviews/queue/stats') {
    return { body: empty ? { pending: 0, in_review: 0, approved: 0, rejected: 0, total: 0 } : workspace['/api/reviews/queue/stats'] };
  }
  if (path === '/api/reviews/queue') {
    if (scenario === 'error') return { status: 500, body: { detail: 'Synthetic failure' } };
    return { body: empty ? { items: [], total: 0 } : { items: [
      { scan_id: SCAN_ID, file_name: 'Quarterly enrolment report.pdf', scan_type: 'pdf', status: 'pending',
        fix_count: 3, pending_count: 1, avg_confidence: 0.62, created_at: '2026-10-08T10:00:00Z' },
      { scan_id: 'scan-2', file_name: 'Course handbook.docx', scan_type: 'word', status: 'pending',
        fix_count: 2, pending_count: 2, avg_confidence: 0.88, created_at: '2026-10-07T10:00:00Z' },
    ], total: 2 } };
  }

  if (/^\/education\/compliance\/[^/]+\/issues$/.test(path)) return { body: workspace['/education/compliance/preview-dept/issues'] };
  if (/^\/education\/compliance\/[^/]+\/trend$/.test(path)) return { body: workspace['/education/compliance/preview-dept/trend'] };
  if (/^\/analytics\/projection\//.test(path)) return { body: { projection: null } };
  if (/^\/analytics\/trend\/.+\/analysis$/.test(path)) return { body: { analysis: null } };
  if (/^\/analytics\/issues\/[^/]+\/stats$/.test(path)) return { body: workspace['/analytics/issues/preview-dept/stats'] };
  if (/^\/analytics\/issues\/[^/]+$/.test(path)) {
    return { body: { issues: empty ? [] : issues.map((issue) => ({ ...issue, status: 'OPEN', scan_id: SCAN_ID })), total: empty ? 0 : issues.length } };
  }
  if (path === '/integrations/status') return scenario === 'loading' ? { delayForever: true } : { body: workspace['/integrations/status'] };
  if (path === '/integrations/files') {
    if (scenario === 'loading') return { delayForever: true };
    return { body: { total: empty ? 0 : 2, files: empty ? [] : [
      { id: 'cf-1', provider: 'google', file_name: 'Syllabus.docx', file_type: 'docx', file_size_bytes: 20480,
        last_scanned_at: '2026-10-08T10:00:00Z', last_compliance_score: 84, web_view_link: 'https://docs.example.test/1', needs_rescan: false },
      { id: 'cf-2', provider: 'microsoft', file_name: 'Lecture 2.pptx', file_type: 'pptx', file_size_bytes: 512000,
        last_scanned_at: null, last_compliance_score: null, web_view_link: null, needs_rescan: true }] } };
  }
  if (path === '/alerts/settings') {
    return { body: { alert_on_scan_complete: true, alert_on_critical_issues: true, alert_weekly_summary: true, weekly_summary_day: 0, weekly_summary_hour: 9, email_addresses: ['alex@example.test'], is_paused: false } };
  }
  if (path === '/admin/regulatory-profile') {
    return { body: { schema_version: 1, profile_revision: 1, country_code: 'US', regulatory_framework: 'US_ADA_TITLE_II',
      title_ii_entity_class: 'large', custom_deadline: null, custom_deadline_verified: false, configuration_complete: true,
      deadline: (workspace['/education/stats'] as { deadline: unknown }).deadline,
      supported_frameworks: [
        { code: 'US_ADA_TITLE_II', name: 'US ADA Title II', default_country_code: 'US', requires_explicit_selection: false,
          requires_title_ii_entity_class: true, allows_custom_deadline: false },
        { code: 'EU_EAA', name: 'EU European Accessibility Act', default_country_code: null, requires_explicit_selection: false,
          requires_title_ii_entity_class: false, allows_custom_deadline: false },
      ] } };
  }
  if (path === '/llm/providers') return { body: { providers: [], primary: null } };
  if (path === '/llm/lms-policy') {
    const readiness = { ready: false, reason: 'No credential configured', locality: 'remote', credential_source: null };
    return { body: { schema_version: 1, policy_revision: 1, enabled: false, provider: null, remediation_enabled: false,
      alt_text_enabled: false, pilot_gemini_approved: false,
      provider_readiness: { ollama: { ...readiness, locality: 'local' }, gemini: readiness, openai: readiness, anthropic: readiness, xai: readiness } } };
  }
  if (path === '/llm/health') return { body: { status: 'ok', providers: [] } };
  if (path === '/admin/users') return { body: { users: [
    { id: 'preview-user', name: 'Alex Taylor', email: 'alex@example.test', role: 'admin', department_id: 'preview-dept',
      created_at: '2026-01-01T10:00:00Z', last_login: '2026-10-08T10:00:00Z' },
    { id: 'user-2', name: 'Sam Rivera', email: 'sam@example.test', role: 'faculty', department_id: 'preview-dept',
      created_at: '2026-02-01T10:00:00Z', last_login: null }], total: 2 } };
  if (path === '/admin/invitations') return { body: { invitations: [
    { id: 'invite-1', email: 'new@example.test', role: 'faculty', status: 'pending', created_at: '2026-10-01T10:00:00Z',
      expires_at: '2026-10-31T10:00:00Z' }], total: 1 } };
  if (path === '/admin/stats') return { body: { total_users: 2, total_scans: 12, historical_scan_count: 12, enrolled_document_count: 8,
    verified_document_count: 6, unverified_document_count: 2, total_issues: 24, avg_compliance_score: 78, scans_this_month: 12,
    issues_resolved: 6, active_users: 2 } };
  if (path === '/api/jobs/worker-status') {
    const zero = { pending: 0, processing: 0, completed: 5, failed: 0 };
    return { body: { generated_at: '2026-10-08T10:00:00Z', status: 'healthy', health_state: 'healthy_idle', queue: zero,
      workers: { live: 1, draining: 0, latest_heartbeat_at: '2026-10-08T10:00:00Z', latest_heartbeat_age_seconds: 4 },
      progress: { jobs_claimed: 5, jobs_completed: 5, jobs_failed: 0, oldest_pending_created_at: null, oldest_pending_age_seconds: null,
        oldest_processing_heartbeat_at: null, oldest_running_job_age_seconds: null, runnable_pending: 0, expired_processing: 0,
        stalled_processing: 0, latest_progress_at: '2026-10-08T10:00:00Z', latest_progress_age_seconds: 4 },
      maintenance: { artifact_cleanup_due: 0 },
      weekly_summary_scheduler: { state: 'healthy', last_success_at: '2026-10-05T10:00:00Z', last_success_age_seconds: 300, last_error_code: null },
      reconciliation: { required: 0, manual_required: 0, failed_manual: 0 },
      orphans: { pending_move: 0, quarantined: 0, restore_required: 0, reviewed: 0, purging: 0 } } };
  }
  if (path === '/canvas/status') return { body: { connected: true, canvas_instance_url: 'https://canvas.example.test', user_email: 'alex@example.test' } };
  if (path === '/brightspace/status') return { body: { connected: true } };
  if (path === '/canvas/courses') {
    return { body: empty ? [] : [{ id: '101', name: 'Introduction to Statistics', course_code: 'STAT101', workflow_state: 'available', total_students: 42, term: { name: 'Autumn 2026' } }] };
  }
  if (path === '/brightspace/courses') {
    return { body: empty ? [] : [{ org_unit_id: 1, name: 'Introduction to Statistics', code: 'STAT101' }] };
  }
  if (/^\/canvas\/courses\/[^/]+\/files$/.test(path)) {
    return { body: { files: [{ id: 'file-1', display_name: 'Week 1 lecture notes.pdf', size: 245760, content_type: 'application/pdf',
      url: 'https://canvas.example.test/files/1', created_at: '2026-09-01T10:00:00Z', updated_at: '2026-09-02T10:00:00Z' }] } };
  }
  if (/^\/canvas\/courses\/[^/]+\/scan-status$/.test(path)) {
    return { body: { course_id: '101', total_files: 1, scanned_files: 1, average_compliance: 59.7, files: [{ provider_file_id: 'file-1',
      file_name: 'Week 1 lecture notes.pdf', scan_id: SCAN_ID, compliance_score: 59.7, issues_count: 4, status: 'completed', has_remediated_version: false }] } };
  }
  if (/^\/canvas\/content\/courses\/[^/]+\/status$/.test(path)) {
    return { body: { course_id: '101', overall_compliance: 70,
      by_type: [{ content_type: 'page', total: 1, scanned: 1, average_compliance: 59.7, issues: 4 },
        { content_type: 'assignment', total: 1, scanned: 1, average_compliance: 92, issues: 0 }],
      items: [
        { cloud_file_id: CLOUD_FILE_ID, provider_file_id: 'page-1', provider: 'canvas', provider_parent_id: '101', title: 'Week 1 overview page',
          content_type: 'page', compliance_score: 59.7, issue_count: 4, writeback_status: null, has_remediated_version: true,
          remediation_origin: 'automatic', current_remediation_artifact_id: null, last_scanned_at: '2026-10-08T10:00:00Z',
          content_updated_at: '2026-10-01T10:00:00Z', scan_id: SCAN_ID },
        { cloud_file_id: 'cloud-file-2', provider_file_id: 'assignment-1', provider: 'canvas', provider_parent_id: '101', title: 'Assignment 1 brief',
          content_type: 'assignment', compliance_score: 92, issue_count: 0, writeback_status: null, has_remediated_version: false,
          remediation_origin: null, current_remediation_artifact_id: null, last_scanned_at: '2026-10-08T10:00:00Z',
          content_updated_at: '2026-10-01T10:00:00Z', scan_id: 'scan-2' }] } };
  }
  if (/^\/brightspace\/content\/courses\/[^/]+\/status$/.test(path)) {
    return { body: { org_unit_id: 1, total_items: 2, scanned_items: 2, average_compliance: 70, items: [
      { cloud_file_id: CLOUD_FILE_ID, title: 'Week 1 overview page', content_type: 'page', compliance_score: 59.7, issue_count: 4,
        writeback_status: null, has_remediated_version: true, approval_eligible: true, remediation_origin: 'automatic', module_path: 'Week 1' },
      { cloud_file_id: 'cloud-file-2', title: 'Assignment 1 brief', content_type: 'assignment', compliance_score: 92, issue_count: 0,
        writeback_status: null, has_remediated_version: false, approval_eligible: false, remediation_origin: null, module_path: 'Week 2' }] } };
  }
  if (/^\/(canvas|brightspace)\/content\/[^/]+\/diff$/.test(path)) {
    return { body: { cloud_file_id: CLOUD_FILE_ID, scan_id: SCAN_ID, scan_type: 'html', content_type: 'page', title: 'Week 1 overview page',
      original_html: '<h1>Overview</h1><p><img src="chart.png"></p><p><a href="/syllabus">click here</a></p>',
      remediated_html: '<h1>Overview</h1><p><img src="chart.png" alt="Bar chart of enrolment"></p><p><a href="/syllabus">Read the syllabus</a></p>',
      issues_fixed: 2, issues_remaining: 1, issues: [] } };
  }
  if (path === '/canvas/content/overview') return { body: { courses: [] } };
  if (method !== 'GET') return { body: {} };
  return { status: 404, body: { detail: 'Synthetic endpoint unavailable' } };
}

/** Route every dashboard API call to fixture data. */
export async function installApiMock(page: Page, options: MockOptions = {}): Promise<void> {
  await page.route(/http:\/\/(localhost:8000|127\.0\.0\.1:5280)\//, async (route: Route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const cors = {
      'access-control-allow-origin': request.headers().origin ?? '*',
      'access-control-allow-credentials': 'true',
      'access-control-allow-headers': request.headers()['access-control-request-headers'] ?? '*',
      'access-control-allow-methods': 'GET,POST,PUT,PATCH,DELETE,OPTIONS',
    };
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: cors });
      return;
    }
    const responder = resolveRequest(request.method(), path, options);
    if (responder.delayForever) return; // never fulfilled: exercises the loading state
    await route.fulfill({
      status: responder.status ?? 200,
      contentType: 'application/json',
      body: JSON.stringify(responder.body ?? {}),
      headers: cors,
    });
  });
}
