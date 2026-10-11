/* Installed public C API: two pages and decoded bytes for every retained codec. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <tiffio.h>

struct codec {
    const char *name;
    uint16_t compression;
    uint16_t channels;
    uint16_t bits;
};

static int check(const char *directory, const struct codec *codec) {
    char path[512];
    if (snprintf(path, sizeof path, "%s/codec-%s.tif", directory, codec->name) >=
        (int)sizeof path) return 1;
    if (!TIFFIsCODECConfigured(codec->compression)) return 2;
    TIFF *out = TIFFOpen(path, "w");
    if (!out) return 3;
    const size_t row_size = 32U * codec->channels * codec->bits / 8U;
    for (uint16_t page = 0; page < 2; ++page) {
        if (!TIFFSetField(out, TIFFTAG_IMAGEWIDTH, 32U) ||
            !TIFFSetField(out, TIFFTAG_IMAGELENGTH, 16U) ||
            !TIFFSetField(out, TIFFTAG_BITSPERSAMPLE, codec->bits) ||
            !TIFFSetField(out, TIFFTAG_SAMPLESPERPIXEL, codec->channels) ||
            !TIFFSetField(out, TIFFTAG_PHOTOMETRIC,
                          codec->channels == 3 ? PHOTOMETRIC_RGB : PHOTOMETRIC_MINISBLACK) ||
            !TIFFSetField(out, TIFFTAG_COMPRESSION, codec->compression) ||
            !TIFFSetField(out, TIFFTAG_ROWSPERSTRIP, 16U) ||
            !TIFFSetField(out, TIFFTAG_PLANARCONFIG, PLANARCONFIG_CONTIG)) {
            TIFFClose(out);
            return 4;
        }
        if (codec->compression == COMPRESSION_WEBP &&
            !TIFFSetField(out, TIFFTAG_WEBP_LOSSLESS, 1)) {
            TIFFClose(out);
            return 5;
        }
        uint8_t strip[32U * 3U * 16U];
        memset(strip, page ? 0xaa : 0x55, row_size * 16U);
        if (codec->compression == COMPRESSION_JBIG) {
            if (TIFFWriteEncodedStrip(out, 0, strip, (tmsize_t)(row_size * 16U)) !=
                (tmsize_t)(row_size * 16U)) {
                TIFFClose(out);
                return 6;
            }
        } else {
            for (uint32_t row = 0; row < 16; ++row) {
                if (TIFFWriteScanline(out, strip + row * row_size, row, 0) < 0) {
                    TIFFClose(out);
                    return 7;
                }
            }
        }
        if (!TIFFWriteDirectory(out)) { TIFFClose(out); return 8; }
    }
    TIFFClose(out);
    TIFF *in = TIFFOpen(path, "r");
    if (!in) return 9;
    if (TIFFNumberOfDirectories(in) != 2) { TIFFClose(in); return 10; }
    for (uint16_t page = 0; page < 2; ++page) {
        uint16_t compression = 0;
        uint32_t width = 0, height = 0;
        if (!TIFFSetDirectory(in, page) ||
            !TIFFGetField(in, TIFFTAG_IMAGEWIDTH, &width) ||
            !TIFFGetField(in, TIFFTAG_IMAGELENGTH, &height) ||
            !TIFFGetField(in, TIFFTAG_COMPRESSION, &compression) ||
            width != 32 || height != 16 || compression != codec->compression) {
            TIFFClose(in);
            return 11;
        }
        uint8_t row[32U * 3U];
        if (codec->compression == COMPRESSION_JBIG) {
            uint8_t strip[32U * 3U * 16U] = {0};
            if (TIFFReadEncodedStrip(in, 0, strip, (tmsize_t)(row_size * 16U)) !=
                (tmsize_t)(row_size * 16U)) { TIFFClose(in); return 12; }
            for (size_t i = 0; i < row_size * 16U; ++i)
                if (strip[i] != (page ? 0xaa : 0x55)) { TIFFClose(in); return 13; }
        } else {
            for (uint32_t y = 0; y < 16; ++y) {
                if (TIFFReadScanline(in, row, y, 0) < 0) { TIFFClose(in); return 14; }
                for (size_t i = 0; i < row_size; ++i) {
                    int difference = (int)row[i] - (page ? 0xaa : 0x55);
                    if (difference < 0) difference = -difference;
                    if (difference > (codec->compression == COMPRESSION_JPEG ? 2 : 0)) {
                        TIFFClose(in);
                        return 15;
                    }
                }
            }
        }
    }
    TIFFClose(in);
    printf("%s two-page encode/decode pixels passed\n", codec->name);
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    const struct codec codecs[] = {
        {"lzw", COMPRESSION_LZW, 1, 8},
        {"jpeg", COMPRESSION_JPEG, 3, 8},
        {"deflate", COMPRESSION_ADOBE_DEFLATE, 1, 8},
        {"jbig", COMPRESSION_JBIG, 1, 1},
        {"lerc", COMPRESSION_LERC, 1, 8},
        {"lzma", COMPRESSION_LZMA, 1, 8},
        {"zstd", COMPRESSION_ZSTD, 1, 8},
        {"webp", COMPRESSION_WEBP, 3, 8},
    };
    for (size_t i = 0; i < sizeof codecs / sizeof codecs[0]; ++i) {
        int result = check(argv[1], &codecs[i]);
        if (result) {
            fprintf(stderr, "%s codec failed at check %d\n", codecs[i].name, result);
            return result;
        }
    }
    return 0;
}
