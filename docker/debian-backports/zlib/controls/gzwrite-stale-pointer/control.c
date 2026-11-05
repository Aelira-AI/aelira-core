/* Isolated exact-source control: no files, no real deflate stream. The
 * extracted gz_write runs against stubbed gz_comp/gz_init/gz_zero. A direct
 * write fails partway through; after the error is cleared (the CNA's stated
 * precondition for CVE-2026-76844) the next buffered write must not derive
 * its offset from the abandoned caller buffer. With only the fix text
 * removed, the buffered path memcpys past the 256-byte input buffer and
 * AddressSanitizer stops it.
 */
#include "gzguts.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Stand-in for the real gz_comp (defined elsewhere in gzwrite.c, outside the
 * extracted function): consume sixteen bytes of the input, then report a
 * failed underlying write, as a full write to a closed or broken descriptor
 * would after partial progress. */
static int gz_comp(gz_statep state, int flush) {
    (void)flush;
    state->strm.avail_in -= 16;
    state->strm.next_in += 16;
    state->err = Z_ERRNO;
    return -1;
}
static int gz_init(gz_statep state) { (void)state; return 0; }
static int gz_zero(gz_statep state, z_off64_t len) { (void)state; (void)len; return 0; }

#include FUNCTION_FILE

int main(void) {
    gz_statep state = calloc(1, sizeof(*state));
    assert(state != NULL);
    state->mode = GZ_WRITE;
    state->err = Z_OK;
    state->size = 256;
    state->in = calloc(1, 256);
    assert(state->in != NULL);
    unsigned char smallbuf[64];
    memset(smallbuf, 's', sizeof(smallbuf));

    /* Direct-path write whose underlying write fails. The caller pointer is
     * a legal one-past-end pointer just past state->in, which keeps the
     * stale-pointer arithmetic inside one ASan allocation: nothing here
     * dereferences it (the stubbed gz_comp never reads it), but the
     * unfixed code leaves next_in pointing at it, and the buffered path's
     * have = (next_in + avail_in) - in then resolves to 1280, so the
     * memcpy below lands 1024 bytes past the poisoned end of a 256-byte
     * buffer and AddressSanitizer reports it on every run. */
    const unsigned char *caller = state->in + 256;
    assert(gz_write(state, caller, 1024) == 0);

    /* Clear the error as gzclearerr() does, then write again. Patched: the
     * stream state no longer references the caller buffer, so the write is
     * buffered in full. Unpatched: the stale pointer yields have = 1280,
     * the buffer arithmetic underflows, and the memcpy overruns the heap. */
    state->err = Z_OK;
    assert(gz_write(state, smallbuf, sizeof(smallbuf)) == sizeof(smallbuf));
    assert(state->strm.avail_in == sizeof(smallbuf));
    assert(state->strm.next_in == state->in);

    free(state->in);
    free(state);
    puts("Exact-source gz_write stale-pointer reset control passed");
    return 0;
}
