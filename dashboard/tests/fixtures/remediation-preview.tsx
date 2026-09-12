/** Local visual fixture: bun tests/fixtures/remediation-preview.tsx */
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import process from 'node:process';
import { renderToStaticMarkup } from 'react-dom/server';
import postcss from 'postcss';
import tailwind from '@tailwindcss/postcss';
import { ScoreComparison } from '../../src/components/ScoreComparison';
import { remediationScore } from '../../src/utils/remediationScore';
import type { RemediationScoreJob } from '../../src/utils/remediationScore';

const root = resolve(import.meta.dirname, '../..');
const port = Number(process.env.SCORE_PREVIEW_PORT || 4319);
const cssFile = resolve(root, 'src/index.css');
const css = (await postcss([tailwind({ base: root })]).process(await readFile(cssFile, 'utf8'), { from: cssFile })).css;
const makeJob = (score: number): RemediationScoreJob => ({
  original_score: 95.7, remediated_score: score, score_verified: true, human_review_required: false,
  score_measurement: { method_version: 'document-scan-v1', source_sha256: 'a'.repeat(64),
    output_sha256: 'b'.repeat(64), source_score: 95.7, output_score: score },
});
const cases: Record<string, RemediationScoreJob> = {
  improved: makeJob(98.2), unchanged: makeJob(95.7), lower: makeJob(95),
  missing: { score_verified: false, score_verification_reason: 'original_file_missing' },
  output_failed: { score_verified: false, score_verification_reason: 'output_scan_failed' },
  incomplete: { score_verified: false, score_verification_reason: 'incomplete_comparison' },
  mismatch: { ...makeJob(98.2), original_score: 75 },
  legacy: { original_score: 75, remediated_score: 100 },
  unknown_reason: { score_verified: false, score_verification_reason: 'Unrecognized server detail' },
  nonfinite: makeJob(Infinity),
};
createServer((request, response) => {
  if (request.url === '/fixture.css') {
    response.writeHead(200, { 'content-type': 'text/css' }); response.end(css); return;
  }
  const variant = new URL(request.url || '/', 'http://localhost').searchParams.get('case') || 'lower';
  const job = Object.hasOwn(cases, variant) ? cases[variant] : cases.lower;
  const scan = { compliance_score: 95.7 };
  const comparison = remediationScore(job, scan);
  response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
  response.end(`<!doctype html><html lang="en" class="dark"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/fixture.css"><title>Local dashboard result fixture</title></head><body><main style="max-width:900px;margin:32px auto;padding:16px"><p style="margin-bottom:24px">Local UI fixture • synthetic scan</p>${renderToStaticMarkup(<section className="card"><h1 className="text-xl font-bold mb-4">{comparison.title}</h1><ScoreComparison job={job} scan={scan} /></section>)}</main></body></html>`);
}).listen(port, '127.0.0.1', () => process.stdout.write(`Dashboard fixture: http://127.0.0.1:${port}/?case=lower\n`));
