import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { assertNoBlockingViolations } from './support/a11y';
import { apiGet, scanIdFromUrl } from './support/scans';
import { apiUrl, dismissCookieBanner } from './support/stack';

const clip = resolve(import.meta.dirname, '../../../tests/journeys/fixtures/teaching-clip.mp4');
const spoken = 'Welcome to the biology lab. Today we measure plant growth and record the results in a table.';

interface Cue {
  start_time: number;
  end_time: number;
  text: string;
}

interface FlashingAssessment {
  state: 'unassessed';
  reason: 'detector_unavailable' | 'disabled' | 'analysis_failed';
  version: 'unassessed-v1';
}

/** Lower-case words only, so punctuation and capitalisation cannot decide the verdict. */
const words = (text: string) => text.toLowerCase().replace(/[^a-z ]+/g, ' ').split(/\s+/).filter(Boolean);

test('media: a synthetic teaching clip is transcribed for real, captions are well formed, unsupported steps are reported', async ({ page }, testInfo) => {
  await page.goto('/upload');
  await dismissCookieBanner(page);
  await page.getByRole('button', { name: /^Videos/ }).click();
  await assertNoBlockingViolations(page, 'media-options', testInfo);
  await page.locator('input[type=file]').setInputFiles(clip);
  await expect(page.getByText('teaching-clip.mp4', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: /^Scan 1 file/ }).click();
  await expect(page.getByText('teaching-clip.mp4 scan complete', { exact: true })).toBeVisible({ timeout: 240_000 });
  await expect(page.getByText('Overall compliance unassessed.', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Download Report' })).toHaveCount(0);
  await assertNoBlockingViolations(page, 'media-scan-complete', testInfo);
  await page.getByRole('link', { name: 'View Full Report' }).click();
  await expect(page.getByRole('heading', { name: 'Rule-based findings (2)' })).toBeVisible();
  await expect(page.getByText('Unverified', { exact: true })).toBeVisible();
  await expect(page.getByText(
    'Overall compliance is unassessed. Available source findings are shown below; video flashing safety has not been established.',
    { exact: true },
  )).toBeVisible();
  const scanId = scanIdFromUrl(page.url());

  const record = await apiGet<{
    scan: {
      status: string;
      result: {
        compliance_score: number | null;
        wcag_level: string | null;
        issues: { rule: string; message: string }[];
        structure: {
          duration: number;
          transcription_count: number;
          audio_descriptions_count: number;
          has_captions: boolean;
          verification_passed: boolean;
          manual_review_required: boolean;
          flashing_assessment: FlashingAssessment;
          transcription_output: {
            compliance_score: number | null;
            flashing_assessment: FlashingAssessment;
            issues_count: number;
            issues: { rule: string; message: string }[];
            transcription: { segments: Cue[]; full_text: string };
            captions: { webvtt: string; srt: string };
          };
        };
      };
    };
  }>(page.request, `/education/scans/${scanId}`);
  expect(record.scan.status.toLowerCase()).toBe('completed');
  const { structure, issues } = record.scan.result;

  // A finished transcription does not establish an overall video score or flashing safety.
  expect(record.scan.result.compliance_score).toBeNull();
  expect(record.scan.result.wcag_level).toBeNull();
  expect(structure.flashing_assessment).toEqual({
    state: 'unassessed', reason: 'detector_unavailable', version: 'unassessed-v1',
  });
  expect(structure.transcription_output.compliance_score).toBeNull();
  expect(structure.transcription_output.flashing_assessment).toEqual(structure.flashing_assessment);
  expect(structure).not.toHaveProperty('flashing_analysis');
  expect(structure.transcription_output).not.toHaveProperty('flashing_analysis');
  expect(structure.transcription_output.issues_count).toBe(2);
  expect(structure.transcription_output.issues.map((issue) => issue.rule))
    .toEqual(['WCAG 1.2.2', 'WCAG 1.2.3']);
  expect([...issues, ...structure.transcription_output.issues].map((issue) => issue.message).join(' '))
    .not.toMatch(/(?:dangerous|problematic) flashing (?:content )?detected/i);

  // Real speech recognition on the clip's real audio: the words that were spoken, in order.
  const transcript = structure.transcription_output.transcription;
  expect(transcript.segments.length).toBeGreaterThan(0);
  expect(words(transcript.full_text)).toEqual(words(spoken));

  // The caption files the product built are well formed and fit inside the clip.
  const { webvtt, srt } = structure.transcription_output.captions;
  expect(webvtt.startsWith('WEBVTT')).toBe(true);
  const cues = [...webvtt.matchAll(/(\d\d):(\d\d):(\d\d)\.(\d{3}) --> (\d\d):(\d\d):(\d\d)\.(\d{3})/g)];
  expect(cues.length).toBe(transcript.segments.length);
  let previousEnd = 0;
  for (const cue of cues) {
    const [start, end] = [1, 5].map((i) => Number(cue[i]) * 3600 + Number(cue[i + 1]) * 60 + Number(cue[i + 2]) + Number(cue[i + 3]) / 1000);
    expect(start).toBeGreaterThanOrEqual(previousEnd);
    expect(end).toBeGreaterThan(start);
    expect(end).toBeLessThanOrEqual(structure.duration + 0.5);
    previousEnd = end;
  }
  const srtSpeech = srt
    .split('\n')
    .filter((line) => line && !/^\d+$/.test(line) && !line.includes('-->'))
    .map((line) => line.replace(/^\[[^\]]*\]:\s*/, ''))
    .join(' ');
  expect(words(srtSpeech)).toEqual(words(spoken));
  expect(srt).toMatch(/^1\n00:00:00,000 --> /);
  // Speakers are never identified: no WebVTT voice span and no "[Name]:" SRT prefix.
  expect(webvtt).not.toContain('<v ');
  expect(srt.split('\n').filter((line) => /^\[[^\]]*\]:/.test(line))).toEqual([]);

  // The score and findings describe the uploaded file, which has no caption track of its own.
  expect(structure.has_captions).toBe(false);
  expect(issues.map((i) => i.rule)).toEqual(['WCAG 1.2.2', 'WCAG 1.2.3']);
  expect(structure.verification_passed, 'a generated caption file is not a verified accessibility result').toBe(false);
  expect(structure.manual_review_required).toBe(true);

  // Audio description needs a vision model; none is configured, so it is reported, not invented.
  expect(structure.audio_descriptions_count).toBe(0);
  await expect(page.getByText('Audio description not provided for visual content')).toBeVisible();
  const ai = await apiGet<{ primary_provider: string | null }>(page.request, '/api/ai/health');
  expect(ai.primary_provider).toBeNull();

  // Partial findings remain visible, but an unassessed scan cannot issue a compliance PDF.
  await expect(page.getByRole('button', { name: 'Report', exact: true })).toHaveCount(0);
  const reportResponse = await page.request.get(new URL(`/education/scans/${scanId}/report`, apiUrl).toString());
  expect(reportResponse.status()).toBe(409);
  expect(await reportResponse.json()).toEqual({
    detail: 'A compliance PDF is unavailable for an unassessed scan; partial findings remain in scan details.',
  });

  // Remediation delivers the generated captions as a separate file flagged for review.
  // Their structure is checked against the transcription; their accuracy is not.
  await page.getByRole('link', { name: 'Open Remediation Review' }).click();
  await page.getByRole('button', { name: 'Start Remediation' }).click();
  await expect(page.getByRole('heading', { name: 'Output needs review' })).toBeVisible({ timeout: 180_000 });
  await assertNoBlockingViolations(page, 'media-remediation-captions', testInfo);
  const pending = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download improved working file' }).click();
  const download = await pending;
  expect(download.suggestedFilename(), 'captions never take the media name').toBe('teaching-clip-captions.vtt');
  const delivered = (await readFile(await download.path())).toString('utf8');
  expect(delivered.startsWith('WEBVTT\n')).toBe(true);
  expect(delivered, 'the delivered captions assert no speaker').not.toContain('<v ');
  const deliveredSpeech = delivered
    .split('\n')
    .filter((line) => line && line !== 'WEBVTT' && !/^\d+$/.test(line) && !line.includes('-->'))
    .join(' ');
  expect(words(deliveredSpeech)).toEqual(words(spoken));
  const job = await apiGet<{
    status: string;
    download_available: boolean;
    artifact_id: string | null;
    fixed_count: number;
    human_review_required: boolean;
  }>(page.request, `/education/scans/${scanId}/remediation/latest`);
  expect(job.status).toBe('completed');
  expect(job.download_available).toBe(true);
  expect(job.artifact_id).not.toBeNull();
  expect(job.fixed_count).toBe(1);
  expect(job.human_review_required, 'generated captions always need human review').toBe(true);

  await page.goto('/dashboard');
  const recentRow = page.getByRole('table', { name: 'Recent scans' }).getByRole('row').filter({ hasText: 'teaching-clip.mp4' });
  await expect(recentRow).toContainText('Unverified');
  await expect(recentRow.getByRole('button', { name: 'View teaching-clip.mp4' })).toBeVisible();
  await expect(recentRow.getByRole('button', { name: 'Remediate teaching-clip.mp4' })).toBeVisible();
  await expect(recentRow.getByRole('button', { name: 'Download report for teaching-clip.mp4' })).toHaveCount(0);

  await page.goto('/history');
  const historyRow = page.getByRole('table', { name: /Scan history showing/ }).getByRole('row').filter({ hasText: 'teaching-clip.mp4' });
  await expect(historyRow).toContainText('Unverified');
  await expect(historyRow.getByRole('button', { name: 'View details for teaching-clip.mp4' })).toBeVisible();
  await expect(historyRow.getByRole('button', { name: 'Open remediation review for teaching-clip.mp4' })).toBeVisible();
  await expect(historyRow.getByRole('button', { name: 'Download PDF report for teaching-clip.mp4' })).toHaveCount(0);

  await page.setViewportSize({ width: 390, height: 844 });
  const mobileCard = page.locator('.lg\\:hidden .card').filter({ hasText: 'teaching-clip.mp4' });
  await expect(mobileCard).toContainText('Unverified');
  await expect(mobileCard.getByRole('button', { name: 'View details for teaching-clip.mp4' })).toBeVisible();
  await expect(mobileCard.getByRole('button', { name: 'Open remediation review for teaching-clip.mp4' })).toBeVisible();
  await expect(mobileCard.getByRole('button', { name: 'Download report for teaching-clip.mp4' })).toHaveCount(0);
});
