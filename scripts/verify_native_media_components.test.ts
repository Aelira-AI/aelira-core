import { expect, test } from 'bun:test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  checkCapabilities, checkConfigFile, checkLayerPaths, checkPackages, checkPolicyProbe, checkResourceLimits,
  cupsVersion, debianFfmpegPackages, expectedGraphics, expectedProbes, forbiddenLayerMember, imageMagickPackages, imageMagickVersion,
} from './verify_native_media_components';

test('exact reviewed package versions are required', () => {
  const lines = [...imageMagickPackages.map((name) => `${name}\t${imageMagickVersion}`), `libcups2t64\t${cupsVersion}`];
  expect(() => checkPackages(lines.join('\n'))).not.toThrow();
  expect(() => checkPackages(lines.map((line) => line.replace(cupsVersion, '2.4.10-3+deb13u3')).join('\n'))).toThrow();
  expect(() => checkPackages(lines.slice(1).join('\n'))).toThrow();
  expect(() => checkPackages([...lines, lines[0]].join('\n'))).toThrow();
});

test('registered encoders fail even with starred format names or lowercase modules', () => {
  const formats = '   Format  Module    Mode  Description\n     PNG* PNG       rw- Portable Network Graphics\n    JPEG* JPEG      rw- Joint Photographic Experts Group\n';
  const modules = 'Image Coder\n-------------------------------------------------------------------------------\npng\njpeg\n';
  expect(() => checkCapabilities(formats, modules)).not.toThrow();
  expect(() => checkCapabilities(`${formats}    FLIF* FLIF      -w+ FLIF encoder\n`, modules)).toThrow('FLIF format');
  expect(() => checkCapabilities(formats, `${modules}uhdr\n`)).toThrow('UHDR coder');
  expect(() => checkCapabilities(`${formats}     SVG  SVG       rw+ Scalable Vector Graphics (RSVG 2.60.0)\n`, modules)).toThrow('SVG format');
  expect(() => checkCapabilities(`${formats}    MSVG  SVG       rw+ ImageMagick's own SVG internal renderer\n`, modules)).toThrow('MSVG format');
  expect(() => checkCapabilities(formats, `${modules}svg\n`)).toThrow('SVG coder');
  expect(() => checkCapabilities(formats.replace('rw- Portable', 'r-- Portable'), modules)).toThrow('PNG write');
  expect(() => checkCapabilities(formats, 'Image Coder\npng\n')).toThrow('JPEG coder');
});

test('layer paths expose encoder, backend, alias and whiteout introductions', () => {
  for (const path of [
    './usr/lib/x86_64-linux-gnu/ImageMagick-7.1.1/modules-Q16/coders/flif.so',
    'usr/lib/aarch64-linux-gnu/ImageMagick-7.1.1/modules-Q16/coders/uhdr.la',
    'usr/lib/x86_64-linux-gnu/libflif.so.0', 'usr/lib/libuhdr.so', 'usr/lib/libultrahdr.so',
    'usr/lib/cups/backend/ipp', 'usr/lib/cups/apple/ipp',
    'usr/sbin/cupsd', 'usr/lib/cups/backend/.wh.ipp',
    'usr/lib/x86_64-linux-gnu/ImageMagick-7.1.1/modules-Q16/coders/svg.so',
    './usr/lib/aarch64-linux-gnu/ImageMagick-7.1.1/modules-Q16/coders/svg.la',
    'usr/lib/x86_64-linux-gnu/ImageMagick-7.1.1/modules-Q16/coders/.wh.svg.so',
  ]) {
    expect(forbiddenLayerMember(path)).toBe(true);
    expect(() => checkLayerPaths([path])).toThrow('vulnerable component path');
  }
  for (const path of ['usr/lib/aarch64-linux-gnu/libcups.so.2', 'usr/bin/magick', 'usr/share/doc/cups/README', 'usr/lib/apt/methods/http', 'usr/lib/apt/methods/https',
    'usr/lib/x86_64-linux-gnu/ImageMagick-7.1.1/modules-Q16/coders/png.so',
    'usr/lib/x86_64-linux-gnu/gdk-pixbuf-2.0/2.10.0/loaders/libpixbufloader-svg.so',
    'usr/share/icons/hicolor/scalable/apps/svg.svg',
    'opt/ffmpeg9/lib/libavcodec.so.63', 'opt/ffmpeg9/bin/ffmpeg', 'usr/local/bin/ffmpeg',
    'usr/lib/aarch64-linux-gnu/libavif.so.16', 'usr/lib/x86_64-linux-gnu/libavahi-client.so.3',
    'usr/lib/aarch64-linux-gnu/.wh.libavcodec.so.61']) {
    expect(forbiddenLayerMember(path)).toBe(false);
    expect(() => checkLayerPaths([path])).not.toThrow();
  }
});

test('Debian FFmpeg, librist, cJSON and libsndfile files are rejected in any layer', () => {
  for (const path of [
    'usr/lib/x86_64-linux-gnu/libavcodec.so.61', './usr/lib/aarch64-linux-gnu/libavformat.so.61.7.100',
    'usr/lib/aarch64-linux-gnu/libswscale.so.8', 'usr/lib/aarch64-linux-gnu/libpostproc.so.58',
    'usr/lib/aarch64-linux-gnu/librist.so.4', 'usr/lib/x86_64-linux-gnu/libcjson.so.1.7.18',
    'usr/lib/aarch64-linux-gnu/libsndfile.so.1', 'usr/bin/ffmpeg', 'usr/bin/ffprobe',
  ]) {
    expect(forbiddenLayerMember(path)).toBe(true);
    expect(() => checkLayerPaths([path])).toThrow('vulnerable component path');
  }
  expect(debianFfmpegPackages).toContain('librist4');
  expect(debianFfmpegPackages).toContain('libcjson1');
});

function probeOutput(overrides: Record<string, string> = {}, extra: { drop?: string; lines?: string[]; gs?: string[]; latexml?: Record<string, string>; launcher?: Record<string, string>; paths?: string } = {}): string {
  const lines: string[] = [];
  for (const [name, expected] of Object.entries(expectedProbes)) {
    if (name === extra.drop) continue;
    lines.push(`probe ${name} ${overrides[name] ?? (typeof expected === 'string' ? expected : 'policy:TXT')}`);
  }
  const latexml = { allowed: 'exit 0 errors 0', 'ps-named-png-refused': 'policy-refusals 1 gs-calls 0 images 0',
    'ps-named-png-control': 'policy-refusals 0 gs-calls 2 images 1', ...extra.latexml };
  lines.push(`latexml allowed ${latexml.allowed}`);
  for (const [source, format] of expectedGraphics) lines.push(`graphic ${source} ${format} 100x67`);
  for (const flags of extra.gs ?? ['-dSAFER -dBATCH -dNOPAUSE -sDEVICE=pngalpha', '-dSAFER -dBATCH -dNOPAUSE -sDEVICE=pamcmyk32']) {
    lines.push(`gs ${flags} `);
  }
  lines.push(`latexml ps-named-png-refused ${latexml['ps-named-png-refused']}`);
  lines.push(`latexml ps-named-png-control ${latexml['ps-named-png-control']}`);
  const launcher = { 'planted-launcher': 'exits 0,0 policy-refusals 1 images 0 home-removed yes',
    'planted-control': 'exits 0,0 policy-refusals 0 images 1 home-removed yes',
    'media-bzpng': 'refused no rendered yes', 'media-miff': 'refused yes rendered no',
    'media-psd': 'refused yes rendered no', ...extra.launcher };
  for (const [name, value] of Object.entries(launcher)) lines.push(`launcher ${name} ${value}`);
  lines.push(`policy-paths ${extra.paths ?? '/etc/ImageMagick-7/policy.xml [built-in]'} `);
  lines.push(...(extra.lines ?? []));
  lines.push('imagemagick-policy-probe-complete');
  return lines.join('\n');
}

test('the policy probe accepts only the reviewed refusals, controls and LaTeXML graphics', () => {
  expect(() => checkPolicyProbe(probeOutput())).not.toThrow();
  // Outcomes recorded from the same probe against Debian's policy.xml and delegates.xml.
  const debian = {
    msl: 'accepted', mvg: 'accepted', 'mvg-mixed-case': 'accepted', cut: 'accepted', text: 'accepted',
    txt: 'accepted', ps: 'accepted', 'ps-extension': 'accepted', 'ps-named-png': 'accepted', xc: 'other',
    svg: 'other', msvg: 'other', 'perl-mvg': 'accepted', 'perl-ps-named-png': 'accepted',
    'over-width': 'accepted', 'over-height': 'accepted', 'over-width-eps': 'accepted',
    'write-gif': 'accepted', 'write-pdf': 'accepted',
  };
  for (const [name, outcome] of Object.entries(debian)) {
    expect(() => checkPolicyProbe(probeOutput({ [name]: outcome }))).toThrow(`policy probe ${name}`);
  }
  expect(() => checkPolicyProbe(probeOutput({ 'control-mvg': 'policy:MVG' }))).toThrow('control-mvg');
  expect(() => checkPolicyProbe(probeOutput({ mvg: 'policy:MSL' }))).toThrow('probe mvg');
  expect(() => checkPolicyProbe(probeOutput({ 'gs-default-file-read': 'accepted' }))).toThrow('gs-default-file-read');
  expect(() => checkPolicyProbe(probeOutput({}, { drop: 'cut' }))).toThrow('cut did not run');
  expect(() => checkPolicyProbe(probeOutput({}, { lines: ['probe new-probe accepted'] }))).toThrow('unexpected policy probe');
  expect(() => checkPolicyProbe(probeOutput({}, { lines: ['probe cut accepted'] }))).toThrow('duplicate probe');
  expect(() => checkPolicyProbe(probeOutput().replace('\nimagemagick-policy-probe-complete', ''))).toThrow('did not complete');
  expect(() => checkPolicyProbe(probeOutput().replace('graphic x2.png PNG', 'graphic x2.png PDF'))).toThrow('graphic 2');
  expect(() => checkPolicyProbe(probeOutput().replace('graphic x7.png PNG 100x67\n', ''))).toThrow('wrote 8 graphics');
  expect(() => checkPolicyProbe(probeOutput({}, { latexml: { allowed: 'exit 0 errors 1' } }))).toThrow('reported errors');
});

test('Ghostscript must run with SAFER, BATCH, NOPAUSE and one of two raster devices', () => {
  const ok = '-dSAFER -dBATCH -dNOPAUSE';
  expect(() => checkPolicyProbe(probeOutput({}, { gs: [`${ok} -sDEVICE=pngalpha`] }))).toThrow('both Ghostscript devices');
  expect(() => checkPolicyProbe(probeOutput({}, { gs: ['-dBATCH -dNOPAUSE -sDEVICE=pngalpha', `${ok} -sDEVICE=pamcmyk32`] }))).toThrow('without -dSAFER');
  expect(() => checkPolicyProbe(probeOutput({}, { gs: [`${ok} -dNOSAFER -sDEVICE=pngalpha`, `${ok} -sDEVICE=pamcmyk32`] }))).toThrow('-dNOSAFER');
  expect(() => checkPolicyProbe(probeOutput({}, { gs: [`${ok} -sDEVICE=pdfwrite`, `${ok} -sDEVICE=pamcmyk32`] }))).toThrow('device');
  expect(() => checkPolicyProbe(probeOutput({}, { gs: [`${ok} -sDEVICE=pngalpha -sDEVICE=pdfwrite`, `${ok} -sDEVICE=pamcmyk32`] }))).toThrow('device');
});

test('LaTeXML must refuse PostScript in a .png file before Ghostscript, and only through the policy', () => {
  expect(() => checkPolicyProbe(probeOutput({}, { latexml: { 'ps-named-png-refused': 'policy-refusals 0 gs-calls 2 images 1' } }))).toThrow('refuse PostScript');
  expect(() => checkPolicyProbe(probeOutput({}, { latexml: { 'ps-named-png-refused': 'policy-refusals 1 gs-calls 1 images 0' } }))).toThrow('refuse PostScript');
  expect(() => checkPolicyProbe(probeOutput({}, { latexml: { 'ps-named-png-control': 'policy-refusals 1 gs-calls 0 images 0' } }))).toThrow('control');
  expect(() => checkPolicyProbe(probeOutput({}, { paths: '/etc/ImageMagick-7/policy.xml /tmp/h/.config/ImageMagick/policy.xml [built-in]' }))).toThrow('loaded policies');
});

test('a policy planted in the conversion directory must not loosen the LaTeX launcher', () => {
  // Recorded with HOME set to the conversion directory, as the launcher did before.
  expect(() => checkPolicyProbe(probeOutput({}, { launcher: { 'planted-launcher': 'exits 0,0 policy-refusals 0 images 1 home-removed yes' } }))).toThrow('planted in the conversion directory');
  expect(() => checkPolicyProbe(probeOutput({}, { launcher: { 'planted-launcher': 'exits 0,0 policy-refusals 1 images 0 home-removed no' } }))).toThrow('planted in the conversion directory');
  expect(() => checkPolicyProbe(probeOutput({}, { launcher: { 'planted-launcher': 'exits 126,126 policy-refusals 0 images 0 home-removed yes' } }))).toThrow('planted in the conversion directory');
  expect(() => checkPolicyProbe(probeOutput({}, { launcher: { 'planted-control': 'exits 0,0 policy-refusals 1 images 0 home-removed yes' } }))).toThrow('control did not render');
  expect(() => checkPolicyProbe(probeOutput().replace(/launcher planted-control[^\n]*\n/, ''))).toThrow('control did not render');
});

test('MIFF and PSD are refused and a bzip2-wrapped PNG stays reachable through the launcher', () => {
  // Outcomes recorded against Debian's open policy, where each is accepted.
  for (const name of ['miff-prefix', 'miff-extension', 'miff-named-png', 'psd-prefix', 'psd-extension', 'psd-named-png']) {
    expect(() => checkPolicyProbe(probeOutput({ [name]: 'accepted' }))).toThrow(`policy probe ${name}`);
  }
  // The bzip2-wrapped PNG must stay reachable: a refusal here would be a false
  // not_affected for CVE-2026-106565, so the check requires 'accepted'.
  expect(() => checkPolicyProbe(probeOutput({ 'bzpng-extension': 'policy:PNG' }))).toThrow('bzpng-extension');
  expect(() => checkPolicyProbe(probeOutput({ 'control-miff': 'policy:MIFF' }))).toThrow('control-miff');
  expect(() => checkPolicyProbe(probeOutput({}, { launcher: { 'media-bzpng': 'refused yes rendered no' } }))).toThrow('CVE-2026-106565 reachability');
  expect(() => checkPolicyProbe(probeOutput({}, { launcher: { 'media-miff': 'refused no rendered yes' } }))).toThrow('MIFF file named .png');
  expect(() => checkPolicyProbe(probeOutput({}, { launcher: { 'media-psd': 'refused no rendered yes' } }))).toThrow('PSD file named .png');
});

test('effective resource limits must match the policy, including the time cap', () => {
  const shipped = 'Resource limits:\n  Width: 16000P\n  Height: 16000P\n  Area: 64MP\n  List length: 32B\n  Memory: 256MiB\n  Map: 512MiB\n  Disk: 1GiB\n  File: 768\n  Thread: 2\n  Throttle: 0\n  Time: 50 seconds\n';
  expect(() => checkResourceLimits(shipped)).not.toThrow();
  const debian = 'Resource limits:\n  Width: 32000P\n  Height: 32000P\n  Area: 256MP\n  List length: unlimited\n  Memory: 1GiB\n  Map: 2GiB\n  Disk: 2GiB\n  File: 768\n  Thread: 19\n  Throttle: 0\n  Time: unlimited\n';
  expect(() => checkResourceLimits(debian)).toThrow('Width limit');
  expect(() => checkResourceLimits(shipped.replace('Time: 50 seconds', 'Time: 500 seconds'))).toThrow('Time limit');
  expect(() => checkResourceLimits(shipped.replace('  Disk: 1GiB\n', ''))).toThrow('Disk limit is missing');
});

test('shipped ImageMagick files must equal the reviewed ones', () => {
  for (const name of ['policy.xml', 'delegates.xml']) {
    const reviewed = readFileSync(join(import.meta.dir, '..', 'docker', 'imagemagick', name));
    expect(() => checkConfigFile(name, Buffer.from(reviewed), reviewed)).not.toThrow();
    expect(() => checkConfigFile(name, Buffer.concat([reviewed, Buffer.from('\n')]), reviewed)).toThrow('differs');
  }
  const policy = readFileSync(join(import.meta.dir, '..', 'docker', 'imagemagick', 'policy.xml'), 'utf8');
  const coderRules = [...policy.matchAll(/<policy domain="coder" rights="([^"]+)" pattern="([^"]+)"\/>/g)].map((m) => [m[1], m[2]]);
  expect(coderRules[0]).toEqual(['none', '*']);
  const allowed = coderRules.filter(([rights]) => rights !== 'none').flatMap(([, pattern]) => pattern.replace(/[{}]/g, '').split(','));
  expect(allowed.sort()).toEqual(['EPS', 'JPEG', 'JPG', 'PAM', 'PDF', 'PNG']);
  expect(new Set(allowed).size).toBe(allowed.length);
  const delegates = readFileSync(join(import.meta.dir, '..', 'docker', 'imagemagick', 'delegates.xml'), 'utf8');
  const commands = [...delegates.matchAll(/<delegate decode="([^"]+)"[^>]* command="([^"]+)"\/>/g)];
  expect(commands.map((m) => m[1])).toEqual(['ps:alpha', 'ps:cmyk']);
  for (const [, , command] of commands) {
    expect(command.startsWith('&apos;gs&apos; ')).toBe(true);
    for (const flag of ['-dSAFER', '-dBATCH', '-dNOPAUSE']) expect(command).toContain(flag);
  }
});
