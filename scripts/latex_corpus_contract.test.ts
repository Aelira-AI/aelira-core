import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { assertPreservedTex, assertUnverified } from './latex_corpus_contract.ts';

const root = 'tests/fixtures/latex_validation';
const manifest = JSON.parse(readFileSync(`${root}/corpus.json`, 'utf8'));
for (const fixture of manifest.cases) {
  const source = readFileSync(`${root}/${fixture.file}`, 'utf8');
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
