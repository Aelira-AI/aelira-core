/** #444 saved HTML through the real scan, queue, worker and managed download. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { assertPreservedTex, assertUnverified, withReviewedLanguage } from './latex_corpus_contract.ts';
import { assertSavedHtml } from './latex_html_corpus_contract.ts';

const digest = (bytes: Uint8Array) => createHash('sha256').update(bytes).digest('hex');
const IDs = [...Array.from({ length: 18 }, (_, n) => `M${String(n + 1).padStart(2, '0')}`),
  'P01', 'P02', 'P03', 'P04', 'N01', 'N02', 'N03', 'N04'];
const expectedRefusal = new Set(['P02', 'P03', 'N01', 'N02', 'N03', 'N04']);
const environmentalReasons = new Set(['tool_unavailable', 'unmeasured_tool_version', 'package_route_unavailable',
  'language_environment_unavailable', 'timeout', 'diagnostics_truncated']);
const sourceFailureCodes: Record<string, string[]> = {
  N01: ['unsupported_command'], N02: ['missing_dependency'], N03: ['missing_asset'], N04: ['malformed_expression'],
};
const contentFailureCodes = new Set(['raw_tex', 'error_node', 'missing_mathml', 'unresolved_reference',
  'metadata_ambiguous', 'metadata_unsupported', 'metadata_not_preserved', 'semantics_unconfirmed',
  'semantics_unsupported', 'semantics_not_preserved']);
const exactVersion = /^\d+(?:\.\d+){1,3}$/;

const intendedCodes = (id: string) => id in sourceFailureCodes ? new Set(sourceFailureCodes[id]) :
  id === 'M10' ? new Set([...contentFailureCodes, 'unsupported_command']) : contentFailureCodes;

/** A nonzero converter exit must be explained by the declared source defect in that same stage. */
export function assertNoEnvironmentFailure(diagnostics: any, id = '', reviewedSource = '') {
  const n02Line = reviewedSource.split('\n').findIndex(line => line.trim() === '\\input{chapters/absent.tex}') + 1;
  for (const stage of diagnostics?.stages || []) {
    const findings = stage.diagnostics || [];
    assert(!findings.some((finding: any) => environmentalReasons.has(finding.code)),
      'Environment or truncated diagnostics block corpus classification');
    assert(stage.exit_code == null || (Number.isInteger(stage.exit_code) && stage.exit_code >= 0 && stage.exit_code < 128),
      'Signal-terminated or invalid converter exit blocks corpus classification');
    for (const finding of findings) if (finding.code === 'missing_dependency') {
      assert.equal(id, 'N02', 'Unrelated missing dependency blocks corpus classification');
      assert(n02Line > 0 && [n02Line, n02Line + 1].includes(finding.source_line),
        'Missing dependency must identify the authored absent include line');
    }
    if (findings.some((finding: any) => finding.code === 'process_failed') ||
      (typeof stage.exit_code === 'number' && stage.exit_code !== 0)) {
      const sourceError = new Set([...(sourceFailureCodes[id] || []), ...(id === 'M10' ? ['unsupported_command'] : [])]);
      assert(findings.some((finding: any) => sourceError.has(finding.code) &&
        (finding.code !== 'missing_dependency' || [n02Line, n02Line + 1].includes(finding.source_line))),
      'A crashed or failed converter is not an intended source refusal');
    }
    if (['latexml', 'latexmlpost', 'pandoc'].includes(stage.tool))
      assert.match(String(stage.version), exactVersion, `${stage.tool} stage has an exact measured version`);
  }
  const decision = diagnostics?.decision;
  if (decision) {
    assert(!decision.reasons.some((reason: string) => environmentalReasons.has(reason)),
      'Environment failure cannot count as a safe refusal');
    assert.match(String(decision.tool_versions?.[decision.selected_route]), exactVersion,
      'Selected converter has an exact measured version');
  }
}

export function assertIntendedRefusal(id: string, diagnostics: any, reviewedSource: string) {
  assertNoEnvironmentFailure(diagnostics, id, reviewedSource);
  const intended = intendedCodes(id);
  assert(diagnostics?.stages?.some((stage: any) => stage.diagnostics?.some((finding: any) =>
    intended.has(finding.code) &&
    (finding.code !== 'missing_dependency' || (() => {
      const line = reviewedSource.split('\n').findIndex(value => value.trim() === '\\input{chapters/absent.tex}') + 1;
      return line > 0 && [line, line + 1].includes(finding.source_line);
    })()))), `${id}: refusal lacks its declared source or content-loss diagnostic`);
}

export async function verifyLatexHtmlCorpus(context: {
  request: (path: string, init?: RequestInit) => Promise<any>;
  download: (path: string, init?: RequestInit) => Promise<Response>;
  poll: <T>(probe: () => Promise<T>, done: (value: T) => boolean) => Promise<T>;
  output: string;
  priorEvidence: object[];
  revision: string;
  harnessHashes: Record<string, string>;
  trackedDiffHash: string;
}) {
  const { request, download, poll, output, priorEvidence } = context;
  const root = 'tests/fixtures/latex_research';
  const bytes = await readFile(`${root}/corpus.json`);
  const manifest = JSON.parse(bytes.toString());
  assert.deepEqual(manifest.cases.map((item: any) => item.id), IDs);
  assert.equal(manifest.source_count, 28);
  const evidence: object[] = [];
  const failures: string[] = [];
  const startedAt = Date.now();
  for (const entry of manifest.cases) {
    try {
      for (const source of entry.sources) assert.equal(digest(await readFile(`${root}/${source.path}`)), source.sha256);
      if (entry.id === 'P01') {
        const linked = priorEvidence.filter((row: any) => row.fixture === 'P01' || row.fixture === 'P01-reviewed') as any[];
        assert.equal(linked.length, 2, 'Project evidence must already exist in the real TEX/project journey');
        for (const row of linked) {
          assert.equal(row.status, 'refused');
          assert.equal(row.conversion.status, 'refused');
          assert.equal(row.html_available, false);
          assert.equal(row.remediation_http_status, 400);
          assert.equal(row.downloaded_original_sha256, row.archive_sha256);
        }
        evidence.push({ fixture: 'P01', status: 'linked_project_refusal', linked_cases: linked.map(row => row.fixture),
          source_files: entry.sources, archive_sha256: linked.map(row => row.archive_sha256),
          conversion: linked.map(row => row.conversion), html_available: false });
        console.log('PASS HTML P01: linked intact project archive and safe conversion refusal');
        continue;
      }
      const original = await readFile(`${root}/${entry.entrypoint}`);
      const sourceRecord = entry.sources.find((s: any) => s.path === entry.entrypoint);
      assert.equal(digest(original), sourceRecord.sha256);
      const reviewed = Buffer.from(withReviewedLanguage(original.toString(), entry.id === 'P04' ? 'de' : 'en'));
      assertPreservedTex(original.toString(), reviewed.toString());
      const sourceHash = digest(reviewed);
      await writeFile(resolve(output, `html-input-${entry.id}.tex`), reviewed);
      const form = new FormData();
      form.append('file', new Blob([reviewed]), `html-${entry.id}.tex`);
      const queuedScan = await request('/education/latex/scan?use_ollama=false', { method: 'POST', body: form });
      assert(queuedScan.scan_id);
      const scanResult = await poll(() => request(`/education/scans/${queuedScan.scan_id}`),
        (v: any) => ['completed', 'failed'].includes(v.scan?.status?.toLowerCase()));
      assert.equal(scanResult.scan.status.toLowerCase(), 'completed', 'Source scan must complete before evaluating export');
      const findings = scanResult.scan.result.issues.length;
      const queued = await request(`/education/remediate/${queuedScan.scan_id}?use_ai=false&verify_fixes=true`, {
        method: 'POST', headers: { Prefer: 'respond-async', 'Content-Type': 'application/json' },
        body: JSON.stringify({ use_ai: false, generate_alt_text: false, latex_formats: ['html'] }),
      });
      assert(queued.job_id, 'HTML journey requires a durable queue job');
      const job = await poll(() => request(`/education/remediation/jobs/${queued.job_id}`),
        (v: any) => ['completed', 'failed', 'cancelled', 'dead_letter'].includes(v.status));
      const latest = await request(`/education/scans/${queuedScan.scan_id}/remediation/latest`);
      for (const field of ['job_id', 'status', 'artifact_id', 'download_available', 'fixed_count', 'remaining_count',
        'remediated_score', 'score_verified', 'latex_evidence', 'human_review_required']) {
        assert.deepEqual(latest[field], job[field], `Reload preserves ${field}`);
      }
      assert.equal(job.total_issues, findings);
      assert.equal(job.human_review_required, true);
      assert.equal(job.score_verified, false, 'Saved HTML has no verified accessibility score');
      assert.equal(job.remediated_score, null);
      const receipt = job.latex_evidence?.html;
      const decision = receipt?.conversion_diagnostics?.decision;
      if (receipt) {
        assertUnverified(receipt);
        assert.equal(receipt.representation, 'html');
        assert.equal(receipt.source_sha256, sourceHash);
        const diagnostics = receipt.conversion_diagnostics;
        if (diagnostics) {
          assertNoEnvironmentFailure(diagnostics, entry.id, reviewed.toString());
          assert.equal(diagnostics.source_sha256, job.latex_evidence?.tex?.candidate_sha256,
            'Converter input binds the saved reviewed TEX candidate');
          if (decision) {
            assert.equal(decision.source_sha256, diagnostics.source_sha256,
              'Conversion decision binds the exact TEX passed to the converter');
            assert.equal(decision.profile, 'single-source-html');
            assert.equal(decision.support_scope, 'declared-synthetic-controls-only');
            assert(decision.selected_route === 'latexml' || decision.selected_route === 'pandoc');
            for (const [tool, version] of Object.entries(decision.tool_versions || {})) {
              assert.match(String(version), exactVersion, `${tool} has an exact measured version`);
            }
            assert(Object.keys(decision.tool_versions || {}).length > 0, 'Converter tool versions must be measured');
          }
        }
      }
      const path = `/education/remediation/jobs/${queued.job_id}/download`;
      const response = await download(path);
      const formats = await request(`/education/scans/${queuedScan.scan_id}/remediated/formats`);
      const compatible = await download(`/education/scans/${queuedScan.scan_id}/remediated?format=html`);
      const base = { fixture: entry.id, status: job.status, scan_id: queuedScan.scan_id, job_id: queued.job_id,
        manifest_sha256: digest(bytes), original_source_sha256: sourceRecord.sha256, source_sha256: sourceHash,
        source_preservation: 'passed', findings, tool_versions: decision?.tool_versions ?? null, decision: decision ?? null,
        converter_input_sha256: receipt?.conversion_diagnostics?.source_sha256 ?? null,
        conversion_diagnostics: receipt?.conversion_diagnostics ?? null,
        human_review_required: job.human_review_required, accessibility_status: receipt?.accessibility_status ?? 'not_verified',
        fidelity: receipt?.fidelity ?? { status: 'not_assessed', method: 'none', findings_count: null },
        human_review: receipt?.human_review ?? { status: 'not_assessed', method: 'none', findings_count: null },
        assistive_technology: receipt?.assistive_technology ?? { status: 'not_assessed', method: 'none', findings_count: null } };
      if (entry.id === 'P04' && findings === 0) {
        assert.equal(job.status, 'completed');
        assert.equal(job.fixed_count, 0);
        assert.equal(job.artifact_id, null);
        assert.equal(job.download_available, false);
        assert.equal(response.status, 404);
        assert.equal(compatible.status, 404);
        assert.deepEqual(formats.available_formats, []);
        evidence.push({ ...base, outcome: 'no_op', conversion_observation: 'not_run', download_status: 404 });
        console.log('PASS HTML P04: German source, zero findings, durable no-op');
      } else if (job.download_available) {
        assert(!expectedRefusal.has(entry.id), `${entry.id}: negative/manual case delivered an artifact`);
        assert.equal(job.status, 'completed');
        assert.equal(response.status, 200);
        assert.equal(compatible.status, 200);
        assert.match(response.headers.get('content-type') || '', /^text\/html/);
        assert.match(compatible.headers.get('content-type') || '', /^text\/html/);
        assert(job.artifact_id);
        assert.equal(formats.available_formats.length, 1);
        assert.equal(formats.available_formats[0].format, 'html', 'HTML-only cannot publish a TEX fallback');
        const saved = new Uint8Array(await response.arrayBuffer());
        const savedHash = digest(saved);
        assert.equal(digest(new Uint8Array(await compatible.arrayBuffer())), savedHash);
        assert.equal(receipt?.candidate_sha256, savedHash);
        assert.equal(receipt?.conversion.status, 'completed');
        assert.equal(receipt?.conversion.method, 'latex-export-v1');
        assert.equal(receipt?.conversion_diagnostics?.status, 'accepted');
        assert.equal(receipt?.conversion_diagnostics?.candidate_sha256, savedHash);
        assert(decision, 'Delivered HTML requires a selected, measured converter decision');
        assert(receipt?.conversion_diagnostics?.stages?.length > 0, 'Delivered HTML requires conversion stages');
        assert.equal(response.headers.get('etag'), `"${savedHash}"`);
        assert.equal(job.score_measurement, null, 'HTML conversion cannot inherit a source score measurement');
        const checks = assertSavedHtml(entry.id, Buffer.from(saved).toString('utf8'));
        const repeat = await download(path);
        assert.equal(repeat.status, 200);
        assert.equal(digest(new Uint8Array(await repeat.arrayBuffer())), savedHash);
        await writeFile(resolve(output, `html-saved-${entry.id}.html`), saved);
        evidence.push({ ...base, outcome: 'delivered', artifact_id: job.artifact_id, output_sha256: savedHash,
          download_status: 200, checks });
        console.log(`PASS HTML ${entry.id}: real saved HTML and bounded content oracle`);
      } else {
        assert.notEqual(entry.id, 'M01', 'M01 must genuinely download saved HTML');
        assert.equal(job.status, 'failed');
        assert.equal(job.artifact_id, null);
        assert.equal(job.fixed_count, 0);
        assert.equal(job.remaining_count, findings);
        assert.equal(response.status, 404);
        assert.equal(compatible.status, 404);
        assert.deepEqual(formats.available_formats, []);
        if (job.error_code === 'manual_required') {
          assert(['P02', 'P03', 'N01', 'N02', 'N03', 'N04'].includes(entry.id),
            'Manual source refusal is declared only for authored intent or negative controls');
          assert(job.human_review_required, 'Source review refusal must retain author intent');
          assert(!receipt?.conversion_diagnostics || ['accepted', 'refused'].includes(receipt.conversion_diagnostics.status));
          evidence.push({ ...base, outcome: 'manual_review_refusal',
            conversion_observation: receipt?.conversion_diagnostics?.status ?? 'not_run',
            error_code: job.error_code, download_status: 404 });
          console.log(`PASS HTML ${entry.id}: source review refusal, no conversion or artifact`);
        } else {
          assert.equal(receipt?.conversion.status, 'failed');
          assert(receipt?.conversion_diagnostics?.stages?.length > 0, 'Refusal needs a real converter diagnostic');
          assert(receipt.conversion_diagnostics.stages.some((stage: any) => stage.diagnostics?.length),
            'Refusal needs an observed source or converter problem');
          assert.equal(receipt?.conversion_diagnostics?.status, 'refused');
          assertIntendedRefusal(entry.id, receipt.conversion_diagnostics, reviewed.toString());
          evidence.push({ ...base, outcome: 'diagnostic_refusal', error_code: job.error_code, download_status: 404 });
          console.log(`PASS HTML ${entry.id}: diagnostic refusal, no artifact or score`);
        }
      }
      assert.equal(digest(await readFile(`${root}/${entry.entrypoint}`)), sourceRecord.sha256,
        'Authored fixture source must remain byte-identical');
    } catch (error) {
      const failure = `${entry.id}: ${error instanceof Error ? error.message : String(error)}`;
      failures.push(failure);
      console.error(`FAIL HTML ${failure}`);
    }
  }
  assert.deepEqual(evidence.map((row: any) => row.fixture), IDs.filter(id => !failures.some(f => f.startsWith(`${id}:`))),
    'HTML report accounts for every successful case exactly once');
  await writeFile(resolve(output, 'latex-html-report.json'), JSON.stringify({
    schema_version: 1, revision: context.revision, tracked_diff_sha256: context.trackedDiffHash,
    harness_sha256: context.harnessHashes, manifest_sha256: digest(bytes), case_ids: IDs, source_count: manifest.source_count,
    configuration: { use_ai: false, generate_alt_text: false, latex_formats: ['html'], verify_fixes: true },
    elapsed_ms: Date.now() - startedAt, evidence, failures,
  }, null, 2));
  assert.equal(failures.length, 0, 'Saved HTML corpus gate failed; see latex-html-report.json');
}
