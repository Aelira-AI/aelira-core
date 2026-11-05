import { describe, expect, test } from 'bun:test';
import { type Backport, checkLayerHashes, loadBackports, parseBackportConf, parseReceipt } from './verify_debian_backports';

const hash = 'a'.repeat(64);
const backports = loadBackports();
const libxml2 = backports.find((item) => item.source === 'libxml2')!;
const libx11 = backports.find((item) => item.source === 'libx11')!;
const zlib = backports.find((item) => item.source === 'zlib')!;

/** The receipt build-backport.sh writes for a recipe, with placeholder build hashes. */
function receiptFor(backport: Backport, arch = 'arm64'): string {
  const debVersion = backport.rebuiltVersion.replace(/^\d+:/, '');
  const multiarch = arch === 'arm64' ? 'aarch64-linux-gnu' : 'x86_64-linux-gnu';
  return [
    'format=aelira-debian-backport-v1',
    `source=${backport.source}`,
    `source_version=${backport.sourceVersion}`,
    `rebuilt_version=${backport.rebuiltVersion}`,
    `architecture=${arch}`,
    ...backport.inputs.map(([file, sha]) => `input_sha256=${file} ${sha}`),
    ...backport.patches.map((patch) => `patch_sha256=${patch.file} ${patch.sha256} ${patch.cves.join(',')}`),
    ...backport.fixed.map((cve) => `fixed=${cve}`),
    ...backport.assertions.map(([cve, file]) => `assertion=${cve} ${file}`),
    ...backport.controls.map((name) => `control=${name}`),
    ...backport.binaries.map((name) =>
      `package_sha256=${name}_${debVersion}_${name === 'libx11-data' || name === 'libx11-doc' ? 'all' : arch}.deb ${hash}`),
    ...backport.libraries.map((library) => `library=${library.pkg} /usr/lib/${multiarch}/${library.file} ${hash}`),
    ...backport.preseeded.map((entry) => `preseeded=${entry.pkg} ${entry.file}`),
  ].join('\n');
}

describe('backport recipes', () => {
  test('the repository recipes cover the reviewed CVEs and nothing else', () => {
    expect(backports.map((item) => item.source)).toEqual(['libx11', 'libxml2', 'libxrender', 'zlib']);
    expect(libxml2.fixed).toEqual(['CVE-2026-6653', 'CVE-2026-74860', 'CVE-2026-86138', 'CVE-2026-86140',
      'CVE-2026-86142', 'CVE-2026-86143', 'CVE-2026-86144']);
    expect(libxml2.assertions).toEqual([['CVE-2026-86139', 'uri.c']]);
    expect(libx11.fixed).toEqual(['CVE-2026-88806', 'CVE-2026-94283', 'CVE-2026-94284', 'CVE-2026-94285']);
    expect(backports.find((item) => item.source === 'libxrender')!.fixed).toEqual(['CVE-2026-88807']);
    expect(backports.find((item) => item.source === 'zlib')!.fixed).toEqual(['CVE-2026-76844']);
    expect(zlib.preseeded).toEqual([{ pkg: 'zlib1g', file: 'libz.so.1.3.1' }]);
    for (const backport of backports.filter((item) => item.source !== 'zlib')) {
      expect(backport.preseeded).toEqual([]);
    }
    for (const backport of backports) {
      expect(backport.disclosure).toBe(backport.source === 'zlib'
        ? 'docs/security/debian-backports-0.13.0.md'
        : 'docs/security/debian-backports-0.10.0.md');
    }
  });

  test('rejects malformed recipes', () => {
    const raw = 'source x\nsource_version 1-1\nrebuilt_version 1-1+aelira1\nbuild arch\ndisclosure d.md\n' +
      `changelog_date Fri, 23 Oct 2026 00:00:00 +0000\ndsc x.dsc ${hash}\npatch p.patch ${hash} 0 CVE-2026-1234\n` +
      'binary x1\nlibrary x1 libx.so.1.0.0\nruntime x1\ndev x1\n';
    expect(parseBackportConf(raw).fixed).toEqual(['CVE-2026-1234']);
    expect(parseBackportConf(raw.replace('rebuilt_version 1-1+aelira1', 'rebuilt_version 1-1+b7+aelira1')).rebuiltVersion)
      .toBe('1-1+b7+aelira1');
    expect(parseBackportConf(`${raw}tests\n`).controls).toEqual(['package-test-suite']);
    for (const bad of [
      raw.replace('rebuilt_version 1-1+aelira1', 'rebuilt_version 1-2'),
      raw.replace('rebuilt_version 1-1+aelira1', 'rebuilt_version 1-1+deb13u1+aelira1'),
      raw.replace('rebuilt_version 1-1+aelira1', 'rebuilt_version 1-1+b7'),
      raw.replace('rebuilt_version 1-1+aelira1', 'rebuilt_version 1-10+aelira1'),
      `${raw}tests\ntests\n`,
      `${raw}tests extra\n`,
      raw.replace(`dsc x.dsc ${hash}`, `dsc x.dsc ${'g'.repeat(64)}`),
      raw.replace(' 0 CVE-2026-1234', ' fuzz CVE-2026-1234'),
      raw.replace(' 0 CVE-2026-1234', ' 0 GHSA-1234'),
      raw.replace('runtime x1', 'runtime x2'),
      raw.replace('library x1', 'library x2'),
      raw.replace(/^patch .*\n/m, ''),
      `${raw}mystery directive\n`,
      `${raw}source y\n`,
      `${raw}preseeded x1 libz.so.9\n`,
      `${raw}preseeded x1 libx.so.1.0.0 libextra\n`,
      `${raw}preseeded x1 notalib\n`,
    ]) expect(() => parseBackportConf(bad)).toThrow();
  });
});

describe('backport receipts', () => {
  test('accepts the exact rebuilt source, patch and package identity on both architectures', () => {
    for (const backport of backports) {
      for (const arch of ['arm64', 'amd64']) {
        expect(parseReceipt(receiptFor(backport, arch), backport).libraries.length).toBe(backport.libraries.length);
      }
    }
  });

  test('rejects a stock package, changed pins and dropped patches', () => {
    const receipt = receiptFor(libxml2);
    expect(() => parseReceipt(receipt.replace(`rebuilt_version=${libxml2.rebuiltVersion}`,
      `rebuilt_version=${libxml2.sourceVersion}`), libxml2)).toThrow();
    expect(() => parseReceipt(receipt.replace(/^input_sha256=(\S+) 4fe913/m, 'input_sha256=$1 000000'), libxml2)).toThrow();
    expect(() => parseReceipt(receipt.replace(/^patch_sha256=CVE-2026-6653.patch 6c2b8a/m,
      'patch_sha256=CVE-2026-6653.patch 000000'), libxml2)).toThrow();
    expect(() => parseReceipt(receipt.replace(/^patch_sha256=CVE-2026-86144.*\n/m, ''), libxml2)).toThrow();
    expect(() => parseReceipt(receipt.replace(/^fixed=CVE-2026-86144\n/m, ''), libxml2)).toThrow();
    expect(() => parseReceipt(receipt.replace(/^assertion=.*\n/m, ''), libxml2)).toThrow();
    expect(() => parseReceipt(receipt.replace(/^control=.*\n/m, ''), libxml2)).toThrow();
    expect(() => parseReceipt(`${receipt}\nfixed=CVE-2026-76781`, libxml2)).toThrow();
  });

  test('rejects missing, duplicate or inconsistent build artifacts', () => {
    const receipt = receiptFor(libx11);
    const lines = receipt.split('\n');
    expect(() => parseReceipt(receipt.replace(/^package_sha256=libx11-data_.*\n/m, ''), libx11)).toThrow();
    expect(() => parseReceipt(`${receipt}\n${lines.find((line) => line.startsWith('package_sha256='))}`, libx11)).toThrow();
    expect(() => parseReceipt(receipt.replace('architecture=arm64', 'architecture=amd64'), libx11)).toThrow();
    expect(() => parseReceipt(receipt.replace(/^library=libx11-xcb1 .*\n?/m, ''), libx11)).toThrow();
    expect(() => parseReceipt(receipt.replace('source=libx11', 'source=libxrender'), libx11)).toThrow();
    expect(() => parseReceipt(`${receipt}\nsurprise=1`, libx11)).toThrow();
  });

  test('binds preseeded entries to the recipe on both sides', () => {
    const zlibReceipt = receiptFor(zlib);
    expect(parseReceipt(zlibReceipt, zlib).preseeded).toEqual([{ pkg: 'zlib1g', file: 'libz.so.1.3.1' }]);
    // A preseeded library may not be silently dropped from, added to, or
    // retargeted inside the receipt, and recipes without one may not gain
    // the base-image tolerance.
    expect(() => parseReceipt(zlibReceipt.replace(/^preseeded=.*(\n|$)/m, ''), zlib)).toThrow();
    expect(() => parseReceipt(`${zlibReceipt}\npreseeded=zlib1g libz.so.9`, zlib)).toThrow();
    expect(() => parseReceipt(zlibReceipt.replace('preseeded=zlib1g libz.so.1.3.1',
      'preseeded=zlib1g-dev libz.so.1.3.1'), zlib)).toThrow();
    expect(() => parseReceipt(`${receiptFor(libxml2)}\npreseeded=libxml2 libxml2.so.2.9.14`, libxml2)).toThrow();
  });

  test('rejects missing, duplicate and stock library layer payloads', () => {
    expect(() => checkLayerHashes([hash], hash)).not.toThrow();
    expect(() => checkLayerHashes([], hash)).toThrow();
    expect(() => checkLayerHashes([hash, hash], hash)).toThrow();
    expect(() => checkLayerHashes(['b'.repeat(64)], hash)).toThrow();
  });
});
