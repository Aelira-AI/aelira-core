/* Small installed-package consumer: two pages through the public C ABI. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <tiffio.h>

static int verify_pages(const char *path, uint16_t codec) {
    TIFF *in = TIFFOpen(path, "r");
    if (!in) return 5;
    if (TIFFNumberOfDirectories(in) != 2) { TIFFClose(in); return 6; }
    for (uint16_t page = 0; page < 2; ++page) {
        uint8_t row[16] = {0};
        uint32_t width = 0, height = 0;
        uint16_t compression = 0, bits = 0, channels = 0;
        if (!TIFFSetDirectory(in, page) ||
            !TIFFGetField(in, TIFFTAG_IMAGEWIDTH, &width) ||
            !TIFFGetField(in, TIFFTAG_IMAGELENGTH, &height) ||
            !TIFFGetField(in, TIFFTAG_BITSPERSAMPLE, &bits) ||
            !TIFFGetField(in, TIFFTAG_SAMPLESPERPIXEL, &channels) ||
            !TIFFGetField(in, TIFFTAG_COMPRESSION, &compression) ||
            width != 16 || height != 1 || bits != 8 || channels != 1 ||
            compression != codec || TIFFReadScanline(in, row, 0, 0) < 0) {
            TIFFClose(in);
            return 7;
        }
        for (size_t i = 0; i < sizeof row; ++i) {
            if (row[i] != (page ? 0xa5 : 0x5a)) {
                TIFFClose(in);
                return 8;
            }
        }
    }
    TIFFClose(in);
    return 0;
}

int main(int argc, char **argv) {
    if (argc == 4 && strcmp(argv[1], "--verify") == 0) {
        char *end = NULL;
        unsigned long codec = strtoul(argv[3], &end, 10);
        if (!end || *end != '\0' || codec > UINT16_MAX) return 2;
        return verify_pages(argv[2], (uint16_t)codec);
    }
    if (argc != 2) return 2;
    const uint16_t codecs[] = {
        COMPRESSION_LZW, COMPRESSION_JPEG, COMPRESSION_ADOBE_DEFLATE,
        COMPRESSION_JBIG, COMPRESSION_LERC, COMPRESSION_LZMA,
        COMPRESSION_ZSTD, COMPRESSION_WEBP
    };
    for (size_t i = 0; i < sizeof codecs / sizeof codecs[0]; ++i) {
        if (!TIFFIsCODECConfigured(codecs[i])) {
            fprintf(stderr, "missing TIFF codec %u\n", codecs[i]);
            return 8;
        }
    }
    TIFF *out = TIFFOpen(argv[1], "w");
    if (!out) return 3;
    for (uint16_t page = 0; page < 2; ++page) {
        uint8_t row[16];
        memset(row, page ? 0xa5 : 0x5a, sizeof row);
        if (!TIFFSetField(out, TIFFTAG_IMAGEWIDTH, 16U) ||
            !TIFFSetField(out, TIFFTAG_IMAGELENGTH, 1U) ||
            !TIFFSetField(out, TIFFTAG_BITSPERSAMPLE, 8) ||
            !TIFFSetField(out, TIFFTAG_SAMPLESPERPIXEL, 1) ||
            !TIFFSetField(out, TIFFTAG_PHOTOMETRIC, PHOTOMETRIC_MINISBLACK) ||
            !TIFFSetField(out, TIFFTAG_COMPRESSION, COMPRESSION_ADOBE_DEFLATE) ||
            TIFFWriteScanline(out, row, 0, 0) < 0 ||
            !TIFFWriteDirectory(out)) {
            TIFFClose(out);
            return 4;
        }
    }
    TIFFClose(out);
    if (verify_pages(argv[1], COMPRESSION_ADOBE_DEFLATE)) return 9;
    puts("installed libtiff: two-page Deflate write/read passed");
    return 0;
}
