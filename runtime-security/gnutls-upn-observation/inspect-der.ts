import { readFile, writeFile } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { createHash } from 'node:crypto';
const root = resolve(process.argv[2] ?? '');
if (!process.argv[2]) throw new Error('Fixture directory required');
const manifest = JSON.parse(await readFile(join(root, 'manifest.json'), 'utf8'));
type Node = { tag: number; value: Buffer; children: Node[] };
function parse(bytes: Buffer): Node {
  let nodes = 0;
  function one(start: number, end: number, depth: number): [Node, number] {
    if (++nodes > 1000 || depth > 12 || start + 2 > end) throw new Error('DER node bound');
    const tag = bytes[start++]; if ((tag & 31) === 31) throw new Error('Unexpected DER tag form');
    let length = bytes[start++];
    if (length & 128) {
      const count = length & 127;
      if (!count || count > 3 || start + count > end || bytes[start] === 0) throw new Error('Invalid DER length');
      length = 0; for (let i = 0; i < count; i++) length = length * 256 + bytes[start++];
      if (length < 128) throw new Error('Nonminimal DER length');
    }
    const limit = start + length; if (limit > end) throw new Error('Truncated DER');
    const children: Node[] = []; let cursor = start;
    if (tag & 32) while (cursor < limit) { const [child, next] = one(cursor, limit, depth + 1); children.push(child); cursor = next; }
    return [{ tag, value: bytes.subarray(start, limit), children }, limit];
  }
  const [node, end] = one(0, bytes.length, 0); if (end !== bytes.length) throw new Error('Trailing DER'); return node;
}
const output: Record<string, any> = {};
for (const [name, identity] of Object.entries(manifest.certificates) as [string, any][]) {
  const bytes = await readFile(join(root, identity.file));
  if (createHash('sha256').update(bytes).digest('hex') !== identity.sha256 || bytes.length !== identity.size || bytes.length > 65_536) throw new Error('Changed fixture');
  const certificate = parse(bytes);
  if (certificate.tag !== 48 || certificate.children.length !== 3 || certificate.children[0].tag !== 48) throw new Error('Unexpected certificate DER');
  const extensions = certificate.children[0].children.find(n => n.tag === 163)?.children[0];
  if (!extensions || extensions.tag !== 48) throw new Error('Certificate extensions missing');
  const selected: any[] = [];
  for (const extension of extensions.children) {
    if (extension.tag !== 48 || extension.children[0]?.tag !== 6) throw new Error('Unexpected extension');
    const oid = extension.children[0].value.toString('hex');
    if (!['551d11', '551d1e'].includes(oid)) continue;
    const payload = extension.children.at(-1)!;
    if (payload.tag !== 4) throw new Error('Extension payload type');
    const strings: any[] = [];
    function walk(node: Node) {
      if (node.tag === 6) strings.push({ tag: 6, oid_hex: node.value.toString('hex') });
      if ([12, 22].includes(node.tag)) {
        const value = node.value.toString('utf8'); if (!Buffer.from(value).equals(node.value)) throw new Error('Invalid UTF8 fixture');
        strings.push({ tag: node.tag, value, utf8_hex: node.value.toString('hex'), code_points: [...value].map(c => c.codePointAt(0)) });
      }
      node.children.forEach(walk);
    }
    walk(parse(payload.value));
    selected.push({ oid_hex: oid, critical: extension.children[1]?.tag === 1 && extension.children[1].value.equals(Buffer.from([255])), der_hex: payload.value.toString('hex'), strings });
  }
  output[name] = { sha256: identity.sha256, extensions: selected };
}
const result = { schema: 'aelira-upn-sentinel-der-inspection-v1', status: 'pass', certificates: output };
await writeFile(join(root, 'der-inspection.json'), JSON.stringify(result, null, 2) + '\n', { flag: 'wx' });
console.log(JSON.stringify({ schema: result.schema, status: result.status, certificates: Object.keys(output).length }));
