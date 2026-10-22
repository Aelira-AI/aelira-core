import { expect, test } from 'bun:test';
import { checkCapabilities, checkLayerPaths, checkPackages, cupsVersion, debianFfmpegPackages, forbiddenLayerMember, imageMagickPackages, imageMagickVersion } from './verify_native_media_components';

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
