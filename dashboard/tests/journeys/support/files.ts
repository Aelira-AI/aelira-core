import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const run = promisify(execFile);

/** Document properties of a saved PDF, read by poppler rather than by the product. */
export async function pdfInfo(path: string): Promise<Record<string, string>> {
  const { stdout } = await run('pdfinfo', [path]);
  const info: Record<string, string> = {};
  for (const line of stdout.split('\n')) {
    const split = line.indexOf(':');
    if (split > 0) info[line.slice(0, split).trim()] = line.slice(split + 1).trim();
  }
  return info;
}

/** One part of an Office package, extracted with unzip. */
export async function officePart(path: string, part: string): Promise<string> {
  const { stdout } = await run('unzip', ['-p', path, part], { maxBuffer: 32 * 1024 * 1024 });
  return stdout;
}

/** Every a:t, w:t or shared string in an Office part, in order. */
export function textRuns(xml: string): string[] {
  return [...xml.matchAll(/<(?:a:t|w:t|t)(?:\s[^>]*)?>([\s\S]*?)<\/(?:a:t|w:t|t)>/g)].map((m) => m[1]);
}
