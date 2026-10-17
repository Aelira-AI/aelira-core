import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { assertNoBlockingViolations } from './support/a11y';
import { apiGet, scanIdFromUrl } from './support/scans';
import { dismissCookieBanner } from './support/stack';
import { pdfInfo } from './support/files';

const clip = resolve(import.meta.dirname, '../../../tests/journeys/fixtures/teaching-clip.mp4');
const spoken = 'Welcome to the biology lab. Today we measure plant growth and record the results in a table.';

interface Cue {
  start_time: number;
  end_time: number;
  text: string;
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
  await assertNoBlockingViolations(page, 'media-scan-complete', testInfo);
  await page.getByRole('link', { name: 'View Full Report' }).click();
  await expect(page.getByRole('heading', { name: 'Rule-based findings (2)' })).toBeVisible();
  const scanId = scanIdFromUrl(page.url());

  const record = await apiGet<{
    scan: {
      status: string;
      result: {
        compliance_score: number;
        issues: { rule: string; message: string }[];
        structure: {
          duration: number;
          transcription_count: number;
          audio_descriptions_count: number;
          has_captions: boolean;
          verification_passed: boolean;
          manual_review_required: boolean;
          transcription_output: {
            transcription: { segments: Cue[]; full_text: string };
            captions: { webvtt: string; srt: string };
          };
        };
      };
    };
  }>(page.request, `/education/scans/${scanId}`);
  expect(record.scan.status.toLowerCase()).toBe('completed');
  const { structure, issues } = record.scan.result;

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

  // The dashboard's own downloadable output for the scan is a PDF report of exactly these findings.
  const pending = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Report', exact: true }).click();
  const download = await pending;
  const reportPath = resolve(import.meta.dirname, '../../journey-results/downloads/media-report.pdf');
  await download.saveAs(reportPath);
  expect((await readFile(reportPath)).subarray(0, 4).toString()).toBe('%PDF');
  expect((await pdfInfo(reportPath)).Pages).toBe('1');

  // Remediation cannot caption media; it must say so and offer no file.
  await page.getByRole('link', { name: 'Open Remediation Review' }).click();
  await page.getByRole('button', { name: 'Start Remediation' }).click();
  await expect(page.getByText('Manual review required')).toBeVisible({ timeout: 180_000 });
  await expect(page.getByRole('button', { name: /Download/ })).toHaveCount(0);
  await assertNoBlockingViolations(page, 'media-remediation-unsupported', testInfo);
  const job = await apiGet<{ status: string; download_available: boolean; artifact_id: string | null; fixed_count: number }>(
    page.request,
    `/education/scans/${scanId}/remediation/latest`,
  );
  expect(job.status).toBe('failed');
  expect(job.download_available).toBe(false);
  expect(job.artifact_id).toBeNull();
  expect(job.fixed_count).toBe(0);
});
