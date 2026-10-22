/* Exact-source control for CVE-2026-94285: no X server, locale or network. */
#include <X11/Xlibint.h>
#include "XlcGeneric.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include FUNCTION_FILE

/* One GBK-like two-byte codeset: lead 0x81-0xFE, trail 0x40-0xFE. */
static ByteInfoRec lead = {0x81, 0xfe}, trail = {0x40, 0xfe};
static ByteInfoListRec bytes[2] = {{1, 1, &lead}, {2, 1, &trail}};
static CodeSetRec codeset;
static CodeSet list[1] = {&codeset};
static XLCdGenericRec generic;
static XLCdRec lcd;

static CodeSet parse(const unsigned char *input, int length) {
    unsigned char *copy = malloc((size_t)length);
    CodeSet result;
    assert(copy);
    memcpy(copy, input, (size_t)length);
    result = byteM_parse_codeset(&lcd, (const char *)copy, length);
    free(copy);
    return result;
}

int main(int argc, char **argv) {
    static const unsigned char pair[2] = {0x81, 0x41}, last[1] = {0x81}, ascii[1] = {0x41};
    (void)argv;
    codeset.side = XlcNONE; codeset.length = 2; codeset.byteM = bytes;
    generic.gen.codeset_num = 1; generic.gen.codeset_list = list;
    lcd.core = (XLCdCore)&generic;
    if (argc > 1) { parse(last, 1); return 0; }
    assert(parse(pair, 2) == &codeset);
    assert(parse(ascii, 1) == NULL);
    assert(parse(last, 1) == NULL);
    puts("Generic converter trailing-byte bound control passed");
    return 0;
}
