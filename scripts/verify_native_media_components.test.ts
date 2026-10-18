import { expect, test } from 'bun:test';
import { checkPackages, cupsVersion, forbiddenLayerMember, imageMagickPackages, imageMagickVersion } from './verify_native_media_components';

test('exact reviewed package versions are required', () => {
  const lines = [...imageMagickPackages.map((name) => `${name}\t${imageMagickVersion}`), `libcups2t64\t${cupsVersion}`];
  expect(() => checkPackages(lines.join('\n'))).not.toThrow();
  expect(() => checkPackages(lines.map((line) => line.replace(cupsVersion, '2.4.10-3+deb13u3')).join('\n'))).toThrow();
  expect(() => checkPackages(lines.slice(1).join('\n'))).toThrow();
});

test('layer paths expose encoder, backend, alias and whiteout introductions', () => {
  for (const path of [
    './usr/lib/x86_64-linux-gnu/ImageMagick-7.1.1/modules-Q16/coders/flif.so',
    'usr/lib/aarch64-linux-gnu/ImageMagick-7.1.1/modules-Q16/coders/uhdr.la',
    'usr/lib/cups/backend/ipp', 'usr/lib/cups/apple/ipp',
    'usr/sbin/cupsd', 'usr/lib/cups/backend/.wh.ipp',
  ]) expect(forbiddenLayerMember(path)).toBe(true);
  for (const path of ['usr/lib/aarch64-linux-gnu/libcups.so.2', 'usr/bin/magick', 'usr/share/doc/cups/README']) {
    expect(forbiddenLayerMember(path)).toBe(false);
  }
});
