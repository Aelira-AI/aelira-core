import assert from 'node:assert/strict';
import test from 'node:test';
import { assertSavedHtml } from './latex_html_corpus_contract.ts';
import { assertIntendedRefusal, assertNoEnvironmentFailure } from './verify_latex_html_corpus_stack.ts';

const wrap = (math: string) => `<html lang="en"><body>${math}</body></html>`;
const control: Record<string, string> = {
  M01: wrap('<math><mfrac><mrow><mi>a</mi><mo>+</mo><mi>b</mi></mrow><mrow><mi>c</mi><mo>−</mo><mi>d</mi></mrow></mfrac></math>'),
  M02: wrap('<math><mfrac><mrow><mn>1</mn><mo>+</mo><mfrac><mi>a</mi><mi>b</mi></mfrac></mrow><mrow><mi>c</mi><mo>+</mo><mfrac><mi>d</mi><mi>e</mi></mfrac></mrow></mfrac></math>'),
  M03: wrap('<math><msup><mi>x</mi><msub><mi>a</mi><mi>b</mi></msub></msup></math>'),
  M04: wrap('<math><msup><msub><mi>x</mi><mi>b</mi></msub><mi>a</mi></msup></math>'),
  M05: wrap('<math><msup><mi>T</mi><mi>μ</mi></msup><msub><mrow></mrow><mrow><mi>ν</mi><mi>ρ</mi></mrow></msub></math>'),
  M06: wrap('<math><mtable><mtr><mtd><mn>1</mn></mtd><mtd><mn>0</mn></mtd><mtd><mo>−</mo><mi>i</mi></mtd></mtr><mtr><mtd><mi>i</mi></mtd><mtd><mn>2</mn></mtd><mtd><mn>3</mn></mtd></mtr></mtable></math>'),
  M07: wrap('<math><msubsup><mo>∫</mo><mn>0</mn><mi>∞</mi></msubsup><msup><mi>e</mi><mrow><mo>−</mo><mi>α</mi><mi>t</mi></mrow></msup><mi>d</mi><mi>t</mi></math>'),
  M08: wrap('<math><mi>Γ</mi><mo>+</mo><mi>γ</mi><mo>+</mo><mi>φ</mi><mo>+</mo><mi>ϕ</mi><mo>+</mo><mi>ϵ</mi><mo>+</mo><mi>ε</mi></math>'),
  M09: wrap('<math><mo>⟨</mo><mi>ψ</mi><mo>|</mo><mover><mi>H</mi><mo>^</mo></mover><mo>|</mo><mi>ψ</mi><mo>⟩</mo></math>'),
  M10: wrap('<math><mo>⟨</mo><mi>ϕ</mi><mo>|</mo><mo>|</mo><mi>ψ</mi><mo>⟩</mo><mo>+</mo><mfrac><mrow><msup><mo>∂</mo><mn>2</mn></msup><mi>f</mi></mrow><mrow><mo>∂</mo><msup><mi>x</mi><mn>2</mn></msup></mrow></mfrac></math>'),
  M11: wrap('<math><mi mathvariant="bold">F</mi><mo>=</mo><mi>q</mi><mi mathvariant="bold">v</mi><mo>×</mo><mi mathvariant="bold">B</mi></math>'),
  M12: wrap('<p>The measured speed is <math><mn>3.00</mn><mo>×</mo><msup><mn>10</mn><mn>8</mn></msup><mtext>m</mtext><mo>/</mo><mtext>s</mtext></math>.</p>'),
  M13: wrap('<math><mi>f</mi><mo>(</mo><mi>x</mi><mo>)</mo><mo>=</mo><mtable><mtr><mtd><msup><mi>x</mi><mn>2</mn></msup></mtd><mtd><mi>x</mi><mo>≥</mo><mn>0</mn></mtd></mtr><mtr><mtd><mo>−</mo><mi>x</mi></mtd><mtd><mi>x</mi><mo>&lt;</mo><mn>0</mn></mtd></mtr></mtable></math>'),
  M14: wrap('<p>Start with Equation <a href="#energy">1</a>.</p><table><tr id="energy"><td><math><mi>E</mi><mo>=</mo><mi>m</mi><msup><mi>c</mi><mn>2</mn></msup></math></td></tr><tr id="mass"><td><math><mfrac><mi>E</mi><msup><mi>c</mi><mn>2</mn></msup></mfrac><mo>=</mo><mi>m</mi></math></td></tr></table><p>Return to Equation <a href="#mass">2</a>.</p>'),
  M15: wrap('<p>First <math><mi>a</mi><mo>=</mo><mn>1</mn></math>, then <math><mi>b</mi><mo>=</mo><mn>2</mn></math>, and finally <math><mi>c</mi><mo>=</mo><mi>a</mi><mo>+</mo><mi>b</mi></math>. All three statements precede this sentence.</p>'),
  M17: wrap('<p>In this document the following notation is an ordered pair, not an interval.</p><math><mo>(</mo><mi>a</mi><mo>,</mo><mi>b</mi><mo>)</mo></math>'),
  M18: wrap('<p>Assume a is less than b. In this document the following notation is an open interval.</p><math><mo>(</mo><mi>a</mi><mo>,</mo><mi>b</mi><mo>)</mo></math>'),
  P04: '<html lang="de"><body><p>Die Geschwindigkeit ist <math><mi>v</mi><mo>=</mo><mn>3</mn></math>. Dieser Satz ist auf Deutsch.</p></body></html>',
};
const indexedFraction = (i: number) => `<mfrac><mrow><msub><mi>α</mi><mn>${i}</mn></msub><msup><mi>x</mi><mn>${i}</mn></msup><mo>+</mo><msub><mi>β</mi><mn>${i}</mn></msub><msub><mi>y</mi><mn>${i}</mn></msub></mrow><mrow><mn>1</mn><mo>+</mo><msub><mi>γ</mi><mn>${i}</mn></msub><msup><mi>z</mi><mn>${i + 1}</mn></msup></mrow></mfrac>`;
const sentinel = '<mfrac><mrow><mn>97</mn><msub><mi>q</mi><mrow><mi>e</mi><mi>n</mi><mi>d</mi></mrow></msub></mrow><mrow><mn>1</mn><mo>+</mo><msup><mi>z</mi><mn>2</mn></msup></mrow></mfrac>';
control.M16 = wrap(`<math><mi>S</mi><mo>=</mo>${Array.from({ length: 16 }, (_, i) => indexedFraction(i + 1)).join('<mo>+</mo>')}<mo>+</mo>${sentinel}</math>`);

for (const [id, html] of Object.entries(control)) test(`${id}: known valid saved structure passes`, () => {
  assertSavedHtml(id, html);
});
test('native MathML styling and grouping preserve bounded content', () => {
  const integral = control.M07.replace('<mi>d</mi>', '<mo>𝑑</mo>');
  const physics = control.M10.replace('<mo>|</mo><mo>|</mo>', '<mo>|</mo>')
    .replace('<mrow><mo>∂</mo><msup><mi>x</mi><mn>2</mn></msup></mrow>',
      '<msup><mrow><mo>∂</mo><mi>x</mi></mrow><mn>2</mn></msup>');
  const vector = control.M11.replace('<mi mathvariant="bold">F</mi>', '<mi>𝐅</mi>')
    .replace('<mi mathvariant="bold">v</mi>', '<mi>𝐯</mi>')
    .replace('<mi mathvariant="bold">B</mi>', '<mi>𝐁</mi>');
  const piecewise = control.M13.replace('<mo>=</mo>', '<mo>=</mo><mo>{</mo>');
  for (const [id, html] of [['M07', integral], ['M10', physics], ['M11', vector], ['M13', piecewise]]) {
    assertSavedHtml(id, html);
  }
  assert.throws(() => assertSavedHtml('M07', integral.replace('<mo>𝑑</mo>', '<mo>𝑞</mo>')));
  assert.throws(() => assertSavedHtml('M07', integral.replace('<mi>∞</mi>', '<mn>2</mn>')));
  for (const [before, after] of [
    ['<mi>f</mi>', '<mi>g</mi>'], ['<mi>x</mi>', '<mi>y</mi>'],
    ['<mn>2</mn>', '<mn>1</mn>'], ['<mo>+</mo>', '<mo>−</mo>'],
    ['<msup><mrow><mo>∂</mo><mi>x</mi></mrow><mn>2</mn></msup>',
      '<mrow><mo>∂</mo><mi>x</mi><mn>2</mn></mrow>'],
  ]) assert.throws(() => assertSavedHtml('M10', physics.replace(before, after)));
  for (const [before, after] of [
    ['𝐅', 'F'], ['𝐯', 'v'], ['𝐁', 'B'], ['<mo>×</mo>', '<mo>⋅</mo>'],
    ['<mi>q</mi><mi>𝐯</mi>', '<mi>𝐯</mi><mi>q</mi>'],
  ]) assert.throws(() => assertSavedHtml('M11', vector.replace(before, after)));
  assert.throws(() => assertSavedHtml('M13', piecewise.replace('<mo>≥</mo>', '<mo>&gt;</mo>')));
  assert.throws(() => assertSavedHtml('M13', piecewise.replace('<mo>{</mo>', '<mo>}</mo>')));
});
test('M01: same tokens with changed denominator fails', () => {
  assert.throws(() => assertSavedHtml('M01', control.M01.replace('<mo>−</mo>', '<mo>+</mo>')));
  assert.throws(() => assertSavedHtml('M01', control.M01.replace('<mi>c</mi><mo>−</mo><mi>d</mi>', '<mi>c</mi><msup><mo>−</mo><mi>d</mi></msup>')));
  assert.throws(() => assertSavedHtml('M01', control.M01.replace('<math>', '<math><msqrt>').replace('</math>', '</msqrt></math>')));
  assert.throws(() => assertSavedHtml('M01', control.M01.replace('<mi>a</mi><mo>+</mo><mi>b</mi>',
    '<mfrac><mi>a</mi><mrow><mo>+</mo><mi>b</mi></mrow></mfrac>')));
  assert.throws(() => assertSavedHtml('M01', control.M01.replace('<mi>a</mi><mo>+</mo><mi>b</mi>',
    '<mtable><mtr><mtd><mi>a</mi><mo>+</mo><mi>b</mi></mtd></mtr></mtable>')));
  assert.throws(() => assertSavedHtml('M01', control.M01.replace('</mfrac>', '</mfrac><mo>+</mo><mn>1</mn>')));
  assertSavedHtml('M01', control.M01.replace('<mfrac>', '<mrow><mfrac>').replace('</mfrac>', '</mfrac></mrow>'));
});
for (const [id, source, replacement] of [
  ['M02', '<mi>e</mi>', '<mi>f</mi>'], ['M05', '<mi>ρ</mi>', '<mi>σ</mi>'],
  ['M07', '<mi>∞</mi>', '<mn>1</mn>'], ['M08', '<mi>ϕ</mi>', '<mi>φ</mi>'],
  ['M09', '<mo>^</mo>', '<mo>¯</mo>'], ['M10', '<mi>f</mi>', '<mi>g</mi>'], ['M11', '<mo>×</mo>', '<mo>⋅</mo>'],
  ['M12', '<mn>8</mn>', '<mn>9</mn>'], ['M13', '<mo>≥</mo>', '<mo>&gt;</mo>'],
  ['M15', '<mi>b</mi><mo>=</mo><mn>2</mn>', '<mi>b</mi><mo>=</mo><mn>3</mn>'],
  ['M17', 'ordered pair, not an interval', 'open interval'],
  ['M18', 'open interval', 'closed interval'], ['P04', 'lang="de"', 'lang="en"'],
]) test(`${id}: declared content corruption fails`, () => {
  assert(control[id].includes(source));
  assert.throws(() => assertSavedHtml(id, control[id].replace(source, replacement)));
});
test('unknown or malformed MathML fails closed', () => {
  assert.throws(() => assertSavedHtml('unknown', control.M01));
  assert.throws(() => assertSavedHtml('M01', control.M01.replace('</mfrac>', '')));
});
test('M03 and M04: attachment corruption fails', () => {
  assert.throws(() => assertSavedHtml('M03', control.M04));
  assert.throws(() => assertSavedHtml('M04', control.M03));
});
test('M06: swapped coordinates and sign fail', () => {
  assert.throws(() => assertSavedHtml('M06', control.M06.replace('<mn>0</mn>', '<mn>9</mn>')));
  assert.throws(() => assertSavedHtml('M06', control.M06.replace('<mo>−</mo>', '<mo>+</mo>')));
  assert.throws(() => assertSavedHtml('M06', control.M06.replace('<mn>1</mn>', '<mfrac><mn>1</mn><mn>1</mn></mfrac>')));
});
test('M05, M07 and M13: index and power attachments fail', () => {
  assert.throws(() => assertSavedHtml('M05', control.M05.replace('<mi>ν</mi><mi>ρ</mi>', '<mi>ρ</mi><mi>ν</mi>')));
  assert.throws(() => assertSavedHtml('M05', control.M05.replace('<msup><mi>T</mi><mi>μ</mi></msup>', '<mi>T</mi><mi>μ</mi>')));
  assert.throws(() => assertSavedHtml('M07', control.M07.replace('<msubsup><mo>∫</mo><mn>0</mn><mi>∞</mi></msubsup>', '<mo>∫</mo><mn>0</mn><mi>∞</mi>')));
  assert.throws(() => assertSavedHtml('M07', control.M07.replace('<msup><mi>e</mi><mrow><mo>−</mo><mi>α</mi><mi>t</mi></mrow></msup>', '<msup><mi>e</mi><mrow><mo>−</mo><mi>α</mi></mrow></msup><mi>t</mi>')));
  assert.throws(() => assertSavedHtml('M13', control.M13.replace('<msup><mi>x</mi><mn>2</mn></msup>', '<msub><mi>x</mi><mn>2</mn></msub>')));
});
test('M10: derivative attachment and top-level operation fail', () => {
  assert.throws(() => assertSavedHtml('M10', control.M10.replace('<msup><mo>∂</mo><mn>2</mn></msup>', '<mo>∂</mo><mn>2</mn>')));
  assert.throws(() => assertSavedHtml('M10', control.M10.replace('<msup><mi>x</mi><mn>2</mn></msup>', '<msub><mi>x</mi><mn>2</mn></msub>')));
  assert.throws(() => assertSavedHtml('M10', control.M10.replace('<mo>+</mo>', '<mo>−</mo>')));
});
test('M14: two math rows can have multiple math nodes and resolvable references', () => {
  const split = control.M14.replace('<math><mi>E</mi><mo>=</mo>', '<math><mi>E</mi></math><math><mo>=</mo>');
  assertSavedHtml('M14', split);
  assertSavedHtml('M14', control.M14.replace('<mi>m</mi><msup>', '<mi>m</mi><mo>⁢</mo><msup>'));
  for (const damaged of [control.M14.replace('href="#mass"', 'href="#energy"'),
    control.M14.replace('id="mass"', 'id="wrong"'), control.M14.replace('<mi>E</mi>', '<mi>Q</mi>'),
    control.M14.replace('<mn>2</mn>', '<mn>3</mn>'),
    control.M14.replace('<mfrac><mi>E</mi>', '<mfrac><mfrac><mi>E</mi><mn>1</mn></mfrac>'),
    control.M14.replace('<mfrac><mi>E</mi><msup><mi>c</mi><mn>2</mn></msup>',
      '<mfrac><mi>E</mi><mrow><msup><mi>c</mi><mn>2</mn></msup><mo>+</mo><mn>1</mn></mrow>')])
    assert.throws(() => assertSavedHtml('M14', damaged));
});
test('M16: 17 authored fractions pass even when split between MathML blocks', () => {
  assertSavedHtml('M16', control.M16.replace(`${indexedFraction(9)}`, `</math><math>${indexedFraction(9)}`));
});
test('M16: losses, replacements, reorder and insertions fail', () => {
  for (const damaged of [
    control.M16.replace(indexedFraction(8), ''),
    control.M16.replace(indexedFraction(8) + '<mo>+</mo>' + indexedFraction(9), indexedFraction(9) + '<mo>+</mo>' + indexedFraction(8)),
    control.M16.replace('<msub><mi>α</mi><mn>1</mn></msub>', '<msub><mi>α</mi><mn>2</mn></msub>'),
    control.M16.replace('<msub><mi>β</mi><mn>5</mn></msub>', '<msub><mi>δ</mi><mn>5</mn></msub>'),
    control.M16.replace('<msub><mi>γ</mi><mn>7</mn></msub>', '<msub><mi>γ</mi><mn>8</mn></msub>'),
    control.M16.replace(indexedFraction(3), indexedFraction(3).replace('<mo>+</mo>', '')),
    control.M16.replace('<mn>97</mn>', '<mn>96</mn>'),
    control.M16.replace('<mi>S</mi><mo>=</mo>', ''),
    control.M16.replace(indexedFraction(10) + '<mo>+</mo>', indexedFraction(10) + '<mo>−</mo>'),
    control.M16.replace(indexedFraction(10) + '<mo>+</mo>', indexedFraction(10) + '<mo>+</mo><mo>+</mo>'),
    control.M16.replace('<mn>1</mn><mo>+</mo><msup><mi>z</mi><mn>2</mn></msup></mrow></mfrac></math>', '<mn>1</mn><mo>−</mo><msup><mi>z</mi><mn>2</mn></msup></mrow></mfrac></math>'),
    control.M16.replace('</math>', indexedFraction(17) + '</math>'),
    control.M16.replace('</math>', '<mi>lost-tail</mi></math>'),
    control.M16.replace('<math>', '<math><msqrt>').replace('</math>', '</msqrt></math>'),
  ]) assert.throws(() => assertSavedHtml('M16', damaged));
});

test('conversion observations reject every environmental failure and unknown converter version', () => {
  const control = { stages: [{ tool: 'inspection', version: 'unknown', diagnostics: [] },
    { tool: 'latexml', version: '0.8.8', diagnostics: [] }],
  decision: { selected_route: 'latexml', tool_versions: { latexml: '0.8.8' }, reasons: ['partial_support'] } };
  assertNoEnvironmentFailure(control);
  for (const code of ['timeout', 'tool_unavailable', 'language_environment_unavailable',
    'diagnostics_truncated', 'package_route_unavailable']) {
    for (const index of [0, 1]) assert.throws(() => assertNoEnvironmentFailure({ ...control,
      stages: control.stages.map((stage, i) => i === index ? { ...stage, diagnostics: [{ code }] } : stage) }));
  }
  assert.throws(() => assertNoEnvironmentFailure({ ...control,
    decision: { ...control.decision, reasons: ['tool_unavailable'] } }));
  assert.throws(() => assertNoEnvironmentFailure({ ...control,
    stages: [control.stages[0], { ...control.stages[1], version: 'unknown' }] }));
  assert.throws(() => assertNoEnvironmentFailure({ ...control,
    decision: { ...control.decision, tool_versions: { latexml: 'unknown' } } }));
});
test('a crash or unrelated missing dependency cannot masquerade as an intended refusal', () => {
  const source = String.raw`\input{chapters/absent.tex}`;
  const stage = { tool: 'latexml', version: '0.8.8', exit_code: 9, diagnostics: [{ code: 'process_failed' }] };
  const observation = (findings: object[], exit_code = 9) => ({ stages: [{ ...stage, exit_code, diagnostics: findings }] });
  assert.throws(() => assertIntendedRefusal('M02', observation([{ code: 'process_failed' }]), source));
  assert.throws(() => assertIntendedRefusal('N01', observation([{ code: 'process_failed' }]), source));
  assert.throws(() => assertIntendedRefusal('N01', observation([{ code: 'process_failed' },
    { code: 'unsupported_command' }], -9), source));
  assert.throws(() => assertIntendedRefusal('N01', observation([{ code: 'process_failed' },
    { code: 'unsupported_command' }], 137), source));
  assert.throws(() => assertIntendedRefusal('M02', observation([{ code: 'missing_dependency', source_line: 1 }]), source));
  assert.throws(() => assertIntendedRefusal('N02', observation([{ code: 'process_failed' },
    { code: 'missing_dependency', source_line: 3 }]), source));
  assert.throws(() => assertIntendedRefusal('N02', observation([{ code: 'process_failed' },
    { code: 'missing_dependency' }]), source));
  assertIntendedRefusal('N02', observation([{ code: 'process_failed' },
    { code: 'missing_dependency', source_line: 1 }]), source);
  assertIntendedRefusal('N01', observation([{ code: 'process_failed' },
    { code: 'unsupported_command' }]), String.raw`\benchUnknownMacro{a}{b}`);
  assertIntendedRefusal('M02', observation([{ code: 'semantics_not_preserved' }], 0), source);
});
