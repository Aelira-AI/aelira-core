import { describe, expect, test } from 'bun:test';
import { checkLayerHashes, parseReceipt } from './verify_libxml2_6653';

const version = '2.12.7+dfsg+really2.9.14-2.1+deb13u3+aelira1';
const hash = 'a'.repeat(64);
const receipt = [
  'source_version=2.12.7+dfsg+really2.9.14-2.1+deb13u3',
  `rebuilt_version=${version}`,
  'architecture=arm64',
  'source_sha256=4fe913dec8b1ab89d13b489b419a8203176ea39e931eaa0d25b17eafb9c279e9',
  'debian_sha256=3b6d265f482d6a8fbe3c056d2006fb3b563b4a838f7258b388ac5f0b29206921',
  'patch_sha256=6c2b8a7b6fa470efcb1854e5c73bd7e1ae1663d8b111e27ce9232bbc1fe44f54',
  ...['libxml2', 'libxml2-dev', 'libxml2-utils'].map((name) =>
    `package_sha256=${name}_${version}_arm64.deb ${hash}`),
  `library_sha256=${hash}`,
  'library_path=/usr/lib/aarch64-linux-gnu/libxml2.so.2.9.14',
].join('\n');

describe('libxml2 image receipt and layer refusal', () => {
  test('accepts the exact rebuilt source/package identity', () => {
    expect(parseReceipt(receipt).get('library_sha256')).toBe(hash);
    expect(() => checkLayerHashes([hash], hash)).not.toThrow();
  });

  test('rejects stock Debian package and altered source or patch pins', () => {
    expect(() => parseReceipt(receipt.replace(`rebuilt_version=${version}`, 'rebuilt_version=2.12.7+dfsg+really2.9.14-2.1+deb13u3'))).toThrow();
    expect(() => parseReceipt(receipt.replace('source_sha256=4fe913', 'source_sha256=000000'))).toThrow();
    expect(() => parseReceipt(receipt.replace('patch_sha256=6c2b8a', 'patch_sha256=000000'))).toThrow();
  });

  test('rejects missing, duplicate or inconsistent build artifacts', () => {
    expect(() => parseReceipt(receipt.replace(/^package_sha256=libxml2-dev_.*\n/m, ''))).toThrow();
    expect(() => parseReceipt(`${receipt}\n${receipt.split('\n')[6]}`)).toThrow();
    expect(() => parseReceipt(receipt.replace('architecture=arm64', 'architecture=amd64'))).toThrow();
  });

  test('rejects missing, duplicate and stock library layer payloads', () => {
    expect(() => checkLayerHashes([], hash)).toThrow();
    expect(() => checkLayerHashes([hash, hash], hash)).toThrow();
    expect(() => checkLayerHashes(['b'.repeat(64)], hash)).toThrow();
  });
});
