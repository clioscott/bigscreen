#!/bin/bash
# Build the v2 display environment on the Pi (run as scott, from ~/bigscreen):
#     bash deploy/pi-setup.sh
#
# - ~/bigscreen/.venv-display : Python venv with numpy, orjson, Pillow and a patched rgbmatrix
#   (FrameCanvas.SetBuffer — see deploy/rgbmatrix_setbuffer.py)
# - compiles pinned to CPU 0 at lowest priority so the running display keeps its cores.
set -euo pipefail
REPO="$HOME/bigscreen"
VENV="$REPO/.venv-display"
SRC_V1="$HOME/bigscreen-calendar/rpi-rgb-led-matrix"
BUILD="$HOME/bigscreen-build/rpi-rgb-led-matrix"
RUN="taskset -c 0 nice -n 19"

[ -d "$VENV" ] || python3 -m venv "$VENV"
$RUN "$VENV/bin/pip" install -q --upgrade pip
$RUN "$VENV/bin/pip" install -q numpy orjson "pillow>=11" cython scikit-build-core cmake ninja

# Start from the same library revision v1 runs (known-good with these panels), then patch.
rm -rf "$BUILD"
mkdir -p "$(dirname "$BUILD")"
cp -a "$SRC_V1" "$BUILD"
rm -f "$BUILD"/bindings/python/rgbmatrix/core.cpp "$BUILD"/bindings/python/rgbmatrix/*.so
"$VENV/bin/python" "$REPO/deploy/rgbmatrix_setbuffer.py" "$BUILD"
CMAKE_BUILD_PARALLEL_LEVEL=1 $RUN "$VENV/bin/pip" install --no-build-isolation -q "$BUILD"

"$VENV/bin/python" -c "from rgbmatrix import RGBMatrix; from rgbmatrix.core import FrameCanvas; assert hasattr(FrameCanvas, 'SetBuffer'); print('rgbmatrix OK: SetBuffer available')"
