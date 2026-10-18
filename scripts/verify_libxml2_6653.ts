#!/usr/bin/env bun
/** Bind the installed Debian libxml2 package, loaded Perl consumer and image layer bytes. */
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { closeSync, mkdtempSync, openSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const version = '2.12.7+dfsg+really2.9.14-2.1+deb13u3+aelira1';
const sourceHash = '4fe913dec8b1ab89d13b489b419a8203176ea39e931eaa0d25b17eafb9c279e9';
const debianHash = '3b6d265f482d6a8fbe3c056d2006fb3b563b4a838f7258b388ac5f0b29206921';
const patchHash = '6c2b8a7b6fa470efcb1854e5c73bd7e1ae1663d8b111e27ce9232bbc1fe44f54';
const libraryMember = /^usr\/lib\/(?:aarch64|x86_64)-linux-gnu\/libxml2\.so\.2\.9\.14$/;

function run(command: string, args: string[], outputFd?: number): string {
  const result = spawnSync(command, args, {
    encoding: outputFd === undefined ? 'utf8' : undefined,
    maxBuffer: 128 * 1024 * 1024,
    stdio: outputFd === undefined ? ['ignore', 'pipe', 'pipe'] : ['ignore', outputFd, 'pipe'],
  });
  if (result.error || result.status !== 0) {
    throw new Error(`${command} ${args[0] ?? ''} failed: ${result.error ?? result.stderr ?? result.status}`);
  }
  return typeof result.stdout === 'string' ? result.stdout : '';
}

export function parseReceipt(raw: string): Map<string, string> {
  const values = new Map<string, string>();
  const packages = new Map<string, string>();
  for (const line of raw.trim().split('\n')) {
    const match = /^([a-z0-9_]+)=(.+)$/.exec(line);
    if (!match) throw new Error(`invalid libxml2 receipt line: ${line}`);
    if (match[1] !== 'package_sha256' && values.has(match[1])) {
      throw new Error(`duplicate libxml2 receipt field: ${match[1]}`);
    }
    if (match[1] === 'package_sha256') {
      const packageMatch = /^(libxml2(?:-dev|-utils)?_[^ ]+_(?:arm64|amd64)\.deb) ([a-f0-9]{64})$/.exec(match[2]);
      if (!packageMatch || packages.has(packageMatch[1])) throw new Error('invalid or duplicate libxml2 package hash');
      packages.set(packageMatch[1], packageMatch[2]);
    } else values.set(match[1], match[2]);
  }
  if (values.get('source_version') !== '2.12.7+dfsg+really2.9.14-2.1+deb13u3' ||
      values.get('rebuilt_version') !== version ||
      values.get('source_sha256') !== sourceHash ||
      values.get('debian_sha256') !== debianHash ||
      values.get('patch_sha256') !== patchHash) {
    throw new Error('libxml2 receipt differs from reviewed source and package pins');
  }
  if (!/^[a-f0-9]{64}$/.test(values.get('library_sha256') ?? '') ||
      !/^\/usr\/lib\/(?:aarch64|x86_64)-linux-gnu\/libxml2\.so\.2\.9\.14$/.test(values.get('library_path') ?? '')) {
    throw new Error('invalid libxml2 library hash or path in receipt');
  }
  if (packages.size !== 3) throw new Error('libxml2 receipt must list all three rebuilt packages');
  const arch = values.get('architecture');
  for (const name of ['libxml2', 'libxml2-dev', 'libxml2-utils']) {
    if (!packages.has(`${name}_${version}_${arch}.deb`)) throw new Error(`missing ${name} build hash`);
  }
  return values;
}

export function checkLayerHashes(hashes: string[], expected: string): void {
  if (hashes.length !== 1 || hashes[0] !== expected) {
    throw new Error(`expected one patched libxml2 layer payload, found ${hashes.length} with matching hash ${hashes.filter((hash) => hash === expected).length}`);
  }
}

export function checkInstalled(image: string, dev: boolean): Map<string, string> {
  const base = ['run', '--rm', '--network', 'none', '--read-only', '--entrypoint'];
  const packages = ['libxml2', ...(dev ? ['libxml2-dev', 'libxml2-utils'] : [])];
  const installed = run('docker', [...base, 'dpkg-query', image, '-W', '-f=${Package}\t${Version}\n', ...packages]);
  const rows = new Map(installed.trim().split('\n').map((line) => line.split('\t') as [string, string]));
  for (const name of packages) if (rows.get(name) !== version) throw new Error(`${name} package version mismatch`);
  const receiptPath = '/usr/share/doc/aelira-libxml2-6653/receipt.txt';
  const receipt = parseReceipt(run('docker', [...base, 'cat', image, receiptPath]));
  const expectedArch = receipt.get('library_path')!.includes('aarch64') ? 'arm64' : 'amd64';
  if (receipt.get('architecture') !== expectedArch) throw new Error('libxml2 receipt architecture mismatch');
  const script = String.raw`
    set -eu
    receipt=/usr/share/doc/aelira-libxml2-6653/receipt.txt
    path=$(sed -n 's/^library_path=//p' "$receipt")
    hash=$(sed -n 's/^library_sha256=//p' "$receipt")
    test "$(readlink -f /usr/lib/*/libxml2.so.2)" = "$path"
    printf '%s  %s\n' "$hash" "$path" | sha256sum --check --strict --quiet
    test "$(dpkg-query -S "$path" | cut -d: -f1)" = libxml2
    LIBXML_EXPECTED_PATH="$path" perl -MXML::LibXML -MXML::LibXSLT -MCwd=realpath -e '
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
  const out = run('docker', ['run', '--rm', '--network', 'none', '--read-only',
    '--tmpfs', '/tmp:rw,nosuid,nodev,size=32m,mode=1777', '--entrypoint', '/bin/sh',
    image, '-ec', script]);
  if (!out.includes('loaded-library-and-consumers-ok')) throw new Error('native libxml2 probe gave no success marker');
  return receipt;
}

function checkFullConsumers(image: string): void {
  const script = String.raw`
    set -eu
    export XDG_CACHE_HOME=/tmp/.cache TEXMFVAR=/tmp/texmf-var TEXMFCONFIG=/tmp/texmf-config
    cat > /tmp/consumer.tex <<'TEX'
\documentclass{article}
\begin{document}
Accessible equation: \(x^2 + y^2 = z^2\).
\end{document}
TEX
    latexml --destination=/tmp/consumer.xml /tmp/consumer.tex >/tmp/latexml.log 2>&1
    latexmlpost --format=html5 --destination=/tmp/consumer.html /tmp/consumer.xml >/tmp/latexmlpost.log 2>&1
    grep -q '<math' /tmp/consumer.html
    ffmpeg -v error -f lavfi -i sine=frequency=440:sample_rate=44100 -t 0.2 -c:a aac -f dash /tmp/consumer.mpd
    ffprobe -v error -show_entries stream=codec_name -of default=noprint_wrappers=1 /tmp/consumer.mpd | grep -q codec_name=aac
    cat > /tmp/consumer.svg <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"><rect width="2" height="2" fill="red"/></svg>
SVG
    perl -MImage::Magick -MCwd=realpath -e '
      use strict; use warnings;
      my $image = Image::Magick->new;
      my $error = $image->Read("MSVG:/tmp/consumer.svg");
      die "ImageMagick MSVG decode failed: $error\n" if $error;
      die "ImageMagick SVG dimensions changed\n" unless $image->Get("width") == 2 && $image->Get("height") == 2;
      open my $maps, "<", "/proc/self/maps" or die $!;
      my %loaded;
      while (<$maps>) {
        $loaded{realpath($1)} = 1 if m{(/usr/lib/[^ ]+/libxml2[.]so[.]2[.]9[.]14)};
      }
      die "ImageMagick loaded unexpected libxml2\n" unless keys(%loaded) == 1 && $loaded{$ENV{LIBXML_EXPECTED_PATH}};
    '
    echo full-native-xml-consumers-ok
  `;
  const receipt = '/usr/share/doc/aelira-libxml2-6653/receipt.txt';
  const expected = run('docker', ['run', '--rm', '--network', 'none', '--read-only',
    '--entrypoint', '/bin/sh', image, '-ec', `sed -n 's/^library_path=//p' ${receipt}`]).trim();
  const out = run('docker', ['run', '--rm', '--network', 'none', '--read-only',
    '--tmpfs', '/tmp:rw,nosuid,nodev,size=128m,mode=1777',
    '--env', `LIBXML_EXPECTED_PATH=${expected}`, '--entrypoint', '/bin/sh', image, '-ec', script]);
  if (!out.includes('full-native-xml-consumers-ok')) throw new Error('native LaTeXML/FFmpeg/ImageMagick probe gave no success marker');
}

function checkLayers(image: string, receipt: Map<string, string>): void {
  const work = mkdtempSync(join(tmpdir(), 'aelira-libxml2-layers-'));
  try {
    const archive = join(work, 'image.tar');
    const fd = openSync(archive, 'w');
    try { run('docker', ['save', image], fd); } finally { closeSync(fd); }
    const manifests = JSON.parse(run('tar', ['-xOf', archive, 'manifest.json']));
    if (!Array.isArray(manifests) || manifests.length !== 1 || !Array.isArray(manifests[0].Layers)) {
      throw new Error('invalid Docker image manifest');
    }
    const payloadHashes: string[] = [];
    for (const layer of manifests[0].Layers) {
      if (typeof layer !== 'string' || layer.startsWith('/') || layer.split('/').includes('..')) {
        throw new Error('unsafe Docker layer name');
      }
      const layerPath = join(work, 'layer.tar');
      const layerFd = openSync(layerPath, 'w');
      try { run('tar', ['-xOf', archive, layer], layerFd); } finally { closeSync(layerFd); }
      for (const raw of run('tar', ['-tf', layerPath]).split('\n')) {
        const path = raw.replace(/^\.\//, '');
        if (!libraryMember.test(path)) continue;
        const bytes = spawnSync('tar', ['-xOf', layerPath, raw], { maxBuffer: 16 * 1024 * 1024 });
        if (bytes.error || bytes.status !== 0 || !Buffer.isBuffer(bytes.stdout)) {
          throw new Error(`cannot extract libxml2 from layer ${layer}`);
        }
        const hash = createHash('sha256').update(bytes.stdout).digest('hex');
        if (hash !== receipt.get('library_sha256')) throw new Error(`unpatched libxml2 occurs in shipped layer ${layer}`);
        payloadHashes.push(hash);
      }
    }
    checkLayerHashes(payloadHashes, receipt.get('library_sha256')!);
  } finally { rmSync(work, { recursive: true, force: true }); }
}

if (import.meta.main) {
  try {
    const [image, ...flags] = process.argv.slice(2);
    if (!image || flags.some((flag) => !['--dev', '--package-smoke'].includes(flag)) ||
        new Set(flags).size !== flags.length) {
      throw new Error('usage: bun scripts/verify_libxml2_6653.ts IMAGE [--dev] [--package-smoke]');
    }
    const receipt = checkInstalled(image, flags.includes('--dev'));
    if (!flags.includes('--package-smoke')) checkFullConsumers(image);
    checkLayers(image, receipt);
    console.log('Patched system libxml2 package, native consumers and image layers verified');
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}
