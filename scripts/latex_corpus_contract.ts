/** Bounded saved-TEX oracle. No compiler, mathematical or accessibility certification. */
import assert from 'node:assert/strict';

export function assertPreservedTex(source: string, saved: string) {
  const marker = '\\begin{document}';
  const split = source.indexOf(marker);
  assert(split >= 0 && source.indexOf(marker, split + marker.length) === -1);
  const savedSplit = saved.indexOf(marker);
  assert(savedSplit >= 0);
  // Exact comparison includes equations, references, captions, missing assets,
  // malformed expressions and the final sentinel. No whitespace normalization.
  assert.equal(saved.slice(savedSplit), source.slice(split), 'Authored document body changed');
  const original = source.slice(0, split).split('\n');
  const candidate = saved.slice(0, savedSplit).split('\n');
  let cursor = 0;
  for (const line of candidate) {
    if (line === original[cursor]) cursor++;
  }
  assert.equal(cursor, original.length, 'Authored preamble lines changed or disappeared');
  // Metadata/package additions are permitted by this source-preservation probe.
  // Their correctness, compilation and rendered behavior need separate checks.
}

export function assertUnverified(receipt: any) {
  assert(receipt, 'Representation evidence is required');
  assert.equal(receipt.accessibility_status, 'not_verified');
  assert.equal(receipt.human_review_required, true);
  for (const name of ['fidelity', 'human_review', 'assistive_technology']) {
    assert.deepEqual(receipt[name], { status: 'not_assessed', method: 'none', findings_count: null });
  }
  assert.equal(receipt.structural_validation.status, 'not_assessed');
}
