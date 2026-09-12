/** Dependencies replaced only by lti-preview.ts; never used in production. */
export function useLTISession() {
  return {
    accessToken: 'placeholder-lti-session', courseId: 'fixture-course',
    courseName: 'Synthetic Accessibility Course', platform: 'canvas',
    accountWide: false, loading: false, error: null,
  };
}

const files = [
  { id: 'fixture-word', display_name: 'Lecture notes.docx', content_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', size: 14336 },
  { id: 'fixture-slides', display_name: 'Week 1 slides.pptx', content_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation', size: 28672 },
  { id: 'fixture-sheet', display_name: 'Results workbook.xlsx', content_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', size: 8192 },
  { id: 'fixture-unscanned', display_name: 'Reading list.pdf', content_type: 'application/pdf', size: 2048 },
].map(file => ({ ...file, filename: file.display_name, url: '' }));

const scores = [95.7, 80.3, 0, null];
const statuses = files.map((file, index) => ({
  file_id: file.id, provider_file_id: file.id, file_name: file.display_name,
  scan_id: scores[index] === null ? null : `scan-${file.id}`,
  compliance_score: scores[index], issues_count: index + 1,
  status: scores[index] === null ? 'not_scanned' : 'completed',
  has_remediated_version: false,
}));
const items = [
  ...files.map((file, index) => ({
    cloud_file_id: `cloud-${file.id}`, provider_file_id: file.id, title: file.display_name,
    content_type: 'file', compliance_score: scores[index], scan_id: statuses[index].scan_id,
  })),
  { cloud_file_id: 'cloud-page', provider_file_id: 'page-1', title: 'Course introduction',
    content_type: 'page', compliance_score: 98.2, scan_id: 'scan-page' },
  { cloud_file_id: 'cloud-assignment', provider_file_id: 'assignment-1', title: 'Week 1 assignment',
    content_type: 'assignment', compliance_score: 75, scan_id: 'scan-assignment' },
].map(item => ({ ...item, provider: 'canvas', provider_parent_id: 'fixture-course',
  issue_count: item.compliance_score === null ? 0 : 2, writeback_status: null,
  has_remediated_version: false, remediation_origin: null,
  last_scanned_at: item.compliance_score === null ? null : '2026-09-12T00:00:00Z',
  content_updated_at: null,
}));

export const apiClient = {
  async get(url: string) {
    const path = url.split('?')[0];
    if (path === '/canvas/courses/fixture-course/files') return { data: files };
    if (path === '/canvas/courses/fixture-course/scan-status') return {
      data: { files: statuses, course_name: 'Synthetic Accessibility Course', average_compliance: 58.7 },
    };
    if (path === '/canvas/content/courses/fixture-course/status') return {
      data: { course_id: 'fixture-course', overall_compliance: 69.84, by_type: [], items },
    };
    throw new Error('This action is unavailable in the local visual fixture.');
  },
  async post() {
    throw new Error('Remote actions are disabled in the local visual fixture.');
  },
};

export function createLTIClient() { return apiClient; }
