import { test, expect, type Page, type TestInfo } from '@playwright/test';
import { mkdir, readFile } from 'node:fs/promises';
import { Buffer } from 'node:buffer';
import { basename, resolve } from 'node:path';
import { assertNoBlockingViolations } from './support/a11y';
import { officePart, pdfInfo, textRuns } from './support/files';
import { pressWithKeyboard, resetFocus } from './support/keyboard';
import { apiGet, issueText, scanIdFromUrl, type RemediationJob, type ScanRecord } from './support/scans';
import { dismissCookieBanner, sha256 } from './support/stack';

const fixtures = resolve(import.meta.dirname, '../../../tests/fixtures');
const downloads = resolve(import.meta.dirname, '../../journey-results/downloads');
const journeyFixtures = resolve(import.meta.dirname, '../../../tests/journeys/fixtures');

interface Upload {
  scanId: string;
  fixture: string;
}

/**
 * Drive the New Scan flow. With `keyboard` set every action is a Tab press and
 * Enter; the only pointer-free exception is the operating system's file
 * dialog, which Playwright answers directly.
 */
async function scanThroughTheUi(
  page: Page,
  testInfo: TestInfo,
  options: { card: RegExp; file: string; name: string; keyboard: boolean },
): Promise<Upload> {
  const { card, file, name, keyboard } = options;
  await page.goto('/upload');
  await expect(page.getByRole('heading', { name: 'New Scan' })).toBeVisible();
  if (!keyboard) await dismissCookieBanner(page);
  const press = async (locator: ReturnType<Page['getByRole']>) => {
    if (keyboard) {
      await resetFocus(page);
      await pressWithKeyboard(page, locator);
    } else {
      await locator.click();
    }
  };
  await assertNoBlockingViolations(page, `upload-choose-type-${name}`, testInfo);
  await press(page.getByRole('button', { name: card }));
  const chooser = page.waitForEvent('filechooser');
  await press(page.getByRole('button', { name: 'Browse files' }));
  await (await chooser).setFiles(file);
  await expect(page.getByText(name, { exact: true })).toBeVisible();
  await assertNoBlockingViolations(page, `upload-file-chosen-${name}`, testInfo);
  await press(page.getByRole('button', { name: /^Scan 1 file/ }));
  await expect(page.getByText(`${name} scan complete`, { exact: true })).toBeVisible({ timeout: 120_000 });
  await assertNoBlockingViolations(page, `upload-scan-complete-${name}`, testInfo);
  await press(page.getByRole('link', { name: 'View Full Report' }));
  await expect(page.getByRole('heading', { name })).toBeVisible();
  await expect(page.getByText('Loading scan details')).toHaveCount(0);
  return { scanId: scanIdFromUrl(page.url()), fixture: file };
}

async function findingsShownInReport(page: Page, testInfo: TestInfo, scanId: string, name: string) {
  const record = await apiGet<ScanRecord>(page.request, `/education/scans/${scanId}`);
  expect(record.scan.status.toLowerCase()).toBe('completed');
  const issues = record.scan.result?.issues ?? [];
  expect(issues.length, `${name} must produce real findings`).toBeGreaterThan(0);
  await expect(page.getByRole('heading', { name: new RegExp(`^Rule-based findings \\(${issues.length}\\)`) })).toBeVisible();
  await assertNoBlockingViolations(page, `scan-report-${name}`, testInfo);
  return record.scan.result!;
}

async function remediateInTheUi(page: Page, testInfo: TestInfo, scanId: string, keyboard: boolean) {
  const press = async (locator: ReturnType<Page['getByRole']>) => {
    if (keyboard) {
      await resetFocus(page);
      await pressWithKeyboard(page, locator);
    } else {
      await locator.click();
    }
  };
  await press(page.getByRole('link', { name: 'Open Remediation Review' }));
  await expect(page).toHaveURL(new RegExp(`/remediate/${scanId}$`));
  await expect(page.getByRole('button', { name: 'Start Remediation' })).toBeVisible();
  await assertNoBlockingViolations(page, 'remediation-ready', testInfo);
  await press(page.getByRole('button', { name: 'Start Remediation' }));
}

async function downloadWorkingFile(page: Page, name: string, keyboard: boolean): Promise<{ path: string; bytes: Buffer }> {
  const button = page.getByRole('button', { name: 'Download improved working file' });
  await expect(button).toBeVisible({ timeout: 180_000 });
  const pending = page.waitForEvent('download');
  if (keyboard) {
    await resetFocus(page);
    await pressWithKeyboard(page, button);
  } else {
    await button.click();
  }
  const download = await pending;
  const directory = resolve(downloads, `${Date.now()}`);
  await mkdir(directory, { recursive: true });
  const path = resolve(directory, name);
  await download.saveAs(path);
  return { path, bytes: await readFile(path) };
}

function score(value: number | null): string {
  expect(value, 'a measured score is required').not.toBeNull();
  return (value as number).toFixed(1);
}

/** What the dashboard says must equal what the API recorded, and both must match the bytes. */
async function assertOutputMatchesRecord(
  page: Page,
  scanId: string,
  source: Buffer,
  saved: Buffer,
  scanned: { compliance_score: number; issues: unknown[] },
): Promise<RemediationJob> {
  const job = await apiGet<RemediationJob>(page.request, `/education/scans/${scanId}/remediation/latest`);
  expect(job.status).toBe('completed');
  expect(job.download_available).toBe(true);
  expect(job.score_verified).toBe(true);
  expect(job.score_measurement?.source_sha256, 'receipt names the uploaded bytes').toBe(sha256(source));
  expect(job.score_measurement?.output_sha256, 'receipt names the downloaded bytes').toBe(sha256(saved));
  expect(job.original_score).toBe(scanned.compliance_score);
  expect(job.fixed_count + job.manual_count + job.failed_count + job.skipped_count + job.withheld_count).toBe(
    job.total_issues,
  );
  const main = page.locator('main');
  await expect(main).toContainText(`${score(job.original_score)}/100`);
  await expect(main).toContainText(`${score(job.remediated_score)}/100`);
  return job;
}

/** Upload the saved file as a new scan, the way a person checking it would. */
async function rescanSavedFile(page: Page, testInfo: TestInfo, card: RegExp, saved: string) {
  const upload = await scanThroughTheUi(page, testInfo, { card, file: saved, name: basename(saved), keyboard: false });
  const record = await apiGet<ScanRecord>(page.request, `/education/scans/${upload.scanId}`);
  expect(record.scan.status.toLowerCase()).toBe('completed');
  return record.scan.result!;
}

test.describe('document journeys', () => {
  test('PDF by keyboard alone: scan, issues, remediation, exact download, independent re-scan', async ({ page }, testInfo) => {
    const source = `${fixtures}/document_stack/metadata.pdf`;
    const upload = await scanThroughTheUi(page, testInfo, {
      card: /^PDF Documents/,
      file: source,
      name: 'metadata.pdf',
      keyboard: true,
    });
    const scanned = await findingsShownInReport(page, testInfo, upload.scanId, 'metadata.pdf');
    expect(scanned.issues.map(issueText)).toEqual(
      expect.arrayContaining([expect.stringMatching(/title not set/i), expect.stringMatching(/language not set/i)]),
    );

    await remediateInTheUi(page, testInfo, upload.scanId, true);
    const saved = await downloadWorkingFile(page, 'remediated-metadata.pdf', true);
    await assertNoBlockingViolations(page, 'remediation-complete-metadata.pdf', testInfo);
    const job = await assertOutputMatchesRecord(page, upload.scanId, await readFile(source), saved.bytes, scanned);
    expect(job.remediated_score).toBeGreaterThan(job.original_score as number);
    expect(job.human_review_required, 'a PDF/UA identifier cannot be claimed automatically').toBe(true);

    // Independent check 1: a PDF reader sees the title the scan reported missing.
    const before = await pdfInfo(source);
    const after = await pdfInfo(saved.path);
    expect(before.Title ?? '').toBe('');
    expect(after.Title, 'the saved PDF carries a document title').not.toBe('');
    expect(after.Pages).toBe(before.Pages);

    // Independent check 2: the saved bytes, scanned again as a new upload, give the displayed score.
    const rescan = await rescanSavedFile(page, testInfo, /^PDF Documents/, saved.path);
    expect(rescan.compliance_score).toBe(job.remediated_score);
    expect(rescan.issues.length).toBe(job.remaining_count);
    expect(rescan.issues.map(issueText).join(' ')).not.toMatch(/title not set|language not set/i);
  });

  test('DOCX: scan, issues, remediation, review and approval survive a reload, same bytes', async ({ page }, testInfo) => {
    const source = `${fixtures}/document_stack/course.docx`;
    const upload = await scanThroughTheUi(page, testInfo, {
      card: /^Word Documents/,
      file: source,
      name: 'course.docx',
      keyboard: false,
    });
    const scanned = await findingsShownInReport(page, testInfo, upload.scanId, 'course.docx');

    await remediateInTheUi(page, testInfo, upload.scanId, false);
    const saved = await downloadWorkingFile(page, 'remediated-course.docx', false);
    await assertNoBlockingViolations(page, 'remediation-complete-course.docx', testInfo);
    const job = await assertOutputMatchesRecord(page, upload.scanId, await readFile(source), saved.bytes, scanned);
    expect(job.manual_count, 'the 8pt body text cannot be fixed automatically').toBe(1);

    // Independent check: the saved package holds the properties the findings asked for,
    // and the author's text is untouched and in order.
    const core = await officePart(saved.path, 'docProps/core.xml');
    expect(core).toMatch(/<dc:title>[^<]+<\/dc:title>/);
    const documentXml = await officePart(saved.path, 'word/document.xml');
    expect(documentXml).toMatch(/w:pStyle w:val="Heading1"/);
    expect(textRuns(documentXml)).toEqual(textRuns(await officePart(source, 'word/document.xml')));
    const rescan = await rescanSavedFile(page, testInfo, /^Word Documents/, saved.path);
    expect(rescan.compliance_score).toBe(job.remediated_score);
    expect(rescan.issues.length).toBe(job.remaining_count);

    // Review, approve, reload: the decisions come back from storage, and the file is unchanged.
    await page.goto(`/review/${upload.scanId}`);
    await expect(page.getByRole('button', { name: /^Approve all \d+ pending fixes/ })).toBeVisible();
    await assertNoBlockingViolations(page, 'review-pending', testInfo);
    await page.getByRole('button', { name: /^Approve all \d+ pending fixes/ }).click();
    await expect(page.getByRole('button', { name: 'Reviewed (2)' })).toBeVisible();
    await page.reload();
    await expect(page.getByRole('button', { name: 'Reviewed (2)' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Needs Review (0)' })).toBeVisible();
    await page.getByLabel(/I have reviewed this working file/).check();
    await page.getByRole('button', { name: 'Approve this file for publication' }).click();
    await expect(page.getByText(/· Approved for publication/)).toBeVisible();
    await assertNoBlockingViolations(page, 'review-approved', testInfo);

    const again = await downloadWorkingFile(page, 'course-after-approval.docx', false);
    expect(sha256(again.bytes), 'approval does not change the saved file').toBe(sha256(saved.bytes));
    const afterApproval = await apiGet<RemediationJob>(page.request, `/education/scans/${upload.scanId}/remediation/latest`);
    expect(afterApproval.artifact_id).toBe(job.artifact_id);
    expect(afterApproval.score_measurement).toEqual(job.score_measurement);
  });

  test('a delivered DOCX still leaves its picture for human review, with no description written', async ({ page }, testInfo) => {
    const source = `${journeyFixtures}/picture-without-alt.docx`;
    const upload = await scanThroughTheUi(page, testInfo, {
      card: /^Word Documents/,
      file: source,
      name: 'picture-without-alt.docx',
      keyboard: false,
    });
    const scanned = await findingsShownInReport(page, testInfo, upload.scanId, 'picture-without-alt.docx');
    expect(scanned.issues.map(issueText)).toEqual(
      expect.arrayContaining([expect.stringMatching(/Image 1 is missing alternative text/), expect.stringMatching(/missing a title/)]),
    );
    const ai = await apiGet<{ primary_provider: string | null }>(page.request, '/api/ai/health');
    expect(ai.primary_provider, 'no vision model is configured').toBeNull();

    await remediateInTheUi(page, testInfo, upload.scanId, false);
    const saved = await downloadWorkingFile(page, 'remediated-picture-without-alt.docx', false);
    const job = await assertOutputMatchesRecord(page, upload.scanId, await readFile(source), saved.bytes, scanned);
    expect(job.fixed_count, 'only the title could be fixed').toBe(1);
    expect(job.manual_count, 'the picture is left to a person').toBe(1);
    expect(job.remaining_count).toBe(1);
    await expect(page.getByRole('heading', { name: /Manual work remaining \(1\)/ })).toBeVisible();
    await expect(page.getByText('Image 1 is missing alternative text')).toBeVisible();
    await assertNoBlockingViolations(page, 'remediation-partial-picture-without-alt.docx', testInfo);

    // The saved package: the title was added, and the picture still has no description at all.
    expect(await officePart(saved.path, 'docProps/core.xml')).toMatch(/<dc:title>[^<]+<\/dc:title>/);
    const pictureTags = (await officePart(saved.path, 'word/document.xml')).match(/<wp:docPr\b[^>]*>/g) ?? [];
    expect(pictureTags).toHaveLength(1);
    expect(pictureTags[0], 'no alternative text may be written without seeing the picture').not.toMatch(/\b(descr|title)="[^"]/);

    // A second scan of the saved bytes agrees: the picture is still reported.
    const rescan = await rescanSavedFile(page, testInfo, /^Word Documents/, saved.path);
    expect(rescan.issues.map(issueText)).toEqual([expect.stringMatching(/Image 1 is missing alternative text/)]);
  });

  test('an image the product cannot describe is left for human review, never invented', async ({ page }, testInfo) => {
    const source = `${fixtures}/powerpoint/image_heavy.pptx`;
    const upload = await scanThroughTheUi(page, testInfo, {
      card: /^PowerPoint/,
      file: source,
      name: 'image_heavy.pptx',
      keyboard: false,
    });
    const scanned = await findingsShownInReport(page, testInfo, upload.scanId, 'image_heavy.pptx');
    const missingAlt = scanned.issues.filter((i) => /missing alt text/i.test(issueText(i)));
    expect(missingAlt.length, 'the fixture has pictures without alt text').toBeGreaterThan(0);

    // The dashboard asks for AI help by default; this stack has no provider.
    const ai = await apiGet<{ primary_provider: string | null; fallback_provider: string | null }>(page.request, '/api/ai/health');
    expect(ai.primary_provider).toBeNull();
    expect(ai.fallback_provider).toBeNull();

    await remediateInTheUi(page, testInfo, upload.scanId, false);
    await expect(page.getByText('Manual review required')).toBeVisible({ timeout: 180_000 });
    await expect(page.getByText(/No partial artifact was published/)).toBeVisible();
    await expect(page.getByRole('button', { name: /Download/ })).toHaveCount(0);
    await assertNoBlockingViolations(page, 'remediation-withheld', testInfo);

    const job = await apiGet<RemediationJob>(page.request, `/education/scans/${upload.scanId}/remediation/latest`);
    expect(job.status).toBe('failed');
    expect(job.download_available).toBe(false);
    expect(job.artifact_id).toBeNull();
    expect(job.score_verified).toBe(false);
    expect(job.remediated_score).toBeNull();
    expect(job.fixed_count, 'no description was written for any picture').toBe(0);
    expect(job.remaining_count).toBe(job.total_issues);
    expect(job.manual_count + job.failed_count).toBeGreaterThanOrEqual(missingAlt.length);

    // Nothing was offered as a change either: the review queue holds no fabricated alt text.
    const review = await apiGet<{ fixes: { description: string; fixed_content: string | null }[] }>(
      page.request,
      `/api/reviews/${upload.scanId}`,
    );
    const invented = review.fixes.filter((f) => /alt text/i.test(f.description) && (f.fixed_content ?? '').trim() !== '');
    expect(invented, 'no alt-text suggestion may exist without a model that saw the image').toEqual([]);
    const download = await page.request.get(new URL(`/education/remediation/jobs/${job.job_id}/download`, 'http://localhost:18300').toString());
    expect(download.status()).toBe(404);
  });
});
