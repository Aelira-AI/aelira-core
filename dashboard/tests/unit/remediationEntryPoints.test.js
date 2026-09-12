import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = (path) => readFileSync(new URL(`../../src/${path}`, import.meta.url), 'utf8');

describe('remediation entry points', () => {
  it('routes upload and scan detail through durable remediation review', () => {
    for (const path of ['components/upload/FileUploader.tsx', 'pages/ScanDetail.tsx']) {
      const page = source(path);
      assert.match(page, /<RemediationStatusLink/);
      assert.doesNotMatch(page, /scansApi\.remediateScan\(/);
      assert.doesNotMatch(page, /scansApi\.downloadRemediated\(/);
      assert.doesNotMatch(page, /<FormatDownloadButton/);
    }
    assert.match(source('components/upload/FileUploader.tsx'), /Original scan complete/);
  });

  it('keeps history, issue management, and bulk upload honest about remediation', () => {
    for (const path of ['pages/History.tsx', 'pages/Issues.tsx', 'pages/BulkUpload.tsx']) {
      assert.doesNotMatch(source(path), /scansApi\.(remediateScan|downloadRemediated)\(/);
    }
    assert.match(source('pages/History.tsx'), /Open remediation review for/);
    const issues = source('pages/Issues.tsx');
    assert.doesNotMatch(issues, /Auto-Fix Complete|Bulk Remediation Complete|Document remediated\./);
    assert.match(issues, /<RemediationStatusLink/);
    assert.match(issues, /scans_queued/);
    assert.match(issues, /\['pdf', 'word', 'excel', 'powerpoint', 'latex'\]\.includes\(scanInfo\.type\.toLowerCase\(\)\)/);
    assert.match(issues, /batchRemediationReceiptIsConfirmed\(receipt, scanIds\)/);
    const bulk = source('pages/BulkUpload.tsx');
    assert.match(bulk, /startRemediationJob\(remediationScanId/);
    assert.match(bulk, /<RemediationStatusLink/);
    assert.match(bulk, /Original scan complete/);
  });

  it('names bulk upload navigation, row actions and concurrency selection', () => {
    const bulk = source('pages/BulkUpload.tsx');
    assert.match(bulk, /aria-label="Back to upload"/);
    assert.match(bulk, /aria-label=\{`View scan details for \$\{file\.file\.name\}`\}/);
    assert.match(bulk, /aria-label=\{`Remove \$\{file\.file\.name\}`\}/);
    assert.match(bulk, /htmlFor="bulk-concurrency"/);
    assert.match(bulk, /<select\s+id="bulk-concurrency"/);
  });

  it('uses readable history table headers and unverified score labels', () => {
    const history = source('pages/History.tsx');
    assert.doesNotMatch(history, /<th\b[^>]*text-tertiary/);
    assert.doesNotMatch(history, /text-tertiary bg-\[var\(--surface-tertiary\)\]/);
  });

  it('avoids the failing tertiary-on-tertiary pair in changed route badge maps', () => {
    for (const path of ['pages/Issues.tsx', 'pages/BulkUpload.tsx']) {
      assert.doesNotMatch(source(path), /color: 'text-(?:tertiary|\[var\(--content-tertiary\)\])',\s*bg: 'bg-\[var\(--surface-tertiary\)\]'/);
    }
    assert.doesNotMatch(source('pages/Remediate.tsx'), /bg-\[var\(--surface-tertiary\)\][^\n]*\n\s*<FileText[^>]*content-tertiary/);
  });

  it('reads persisted state and polls running jobs without inferring success', () => {
    const component = source('components/results/RemediationStatusLink.tsx');
    assert.match(component, /getLatestRemediationJob\(scanId\)/);
    assert.match(component, /pollRemediationJob/);
    assert.match(component, /controller\.abort\(\)/);
    assert.match(component, /Job status unavailable/);
    assert.match(component, /No remediation job recorded/);
    assert.match(component, /Manual review required/);
    assert.match(component, /Output available for review/);
    assert.match(component, /download_available === true && job\.download_url/);
    const review = source('pages/Remediate.tsx');
    assert.match(review, /const canResume = \['client_timeout', 'monitoring_error', 'request_failed'\]/);
    assert.doesNotMatch(review, /pageState === 'monitoring_error' && job === null/);
  });

  it('labels the file input and separates it from the browse button', () => {
    const uploader = source('components/upload/FileUploader.tsx');
    assert.match(uploader, /noClick: true/);
    assert.match(uploader, /noKeyboard: true/);
    assert.match(uploader, /getInputProps\(\{ 'aria-label':/);
    assert.match(uploader, /onClick=\{open\}/);
    assert.doesNotMatch(uploader, /getRootProps\(\{\s*role: 'button'/);
  });

  it('makes findings keyboard-scrollable and uses readable badge tokens', () => {
    assert.match(source('pages/Remediate.tsx'), /tabIndex=\{0\}[\s\S]{0,120}aria-label="Recorded findings and changes"/);
    assert.doesNotMatch(source('utils/remediationIssueOutcomes.ts'), /text-tertiary/);
    assert.doesNotMatch(source('components/upload/ScanTypeSelector.tsx'), /color: type\.badge[^\n]*#[0-9A-Fa-f]+/);
    assert.doesNotMatch(source('components/upload/FileUploader.tsx'), /llava:7b|~10 seconds/);
    assert.match(source('components/upload/ScanTypeSelector.tsx'), /min-w-0 flex-1[\s\S]{0,60}flex flex-wrap/);
    assert.doesNotMatch(source('components/upload/FileUploader.tsx'), /remediated version with AI-powered fixes applied/);
  });

  it('uses badge and outcome color pairs with at least 4.5:1 contrast in both themes', () => {
    const css = source('index.css');
    const luminance = (hex) => {
      const channels = hex.match(/[0-9a-f]{2}/gi).map((value) => parseInt(value, 16) / 255)
        .map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
      return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
    };
    for (const [foreground, background] of [
      ['content-secondary', 'surface-tertiary'],
      ['feature-success-content', 'feature-success-surface'],
      ['feature-info-content', 'feature-info-surface'],
    ]) {
      const colors = (token) => [...css.matchAll(new RegExp(`--${token}:\\s*(#[0-9a-fA-F]{6})`, 'g'))].map((match) => match[1]);
      const fg = colors(foreground);
      const bg = colors(background);
      assert.equal(fg.length, 2);
      assert.equal(bg.length, 2);
      for (let theme = 0; theme < 2; theme += 1) {
        const a = luminance(fg[theme]);
        const b = luminance(bg[theme]);
        assert.ok((Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05) >= 4.5, `${foreground}/${background} theme ${theme}`);
      }
    }
  });
});
