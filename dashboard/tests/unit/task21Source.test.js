import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../../src/pages/CourseOverview.tsx', import.meta.url), 'utf8');
const canvasSource = readFileSync(new URL('../../src/pages/CanvasContentPage.tsx', import.meta.url), 'utf8');
const brightspaceSource = readFileSync(new URL('../../src/pages/BrightspaceContentPage.tsx', import.meta.url), 'utf8');
const apiTypesSource = readFileSync(new URL('../../src/types/api.ts', import.meta.url), 'utf8');
const typesIndexSource = readFileSync(new URL('../../src/types/index.ts', import.meta.url), 'utf8');

describe('CourseOverview table semantics', () => {
  it('uses a real course anchor without making the row a fake link', () => {
    assert.match(source, /<Link\s+to=\{courseHref\(course\.course_id\)\}/);
    assert.doesNotMatch(source, /role="link"|tabIndex=\{0\}|onClick=\{\(\) => handleCourseClick/);
  });

  it('encodes untrusted course ids before inserting them into route paths', () => {
    assert.match(source, /encodeURIComponent\(courseId\)/);
    assert.doesNotMatch(source, /`\/lti\/course\/\$\{courseId\}/);
    assert.doesNotMatch(source, /`\/canvas\/courses\/\$\{courseId\}/);
  });
});

const truthSources = [
  '../../src/pages/Upload.tsx',
  '../../src/pages/Login.tsx',
  '../../../src/api/lti_routes.py',
  '../../../src/api/brightspace_lti_routes.py',
  '../../../src/api/blackboard_lti_routes.py',
  '../../../src/middleware/quota.py',
  '../../../src/api/user_management.py',
  '../../../src/config/settings.py',
].map((path) => readFileSync(new URL(path, import.meta.url), 'utf8'));

describe('truthful workspace configuration language', () => {
  it('removes stale paywall, pricing, free-tier, and upgrade wording from confirmed surfaces', () => {
    for (const pageSource of truthSources) {
      assert.doesNotMatch(pageSource, /raise this department's tier|pricing tier|free tier|upgrade/iu);
    }
  });
});

describe('API quota types', () => {
  it('preserves QuotaStatus and removes unused BillingInfo definition and re-export', () => {
    assert.match(apiTypesSource, /export interface QuotaStatus/);
    assert.doesNotMatch(apiTypesSource, /interface BillingInfo/);
    assert.doesNotMatch(typesIndexSource, /BillingInfo/);
  });
});

describe('sortable table headers', () => {
  it('uses native buttons and aria-sort in Canvas and Brightspace', () => {
    for (const pageSource of [canvasSource, brightspaceSource]) {
      assert.match(pageSource, /aria-sort=\{sortField ===/);
      assert.match(pageSource, /<button\s+type="button"\s+onClick=\{\(\) => toggleSort/);
      assert.doesNotMatch(pageSource, /<th[^>]*onClick=\{\(\) => toggleSort/s);
    }
  });
});

describe('Brightspace approval accounting', () => {
  it('uses the shared truthful summary and warns for zero or partial outcomes', () => {
    assert.match(brightspaceSource, /brightspaceApprovalSummary\(result\)/);
    assert.match(brightspaceSource, /summary\.status === 'success'/);
    assert.match(brightspaceSource, /toast\.warning\(summary\.message, 'Batch Approve'\)/);
    assert.doesNotMatch(brightspaceSource, /toast\.success\(\s*`Approved \$\{result\.approved_count\}/s);
  });
});
