#!/usr/bin/env bun
/** Check the exact native-image conditions behind the media component dispositions. */
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createReadStream, mkdtempSync, openSync, closeSync, readSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export const imageMagickPackages = [
  'imagemagick', 'imagemagick-7-common', 'imagemagick-7.q16',
  'libimage-magick-perl', 'libimage-magick-q16-perl',
  'libmagickcore-7.q16-10', 'libmagickwand-7.q16-10',
];
export const imageMagickVersion = '8:7.1.1.43+dfsg1-1+deb13u12';
export const cupsVersion = '2.4.10-3+deb13u2';

export function forbiddenLayerMember(raw: string): boolean {
  const path = raw.replace(/^\.\//, '').replace(/^\//, '').toLowerCase();
  const base = path.split('/').at(-1) ?? '';
  return /(?:flif|uhdr|ultrahdr)/.test(base) ||
    /(?:^|\/)(?:flif|uhdr|ultrahdr)(?:\/|$)/.test(path) ||
    /(?:^|\/)imagemagick-[^/]+\/modules-[^/]+\/coders\/(?:\.wh\.)?svg\.(?:so|la)$/.test(path) ||
    /(?:^|\/)cups\/(?:backend|apple)(?:\/|$)/.test(path) ||
    /^(?:ipp|ipps|cupsd|\.wh\.(?:ipp|ipps|cupsd))$/.test(base) &&
      /(?:^|\/)(?:cups|sbin|bin|backend)\//.test(path);
}

export function checkLayerPaths(paths: string[]): void {
  const hit = paths.find(forbiddenLayerMember);
  if (hit) throw new Error(`vulnerable component path occurs in shipped layer: ${hit}`);
}

export function checkPackages(output: string): void {
  const rows = output.trim().split('\n').map((line) => {
    const fields = line.split('\t');
    if (fields.length !== 2) throw new Error(`malformed package row: ${line}`);
    return [fields[0], fields[1]];
  });
  const installed = new Map(rows);
  if (installed.size !== rows.length) throw new Error('duplicate package inventory row');
  for (const name of imageMagickPackages) {
    if (installed.get(name) !== imageMagickVersion) throw new Error(`${name} differs from reviewed version`);
  }
  if (installed.get('libcups2t64') !== cupsVersion) throw new Error('libcups2t64 differs from reviewed version');
}

export function checkCapabilities(formats: string, modules: string): void {
  const rows = new Map<string, string>();
  for (const line of formats.split('\n')) {
    const match = /^\s*([A-Za-z0-9]+)\*?\s+[A-Za-z0-9]+\s+([r-][w-][+\-])\s+/.exec(line);
    if (match) rows.set(match[1].toUpperCase(), match[2]);
  }
  for (const name of ['PNG', 'JPEG']) {
    if (!rows.get(name)?.includes('w')) throw new Error(`${name} write capability is unavailable`);
  }
  const moduleNames = new Set(modules.split('\n').map((line) => line.trim().toUpperCase()));
  for (const name of ['PNG', 'JPEG']) {
    if (!moduleNames.has(name)) throw new Error(`${name} coder module is unavailable`);
  }
  for (const name of ['FLIF', 'UHDR', 'SVG']) {
    if (rows.has(name)) throw new Error(`${name} format is registered`);
    if (moduleNames.has(name)) throw new Error(`${name} coder module is registered`);
  }
  for (const name of ['MSVG', 'RSVG']) {
    if (rows.has(name)) throw new Error(`${name} format is registered`);
  }
}

function run(command: string, args: string[], outputFd?: number): string {
  const result = spawnSync(command, args, {
    encoding: outputFd === undefined ? 'utf8' : undefined,
    maxBuffer: 128 * 1024 * 1024,
    stdio: outputFd === undefined ? ['ignore', 'pipe', 'pipe'] : ['ignore', outputFd, 'pipe'],
  });
  if (result.error || result.status !== 0) {
    throw new Error(`${command} failed: ${result.error ?? result.stderr ?? result.status}`);
  }
  return typeof result.stdout === 'string' ? result.stdout : '';
}

function nativeCheck(image: string): void {
  const packages = run('docker', [
    'run', '--rm', '--network', 'none', '--read-only', '--user', 'aelira',
    '--entrypoint', 'dpkg-query', image, '-W', '-f=${Package}\t${Version}\n',
    ...imageMagickPackages, 'libcups2t64',
  ]);
  checkPackages(packages);
  const magickArgs = [
    'run', '--rm', '--network', 'none', '--read-only', '--user', 'aelira',
    '--entrypoint', 'magick', image, '-list',
  ];
  checkCapabilities(
    run('docker', [...magickArgs, 'format']),
    run('docker', [...magickArgs, 'module']),
  );
  const script = `
    set -eu
    test -f /usr/lib/*/libcups.so.2
    for p in /usr/lib/cups/backend /usr/libexec/cups/backend /usr/lib/cups/apple /usr/libexec/cups/apple; do test ! -e "$p"; done
    for p in /usr/sbin/cupsd /usr/lib/cups/backend/ipp /usr/lib/cups/backend/ipps; do test ! -e "$p"; done
    for f in FLIF UHDR; do
      lower=$(printf '%s' "$f" | tr 'A-Z' 'a-z')
      if magick -size 1x1 xc:red "/tmp/forbidden.$lower" >/dev/null 2>&1; then exit 23; fi
    done
    printf 'push graphic-context\\nviewbox 0 0 1 1\\npop graphic-context\\n' > /tmp/mvg-probe.png
    magick identify mvg:/tmp/mvg-probe.png >/dev/null
    if magick identify /tmp/mvg-probe.png >/dev/null 2>&1; then exit 24; fi
    perl -MImage::Magick -e 'my $i=Image::Magick->new; $i->Read("/tmp/mvg-probe.png"); die "MVG content was decoded without an explicit format\\n" if @$i;'
    magick -size 1x1 xc:red png:/tmp/control.png
    magick /tmp/control.png jpeg:/tmp/control.jpg
    magick identify /tmp/control.png /tmp/control.jpg >/dev/null
    perl -MImage::Magick -e 'for my $f (qw(PNG JPEG)) { my @q=Image::Magick->QueryFormat($f); die "missing $f format\\n" unless @q && grep { defined($_) && length($_) } @q; } for my $f (qw(FLIF UHDR)) { my @q=Image::Magick->QueryFormat($f); die "unexpected $f format\\n" if grep { defined($_) && length($_) } @q; }'
    perl -MImage::Magick -e 'for my $ext (qw(png jpg)) { my $i=Image::Magick->new(size=>"2x2"); my $e=$i->Read("xc:red"); die $e if $e; $e=$i->Write(filename=>"/tmp/perl-control.$ext"); die $e if $e; my $r=Image::Magick->new; $e=$r->Read("/tmp/perl-control.$ext"); die $e if $e; die "bad readback\\n" unless $r->Get("width")==2 && $r->Get("height")==2; }'
    echo native-media-components-ok
  `;
  const output = run('docker', [
    'run', '--rm', '--network', 'none', '--read-only', '--tmpfs', '/tmp:rw,nosuid,nodev,size=16m,mode=1777',
    '--user', 'aelira', '--entrypoint', '/bin/sh', image, '-c', script,
  ]);
  if (!output.includes('native-media-components-ok')) throw new Error('native image check gave no success marker');
}

async function layerCheck(image: string): Promise<void> {
  const work = mkdtempSync(join(tmpdir(), 'aelira-native-media-'));
  try {
    const archive = join(work, 'image.tar');
    const fd = openSync(archive, 'w');
    try { run('docker', ['save', image], fd); } finally { closeSync(fd); }
    const manifests = JSON.parse(run('tar', ['-xOf', archive, 'manifest.json']));
    if (!Array.isArray(manifests) || manifests.length !== 1 ||
        !Array.isArray(manifests[0].Layers) || manifests[0].Layers.length === 0) {
      throw new Error('docker save did not contain one image with layers');
    }
    for (const layer of manifests[0].Layers) {
      if (typeof layer !== 'string' || !/^(?:[A-Za-z0-9_.-]+\/layer\.tar|blobs\/sha256\/[a-f0-9]{64})$/.test(layer) ||
          layer.startsWith('/') || layer.split('/').includes('..')) throw new Error(`unsafe layer path: ${layer}`);
      const layerFile = join(work, 'layer.tar');
      const layerFd = openSync(layerFile, 'w');
      try { run('tar', ['-xOf', archive, layer], layerFd); } finally { closeSync(layerFd); }
      if (layer.startsWith('blobs/sha256/')) {
        const digest = createHash('sha256');
        for await (const bytes of createReadStream(layerFile)) digest.update(bytes);
        if (digest.digest('hex') !== layer.split('/').at(-1)) throw new Error(`layer digest mismatch: ${layer}`);
      }
      const magic = Buffer.alloc(2);
      const readFd = openSync(layerFile, 'r');
      try { readSync(readFd, magic, 0, 2, 0); } finally { closeSync(readFd); }
      const names = run('tar', magic[0] === 0x1f && magic[1] === 0x8b ? ['-tzf', layerFile] : ['-tf', layerFile]).split('\n').filter(Boolean);
      checkLayerPaths(names);
      const verbose = run('tar', magic[0] === 0x1f && magic[1] === 0x8b ? ['-tvzf', layerFile] : ['-tvf', layerFile]);
      for (const line of verbose.split('\n')) {
        const link = line.match(/(?: -> | link to )(.+)$/)?.[1];
        if (link) checkLayerPaths([link]);
      }
    }
  } finally { rmSync(work, { recursive: true, force: true }); }
}

if (import.meta.main) {
  try {
    if (process.argv.length !== 3) throw new Error('usage: bun scripts/verify_native_media_components.ts IMAGE');
    nativeCheck(process.argv[2]);
    await layerCheck(process.argv[2]);
    console.log('Native media components and all shipped layer paths verified');
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}
