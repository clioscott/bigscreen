"""Patch rpi-rgb-led-matrix's Python binding with FrameCanvas.SetBuffer(ndarray).

Usage: python rgbmatrix_setbuffer.py <path to rpi-rgb-led-matrix checkout>

SetBuffer hands an (h, w, 3) C-contiguous uint8 numpy array straight to the C++
FrameCanvas::SetPixels() — one call, no Pillow, no copy. rgb_matrix::Color is
three uint8 fields (r, g, b), the same memory layout as the numpy array.

Why: upstream SetImage() needs Image.getim() (Pillow >= 11) and reads Pillow's
private struct layout; with Pillow 9.5 the v1 code fell back to per-pixel
Python blits, which cost ~47% of the Pi's render time.

Also RGBMatrix.SwapOnVSyncNoGil(): upstream SwapOnVSync() holds the GIL while it
waits for the panels' next refresh (up to 8 ms at 120 Hz), which would stall the
render thread while display/main.py's Presenter sends a frame on its own core.

The patched binding is rpi-rgb-led-matrix's (Henner Zeller and contributors, GPL-2.0); see THIRD_PARTY.md.
"""

import sys
from pathlib import Path

MARK = "# --- bigscreen SetBuffer patch ---"

EXTERN = f'''
{MARK}
cdef extern from "led-matrix.h" namespace "rgb_matrix":
    cdef cppclass _BigscreenFrameCanvas "rgb_matrix::FrameCanvas":
        void SetPixels(int, int, int, int, cppinc.Color*) nogil
    cdef cppclass _BigscreenRGBMatrix "rgb_matrix::RGBMatrix":
        _BigscreenFrameCanvas* SwapOnVSync(_BigscreenFrameCanvas*, uint8_t) nogil
'''

SWAP_MARK = "# --- bigscreen SwapOnVSyncNoGil patch ---"
SWAP_ANCHOR = "cdef class RGBMatrix(Canvas):"
SWAP_METHOD = f'''
    {SWAP_MARK}
    def SwapOnVSyncNoGil(self, FrameCanvas newFrame, uint8_t framerate_fraction = 1):
        """SwapOnVSync without holding the GIL while it waits for the panels' refresh."""
        cdef _BigscreenRGBMatrix* m = <_BigscreenRGBMatrix*>self.__matrix
        cdef _BigscreenFrameCanvas* c = <_BigscreenFrameCanvas*>newFrame.__canvas
        cdef _BigscreenFrameCanvas* out
        with nogil:
            out = m.SwapOnVSync(c, framerate_fraction)
        return __createFrameCanvas(<cppinc.FrameCanvas*>out)
'''

METHOD = '''
    @cython.boundscheck(False)
    @cython.wraparound(False)
    def SetBuffer(self, const uint8_t[:, :, ::1] buf, int x=0, int y=0):
        """Blit an (h, w, 3) C-contiguous uint8 RGB array in a single C++ call."""
        if buf.shape[2] != 3:
            raise ValueError("SetBuffer expects an (h, w, 3) uint8 array")
        cdef int h = buf.shape[0]
        cdef int w = buf.shape[1]
        cdef _BigscreenFrameCanvas* fc = <_BigscreenFrameCanvas*>self._getCanvas()
        with nogil:
            fc.SetPixels(x, y, w, h, <cppinc.Color*><void*>&buf[0, 0, 0])
'''


PILLOW_STUB = """/* bigscreen: Pillow-free stub. Upstream reads Pillow's private Imaging.h struct for
 * SetImage(unsafe=True); we never use that path (SetBuffer replaces it), and the header
 * isn't installed with Pillow wheels. Returning NULL keeps the symbol linkable. */
#include <stddef.h>
#include "pillow.h"

int** get_image32(void* im) {
    (void) im;
    return NULL;
}
"""


def main(root):
    shim = Path(root) / "bindings" / "python" / "rgbmatrix" / "shims" / "pillow.c"
    shim.write_text(PILLOW_STUB)
    core = Path(root) / "bindings" / "python" / "rgbmatrix" / "core.pyx"
    src = core.read_text()
    if MARK in src and SWAP_MARK in src:
        print("already patched")
        return
    if MARK in src:
        raise SystemExit("core.pyx has an older bigscreen patch: start from a clean copy (deploy/pi-setup.sh does)")
    anchor = "cdef class Canvas:"
    if anchor not in src or SWAP_ANCHOR not in src:
        raise SystemExit("unexpected core.pyx layout: no 'cdef class Canvas:' / 'cdef class RGBMatrix(Canvas):'")
    src = src.replace(anchor, EXTERN.lstrip("\n") + "\n" + anchor, 1)
    # add each method right after its class header line
    i = src.index(anchor) + len(anchor)
    src = src[:i] + "\n" + METHOD + src[i:]
    i = src.index(SWAP_ANCHOR) + len(SWAP_ANCHOR)
    src = src[:i] + "\n" + SWAP_METHOD + src[i:]
    core.write_text(src)
    print(f"patched {core}")


if __name__ == "__main__":
    main(sys.argv[1])
