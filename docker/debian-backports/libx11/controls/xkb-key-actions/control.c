/* Exact-source control for CVE-2026-88806: no X server or network. */
#include <X11/Xlibint.h>
#include <X11/extensions/XKBproto.h>
#include "XKBlibint.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static XkbAction actions[256][4];

/* libX11's internal reallocarray wrapper, used by Xmallocarray. */
void *xreallocarray(void *optr, size_t nmemb, size_t size) { return reallocarray(optr, nmemb, size); }

int _XkbCopyFromReadBuffer(XkbReadBufferPtr from, char *to, int size) {
    if (size < 0 || from->size - (from->data - from->start) < size) return 0;
    memcpy(to, from->data, (size_t)size); from->data += size; return 1;
}
int _XkbSkipReadBufferData(XkbReadBufferPtr from, int size) {
    if (size < 0 || from->size - (from->data - from->start) < size) return 0;
    from->data += size; return 1;
}
XkbAction *XkbResizeKeyActions(XkbDescPtr xkb, int key, int needed) {
    assert(key >= xkb->min_key_code && key <= xkb->max_key_code && needed <= 4);
    return actions[key];
}

#include FUNCTION_FILE

/* Keys 8..15 exist, so key_acts has 16 entries. */
static Status read_actions(int first, int count, const unsigned char *desc, int extra) {
    XkbDescRec info = {0};
    XkbClientMapRec map = {0};
    XkbServerMapRec server = {0};
    xkbGetMapReply rep = {0};
    char payload[1024] = {0};
    XkbReadBufferRec buf = {0};
    Status status;
    info.min_key_code = 8; info.max_key_code = 15;
    info.map = &map; info.server = &server;
    map.key_sym_map = calloc(info.max_key_code + 1, sizeof(XkbSymMapRec));
    server.key_acts = calloc(info.max_key_code + 1, sizeof(unsigned short));
    assert(map.key_sym_map && server.key_acts);
    memcpy(payload, desc, (size_t)count);
    rep.firstKeyAct = (CARD8)first; rep.nKeyActs = (CARD8)count;
    buf.start = buf.data = payload; buf.size = XkbPaddedSize(count) + extra;
    status = _XkbReadKeyActions(&buf, &info, &rep);
    free(map.key_sym_map); free(server.key_acts);
    return status;
}

int main(int argc, char **argv) {
    static const unsigned char none[16] = {0};
    static const unsigned char one[16] = {1, 0, 1};
    (void)argv;
    if (argc > 1) return read_actions(12, 5, none, 0) == Success ? 0 : 2;
    assert(read_actions(8, 8, none, 0) == Success);
    assert(read_actions(8, 3, one, 2 * (int)sizeof(XkbAction)) == Success);
    assert(read_actions(8, 3, one, 0) == BadLength);
    assert(read_actions(12, 5, none, 0) == BadLength);
    assert(read_actions(15, 2, none, 0) == BadLength);
    assert(read_actions(255, 1, none, 0) == BadLength);
    puts("XKB key action range control passed");
    return 0;
}
