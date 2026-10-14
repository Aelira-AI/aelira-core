/* Isolated exact-source control: no X server or network. */
#include "Xrenderint.h"
#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { unsigned long version_seq; Bool error; int major_version; int minor_version; } _XrenderVersionState;
static XRenderExtDisplayInfo ext;
static XExtCodes codes;
static xRenderQueryPictFormatsReply response;
static unsigned char payload[4096];
static union { max_align_t align; unsigned char bytes[128]; } request_storage;
static size_t payload_size, reads, eaten;
static int version_minor = 11;
static long fail_allocation = -1;
static size_t allocation_count;

static void *controlled_calloc(size_t n, size_t size) {
    if (fail_allocation >= 0 && allocation_count++ == (size_t)fail_allocation) return NULL;
    return calloc(n, size);
}
static void *controlled_malloc(size_t size) {
    if (fail_allocation >= 0 && allocation_count++ == (size_t)fail_allocation) return NULL;
    return malloc(size);
}
static XRenderExtDisplayInfo *controlled_find_display(Display *dpy) { ext.display = dpy; return &ext; }
static Bool _XRenderVersionHandler(Display *dpy, xReply *rep, char *buf, int len, XPointer data) {
    (void)dpy; (void)rep; (void)buf; (void)len; (void)data; return False;
}
static Status controlled_reply(Display *dpy, xReply *rep, int extra, Bool discard) {
    (void)extra; (void)discard;
    _XrenderVersionState *state = (_XrenderVersionState *)dpy->async_handlers->data;
    state->major_version = 0; state->minor_version = version_minor;
    memcpy(rep, &response, sizeof(response)); return 1;
}
static int controlled_read(Display *dpy, char *out, long size) {
    (void)dpy; assert(size >= 0 && (size_t)size <= payload_size);
    memcpy(out, payload, (size_t)size); reads++; return 0;
}
static void controlled_eat(Display *dpy, unsigned long size) { (void)dpy; eaten += size; }
static void controlled_eat_words(Display *dpy, unsigned long size) { controlled_eat(dpy, size * 4); }
static XRenderPictFormat *_XRenderFindFormat(XRenderInfo *xri, PictFormat format) {
    for (int i=0; i<xri->nformat; i++) if (xri->format[i].id == format) return &xri->format[i];
    return NULL;
}
static Visual *_XRenderFindVisual(Display *dpy, VisualID id) { (void)dpy; (void)id; return NULL; }
static void XRenderFreeXRenderInfo(XRenderInfo *xri) {
    free(xri->format); free(xri->screen); free(xri->depth); free(xri->visual); free(xri);
}
#undef LockDisplay
#undef UnlockDisplay
#undef SyncHandle
#undef GetReq
#undef DeqAsyncHandler
#undef Xcalloc
#undef Xmalloc
#undef Xfree
#define LockDisplay(dpy) ((void)(dpy))
#define UnlockDisplay(dpy) ((void)(dpy))
#define SyncHandle() ((void)0)
#define GetReq(name, req) do { memset(&request_storage, 0, sizeof(request_storage)); (req)=(void *)request_storage.bytes; dpy->request++; } while(0)
#define DeqAsyncHandler(dpy, handler) ((dpy)->async_handlers = (handler)->next)
#define Xcalloc controlled_calloc
#define Xmalloc controlled_malloc
#define Xfree free
#define XRenderFindDisplay controlled_find_display
#define _XReply controlled_reply
#define _XRead controlled_read
#define _XEatData controlled_eat
#define _XEatDataWords controlled_eat_words
@EXACT_FUNCTION@
static void fixture(unsigned screens, unsigned subpixels) {
    memset(&response, 0, sizeof(response)); memset(payload, 0, sizeof(payload));
    response.numFormats=1; response.numScreens=screens; response.numSubpixel=subpixels;
    xPictFormInfo format = {0}; format.id=7; format.type=PictTypeDirect; format.depth=32;
    memcpy(payload, &format, sizeof(format)); size_t pos=sizeof(format);
    for (unsigned i=0;i<screens;i++) { xPictScreen screen={0}; screen.fallback=7; memcpy(payload+pos, &screen, sizeof(screen)); pos+=sizeof(screen); }
    for (unsigned i=0;i<subpixels;i++) { CARD32 value=SubPixelHorizontalRGB+i; memcpy(payload+pos, &value, sizeof(value)); pos+=sizeof(value); }
    payload_size=pos; response.length=(CARD32)(pos/4); reads=0; eaten=0;
    ext.info=NULL; ext.codes=&codes; version_minor=11; fail_allocation=-1; allocation_count=0;
}
static void run(unsigned screens, unsigned subpixels, int expected) {
    Display display={0}; fixture(screens, subpixels);
    int status=XRenderQueryFormats(&display); assert(status==expected);
    if (expected) {
        assert(ext.info != NULL && ext.info->nscreen==(int)screens && reads==1);
        for (unsigned i=0;i<screens;i++) assert(ext.info->screen[i].subpixel==(int)(i<subpixels?SubPixelHorizontalRGB+i:SubPixelUnknown));
        XRenderFreeXRenderInfo(ext.info); ext.info=NULL;
    } else assert(ext.info==NULL && reads==0 && eaten==response.length*4);
    assert(display.async_handlers==NULL);
}
int main(int argc, char **argv) {
    (void)argv;
    run(1,1,1); run(2,2,1); run(2,1,1); run(1,0,1);
    if (argc>1) { run(1,2,0); return 0; }
    run(1,2,0); run(0,1,0); run(2,3,0);
    Display display={0}; fixture(1,1); version_minor=5;
    assert(XRenderQueryFormats(&display)==1 && ext.info->screen[0].subpixel==SubPixelUnknown);
    XRenderFreeXRenderInfo(ext.info); ext.info=NULL;
    for (long fail=0;fail<6;fail++) {
        memset(&display,0,sizeof(display)); fixture(1,1); fail_allocation=fail;
        assert(XRenderQueryFormats(&display)==0 && ext.info==NULL);
    }
    puts("Exact-source Xrender bounds, valid versions and allocation controls passed"); return 0;
}
