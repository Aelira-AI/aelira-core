import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const componentSource = readFileSync(
  new URL('../../src/components/EvidenceReportAction.tsx', import.meta.url),
  'utf8'
);
const scansApiSource = readFileSync(new URL('../../src/api/scans.ts', import.meta.url), 'utf8');
const adminApiSource = readFileSync(new URL('../../src/api/admin.ts', import.meta.url), 'utf8');
const featureSource = readFileSync(
  new URL('../../src/utils/featureAccess.ts', import.meta.url),
  'utf8'
);
const retiredPromiseSources = [
  '../../../src/mailer/templates/weekly_summary.html',
  '../../../src/api/lti_routes.py',
  '../../../src/api/blackboard_lti_routes.py',
  '../../../src/api/brightspace_lti_routes.py',
  '../../../src/mailer/email_service.py',
  '../../../src/mailer/templates/faculty_invitation.html',
  '../../src/pages/Dashboard.tsx',
  '../../src/pages/IntegrationsSettings.tsx',
  '../../../BRANDING.md',
  '../../../.env.example',
].map((path) => readFileSync(new URL(path, import.meta.url), 'utf8'));

describe('accessibility evidence report product contract', () => {
  it('offers one ungated evidence-report download with bounded language', () => {
    assert.match(componentSource, /Download Evidence Report/);
    assert.match(componentSource, /does not determine conformance/);
    assert.doesNotMatch(componentSource, /eligib|threshold|award|medal|certificate/iu);
    assert.equal((componentSource.match(/<button/g) || []).length, 1);
  });

  it('uses only the canonical evidence-report API names', () => {
    assert.match(scansApiSource, /\/analytics\/evidence-report\/\$\{departmentId\}/);
    assert.match(adminApiSource, /include_evidence_report/);
    for (const source of [scansApiSource, adminApiSource, featureSource]) {
      assert.doesNotMatch(source, /include_certificate|ComplianceCertificates|certificate\/\$\{departmentId\}/);
    }
  });

  it('retires certificate promises from active email, LMS, and branding surfaces', () => {
    for (const source of retiredPromiseSources) {
      assert.doesNotMatch(source, /compliance certificates?|certificate_url|reports and certificates|WCAG 2\.1(?: AA)? compliance reports|compliance deadline|achieve full WCAG|ensure compliance|make .*WCAG .*compliant/iu);
    }
  });
});
