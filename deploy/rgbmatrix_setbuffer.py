"""Patch rpi-rgb-led-matrix's Python binding with FrameCanvas.SetBuffer(ndarray).

Usage: python rgbmatrix_setbuffer.py <path to rpi-rgb-led-matrix checkout>

SetBuffer hands an (h, w, 3) C-contiguous uint8 numpy array straight to the C++
FrameCanvas::SetPixels() — one call, no Pillow, no copy. rgb_matrix::Color is
three uint8 fields (r, g, b), the same memory layout as the numpy array.

Why: upstream SetImage() needs Image.getim() (Pillow >= 11) and reads Pillow's
private struct layout; with Pillow 9.5 the v1 code fell back to per-pixel
Python blits, which cost ~47% of the Pi's render time.
"""

import sys
from pathlib import Path

MARK = "# --- bigscreen SetBuffer patch ---"

EXTERN = f'''
{MARK}
cdef extern from "led-matrix.h" namespace "rgb_matrix":
    cdef cppclass _BigscreenFrameCanvas "rgb_matrix::FrameCanvas":
        void SetPixels(int, int, int, int, cppinc.Color*) nogil
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


def main(root):
    core = Path(root) / "bindings" / "python" / "rgbmatrix" / "core.pyx"
    src = core.read_text()
    if MARK in src:
        print("already patched")
        return
    anchor = "cdef class Canvas:"
    if anchor not in src:
        raise SystemExit("unexpected core.pyx layout: no 'cdef class Canvas:'")
    src = src.replace(anchor, EXTERN.lstrip("\n") + "\n" + anchor, 1)
    # add the method right after the Canvas class header line
    i = src.index(anchor) + len(anchor)
    src = src[:i] + "\n" + METHOD + src[i:]
    core.write_text(src)
    print(f"patched {core}")


if __name__ == "__main__":
    main(sys.argv[1])
