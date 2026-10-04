/** Bounded, saved-HTML observations for the fixed public #444 corpus. */
import assert from 'node:assert/strict';

type Node = { tag: string; attrs: Record<string, string>; children: Node[]; value: string };
const decode = (s: string) => s.replace(/&#x([0-9a-f]+);|&#([0-9]+);|&(amp|lt|gt|quot|nbsp|minus);/gi,
  (_, hex, decimal, named) => hex ? String.fromCodePoint(parseInt(hex, 16)) : decimal ? String.fromCodePoint(parseInt(decimal, 10)) :
    ({ amp: '&', lt: '<', gt: '>', quot: '"', nbsp: ' ', minus: '−' } as Record<string, string>)[named.toLowerCase()]);
export function parseHtml(html: string): Node {
  const root: Node = { tag: '#root', attrs: {}, children: [], value: '' };
  const stack = [root];
  const parts = html.match(/<!--[\s\S]*?-->|<![^>]*>|<\/?[\w:-]+(?:\s[^<>]*?)?\/?\s*>|[^<]+/g) || [];
  assert.equal(parts.join(''), html, 'Malformed HTML token boundary');
  for (const part of parts) {
    if (part.startsWith('<!--') || part.startsWith('<!')) continue;
    if (part.startsWith('</')) {
      const tag = part.match(/^<\/([\w:-]+)/)?.[1].toLowerCase();
      const position = stack.findLastIndex(node => node.tag === tag);
      if (stack.some(node => node.tag === 'math') && stack.at(-1)?.tag !== tag) throw new Error('Malformed MathML nesting');
      if (tag === 'math' && position < 0) throw new Error('Unmatched MathML close');
      if (position > 0) stack.length = position;
      continue;
    }
    if (part.startsWith('<')) {
      const tag = part.match(/^<([\w:-]+)/)?.[1].toLowerCase();
      if (!tag) continue;
      const attrs: Record<string, string> = {};
      for (const match of part.matchAll(/([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/g)) attrs[match[1].toLowerCase()] = decode(match[2] ?? match[3] ?? match[4]);
      const node: Node = { tag, attrs, children: [], value: '' };
      stack.at(-1)!.children.push(node);
      if (!part.endsWith('/>') && !['meta', 'link', 'br', 'hr', 'img', 'input'].includes(tag)) stack.push(node);
    } else stack.at(-1)!.children.push({ tag: '#text', attrs: {}, children: [], value: decode(part) });
  }
  assert(!stack.some(node => node.tag === 'math'), 'Unclosed MathML');
  return root;
}
const descendants = (n: Node, tag: string): Node[] => n.children.flatMap(c => [...(c.tag === tag || (tag === '*' && c.tag !== '#text') ? [c] : []), ...descendants(c, tag)]);
const direct = (n: Node, tag: string) => n.children.filter(c => c.tag === tag);
const txt = (n: Node): string => n.value + n.children.map(txt).join('');
const compact = (n: Node) => txt(n).replace(/\s+/g, '').replaceAll('−', '-');
const token = (n: Node) => descendants(n, 'mi').concat(descendants(n, 'mn'), descendants(n, 'mo'), descendants(n, 'mtext')).map(compact);
const has = (n: Node, tag: string, predicate: (n: Node) => boolean) => descendants(n, tag).some(predicate);
const childText = (n: Node) => n.children.filter(c => c.tag !== '#text').map(compact);
const mathNodes = (n: Node) => descendants(n, 'math');
const fraction = (n: Node, num: (n: Node) => boolean, den: (n: Node) => boolean) => (n.tag === 'mfrac' ? [n] : descendants(n, 'mfrac')).some(f => {
  const parts = f.children.filter(c => c.tag !== '#text');
  return parts.length === 2 && num(parts[0]) && den(parts[1]);
});
const power = (n: Node, base: string, exp: string) => (n.tag === 'msup' ? [n] : descendants(n, 'msup')).some(p => {
  const parts = childText(p); return parts.length === 2 && parts[0] === base && parts[1] === exp;
});
const sub = (n: Node, base: string, index: string) => (n.tag === 'msub' ? [n] : descendants(n, 'msub')).some(p => {
  const parts = childText(p); return parts.length === 2 && parts[0] === base && parts[1] === index;
});
const mathUnits = (n: Node): string[] => {
  if (n.tag === '#text') return n.value.trim() ? ['unexpected:text'] : [];
  if (n.tag === 'annotation' || n.tag === 'mspace') return [];
  if (['mi', 'mn', 'mo', 'mtext'].includes(n.tag)) {
    const value = compact(n);
    return value && !['\u2062', '\u2061', ''].includes(value) ? [value] : [];
  }
  if (['msub', 'msup', 'msubsup', 'munderover'].includes(n.tag)) {
    const parts = n.children.filter(c => c.tag !== '#text');
    if (parts.length !== (['msubsup', 'munderover'].includes(n.tag) ? 3 : 2)) return ['malformed-script'];
    return [`${n.tag === 'munderover' ? 'msubsup' : n.tag}:${parts.map(p => mathUnits(p).join('')).join(':')}`];
  }
  if (!['#root', 'math', 'mrow', 'mstyle', 'mpadded', 'mtd', 'mtr', 'mtable', 'mfrac', 'semantics'].includes(n.tag))
    return [`unexpected:${n.tag}`];
  if (n.tag === 'semantics') return n.children.length ? mathUnits(n.children[0]) : ['empty-semantics'];
  return n.children.flatMap(mathUnits);
};
const topUnits = (n: Node): string[] => n.tag === 'mfrac' ? [`fraction:${JSON.stringify(mathUnits(n))}`] :
  n.tag === '#text' ? (n.value.trim() ? ['unexpected:text'] : []) :
  ['mi', 'mn', 'mo', 'mtext', 'msub', 'msup', 'msubsup', 'munderover'].includes(n.tag) ? mathUnits(n) :
  ['math', 'mrow', 'mstyle', 'mpadded', 'mtd', 'mtr', 'mtable'].includes(n.tag) ? n.children.flatMap(topUnits) :
  n.tag === 'semantics' ? (n.children.length ? topUnits(n.children[0]) : ['empty-semantics']) :
  n.tag === 'mspace' || n.tag === 'annotation' ? [] : [`unexpected:${n.tag}`];
const pathTo = (root: Node, wanted: Node): Node[] | null => {
  if (root === wanted) return [root];
  for (const child of root.children) {
    const path = pathTo(child, wanted);
    if (path) return [root, ...path];
  }
  return null;
};
const referenceRow = (root: Node, target: Node): Node | null => {
  const path = pathTo(root, target) || [];
  return [...path].reverse().find(n => n.tag === 'tr' || /(?:^|\s)ltx_eqn_(?:row|div)(?:\s|$)/.test(n.attrs.class || '')) || null;
};

/** Returns named observations so failures cannot be summarized as a generic export success. */
export function htmlChecks(id: string, html: string): Record<string, boolean> {
  const document = parseHtml(html);
  const maths = mathNodes(document);
  const body = descendants(document, 'body')[0] || document;
  const visible = txt(body).replace(/\s+/g, ' ');
  const all = maths.length ? { mathml: true } : { mathml: false };
  const m = maths[0] || document;
  const check = (name: string, value: boolean) => ({ ...all, [name]: value });
  switch (id) {
    case 'M01': {
      const operands = fraction(m,
        n => JSON.stringify(mathUnits(n)) === JSON.stringify(['a', '+', 'b']),
        d => JSON.stringify(mathUnits(d)) === JSON.stringify(['c', '-', 'd']));
      return { ...all, fraction_operands: operands,
        whole_expression: maths.length === 1 && operands && topUnits(m).length === 1 &&
          topUnits(m)[0].startsWith('fraction:') };
    }
    case 'M02': return check('nested_fractions', fraction(m, n => compact(n).includes('1+') && fraction(n, a => compact(a) === 'a', b => compact(b) === 'b'),
      d => compact(d).includes('c+') && fraction(d, a => compact(a) === 'd', b => compact(b) === 'e')));
    case 'M03': return check('exponent_contains_subscript', has(m, 'msup', p => {
      const c = p.children.filter(n => n.tag !== '#text'); return c.length === 2 && compact(c[0]) === 'x' && sub(c[1], 'a', 'b');
    }));
    case 'M04': return check('scripts_share_base', has(m, 'msubsup', n => childText(n).join('|') === 'x|b|a') ||
      has(m, 'msup', n => { const c = n.children.filter(x => x.tag !== '#text'); return c.length === 2 && sub(c[0], 'x', 'b') && compact(c[1]) === 'a'; }));
    case 'M05': return check('tensor_indices', [
      ['msup:T:μ', 'msub::νρ'], ['msubsup:T:νρ:μ'],
    ].some(expected => JSON.stringify(mathUnits(m)) === JSON.stringify(expected)));
    case 'M06': return check('matrix_coordinates', descendants(m, 'mtable').some(table => {
      const rows = direct(table, 'mtr').map(row => direct(row, 'mtd').map(compact));
      return JSON.stringify(rows) === JSON.stringify([['1', '0', '-i'], ['i', '2', '3']]);
    }));
    case 'M07': return check('integral_limits_exponent', [
      ['msubsup:∫:0:∞', 'msup:e:-αt', 'd', 't'],
      ['msup:msub:∫:0:∞', 'msup:e:-αt', 'd', 't'],
    ].some(expected => JSON.stringify(mathUnits(m)) === JSON.stringify(expected)));
    case 'M08': {
      const sequence = token(m).filter(t => ['Γ', 'γ', 'φ', 'ϕ', 'ε', 'ϵ'].includes(t));
      return check('greek_variants', JSON.stringify(sequence) === JSON.stringify(['Γ', 'γ', 'φ', 'ϕ', 'ϵ', 'ε']) ||
        JSON.stringify(sequence) === JSON.stringify(['Γ', 'γ', 'ϕ', 'φ', 'ϵ', 'ε']));
    }
    case 'M09': return check('hatted_operator', token(m).filter(t => t === 'ψ').length === 2 && has(m, 'mover', n => compact(n).includes('H') && /[ˆ^̂]/u.test(compact(n))) && /[⟨〈]/u.test(compact(m)) && /[⟩〉]/u.test(compact(m)));
    case 'M10': {
      const derivative = fraction(m,
        n => JSON.stringify(mathUnits(n)) === JSON.stringify(['msup:∂:2', 'f']),
        d => JSON.stringify(mathUnits(d)) === JSON.stringify(['∂', 'msup:x:2']));
      const sequence = topUnits(m);
      const bracket = ['⟨', 'ϕ', '|', '|', 'ψ', '⟩', '+'];
      const alternate = ['⟨', 'φ', '|', '|', 'ψ', '⟩', '+'];
      return check('physics_second_derivative', maths.length === 1 && derivative &&
        sequence.length === 8 && sequence.at(-1)?.startsWith('fraction:') === true &&
        [bracket, alternate].some(expected => JSON.stringify(sequence.slice(0, 7)) === JSON.stringify(expected)));
    }
    case 'M11': return check('cross_product', ['F', 'q', 'v', 'B', '×'].every(v => token(m).includes(v)) &&
      ['F', 'v', 'B'].every(v => descendants(m, 'mi').some(n => compact(n) === v &&
        (/bold/.test(n.attrs.mathvariant || '') || (pathTo(m, n) || []).some(p => /bold/.test(p.attrs.mathvariant || ''))))));
    case 'M12': return check('quantity_units', token(m).includes('3.00') && power(m, '10', '8') && /[×⋅\u2062]/u.test(compact(m)) &&
      (fraction(m, n => compact(n).includes('m'), d => compact(d).includes('s')) || /m\/s/.test(compact(m)) || has(m, 'msup', n => /s-1/.test(compact(n)))));
    case 'M13': return check('piecewise_boundaries', has(m, 'mtable', n => {
      const rows = direct(n, 'mtr').map(row => direct(row, 'mtd'));
      return rows.length === 2 && rows.every(row => row.length === 2) &&
        JSON.stringify(mathUnits(rows[0][0])) === JSON.stringify(['msup:x:2']) &&
        JSON.stringify(mathUnits(rows[0][1])) === JSON.stringify(['x', '≥', '0']) &&
        JSON.stringify(mathUnits(rows[1][0])) === JSON.stringify(['-', 'x']) &&
        JSON.stringify(mathUnits(rows[1][1])) === JSON.stringify(['x', '<', '0']);
    }) && JSON.stringify(topUnits(m).slice(0, 5)) === JSON.stringify(['f', '(', 'x', ')', '=']));
    case 'M14': {
      const references = descendants(body, 'a').filter(n => n.attrs.href?.startsWith('#') && /^\(?[12]\)?$/.test(compact(n)));
      const energy = references.find(n => compact(n).includes('1'));
      const mass = references.find(n => compact(n).includes('2'));
      const target = (a?: Node) => a && descendants(body, '*').find(n => n.attrs.id === a.attrs.href.slice(1));
      // IDs may be on an empty anchor inside the row; bind to its nearest equation row.
      const energyTarget = target(energy); const massTarget = target(mass);
      const energyRow = energyTarget && referenceRow(body, energyTarget);
      const massRow = massTarget && referenceRow(body, massTarget);
      return { ...all, distinct_references: !!energy && !!mass && energy.attrs.href !== mass.attrs.href && !!energyRow && !!massRow,
        equation_rows: !!energyRow && !!massRow && energyRow !== massRow && compact(energyRow).includes('E=mc2') &&
          compact(massRow).includes('Ec2=m') && power(energyRow, 'c', '2') &&
          fraction(massRow, n => compact(n) === 'E', d => power(d, 'c', '2')) };
    }
    case 'M15': return { ...all, three_equations: maths.length >= 3 && ['a=1', 'b=2', 'c=a+b'].every((v, i) => compact(maths[i]) === v),
      connecting_prose: /First.*then.*and finally.*All three statements precede this sentence\./.test(visible) };
    case 'M16': {
      const fractions = maths.flatMap(node => descendants(node, 'mfrac'));
      const indexed = Array.from({ length: 16 }, (_, offset) => {
        const i = offset + 1; const f = fractions[offset];
        return !!f && fraction(f,
          n => JSON.stringify(mathUnits(n)) === JSON.stringify([`msub:α:${i}`, `msup:x:${i}`, '+', `msub:β:${i}`, `msub:y:${i}`]),
          d => JSON.stringify(mathUnits(d)) === JSON.stringify(['1', '+', `msub:γ:${i}`, `msup:z:${i + 1}`]));
      });
      const last = fractions.at(-1);
      const expectedTop = ['S', '=', ...Array.from({ length: 17 }, (_, i) =>
        i === 0 ? [`fraction:${JSON.stringify(mathUnits(fractions[0] || document))}`] :
          ['+', `fraction:${JSON.stringify(mathUnits(fractions[i] || document))}`]).flat()];
      const observedTop = maths.flatMap(topUnits);
      return { ...all, indexed_fractions: fractions.length === 17 && indexed.every(Boolean),
        complete_expression: fractions.length === 17 && JSON.stringify(observedTop) === JSON.stringify(expectedTop),
        final_sentinel: !!last && fraction(last,
          n => JSON.stringify(mathUnits(n)) === JSON.stringify(['97', 'msub:q:end']),
          d => JSON.stringify(mathUnits(d)) === JSON.stringify(['1', '+', 'msup:z:2'])) &&
          mathUnits(maths.at(-1)!).slice(-5).join('|') === ['97', 'msub:q:end', '1', '+', 'msup:z:2'].join('|') };
    }
    case 'M17': return { ...all, ordered_pair_context: visible.includes('ordered pair, not an interval'), pair: compact(m).includes('(a,b)') };
    case 'M18': return { ...all, interval_context: visible.includes('open interval') && visible.includes('a is less than b'), pair: compact(m).includes('(a,b)') };
    case 'P04': return { ...all, german_language: ['de', 'de-de'].includes((descendants(document, 'html')[0]?.attrs.lang || '').toLowerCase()),
      german_prose: visible.includes('Die Geschwindigkeit ist') && visible.includes('Dieser Satz ist auf Deutsch.'), equation: compact(m) === 'v=3' };
    default: throw new Error(`No bounded HTML oracle for ${id}`);
  }
}

export function assertSavedHtml(id: string, html: string) {
  const checks = htmlChecks(id, html);
  assert(Object.values(checks).every(Boolean), `${id}: saved HTML oracle failed: ${Object.entries(checks).filter(([, v]) => !v).map(([k]) => k).join(', ')}`);
  assert(!/class=["'][^"']*ltx_ERROR/.test(html), `${id}: converter error node`);
  return checks;
}
