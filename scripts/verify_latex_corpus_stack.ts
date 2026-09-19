/** Existing #444 fixtures through the real authenticated queue and saved-file API. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { assertPreservedTex, assertUnverified, withReviewedLanguage } from './latex_corpus_contract.ts';

const digest = (bytes: Uint8Array) => createHash('sha256').update(bytes).digest('hex');

export async function verifyLatexCorpus(context: {
  request: (path: string, init?: RequestInit) => Promise<any>;
  download: (path: string) => Promise<Response>;
  poll: <T>(probe: () => Promise<T>, done: (value: T) => boolean) => Promise<T>;
  output: string;
  evidence: object[];
  failures: string[];
}) {
  const { request, download, poll, output, evidence, failures } = context;
  const root = 'tests/fixtures/latex_validation';
  const manifest = JSON.parse(await readFile(`${root}/corpus.json`, 'utf8'));
  assert.equal(manifest.cases.length, 11, 'Review scope and expectations when expanding the corpus');
  for (const originalFixture of manifest.cases) for (const reviewed of [false, true]) {
    const fixture = { ...originalFixture, id: reviewed ? `${originalFixture.id}-reviewed` : originalFixture.id,
      file: reviewed ? `reviewed-${originalFixture.file}` : originalFixture.file };
    try {
      const path = `${root}/${originalFixture.file}`;
      const baseline = await readFile(path);
      assert.equal(digest(baseline), originalFixture.sha256, 'Fixture must match its pinned source hash');
      const source = reviewed ? Buffer.from(withReviewedLanguage(baseline.toString('utf8'))) : baseline;
      fixture.sha256 = digest(source);
      await writeFile(resolve(output, `input-${fixture.file}`), source);
      const upload = async (bytes: Uint8Array, name: string) => {
        const form = new FormData();
        form.append('file', new Blob([bytes]), name);
        const queued = await request('/education/latex/scan?use_ollama=false', { method: 'POST', body: form });
        const result = await poll(() => request(`/education/scans/${queued.scan_id}`),
          (value: any) => ['completed', 'failed'].includes(value.scan?.status?.toLowerCase()));
        assert.equal(result.scan.status.toLowerCase(), 'completed');
        return { scan: result.scan, id: queued.scan_id };
      };
      const original = await upload(source, fixture.file);
      assert(original.scan.result.issues.length > 0, 'Corpus must exercise real findings');
      if (originalFixture.id === 'N03') {
        assert(original.scan.result.issues.some((row: any) => row.type === 'missing_alt_text'),
          'A caption on a missing image cannot supply an authored alternative');
      }
      const queued = await request(`/education/remediate/${original.id}?use_ai=false&verify_fixes=true`, {
        method: 'POST', headers: { Prefer: 'respond-async', 'Content-Type': 'application/json' },
        body: JSON.stringify({ use_ai: false, generate_alt_text: false, latex_formats: ['tex'] }),
      });
      assert(queued.job_id, 'A real durable remediation job is required');
      const job = await poll(() => request(`/education/remediation/jobs/${queued.job_id}`),
        (value: any) => ['completed', 'failed', 'cancelled', 'dead_letter'].includes(value.status));
      if (!reviewed || ['N03', 'N04'].includes(originalFixture.id)) {
        assert.equal(job.status, 'failed');
        assert.equal(job.error_code, 'manual_required');
        assert.equal(job.download_available, false);
        assert.equal(job.artifact_id, null);
        assert.equal(job.fixed_count, 0);
        assert.equal(job.remaining_count, original.scan.result.issues.length);
        assert.equal(job.score_verified, false);
        assert.equal(job.remediated_score, null);
        assert.equal(job.human_review_required, true);
        assertUnverified(job.latex_evidence.tex);
        assert.equal(job.latex_evidence.tex.source_sha256, fixture.sha256);
        assert.equal(job.latex_evidence.tex.source_check.status, originalFixture.id === 'N04' ? 'unavailable' : 'completed');
        const refused = await download(`/education/remediation/jobs/${queued.job_id}/download`);
        assert.equal(refused.status, 404);
        const reloaded = await request(`/education/scans/${original.id}/remediation/latest`);
        for (const field of ['job_id', 'status', 'artifact_id', 'download_available', 'fixed_count', 'remaining_count', 'score_verified', 'latex_evidence']) {
          assert.deepEqual(reloaded[field], job[field], `Refusal reload must preserve ${field}`);
        }
        const formats = await request(`/education/scans/${original.id}/remediated/formats`);
        assert.deepEqual(formats.available_formats, []);
        assert.equal(digest(await readFile(path)), originalFixture.sha256);
        evidence.push({ fixture: fixture.id, kind: 'latex', scan_id: original.id, job_id: job.job_id,
          status: 'refused', source_sha256: fixture.sha256, download_status: refused.status,
          latex_evidence: job.latex_evidence, expected_source_compilation: fixture.compile_expected,
          compilation: 'not_run', semantic_fidelity: 'not_assessed', assistive_technology: 'not_assessed' });
        console.log(`PASS ${fixture.id}: author review required, durable refusal and no download`);
        continue;
      }
      assert.equal(job.status, 'completed', `${fixture.id}: supported TEX repairs must produce a candidate`);
      assert.equal(job.download_available, true);
      assert(job.artifact_id);
      assert.equal(job.human_review_required, true);
      assert.equal(job.score_verified, true, 'Only the source scanner measurement is verified');
      assert(job.fixed_count > 0);
      assert.equal(job.total_issues, original.scan.result.issues.length);
      const response = await download(`/education/remediation/jobs/${queued.job_id}/download`);
      assert.equal(response.status, 200);
      assert(response.headers.get('content-type')?.startsWith('text/plain'));
      const saved = new Uint8Array(await response.arrayBuffer());
      assertPreservedTex(source.toString('utf8'), Buffer.from(saved).toString('utf8'));
      assert.equal(digest(await readFile(path)), originalFixture.sha256);
      const receipt = job.latex_evidence.tex;
      assertUnverified(receipt);
      assert.deepEqual(Object.keys(job.latex_evidence), ['tex']);
      assert.equal(receipt.source_sha256, fixture.sha256);
      assert.equal(receipt.candidate_sha256, digest(saved));
      assert.equal(receipt.source_check.status, 'completed');
      assert.equal(receipt.source_check.method, 'latex-source-v1');
      assert.equal(receipt.conversion.status, 'not_assessed', 'TEX delivery is not compilation');
      assert.equal(job.score_measurement.source_sha256, fixture.sha256);
      assert.equal(job.score_measurement.output_sha256, digest(saved));
      const rescan = await upload(saved, `saved-${fixture.file}`);
      assert.equal(rescan.scan.result.compliance_score, job.remediated_score);
      assert.equal(rescan.scan.result.issues.length, job.remaining_count);
      assert.equal(receipt.source_check.findings_count, job.remaining_count);
      const reloaded = await request(`/education/scans/${original.id}/remediation/latest`);
      for (const field of ['job_id', 'status', 'artifact_id', 'download_available', 'latex_evidence', 'score_measurement', 'human_review_required']) {
        assert.deepEqual(reloaded[field], job[field], `Fresh read must preserve ${field}`);
      }
      const repeated = await download(`/education/remediation/jobs/${queued.job_id}/download`);
      assert.equal(repeated.status, 200);
      assert.equal(digest(new Uint8Array(await repeated.arrayBuffer())), digest(saved));
      const formats = await request(`/education/scans/${original.id}/remediated/formats`);
      assert.deepEqual(formats.available_formats.map((row: any) => row.format), ['tex']);
      await writeFile(resolve(output, `saved-${fixture.file}`), saved);
      evidence.push({ fixture: fixture.id, kind: 'latex', scan_id: original.id, job_id: queued.job_id,
        artifact_id: job.artifact_id, rescan_id: rescan.id, source_sha256: fixture.sha256,
        output_sha256: digest(saved), score_measurement: job.score_measurement, latex_evidence: job.latex_evidence,
        expected_source_compilation: fixture.compile_expected, compilation: 'not_run',
        saved_source_preservation: 'passed', semantic_fidelity: 'not_assessed', assistive_technology: 'not_assessed' });
      console.log(`PASS ${fixture.id}: real queue, saved TEX preservation, rescan and conservative evidence`);
    } catch (error) {
      const failure = `${fixture.id}: ${error instanceof Error ? error.message : 'Unknown failure'}`;
      failures.push(failure);
      console.error(`FAIL ${failure}`);
    }
  }
}
