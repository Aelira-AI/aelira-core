#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/extensions/Xrender.h>
#include <assert.h>
#include <stdio.h>
#include <dlfcn.h>

int main(void) {
    Dl_info loaded;
    assert(dladdr((void *)XRenderQueryFormats, &loaded) != 0);
    assert(loaded.dli_fname != NULL);
    printf("Loaded XRender library: %s\n", loaded.dli_fname);
    Display *display = XOpenDisplay(NULL);
    assert(display != NULL);
    int event, error, major, minor;
    assert(XRenderQueryExtension(display, &event, &error));
    assert(XRenderQueryVersion(display, &major, &minor));
    assert(XRenderQueryFormats(display));
    XRenderPictFormat *format = XRenderFindStandardFormat(display, PictStandardARGB32);
    assert(format != NULL && format->depth == 32);
    int screen = DefaultScreen(display);
    Pixmap pixmap = XCreatePixmap(display, RootWindow(display, screen), 32, 32, 32);
    Picture picture = XRenderCreatePicture(display, pixmap, format, 0, NULL);
    XRenderColor red = {65535, 0, 0, 65535};
    XRenderFillRectangle(display, PictOpSrc, picture, &red, 0, 0, 32, 32);
    XSync(display, False);
    XImage *image = XGetImage(display, pixmap, 0, 0, 32, 32, AllPlanes, ZPixmap);
    assert(image != NULL && image->width == 32 && image->height == 32);
    unsigned long pixel = XGetPixel(image, 16, 16);
    assert((pixel & 0x00ffffffUL) == 0x00ff0000UL);
    XDestroyImage(image);
    printf("Installed Xrender %d.%d rendered red pixels; subpixel=%d\n", major, minor, XRenderQuerySubpixelOrder(display, screen));
    XRenderFreePicture(display, picture);
    XFreePixmap(display, pixmap);
    XCloseDisplay(display);
    return 0;
}
