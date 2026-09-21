/** Existing #444 fixtures through the real authenticated queue and saved-file API. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir, mkdtemp } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { execFileSync } from 'node:child_process';
import { assertGermanMetadata, assertPreservedTex, assertUnverified, withReviewedLanguage } from './latex_corpus_contract.ts';

const digest = (bytes: Uint8Array) => createHash('sha256').update(bytes).digest('hex');

export async function verifyLatexCorpus(context: {
  request: (path: string, init?: RequestInit) => Promise<any>;
  download: (path: string, init?: RequestInit) => Promise<Response>;
  poll: <T>(probe: () => Promise<T>, done: (value: T) => boolean) => Promise<T>;
  output: string;
  evidence: object[];
  failures: string[];
}) {
  const { request, download, poll, output, evidence, failures } = context;
  const root = 'tests/fixtures/latex_research';
  const manifest = JSON.parse(await readFile(`${root}/corpus.json`, 'utf8'));
  assert.deepEqual(manifest.cases.map((c: any) => c.id), [
    ...Array.from({ length: 18 }, (_, n) => `M${String(n + 1).padStart(2, '0')}`),
    'P01', 'P02', 'P03', 'P04', 'N01', 'N02', 'N03', 'N04',
  ], 'All research cases must be accounted for');
  assert.equal(manifest.source_count, 28);
  assert.equal(manifest.cases.flatMap((c: any) => c.sources).length, 28);
  for (const item of manifest.cases) {
    assert.equal(item.domain_human_review, 'not_run');
    assert.equal(item.assistive_technology, 'not_run');
    for (const file of item.sources) {
      assert(file.path.startsWith(`${item.id}/`) && !file.path.split('/').some((p: string) => !p || p === '.' || p === '..'));
      assert.equal(digest(await readFile(`${root}/${file.path}`)), file.sha256);
    }
  }
  for (const entry of manifest.cases.filter((c: any) => c.id !== 'P01')) for (const reviewed of [false, true]) {
    const originalFixture = { ...entry, file: `${entry.id}.tex`, sha256: entry.sources.find((s: any) => s.path === entry.entrypoint).sha256 };
    const fixture = { ...originalFixture, id: reviewed ? `${originalFixture.id}-reviewed` : originalFixture.id,
      file: reviewed ? `reviewed-${originalFixture.file}` : originalFixture.file };
    try {
      const path = `${root}/${originalFixture.entrypoint}`;
      const baseline = await readFile(path);
      assert.equal(digest(baseline), originalFixture.sha256, 'Fixture must match its pinned source hash');
      const source = reviewed ? Buffer.from(withReviewedLanguage(baseline.toString('utf8'), entry.id === 'P04' ? 'de' : 'en')) : baseline;
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
      if (entry.id === 'P04') assert.equal(original.scan.result.issues.length, 0, 'Authored German fixture has no source findings');
      else assert(original.scan.result.issues.length > 0, 'Corpus must exercise real findings');
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
      if (entry.id === 'P04') {
        assert.equal(job.status, 'completed');
        assert.equal(job.fixed_count, 0);
        assert.equal(job.total_issues, 0);
        assert.equal(job.download_available, false);
        assert.equal(job.artifact_id, null);
        assert.equal(job.score_verified, false);
        assert.equal(job.remediated_score, null);
        const withheld = await download(`/education/remediation/jobs/${job.job_id}/download`);
        assert.equal(withheld.status, 404);
        const reloaded = await request(`/education/scans/${original.id}/remediation/latest`);
        for (const field of ['job_id', 'status', 'artifact_id', 'download_available', 'fixed_count', 'total_issues', 'score_verified']) assert.deepEqual(reloaded[field], job[field]);
        const formats = await request(`/education/scans/${original.id}/remediated/formats`);
        assert.deepEqual(formats.available_formats, []);
        assertGermanMetadata(source.toString('utf8'));
        assert.equal(digest(await readFile(path)), originalFixture.sha256);
        evidence.push({ fixture: fixture.id, kind: 'latex', status: 'no_op', scan_id: original.id, job_id: job.job_id,
          source_sha256: fixture.sha256, source_findings: 0, fixed_count: 0, declared_language: 'de', download_status: 404,
          expected_source_compilation: fixture.compile_expected, compilation: 'not_run',
          semantic_fidelity: 'not_assessed', assistive_technology: 'not_assessed' });
        console.log(`PASS ${fixture.id}: authored German retained, zero findings, durable no-op and no invented repair`);
        continue;
      }
      const expectedRefusal = (!reviewed && entry.id !== 'P04') || ['P02', 'P03', 'N03', 'N04'].includes(entry.id);
      if (expectedRefusal) {
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
      if (entry.id === 'P04') assertGermanMetadata(Buffer.from(saved).toString('utf8'));
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
      evidence.push({ fixture: fixture.id, kind: 'latex', status: 'delivered', scan_id: original.id, job_id: queued.job_id,
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
  const project = manifest.cases.find((c: any) => c.id === 'P01');
  for (const reviewed of [false, true]) {
    const id = reviewed ? 'P01-reviewed' : 'P01';
    try {
      const directory = await mkdtemp(resolve(output, 'project-input-'));
      const sourceFiles = [];
      for (const file of project.sources) {
        const original = await readFile(`${root}/${file.path}`);
        const bytes = reviewed && file.path === project.entrypoint ? Buffer.from(withReviewedLanguage(original.toString('utf8'))) : original;
        const relative = file.path.slice('P01/'.length);
        await mkdir(dirname(resolve(directory, relative)), { recursive: true });
        await writeFile(resolve(directory, relative), bytes);
        sourceFiles.push({ path: file.path, sha256: digest(bytes) });
      }
      const archive = resolve(output, reviewed ? 'input-reviewed-P01.zip' : 'input-P01.zip');
      execFileSync('zip', ['-q', '-X', archive, ...project.sources.map((f: any) => f.path.slice(4))], { cwd: directory });
      const bytes = await readFile(archive);
      const form = new FormData();
      form.append('file', new Blob([bytes]), 'P01.zip');
      form.append('entry_file', 'main.tex');
      const queued = await request('/education/latex/projects?use_ollama=false', { method: 'POST', body: form });
      const terminal = await poll(() => request(`/education/scans/${queued.scan_id}`),
        (v: any) => ['completed', 'failed'].includes(v.scan?.status?.toLowerCase()));
      assert.equal(terminal.scan.status.toLowerCase(), 'completed');
      const report = await request(`/education/latex/projects/${queued.scan_id}`);
      assert.equal(report.state, 'resolved');
      assert.equal(report.human_review_required, true);
      assert.equal(report.manifest.entry, 'main.tex');
      const expectedMembers = sourceFiles.map((f) => ({ path: f.path.slice(4), sha256: f.sha256 })).sort((a, b) => a.path.localeCompare(b.path));
      assert.deepEqual(report.manifest.files.map((f: any) => ({ path: f.path, sha256: f.sha256 })).sort((a: any, b: any) => a.path.localeCompare(b.path)), expectedMembers);
      const sourceHash = digest(Buffer.from(JSON.stringify(Object.fromEntries(expectedMembers.map(f => [f.path, f.sha256])))));
      assert.equal(report.manifest.archive_sha256, digest(bytes));
      assert.equal(report.manifest.source_sha256, sourceHash);
      assert.equal(report.conversion.archive_sha256, digest(bytes));
      assert.equal(report.conversion.source_sha256, sourceHash);
      const original = await download(report.original_url);
      assert.equal(original.status, 200);
      assert.equal(digest(new Uint8Array(await original.arrayBuffer())), digest(bytes));
      assert.equal(report.html_url, null);
      assert.equal(report.conversion.status, 'refused');
      assert.deepEqual(report.conversion.reasons, ['unsupported_command']);
      assert.equal(report.conversion.output_sha256, null);
      assert.equal(report.conversion.accessibility_status, 'not_verified');
      assert.equal(report.conversion.human_review_required, true);
      assert.equal(report.conversion.decision.selected_route, 'pandoc');
      assert.notEqual(report.conversion.tool_version, 'unknown');
      const withheldHtml = await download(`/education/latex/projects/${queued.scan_id}/html`);
      assert.equal(withheldHtml.status, 404);
      // Automatic project edits are explicitly unsupported, even when inspection succeeds.
      const remediation = await download(`/education/remediate/${queued.scan_id}?use_ai=false&verify_fixes=true`, {
        method: 'POST', headers: { Prefer: 'respond-async', 'Content-Type': 'application/json' },
        body: JSON.stringify({ use_ai: false, generate_alt_text: false, latex_formats: ['tex'] }),
      });
      assert.equal(remediation.status, 400);
      const refusal = await remediation.json();
      assert.equal(refusal.detail.code, 'project_source_review_required');
      const reloaded = await request(`/education/latex/projects/${queued.scan_id}`);
      assert.deepEqual(reloaded, report);
      evidence.push({ fixture: id, kind: 'latex_project', status: 'refused', scan_id: queued.scan_id,
        source_sha256: sourceFiles.find((f) => f.path === project.entrypoint)!.sha256, source_files: sourceFiles,
        entrypoint: project.entrypoint, archive_sha256: digest(bytes), downloaded_original_sha256: digest(bytes),
        conversion: report.conversion, html_available: report.html_url !== null, error_code: refusal.detail.code,
        output_sha256: null,
        remediation_http_status: remediation.status,
        compilation: 'not_run', semantic_fidelity: 'not_assessed', assistive_technology: 'not_assessed' });
      console.log(`PASS ${id}: real project queue, intact archive, durable automatic-edit refusal`);
    } catch (error) {
      const failure = `${id}: ${error instanceof Error ? error.message : 'Unknown failure'}`;
      failures.push(failure);
      console.error(`FAIL ${failure}`);
    }
  }
}
