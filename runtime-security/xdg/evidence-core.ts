import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { lstat, open, readFile, realpath } from 'node:fs/promises';
import { isAbsolute, join, resolve, sep } from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

export const MAX_FILE = 2 * 1024 ** 3;
const execute = promisify(execFile);
export const sha = (data: Buffer | string) => createHash('sha256').update(data).digest('hex');
export const same = (a: unknown, b: unknown) => canonical(a) === canonical(b);
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value !== null && typeof value === 'object') {
    return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical((value as Record<string, unknown>)[k])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}
export function assert(ok: unknown, message: string): asserts ok {
  if (!ok) throw new Error(message);
}
export async function command(binary: string, args: string[], timeout = 120_000): Promise<Buffer> {
  const result = await execute(binary, args, { encoding: 'buffer', maxBuffer: 8 * 1024 ** 2, timeout });
  return result.stdout;
}
export function pathName(value: string): boolean {
  return value.length > 0 && value.length < 512 && !isAbsolute(value) && !value.includes('\\') &&
    value.split('/').every(p => p.length > 0 && p !== '.' && p !== '..' && !p.includes('\0'));
}
export async function regular(root: string, name: string, bound = MAX_FILE): Promise<string> {
  assert(pathName(name), 'Unsafe evidence path');
  let current = root;
  for (const part of name.split('/')) {
    current = join(current, part);
    const info = await lstat(current);
    assert(!info.isSymbolicLink(), 'Evidence symlink');
  }
  const info = await lstat(current);
  assert(info.isFile() && info.size <= bound, 'Invalid evidence file');
  assert((await realpath(current)).startsWith(resolve(root) + sep), 'Evidence path escaped root');
  return current;
}
export async function hashFile(path: string): Promise<{ sha256: string; size: number }> {
  const digest = createHash('sha256');
  let size = 0;
  for await (const chunk of createReadStream(path)) { size += chunk.length; digest.update(chunk); }
  return { sha256: digest.digest('hex'), size };
}
export async function small(root: string, name: string): Promise<Buffer> {
  return readFile(await regular(root, name, 1024 ** 2));
}

/** Strict JSON also retains which numeric fields were integer tokens. */
export function json(data: Buffer | string): { value: any; integers: Set<string> } {
  const text = data.toString();
  assert(Buffer.byteLength(text) <= 1024 ** 2, 'JSON exceeds bound');
  let at = 0;
  const integers = new Set<string>();
  const whitespace = () => { while (/[ \t\r\n]/.test(text[at] ?? '') && at < text.length) at++; };
  const pointer = (key: string) => key.replaceAll('~', '~0').replaceAll('/', '~1');
  function string(): string {
    const start = at++;
    while (at < text.length) {
      if (text[at] === '\\') { at += 2; continue; }
      if (text[at++] === '"') return JSON.parse(text.slice(start, at));
    }
    throw new Error('Unterminated JSON string');
  }
  function value(path: string, depth: number): any {
    assert(depth <= 64, 'JSON nesting exceeds bound'); whitespace();
    if (text[at] === '"') return string();
    if (text[at] === '{') {
      at++; whitespace(); const result = Object.create(null);
      if (text[at] === '}') { at++; return result; }
      while (true) {
        assert(text[at] === '"', 'Invalid JSON key'); const key = string(); whitespace();
        assert(!(key in result) && text[at++] === ':', 'Duplicate or invalid JSON key');
        result[key] = value(`${path}/${pointer(key)}`, depth + 1); whitespace();
        if (text[at] === '}') { at++; return result; }
        assert(text[at++] === ',', 'Invalid JSON separator'); whitespace();
      }
    }
    if (text[at] === '[') {
      at++; whitespace(); const result = [];
      if (text[at] === ']') { at++; return result; }
      while (true) {
        result.push(value(`${path}/${result.length}`, depth + 1)); whitespace();
        if (text[at] === ']') { at++; return result; }
        assert(text[at++] === ',', 'Invalid JSON separator');
      }
    }
    for (const [literal, result] of [['true', true], ['false', false], ['null', null]] as const) {
      if (text.startsWith(literal, at)) { at += literal.length; return result; }
    }
    const match = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(text.slice(at));
    assert(match, 'Invalid JSON value'); at += match[0].length;
    const result = Number(match[0]); assert(Number.isFinite(result), 'Nonfinite JSON number');
    if (/^-?(?:0|[1-9]\d*)$/.test(match[0])) integers.add(path);
    return result;
  }
  const result = value('', 0); whitespace(); assert(at === text.length, 'JSON trailer');
  return { value: result, integers };
}
export function keys(value: any, expected: string[]): void {
  assert(value !== null && typeof value === 'object' && !Array.isArray(value) &&
    same(Object.keys(value).sort(), [...expected].sort()), 'Unexpected receipt fields');
}

/** Accept only complete hash records, without comments or extra capture text. */
export function hashManifest(data: Buffer, expected: Record<string, { sha256: string; size: number }>): void {
  const text = data.toString(); assert(text.endsWith('\n'), 'Unterminated hash manifest');
  const rows: Record<string, string> = Object.create(null);
  for (const line of text.slice(0, -1).split('\n')) {
    const match = /^([a-f0-9]{64})  (?:\.\/)?([A-Za-z0-9+._:%~\-]+)$/.exec(line);
    assert(match && pathName(match[2]) && !(match[2] in rows), 'Unexpected hash manifest text');
    rows[match[2]] = match[1];
  }
  assert(same(rows, Object.fromEntries(Object.entries(expected).map(([name, row]) => [name, row.sha256]))), 'Hash manifest closure mismatch');
}

/** Hash logical saved-image contents; do not execute or extract the archive. */
export async function imageContents(path: string, metadata?: Map<string, Buffer>): Promise<Record<string, any>> {
  const file = await open(path, 'r');
  try {
    const size = (await file.stat()).size;
    assert(size <= MAX_FILE, 'Image archive exceeds bound');
    let position = 0, total = 0, captured = 0;
    const result = Object.create(null);
    let pax: Record<string, string> = {};
    async function bytes(length: number, offset: number): Promise<Buffer> {
      const buffer = Buffer.alloc(length); const read = await file.read(buffer, 0, length, offset);
      assert(read.bytesRead === length, 'Truncated image archive'); return buffer;
    }
    const field = (buffer: Buffer) => buffer.toString('utf8').replace(/\0.*$/s, '');
    while (position + 512 <= size) {
      const header = await bytes(512, position); position += 512;
      if (header.every(byte => byte === 0)) {
        assert(Object.keys(pax).length === 0, 'Unconsumed PAX metadata');
        assert(size - position + 512 >= 1024 && size - position + 512 <= 1024 ** 2 && (size - position) % 512 === 0, 'Invalid image trailer size');
        while (position < size) {
          const data = await bytes(Math.min(1024 ** 2, size - position), position);
          assert(data.every(byte => byte === 0), 'Nonzero image trailer'); position += data.length;
        }
        assert(Object.keys(result).length > 0, 'Empty image archive'); return result;
      }
      const sum = [...header].reduce((n, byte, i) => n + (i >= 148 && i < 156 ? 32 : byte), 0);
      assert(parseInt(field(header.subarray(148, 156)).trim(), 8) === sum, 'Tar header checksum');
      const rawSize = field(header.subarray(124, 136)).trim();
      assert(/^[0-7]+$/.test(rawSize), 'Invalid tar size');
      let memberSize = parseInt(rawSize, 8);
      const prefix = field(header.subarray(345, 500));
      let name = (prefix ? prefix + '/' : '') + field(header.subarray(0, 100));
      const type = header[156];
      if (type === 120) {
        assert(Object.keys(pax).length === 0, 'Repeated PAX metadata');
        assert(memberSize <= 65536, 'PAX metadata exceeds bound');
        const data = await bytes(memberSize, position); let offset = 0;
        const next = Object.create(null);
        while (offset < data.length) {
          const space = data.indexOf(32, offset); assert(space > offset, 'Invalid PAX length');
          const length = Number(data.subarray(offset, space).toString());
          assert(Number.isSafeInteger(length) && length > space - offset + 2 && offset + length <= data.length, 'Invalid PAX record');
          const record = data.subarray(space + 1, offset + length - 1).toString();
          assert(data[offset + length - 1] === 10, 'Invalid PAX terminator');
          const split = record.indexOf('='); assert(split > 0, 'Invalid PAX field');
          const key = record.slice(0, split); assert(!(key in next) && ['path', 'size', 'mtime', 'atime', 'ctime'].includes(key), 'Unsupported PAX field');
          next[key] = record.slice(split + 1); offset += length;
        }
        pax = next; position += Math.ceil(memberSize / 512) * 512; continue;
      }
      if (pax.path) name = pax.path;
      if (pax.size) { assert(/^\d+$/.test(pax.size), 'Invalid PAX size'); memberSize = Number(pax.size); }
      pax = {};
      const directory = type === 53;
      if (directory && name.endsWith('/')) name = name.slice(0, -1);
      assert(pathName(name) && !(name in result) && Object.keys(result).length < 4096, 'Unsafe/duplicate image member');
      assert(Number.isSafeInteger(memberSize) && memberSize >= 0 && position + memberSize <= size, 'Invalid image payload');
      if (directory) { assert(memberSize === 0, 'Image directory payload'); result[name] = ['directory', 0, null]; }
      else {
        assert(type === 0 || type === 48, 'Unsupported image member kind');
        total += memberSize; assert(total <= MAX_FILE, 'Image payload exceeds bound');
        const digest = createHash('sha256'); let remaining = memberSize, cursor = position;
        const pieces: Buffer[] = [];
        while (remaining) {
          const data = await bytes(Math.min(1024 ** 2, remaining), cursor);
          if (metadata && memberSize <= 1024 ** 2) pieces.push(data);
          digest.update(data); cursor += data.length; remaining -= data.length;
        }
        result[name] = ['file', memberSize, digest.digest('hex')];
        if (metadata && pieces.length && (pieces[0][0] === 123 || pieces[0][0] === 91)) {
          captured += memberSize; assert(captured <= 16 * 1024 ** 2, 'Image JSON metadata exceeds bound');
          metadata.set(name, Buffer.concat(pieces));
        }
      }
      position += Math.ceil(memberSize / 512) * 512;
    }
    throw new Error('Missing image end marker');
  } finally { await file.close(); }
}

/** Bind saved-image config and uncompressed layer bytes without loading it. */
export async function imageIdentity(path: string, image: string, arch: string): Promise<string[]> {
  assert(/^sha256:[a-f0-9]{64}$/.test(image), 'Invalid image identity');
  const metadata = new Map<string, Buffer>(), rows = await imageContents(path, metadata);
  const manifest = json(metadata.get('manifest.json') ?? '').value;
  assert(Array.isArray(manifest) && manifest.length === 1, 'Saved image manifest count');
  const row = manifest[0];
  assert(typeof row.Config === 'string' && pathName(row.Config) && Array.isArray(row.Layers), 'Saved image paths');
  assert(rows[row.Config]?.[2] === image.slice(7), 'Saved image config digest');
  const config = json(metadata.get(row.Config) ?? '').value;
  assert(config.os === 'linux' && config.architecture === arch && config.rootfs?.type === 'layers', 'Saved image architecture');
  const layers = config.rootfs.diff_ids;
  assert(Array.isArray(layers) && layers.length > 0 && layers.length === row.Layers.length, 'Saved image layer count');
  for (let i = 0; i < layers.length; i++) {
    assert(typeof row.Layers[i] === 'string' && pathName(row.Layers[i]) && rows[row.Layers[i]]?.[0] === 'file' && 'sha256:' + rows[row.Layers[i]][2] === layers[i], 'Saved image layer bytes');
  }
  for (const [name, info] of Object.entries(rows)) {
    assert(['manifest.json', 'index.json', 'oci-layout', 'repositories', row.Config, ...row.Layers].includes(name) || /^(?:blobs(?:\/sha256)?|blobs\/sha256\/[a-f0-9]{64}|[a-f0-9]{64}(?:\/(?:json|VERSION|layer.tar))?)$/.test(name), 'Unexpected saved image member');
    if (/^blobs\/sha256\/[a-f0-9]{64}$/.test(name)) assert(info[2] === name.slice(-64), 'Saved image blob digest');
  }
  return layers;
}
