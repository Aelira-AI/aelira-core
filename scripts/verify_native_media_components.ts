#!/usr/bin/env bun
/**
 * Check the exact native-image conditions behind the media component dispositions, and
 * that Debian's FFmpeg stack (with librist, cJSON and libsndfile) stays out of
 * every shipped layer now that ffmpeg and ffprobe come from /opt/ffmpeg9.
 */
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createReadStream, mkdtempSync, openSync, closeSync, readFileSync, readSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const imageMagickPackages = [
  'imagemagick', 'imagemagick-7-common', 'imagemagick-7.q16',
  'libimage-magick-perl', 'libimage-magick-q16-perl',
  'libmagickcore-7.q16-10', 'libmagickwand-7.q16-10',
];
export const imageMagickVersion = '8:7.1.1.43+dfsg1-1+deb13u12';
export const cupsVersion = '2.4.10-3+deb13u2';
export const debianFfmpegPackages = [
  'ffmpeg', 'libavcodec61', 'libavdevice61', 'libavfilter10', 'libavformat61',
  'libavutil59', 'libpostproc58', 'libswresample5', 'libswscale8',
  'librist4', 'libcjson1', 'libsndfile1',
];

export function forbiddenLayerMember(raw: string): boolean {
  const path = raw.replace(/^\.\//, '').replace(/^\//, '').toLowerCase();
  const base = path.split('/').at(-1) ?? '';
  const whiteout = base.startsWith('.wh.');
  // Debian's FFmpeg libraries live under usr/lib; the image's own build is
  // only under opt/ffmpeg9. A whiteout of a removed file is not an addition.
  return !whiteout && /^usr\/lib\/.*\/?lib(?:avcodec|avdevice|avfilter|avformat|avutil|postproc|swresample|swscale)\.so/.test(path) ||
    !whiteout && /^lib(?:rist|cjson|sndfile)\.so/.test(base) ||
    /^usr\/bin\/(?:ffmpeg|ffprobe|ffplay)$/.test(path) ||
    /(?:flif|uhdr|ultrahdr)/.test(base) ||
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

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), '..');
export const imageMagickConfigFiles = ['policy.xml', 'delegates.xml'];
export const probeScript = join(repoRoot, 'scripts', 'imagemagick_policy_probe.sh');

/** Effective limits with MAGICK_TIME_LIMIT=500, so the policy must cap time. */
export const expectedResourceLimits: Record<string, string> = {
  Width: '16000P', Height: '16000P', Area: '64MP', 'List length': '32B',
  Memory: '256MiB', Map: '512MiB', Disk: '1GiB', Thread: '2', Time: '50 seconds',
};

export function checkResourceLimits(listing: string): void {
  const limits = new Map<string, string>();
  for (const line of listing.split('\n')) {
    const match = /^\s+([A-Za-z][A-Za-z ]*):\s+(\S.*?)\s*$/.exec(line);
    if (match) limits.set(match[1], match[2]);
  }
  for (const [name, value] of Object.entries(expectedResourceLimits)) {
    if (limits.get(name) !== value) {
      throw new Error(`ImageMagick ${name} limit is ${limits.get(name) ?? 'missing'}, expected ${value}`);
    }
  }
}

const policy = (name: string) => `policy:${name}`;
/** Every probe line imagemagick_policy_probe.sh prints, with its required outcome. */
export const expectedProbes: Record<string, string | RegExp> = {
  msl: policy('MSL'), mvg: policy('MVG'), 'mvg-mixed-case': policy('MVG'),
  cut: policy('CUT'), text: policy('TEXT'), txt: policy('TXT'),
  ps: policy('PS'), 'ps-extension': policy('PS'), 'ps-named-png': policy('PS'),
  xc: policy('XC'), https: policy('HTTPS'), 'file-list': /^policy:/,
  svg: 'unregistered', msvg: 'unregistered',
  'perl-mvg': policy('MVG'), 'perl-ps-named-png': policy('PS'),
  'control-msl': 'accepted', 'control-mvg': 'accepted', 'control-cut': 'accepted',
  'control-text': 'accepted', 'control-txt': 'accepted', 'control-ps': 'accepted',
  'control-ps-named-png': 'accepted', 'control-mvg-named-png': 'accepted',
  'mvg-named-png': 'other', 'perl-mvg-named-png-undecoded': 'accepted',
  'edge-width': 'accepted', 'over-width': 'limit', 'over-height': 'limit', 'over-width-eps': 'limit',
  'bzpng-extension': 'accepted', 'bzpng-png-prefix': 'accepted',
  'miff-prefix': policy('MIFF'), 'miff-extension': policy('MIFF'), 'miff-named-png': policy('MIFF'),
  'psd-prefix': policy('PSD'), 'psd-extension': policy('PSD'), 'psd-named-png': policy('PSD'),
  'control-bzpng': 'accepted', 'control-miff': 'accepted', 'control-psd': 'accepted',
  'png-to-png': 'accepted', 'png-to-jpeg': 'accepted', 'jpeg-to-png': 'accepted',
  'cmyk-eps-to-png': 'accepted', 'cmyk-pdf-to-png': 'accepted',
  'write-gif': policy('GIF'), 'write-pdf': policy('PDF'), 'perl-png-jpeg': 'accepted',
  'gs-default-file-read': 'invalidfileaccess', 'gs-nosafer-file-read': 'accepted',
};

/** The graphics LaTeXML writes for the probe document, in document order. */
export const expectedGraphics: Array<[string, string]> = [
  ['x1.png', 'PNG'], ['x2.png', 'PNG'], ['fig-png.png', 'PNG'], ['fig-jpg.jpg', 'JPEG'],
  ['x3.png', 'PNG'], ['x4.jpg', 'JPEG'], ['x5.png', 'PNG'], ['x6.png', 'PNG'], ['x7.png', 'PNG'],
];

export function checkPolicyProbe(output: string): void {
  const lines = output.split('\n').map((line) => line.trim()).filter(Boolean);
  if (lines.at(-1) !== 'imagemagick-policy-probe-complete') throw new Error('policy probe did not complete');
  const probes = new Map<string, string>();
  const graphics: string[][] = [];
  const ghostscript: string[][] = [];
  const latexml = new Map<string, string>();
  const launcher = new Map<string, string>();
  const converter = new Map<string, string>();
  let policyPaths: string | undefined;
  for (const line of lines.slice(0, -1)) {
    const [kind, ...rest] = line.split(/\s+/);
    if (kind === 'probe' && rest.length === 2) {
      if (probes.has(rest[0])) throw new Error(`duplicate probe ${rest[0]}`);
      probes.set(rest[0], rest[1]);
    } else if (kind === 'graphic') graphics.push(rest);
    else if (kind === 'gs') ghostscript.push(rest);
    else if (kind === 'latexml' && rest.length > 0) latexml.set(rest[0], rest.slice(1).join(' '));
    else if (kind === 'policy-paths') policyPaths = rest.join(' ');
    else if (kind === 'launcher' && rest.length > 0) launcher.set(rest[0], rest.slice(1).join(' '));
    else if (kind === 'converter' && rest.length > 0) converter.set(rest[0], rest.slice(1).join(' '));
    else throw new Error(`unexpected policy probe line: ${line}`);
  }
  for (const [name, expected] of Object.entries(expectedProbes)) {
    const actual = probes.get(name);
    if (actual === undefined) throw new Error(`policy probe ${name} did not run`);
    const ok = typeof expected === 'string' ? actual === expected : expected.test(actual);
    if (!ok) throw new Error(`policy probe ${name} gave ${actual}, expected ${expected}`);
  }
  for (const name of probes.keys()) {
    if (!(name in expectedProbes)) throw new Error(`unexpected policy probe ${name}`);
  }
  if (latexml.get('allowed') !== 'exit 0 errors 0') throw new Error('LaTeXML graphics conversion reported errors');
  if (graphics.length !== expectedGraphics.length) throw new Error(`LaTeXML wrote ${graphics.length} graphics`);
  expectedGraphics.forEach(([source, format], index) => {
    const [actualSource, actualFormat, size] = graphics[index];
    if (actualSource !== source || actualFormat !== format || !/^[1-9]\d*x[1-9]\d*$/.test(size ?? '')) {
      throw new Error(`LaTeXML graphic ${index + 1} is ${graphics[index].join(' ')}, expected ${source} ${format}`);
    }
  });
  const devices = new Set<string>();
  for (const flags of ghostscript) {
    for (const flag of ['-dSAFER', '-dBATCH', '-dNOPAUSE']) {
      if (!flags.includes(flag)) throw new Error(`Ghostscript ran without ${flag}`);
    }
    if (flags.includes('-dNOSAFER')) throw new Error('Ghostscript ran with -dNOSAFER');
    const device = flags.filter((flag) => flag.startsWith('-sDEVICE='));
    if (device.length !== 1 || !['-sDEVICE=pngalpha', '-sDEVICE=pamcmyk32'].includes(device[0])) {
      throw new Error(`Ghostscript ran with device ${device.join(',') || 'none'}`);
    }
    devices.add(device[0]);
  }
  if (devices.size !== 2) throw new Error('LaTeXML did not render through both Ghostscript devices');
  if (latexml.get('ps-named-png-refused') !== 'policy-refusals 1 gs-calls 0 images 0') {
    throw new Error('LaTeXML did not refuse PostScript in a .png file before Ghostscript');
  }
  if (!/^policy-refusals 0 gs-calls [1-9]\d* images 1$/.test(latexml.get('ps-named-png-control') ?? '')) {
    throw new Error('LaTeXML PostScript control did not render with the override policy');
  }
  if (launcher.get('planted-launcher') !== 'exits 0,0 policy-refusals 1 images 0 home-removed yes') {
    throw new Error('a policy planted in the conversion directory changed what the LaTeX launcher refuses');
  }
  if (launcher.get('planted-control') !== 'exits 0,0 policy-refusals 0 images 1 home-removed yes') {
    throw new Error('the planted policy control did not render through the LaTeX launcher');
  }
  // Without the converter's check the bare launcher still renders a bzip2-wrapped
  // PNG: the negative control that shows the converter check is what refuses it.
  if (launcher.get('media-bzpng') !== 'refused no rendered yes') {
    throw new Error('the bare-launcher control did not render the bzip2-wrapped PNG');
  }
  for (const name of ['bzpng', 'gzpng']) {
    if (converter.get(name) !== 'compressed-input yes parsed no converted no') {
      throw new Error(`the converter did not refuse the ${name === 'bzpng' ? 'bzip2' : 'gzip'}-wrapped PNG before LaTeXML ran (CVE-2026-106565)`);
    }
  }
  if (converter.get('png') !== 'compressed-input no parsed yes converted yes') {
    throw new Error('the plain PNG control did not convert through the converter');
  }
  for (const format of ['miff', 'psd']) {
    if (launcher.get(`media-${format}`) !== 'refused yes rendered no') {
      throw new Error(`a ${format.toUpperCase()} file named .png was not refused through the LaTeX launcher`);
    }
  }
  if (policyPaths !== '/etc/ImageMagick-7/policy.xml [built-in]') {
    throw new Error(`ImageMagick loaded policies from ${policyPaths ?? 'nowhere'}`);
  }
}

export function checkConfigFile(name: string, shipped: Buffer, reviewed: Buffer): void {
  if (!shipped.equals(reviewed)) throw new Error(`/etc/ImageMagick-7/${name} differs from docker/imagemagick/${name}`);
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
    perl -MImage::Magick -e 'for my $f (qw(PNG JPEG)) { my @q=Image::Magick->QueryFormat($f); die "missing $f format\\n" unless @q && grep { defined($_) && length($_) } @q; } for my $f (qw(FLIF UHDR)) { my @q=Image::Magick->QueryFormat($f); die "unexpected $f format\\n" if grep { defined($_) && length($_) } @q; }'
    for p in ${debianFfmpegPackages.join(' ')}; do
      if [ "$(dpkg-query -W -f='\${db:Status-Status}' "$p" 2>/dev/null || true)" = installed ]; then echo "$p is installed" >&2; exit 24; fi
    done
    for t in ffmpeg ffprobe; do
      test "$(readlink -f "$(command -v "$t")")" = "/opt/ffmpeg9/bin/$t" || { echo "$t is not the /opt/ffmpeg9 build" >&2; exit 25; }
    done
    echo native-media-components-ok
  `;
  const output = run('docker', [
    'run', '--rm', '--network', 'none', '--read-only', '--tmpfs', '/tmp:rw,nosuid,nodev,size=16m,mode=1777',
    '--user', 'aelira', '--entrypoint', '/bin/sh', image, '-c', script,
  ]);
  if (!output.includes('native-media-components-ok')) throw new Error('native image check gave no success marker');
  for (const name of imageMagickConfigFiles) {
    const shipped = spawnSync('docker', [
      'run', '--rm', '--network', 'none', '--read-only', '--user', 'aelira',
      '--entrypoint', 'cat', image, `/etc/ImageMagick-7/${name}`,
    ], { maxBuffer: 1024 * 1024, stdio: ['ignore', 'pipe', 'pipe'] });
    if (shipped.error || shipped.status !== 0) throw new Error(`cannot read /etc/ImageMagick-7/${name}`);
    checkConfigFile(name, shipped.stdout, readFileSync(join(repoRoot, 'docker', 'imagemagick', name)));
  }
  checkResourceLimits(run('docker', [
    'run', '--rm', '--network', 'none', '--read-only', '--user', 'aelira', '--env', 'MAGICK_TIME_LIMIT=500',
    '--entrypoint', 'magick', image, '-list', 'resource',
  ]));
  checkPolicyProbe(run('docker', [
    'run', '--rm', '--network', 'none', '--read-only',
    '--tmpfs', '/tmp:rw,exec,nosuid,nodev,size=128m,mode=1777',
    '--volume', `${probeScript}:/opt/aelira-probe/imagemagick_policy_probe.sh:ro`,
    '--user', 'aelira', '--entrypoint', '/bin/sh', image, '/opt/aelira-probe/imagemagick_policy_probe.sh',
  ]));
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
    console.log('Native media components, the ImageMagick policy and all shipped layer paths verified');
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}
