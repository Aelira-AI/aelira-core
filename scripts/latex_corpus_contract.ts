/** Bounded saved-TEX oracle. No compiler, mathematical or accessibility certification. */
import assert from 'node:assert/strict';

/** Bounded metadata oracle for the fixed German fixture; not reader-language testing. */
export function assertGermanMetadata(saved: string) {
  const preamble = saved.split('\\begin{document}')[0].split('\n').map(line => {
    for (let i = 0; i < line.length; i++) if (line[i] === '%') {
      let escapes = 0;
      for (let j = i - 1; j >= 0 && line[j] === '\\'; j--) escapes++;
      if (escapes % 2 === 0) return line.slice(0, i);
    }
    return line;
  }).join('\n');
  assert(preamble.includes('\\usepackage[ngerman]{babel}'));
  assert(preamble.includes('\\selectlanguage{ngerman}'));
  for (const match of preamble.matchAll(/\b(?:pdflang|lang)\s*=/g)) {
    const tail = preamble.slice(match.index! + match[0].length);
    assert(/^\s*(?:\{(?:de(?:-de)?|german|ngerman)\}|(?:de(?:-de)?|german|ngerman)(?=[,\s}]|$))/i.test(tail),
      'German metadata was overridden or uses an unsupported assignment');
  }
  for (const match of preamble.matchAll(/\\(?:selectlanguage|setdefaultlanguage)\b/g)) {
    assert(/^\s*\{(?:de(?:-de)?|german|ngerman)\}/i.test(preamble.slice(match.index! + match[0].length)),
      'German language command was overridden or is unsupported');
  }
  for (const match of preamble.matchAll(/\\usepackage\[([^\]]+)\]\{babel\}/g)) {
    assert.equal(match[1], 'ngerman', 'The fixture Babel language changed');
  }
}

/** An explicitly authored synthetic variant; never applied by remediation. */
export function withReviewedLanguage(source: string, language: 'en' | 'de' = 'en'): string {
  assert(['en', 'de'].includes(language), 'Only the declared synthetic languages are supported');
  const marker = '\\begin{document}';
  assert.equal(source.split(marker).length, 2);
  return source.replace(marker, `\\usepackage{hyperref}\n\\hypersetup{pdflang={${language}}}\n` + marker);
}

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
