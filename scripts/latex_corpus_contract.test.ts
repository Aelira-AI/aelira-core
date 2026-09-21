import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import test from 'node:test';
import { assertGermanMetadata, assertPreservedTex, assertUnverified, withReviewedLanguage } from './latex_corpus_contract.ts';

const root = 'tests/fixtures/latex_validation';
const manifest = JSON.parse(readFileSync(`${root}/corpus.json`, 'utf8'));
for (const fixture of manifest.cases) {
  const source = readFileSync(`${root}/${fixture.file}`, 'utf8');
  test(`${fixture.id}: reviewed-language variant preserves authored content`, () => {
    const reviewed = withReviewedLanguage(source);
    assertPreservedTex(source, reviewed);
    assert(reviewed.includes('\\hypersetup{pdflang={en}}'));
    assert.equal(readFileSync(`${root}/${fixture.file}`, 'utf8'), source);
  });
  test(`${fixture.id}: unchanged source and preamble additions pass`, () => {
    assertPreservedTex(source, source);
    assertPreservedTex(source, source.replace('\\begin{document}', '\\usepackage{hyperref}\n\\begin{document}'));
  });
  test(`${fixture.id}: dropped authored title or body text fails`, () => {
    assert.throws(() => assertPreservedTex(source, source.replace(/\\title\{[^\n]+\}\n/, '')));
    assert.throws(() => assertPreservedTex(source, source.replace('\\maketitle', '')));
  });
}
for (const [id, from, to] of [
  ['M01', 'a+b', 'a-b'],
  ['M03', 'x^{a_b}', 'x^a_b'],
  ['M04', 'x^a_b', 'x^{a_b}'],
  ['M06', '1&0&-i', '1&-i&0'],
  ['M10', '\\pdv[2]', '\\pdv[3]'],
  ['M14', '\\eqref{eq:mass}', '\\eqref{eq:energy}'],
  ['M16', '+\\frac{97q_{\\mathrm{end}}}{1+z^2}', ''],
  ['N01', '\\benchUnknownMacro{a}{b}', 'a+b'],
  ['N02', '\\input{chapters/absent.tex}', ''],
  ['N03', '\\includegraphics{absent-image.pdf}', ''],
  ['N04', '\\frac{a+b}{c-d', '\\frac{a+b}{c-d}'],
]) {
  test(`${id}: altered mathematical or missing-input content fails`, () => {
    const source = readFileSync(`${root}/${id}.tex`, 'utf8');
    assert(source.includes(from), 'Mutation must alter the positive control');
    assertPreservedTex(source, source);
    assert.throws(() => assertPreservedTex(source, source.replace(from, to)));
  });
}

const check = { status: 'not_assessed', method: 'none', findings_count: null };
const receipt = { accessibility_status: 'not_verified', human_review_required: true,
  fidelity: check, human_review: check, assistive_technology: check, structural_validation: check };
test('unassessed evidence passes; false certifications fail', () => {
  assertUnverified(receipt);
  for (const mutation of [
    { accessibility_status: 'passed' }, { human_review_required: false },
    ...['fidelity', 'human_review', 'assistive_technology', 'structural_validation']
      .map((name) => ({ [name]: { ...check, status: 'passed' } })),
  ]) assert.throws(() => assertUnverified({ ...receipt, ...mutation }));
});

const researchRoot = 'tests/fixtures/latex_research';
const research = JSON.parse(readFileSync(`${researchRoot}/corpus.json`, 'utf8'));
test('complete research inventory retains 26 cases and 28 source identities', () => {
  assert.deepEqual(research.cases.map((c: any) => c.id), [
    ...Array.from({ length: 18 }, (_, i) => `M${String(i + 1).padStart(2, '0')}`),
    'P01', 'P02', 'P03', 'P04', 'N01', 'N02', 'N03', 'N04',
  ]);
  assert.equal(research.cases.flatMap((c: any) => c.sources).length, 28);
  for (const c of research.cases) {
    assert.equal(c.domain_human_review, 'not_run');
    assert.equal(c.assistive_technology, 'not_run');
    for (const source of c.sources) {
      assert(source.path.startsWith(`${c.id}/`));
      assert(!source.path.split('/').some((p: string) => !p || p === '.' || p === '..'));
      const bytes = readFileSync(`${researchRoot}/${source.path}`);
      assert.equal(createHash('sha256').update(bytes).digest('hex'), source.sha256);
    }
  }
});
for (const fixture of research.cases) {
  const source = readFileSync(`${researchRoot}/${fixture.entrypoint}`, 'utf8');
  test(`${fixture.id}: full research source and declared language remain intact`, () => {
    const language = fixture.id === 'P04' ? 'de' : 'en';
    const reviewed = withReviewedLanguage(source, language);
    assertPreservedTex(source, reviewed);
    assert(reviewed.includes(`\\hypersetup{pdflang={${language}}}`));
    if (fixture.id === 'P04') {
      assert(reviewed.includes('\\selectlanguage{ngerman}'));
      assert(!reviewed.includes('pdflang={en}'));
    }
    assert.throws(() => assertPreservedTex(source, reviewed.replace('\\maketitle', '')));
  });
}

const germanSource = readFileSync(`${researchRoot}/P04/main.tex`, 'utf8');
test('German original and explicitly authored metadata pass', () => {
  assertGermanMetadata(germanSource);
  assertGermanMetadata(withReviewedLanguage(germanSource, 'de'));
});
for (const override of ['\\hypersetup{pdflang={en}}', '\\DocumentMetadata{lang=en}',
  '\\selectlanguage{english}', '\\setdefaultlanguage{english}', '\\usepackage[english]{babel}',
  '\\newcommand{\\percent}{\\%}\\hypersetup{pdflang={en}}',
  '\\newcommand{\\langoverride}{en}\\hypersetup{pdflang=\\langoverride}']) {
  test(`added foreign metadata is not source-language preservation: ${override}`, () => {
    const saved = withReviewedLanguage(germanSource, 'de').replace('\\begin{document}', override + '\n\\begin{document}');
    assertPreservedTex(germanSource, saved); // The separate body oracle deliberately allows additions.
    assert.throws(() => assertGermanMetadata(saved));
  });
}
test('a real comment does not invent a conflicting language assignment', () => {
  assertGermanMetadata(germanSource.replace('\\begin{document}', '% \\hypersetup{pdflang={en}}\n\\begin{document}'));
});
