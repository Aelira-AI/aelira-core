#!/usr/bin/env bun
/**
 * Bind the rebuilt Debian packages in an image to their reviewed recipes.
 *
 * Each docker/debian-backports/SOURCE/backport.conf names the pinned Debian
 * source, the reviewed patches and the CVEs they fix. The build writes a
 * receipt into the image. This check requires every receipt to match its
 * recipe exactly, the installed packages and loaded libraries to match the
 * receipt, native consumers to work, and each patched library to occur in
 * exactly one image layer with the receipt's hash.
 */
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { closeSync, mkdtempSync, openSync, readdirSync, readFileSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export const backportRoot = join(import.meta.dir, '..', 'docker', 'debian-backports');
export const receiptDirectory = '/usr/share/doc/aelira-debian-backports';
const sha256 = /^[a-f0-9]{64}$/;
const cveId = /^CVE-\d{4}-\d{4,}$/;
const multiarch: Record<string, string> = { arm64: 'aarch64-linux-gnu', amd64: 'x86_64-linux-gnu' };

export type Backport = {
  source: string;
  sourceVersion: string;
  rebuiltVersion: string;
  disclosure: string;
  inputs: Array<[string, string]>;
  patches: Array<{ file: string; sha256: string; cves: string[] }>;
  fixed: string[];
  assertions: Array<[string, string]>;
  controls: string[];
  binaries: string[];
  libraries: Array<{ pkg: string; file: string }>;
  runtime: string[];
  dev: string[];
};

export type Receipt = {
  architecture: 'arm64' | 'amd64';
  packages: Map<string, string>;
  libraries: Array<{ pkg: string; path: string; sha256: string }>;
};

export type VerifiedBackport = {
  source: string;
  version: string;
  binaries: Set<string>;
  fixed: Set<string>;
  disclosure: string;
};

function fail(message: string): never {
  throw new Error(message);
}

export function parseBackportConf(raw: string): Backport {
  const single = new Map<string, string>();
  const backport: Omit<Backport, 'source' | 'sourceVersion' | 'rebuiltVersion' | 'disclosure' | 'fixed'> = {
    inputs: [], patches: [], assertions: [], controls: [], binaries: [], libraries: [], runtime: [], dev: [],
  };
  let dsc = 0;
  for (const line of raw.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const [key, ...fields] = trimmed.split(/\s+/);
    switch (key) {
      case 'source': case 'source_version': case 'rebuilt_version': case 'build': case 'disclosure':
        if (fields.length !== 1 || single.has(key)) fail(`invalid ${key} in backport recipe`);
        single.set(key, fields[0]);
        break;
      case 'changelog_date':
        if (single.has(key)) fail('duplicate changelog_date in backport recipe');
        single.set(key, fields.join(' '));
        break;
      case 'dsc': case 'input':
        if (fields.length !== 2 || !sha256.test(fields[1])) fail(`invalid ${key} in backport recipe`);
        if (key === 'dsc') {
          if (dsc++ || backport.inputs.length) fail('the .dsc must be the first and only dsc input');
        }
        backport.inputs.push([fields[0], fields[1]]);
        break;
      case 'patch': {
        const cves = (fields[3] ?? '').split(',');
        if (fields.length !== 4 || !sha256.test(fields[1]) || !/^(0|-?\d+(,-?\d+)*)$/.test(fields[2]) ||
            !cves.every((cve) => cveId.test(cve))) fail('invalid patch in backport recipe');
        backport.patches.push({ file: fields[0], sha256: fields[1], cves });
        break;
      }
      case 'assert':
        if (fields.length < 3 || !cveId.test(fields[0])) fail('invalid assert in backport recipe');
        backport.assertions.push([fields[0], fields[1]]);
        break;
      case 'control': case 'check':
        if (fields.length !== 1) fail(`invalid ${key} in backport recipe`);
        backport.controls.push(fields[0]);
        break;
      case 'profile':
        if (fields.length !== 1) fail('invalid profile in backport recipe');
        break;
      case 'binary':
        if (fields.length !== 1 || backport.binaries.includes(fields[0])) fail('invalid binary in backport recipe');
        backport.binaries.push(fields[0]);
        break;
      case 'library':
        if (fields.length !== 2 || !/^lib[\w+.-]+\.so\.\d+(\.\d+)*$/.test(fields[1])) fail('invalid library in backport recipe');
        backport.libraries.push({ pkg: fields[0], file: fields[1] });
        break;
      case 'runtime': case 'dev':
        if (!fields.length || backport[key].length) fail(`invalid ${key} in backport recipe`);
        backport[key].push(...fields);
        break;
      default:
        fail(`unknown backport recipe directive: ${key}`);
    }
  }
  for (const key of ['source', 'source_version', 'rebuilt_version', 'build', 'disclosure', 'changelog_date']) {
    if (!single.get(key)) fail(`backport recipe lacks ${key}`);
  }
  const sourceVersion = single.get('source_version')!;
  const rebuiltVersion = single.get('rebuilt_version')!;
  if (!rebuiltVersion.startsWith(`${sourceVersion}+aelira`) || !/\+aelira\d+$/.test(rebuiltVersion)) {
    fail('rebuilt_version must extend source_version with +aeliraN');
  }
  if (dsc !== 1 || backport.patches.length === 0) fail('backport recipe needs a .dsc and at least one patch');
  for (const library of backport.libraries) {
    if (!backport.binaries.includes(library.pkg)) fail(`library package ${library.pkg} is not built`);
  }
  for (const pkg of [...backport.runtime, ...backport.dev]) {
    if (!backport.binaries.includes(pkg)) fail(`installed package ${pkg} is not built`);
  }
  const fixed = [...new Set(backport.patches.flatMap((patch) => patch.cves))].sort();
  return {
    ...backport,
    source: single.get('source')!,
    sourceVersion,
    rebuiltVersion,
    disclosure: single.get('disclosure')!,
    fixed,
  };
}

export function loadBackports(root = backportRoot): Backport[] {
  return readdirSync(root).sort()
    .filter((name) => statSync(join(root, name)).isDirectory())
    .map((name) => {
      const backport = parseBackportConf(readFileSync(join(root, name, 'backport.conf'), 'utf8'));
      if (backport.source !== name) fail(`backport directory ${name} names source ${backport.source}`);
      return backport;
    });
}

/** Parse a receipt and require it to describe exactly the reviewed recipe. */
export function parseReceipt(raw: string, backport: Backport): Receipt {
  const single = new Map<string, string>();
  const lists = new Map<string, string[]>(
    ['input_sha256', 'patch_sha256', 'fixed', 'assertion', 'control', 'package_sha256', 'library'].map((key) => [key, []]));
  for (const line of raw.trim().split('\n')) {
    const match = /^([a-z0-9_]+)=(.+)$/.exec(line);
    if (!match) fail(`invalid ${backport.source} receipt line: ${line}`);
    const [, key, value] = match;
    const list = lists.get(key);
    if (list) list.push(value);
    else if (['format', 'source', 'source_version', 'rebuilt_version', 'architecture'].includes(key)) {
      if (single.has(key)) fail(`duplicate ${backport.source} receipt field: ${key}`);
      single.set(key, value);
    } else fail(`unknown ${backport.source} receipt field: ${key}`);
  }
  const same = (a: string[], b: string[]) => a.length === b.length && a.every((item, index) => item === b[index]);
  const architecture = single.get('architecture');
  if (architecture !== 'arm64' && architecture !== 'amd64') fail(`invalid ${backport.source} receipt architecture`);
  if (single.get('format') !== 'aelira-debian-backport-v1' || single.get('source') !== backport.source ||
      single.get('source_version') !== backport.sourceVersion ||
      single.get('rebuilt_version') !== backport.rebuiltVersion ||
      !same(lists.get('input_sha256')!, backport.inputs.map(([file, hash]) => `${file} ${hash}`)) ||
      !same(lists.get('patch_sha256')!, backport.patches.map((patch) => `${patch.file} ${patch.sha256} ${patch.cves.join(',')}`)) ||
      !same(lists.get('fixed')!, backport.fixed) ||
      !same(lists.get('assertion')!, backport.assertions.map(([cve, file]) => `${cve} ${file}`)) ||
      !same(lists.get('control')!, backport.controls)) {
    fail(`${backport.source} receipt differs from the reviewed source, patch and control pins`);
  }
  const debVersion = backport.rebuiltVersion.replace(/^\d+:/, '');
  const packages = new Map<string, string>();
  for (const value of lists.get('package_sha256')!) {
    const match = /^(\S+\.deb) ([a-f0-9]{64})$/.exec(value);
    if (!match || packages.has(match[1])) fail(`invalid or duplicate ${backport.source} package hash`);
    packages.set(match[1], match[2]);
  }
  const expected = backport.binaries.map((name) =>
    [`${name}_${debVersion}_${architecture}.deb`, `${name}_${debVersion}_all.deb`]);
  if (packages.size !== backport.binaries.length ||
      !expected.every((names) => names.filter((name) => packages.has(name)).length === 1)) {
    fail(`${backport.source} receipt must list exactly the rebuilt packages`);
  }
  const libraries = lists.get('library')!.map((value) => {
    const match = /^(\S+) (\S+) ([a-f0-9]{64})$/.exec(value);
    if (!match) fail(`invalid ${backport.source} library in receipt`);
    return { pkg: match[1], path: match[2], sha256: match[3] };
  });
  if (!same(libraries.map((item) => `${item.pkg} ${item.path}`),
    backport.libraries.map((item) => `${item.pkg} /usr/lib/${multiarch[architecture]}/${item.file}`))) {
    fail(`${backport.source} receipt libraries differ from the recipe`);
  }
  return { architecture, packages, libraries };
}

export function verified(backport: Backport): VerifiedBackport {
  return {
    source: backport.source,
    version: backport.rebuiltVersion,
    binaries: new Set(backport.binaries),
    fixed: new Set(backport.fixed),
    disclosure: backport.disclosure,
  };
}

export function checkLayerHashes(hashes: string[], expected: string, file = 'library'): void {
  if (hashes.length !== 1 || hashes[0] !== expected) {
    throw new Error(`expected one patched ${file} layer payload, found ${hashes.length} with matching hash ${hashes.filter((hash) => hash === expected).length}`);
  }
}

function run(command: string, args: string[], outputFd?: number): string {
  const result = spawnSync(command, args, {
    encoding: outputFd === undefined ? 'utf8' : undefined,
    maxBuffer: 128 * 1024 * 1024,
    stdio: outputFd === undefined ? ['ignore', 'pipe', 'pipe'] : ['ignore', outputFd, 'pipe'],
  });
  if (result.error || result.status !== 0) {
    const detail = result.error?.message ??
      (typeof result.stderr === 'string' && result.stderr.trim()
        ? result.stderr.slice(-8192).trimEnd()
        : `exit ${result.status ?? 'unknown'}`);
    throw new Error(`${command} ${args[0] ?? ''} failed: ${detail}`);
  }
  return typeof result.stdout === 'string' ? result.stdout : '';
}

const base = ['run', '--rm', '--network', 'none', '--read-only'];
const sh = (image: string, script: string, extra: string[] = []) =>
  run('docker', [...base, ...extra, '--entrypoint', '/bin/sh', image, '-ec', script]);

/** Require installed packages, receipts and loaded-library files to match the recipes. */
export function checkInstalled(image: string, backports: Backport[], mode: 'runtime' | 'dev'): Map<string, Receipt> {
  const rows = new Map<string, string>();
  for (const line of run('docker', [...base, '--entrypoint', 'dpkg-query', image, '-W',
    '-f=${Package}\t${Version}\t${db:Status-Abbrev}\n']).trim().split('\n')) {
    const [name, version, status] = line.split('\t');
    if (status?.startsWith('ii')) rows.set(name, version);
  }
  const receipts = new Map<string, Receipt>();
  for (const backport of backports) {
    for (const name of backport[mode]) {
      if (rows.get(name) !== backport.rebuiltVersion) throw new Error(`${name} package version mismatch`);
    }
    for (const name of backport.binaries) {
      if (rows.has(name) && rows.get(name) !== backport.rebuiltVersion) {
        throw new Error(`${name} is installed at ${rows.get(name)}, not the rebuilt ${backport.rebuiltVersion}`);
      }
    }
    const receipt = parseReceipt(run('docker', [...base, '--entrypoint', 'cat', image,
      `${receiptDirectory}/${backport.source}/receipt.txt`]), backport);
    receipts.set(backport.source, receipt);
    const bindings = receipt.libraries.filter((library) => rows.has(library.pkg));
    if (!bindings.length) throw new Error(`no ${backport.source} library is installed`);
    const script = bindings.map(({ pkg, path, sha256: hash }) => {
      const soname = /^(.*\.so\.\d+)/.exec(path)![1];
      return `test "$(readlink -f ${soname})" = ${path}
        printf '%s  %s\\n' ${hash} ${path} | sha256sum --check --strict --quiet
        test "$(dpkg-query -S ${path} | cut -d: -f1)" = ${pkg}`;
    }).join('\n');
    sh(image, script);
  }
  const installedArch = new Set([...receipts.values()].map((receipt) => receipt.architecture));
  if (installedArch.size !== 1) throw new Error('backport receipts disagree on architecture');
  return receipts;
}

function libraryPath(receipts: Map<string, Receipt>, source: string, file: string): string {
  const library = receipts.get(source)?.libraries.find((item) => item.path.endsWith(`/${file}`));
  if (!library) throw new Error(`no ${file} in the ${source} receipt`);
  return library.path;
}

/** Perl XML::LibXML/XSLT probe and the CVE-2026-6653 entity amplification limit. */
export function checkLibxml2Native(image: string, receipts: Map<string, Receipt>): void {
  const expected = libraryPath(receipts, 'libxml2', 'libxml2.so.2.9.14');
  const script = String.raw`
    LIBXML_EXPECTED_PATH="$1" perl -MXML::LibXML -MXML::LibXSLT -MCwd=realpath -e '
      use strict; use warnings;
      my $expected = $ENV{LIBXML_EXPECTED_PATH};
      open my $maps, "<", "/proc/self/maps" or die $!;
      my %loaded;
      while (<$maps>) {
        if (m{(/usr/lib/[^ ]+/libxml2[.]so[.]2[.]9[.]14)}) {
          $loaded{realpath($1)} = 1;
        }
      }
      die "loaded libxml2 differs from receipt\n" unless keys(%loaded) == 1 && $loaded{$expected};
      my $xml = XML::LibXML->new();
      my $doc = $xml->load_xml(string => q{<!DOCTYPE r [<!ENTITY e "safe">]><r>&e;</r>}, expand_entities => 1, no_network => 1);
      die "entity substitution failed\n" unless $doc->documentElement->textContent eq "safe";
      die "malformed XML accepted\n" if eval { $xml->load_xml(string => q{<r><broken></r>}); 1 };
      my $sheet = $xml->load_xml(string => q{<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform"><xsl:template match="/"><result><xsl:value-of select="r"/></result></xsl:template></xsl:stylesheet>});
      my $result = XML::LibXSLT->new()->parse_stylesheet($sheet)->transform($doc);
      die "XSLT transform failed\n" unless $result->documentElement->textContent eq "safe";
      my $plain = "<r>" . ("a" x (11 * 1024 * 1024)) . "</r>";
      my $large = $xml->load_xml(string => $plain, huge => 1, no_network => 1);
      die "large plain XML rejected\n" unless length($large->documentElement->textContent) == 11 * 1024 * 1024;
      my $dtd = q{<!DOCTYPE r [<!ENTITY e0 "} . ("x" x 1024) . q{">};
      for my $n (1..5) { my $prior = $n - 1; $dtd .= qq{<!ENTITY e$n "} . ("&e$prior;" x 10) . qq{">}; }
      my $bomb = $dtd . q{]><r>&e5;</r>};
      die "high amplification accepted with HUGE\n" if eval {
        $xml->load_xml(string => $bomb, huge => 1, expand_entities => 1, no_network => 1); 1
      };
      print "loaded-library-and-consumers-ok\n";
    '
  `;
  const out = run('docker', [...base, '--tmpfs', '/tmp:rw,nosuid,nodev,size=32m,mode=1777',
    '--entrypoint', '/bin/sh', image, '-ec', script, 'probe', expected]);
  if (!out.includes('loaded-library-and-consumers-ok')) {
    throw new Error('native libxml2 probe gave no success marker');
  }
}

function checkLibxml2Consumers(image: string, receipts: Map<string, Receipt>): void {
  const script = String.raw`
    export XDG_CACHE_HOME=/tmp/.cache TEXMFVAR=/tmp/texmf-var TEXMFCONFIG=/tmp/texmf-config
    cd /tmp
    run_logged() {
      label=$1 log=$2
      shift 2
      if "$@" >"$log" 2>&1; then return 0; else status=$?; fi
      printf '%s failed (exit %s); last 4096 log bytes:\n' "$label" "$status" >&2
      tail -c 4096 "$log" >&2
      exit "$status"
    }
    cat > /tmp/consumer.tex <<'TEX'
\documentclass{article}
\begin{document}
Accessible equation: \(x^2 + y^2 = z^2\).
\end{document}
TEX
    run_logged LaTeXML /tmp/latexml.log latexml --destination=/tmp/consumer.xml /tmp/consumer.tex
    run_logged LaTeXMLPost /tmp/latexmlpost.log latexmlpost --format=html5 --destination=/tmp/consumer.html /tmp/consumer.xml
    if ! grep -q '<math' /tmp/consumer.html; then
      echo 'LaTeXMLPost output lacks MathML; last 4096 log bytes:' >&2
      tail -c 4096 /tmp/latexmlpost.log >&2
      exit 1
    fi
    # The image's ImageMagick policy refuses MSL, ImageMagick's only libxml2
    # reader that submitted content could select (the SVG coder is not installed,
    # and XMP profiles are parsed with libxml2 only when the xmp:validate option
    # is set, which LaTeXML never sets). So this control proves that the policy
    # refuses MSL and XC, decodes an allowed PNG from memory, and that the
    # libMagickCore loaded by Perl maps exactly the rebuilt system libxml2.
    cat > /tmp/control.msl <<'MSL'
<?xml version="1.0" encoding="UTF-8"?>
<image><read filename="/tmp/input.png"/><write filename="/tmp/output.png"/></image>
MSL
    perl -MImage::Magick -MMIME::Base64 -MCwd=realpath -e '
      use strict; use warnings;
      for my $refused (["XC", "xc:red"], ["MSL", "MSL:/tmp/control.msl"]) {
        my ($coder, $spec) = @$refused;
        my $probe = Image::Magick->new(size => "2x2");
        my $error = $probe->Read($spec);
        die "ImageMagick policy did not refuse $coder\n"
          unless $error && $error =~ /not allowed by the security policy .$coder./;
      }
      die "MSL control wrote an output file\n" if -e "/tmp/output.png";
      my $png = decode_base64("iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEElEQVR42mP4z8AARAwQCgAf7gP9Y167WwAAAABJRU5ErkJggg==");
      my $image = Image::Magick->new(magick => "PNG");
      my $error = $image->BlobToImage($png);
      die "ImageMagick PNG blob decode failed: $error\n" if $error;
      die "ImageMagick PNG blob decode gave no single image\n" unless @$image == 1;
      die "ImageMagick PNG control dimensions changed\n" unless $image->Get("width") == 2 && $image->Get("height") == 2;
      for my $x (0..1) {
        for my $y (0..1) {
          my @pixel = $image->GetPixel(x => $x, y => $y, normalize => 1);
          die "ImageMagick PNG control pixel changed at $x,$y\n"
            unless @pixel >= 3 && $pixel[0] > 0.99 && $pixel[1] < 0.01 && $pixel[2] < 0.01;
        }
      }
      open my $maps, "<", "/proc/self/maps" or die $!;
      my (%loaded, $core);
      while (<$maps>) {
        $core = 1 if m{/libMagickCore-7[.]Q16[.]so};
        $loaded{realpath($1)} = 1 if m{(/usr/lib/[^ ]+/libxml2[.]so[.]2[.]9[.]14)};
      }
      die "libMagickCore is not loaded in the Perl process\n" unless $core;
      die "ImageMagick loaded unexpected libxml2\n" unless keys(%loaded) == 1 && $loaded{$ENV{LIBXML_EXPECTED_PATH}};
    '
    echo full-native-xml-consumers-ok
  `;
  const out = run('docker', [...base, '--tmpfs', '/tmp:rw,nosuid,nodev,size=128m,mode=1777',
    '--env', `LIBXML_EXPECTED_PATH=${libraryPath(receipts, 'libxml2', 'libxml2.so.2.9.14')}`,
    '--entrypoint', '/bin/sh', image, '-ec', script]);
  if (!out.includes('full-native-xml-consumers-ok')) throw new Error('native LaTeXML/ImageMagick probe gave no success marker');
}

/**
 * The X libraries are loaded, never driven: the image has no X server.
 * Ghostscript, Chromium, cairo and ImageMagick must resolve libX11 and
 * libXrender to the receipt paths and still start.
 */
function checkXConsumers(image: string, receipts: Map<string, Receipt>): void {
  const x11 = libraryPath(receipts, 'libx11', 'libX11.so.6.4.0');
  const xrender = libraryPath(receipts, 'libxrender', 'libXrender.so.1.3.0');
  const script = String.raw`
    x11=$1 xrender=$2
    # The scanner's pinned Chrome for Testing build, named by the image.
    chrome="$AELIRA_CHROMIUM_EXECUTABLE"
    test -n "$chrome" || chrome=/opt/chrome-for-testing/chrome
    test -x "$chrome"
    seen_x11=0 seen_xrender=0
    for binary in /usr/bin/gs "$chrome" /usr/lib/*/libcairo.so.2 /usr/lib/*/libMagickCore-7.Q16.so.10; do
      ldd "$binary" > /tmp/ldd.txt
      if grep -q 'not found' /tmp/ldd.txt; then cat /tmp/ldd.txt >&2; exit 1; fi
      resolved=$(awk '$1 == "libX11.so.6" { print $3 }' /tmp/ldd.txt)
      if test -n "$resolved"; then test "$(readlink -f "$resolved")" = "$x11"; seen_x11=1; fi
      resolved=$(awk '$1 == "libXrender.so.1" { print $3 }' /tmp/ldd.txt)
      if test -n "$resolved"; then test "$(readlink -f "$resolved")" = "$xrender"; seen_xrender=1; fi
    done
    test "$seen_x11" = 1 && test "$seen_xrender" = 1
    convert -version > /dev/null
    gs --version > /dev/null
    "$chrome" --version > /dev/null
    echo x-consumers-ok
  `;
  const out = run('docker', [...base, '--tmpfs', '/tmp:rw,nosuid,nodev,size=16m,mode=1777',
    '--entrypoint', '/bin/sh', image, '-ec', script, 'probe', x11, xrender]);
  if (!out.includes('x-consumers-ok')) throw new Error('X consumer probe gave no success marker');
}

function checkLayers(image: string, receipts: Map<string, Receipt>): void {
  const expected = new Map<string, string>();
  for (const receipt of receipts.values()) {
    for (const library of receipt.libraries) expected.set(library.path.split('/').pop()!, library.sha256);
  }
  const member = new RegExp(`^usr/lib/(?:aarch64|x86_64)-linux-gnu/(${[...expected.keys()]
    .map((file) => file.replace(/[\\^$.*+?()[\]{}|]/g, '\\$&')).join('|')})$`);
  const work = mkdtempSync(join(tmpdir(), 'aelira-backport-layers-'));
  try {
    const archive = join(work, 'image.tar');
    const fd = openSync(archive, 'w');
    try { run('docker', ['save', image], fd); } finally { closeSync(fd); }
    const manifests = JSON.parse(run('tar', ['-xOf', archive, 'manifest.json']));
    if (!Array.isArray(manifests) || manifests.length !== 1 || !Array.isArray(manifests[0].Layers)) {
      throw new Error('invalid Docker image manifest');
    }
    const found = new Map<string, string[]>([...expected.keys()].map((file) => [file, []]));
    for (const layer of manifests[0].Layers) {
      if (typeof layer !== 'string' || layer.startsWith('/') || layer.split('/').includes('..')) {
        throw new Error('unsafe Docker layer name');
      }
      const layerPath = join(work, 'layer.tar');
      const layerFd = openSync(layerPath, 'w');
      try { run('tar', ['-xOf', archive, layer], layerFd); } finally { closeSync(layerFd); }
      for (const raw of run('tar', ['-tf', layerPath]).split('\n')) {
        const match = member.exec(raw.replace(/^\.\//, ''));
        if (!match) continue;
        const bytes = spawnSync('tar', ['-xOf', layerPath, raw], { maxBuffer: 32 * 1024 * 1024 });
        if (bytes.error || bytes.status !== 0 || !Buffer.isBuffer(bytes.stdout)) {
          throw new Error(`cannot extract ${match[1]} from layer ${layer}`);
        }
        const hash = createHash('sha256').update(bytes.stdout).digest('hex');
        if (hash !== expected.get(match[1])) throw new Error(`unpatched ${match[1]} occurs in shipped layer ${layer}`);
        found.get(match[1])!.push(hash);
      }
    }
    for (const [file, hashes] of found) checkLayerHashes(hashes, expected.get(file)!, file);
  } finally { rmSync(work, { recursive: true, force: true }); }
}

if (import.meta.main) {
  try {
    const [image, ...flags] = process.argv.slice(2);
    if (!image || flags.some((flag) => !['--dev', '--package-smoke'].includes(flag)) ||
        new Set(flags).size !== flags.length) {
      throw new Error('usage: bun scripts/verify_debian_backports.ts IMAGE [--dev] [--package-smoke]');
    }
    const backports = loadBackports();
    const receipts = checkInstalled(image, backports, flags.includes('--dev') ? 'dev' : 'runtime');
    checkLibxml2Native(image, receipts);
    if (!flags.includes('--package-smoke')) {
      checkLibxml2Consumers(image, receipts);
      if (!flags.includes('--dev')) checkXConsumers(image, receipts);
    }
    checkLayers(image, receipts);
    console.log(`Rebuilt Debian packages verified: ${backports.map((backport) =>
      `${backport.source} ${backport.rebuiltVersion} (${backport.fixed.join(', ')})`).join('; ')}`);
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}
