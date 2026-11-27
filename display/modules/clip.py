"""A looping clip of ready-made frames (the control panel's Clarity lab: hub/clarity.py).

The hub renders a clip to exactly the zone's pixels and syncs it as ``<data>/clips/<name>.bin`` (n frames of
h x w x 3 RGB) with ``<name>.json`` = {"n", "h", "w", "fps", ...}. The Pi only blits: nothing is scaled, coloured or
captioned here, so what the wall shows is what the hub made, pixel for pixel. Rotation entry:
{"panel": "clip_tall", "clip": "<name>"}; it loops until the play ends (a held play, or the entry's seconds).
"""

import json
import os
import threading
import time

import numpy as np


def _open(path):
    """The clip's frames, mapped from /dev/shm rather than copied (see weather_maps._open_pack)."""
    if os.name == "nt" or os.path.getsize(path) == 0:
        return np.fromfile(path, dtype=np.uint8)
    return np.memmap(path, dtype=np.uint8, mode="r")


class ClipStore:
    """Watches <data_dir>/clips and opens changed clips on its own thread, never in the render loop."""

    def __init__(self, data_dir, watch=True):
        self.dir = os.path.join(data_dir or "", "clips")
        self.clips = {}          # name -> (meta, frames)
        self._seen = {}
        if watch and data_dir:
            threading.Thread(target=self._loop, name="clip-loader", daemon=True).start()

    def get(self, name):
        return self.clips.get(name)

    def _loop(self):
        while True:
            try:
                self.scan()
            except Exception as e:
                print(f"[clip] scan error: {e}")
            time.sleep(1.0)

    def scan(self):
        if not os.path.isdir(self.dir):
            self.clips.clear()
            self._seen.clear()
            return
        names = {f[:-5] for f in os.listdir(self.dir) if f.endswith(".json")}
        for name in list(self.clips):
            if name not in names:
                self.clips.pop(name, None)
                self._seen.pop(name, None)
        for name in names:
            jp, bp = os.path.join(self.dir, name + ".json"), os.path.join(self.dir, name + ".bin")
            try:
                seen = (os.stat(jp).st_mtime, os.stat(bp).st_mtime, os.path.getsize(bp))
            except OSError:
                continue
            if self._seen.get(name) == seen:
                continue
            with open(jp, encoding="utf-8") as f:
                meta = json.load(f)
            n, h, w = int(meta["n"]), int(meta["h"]), int(meta["w"])
            if n < 1 or seen[2] != n * h * w * 3:
                continue                          # the two files are from different builds; look again next time
            self.clips[name] = (meta, _open(bp).reshape(n, h, w, 3))
            self._seen[name] = seen


_stores = {}


def store_for(data_dir):
    """One store (and one watcher thread) per data dir, however often the screen is rebuilt."""
    if data_dir not in _stores:
        _stores[data_dir] = ClipStore(data_dir)
    return _stores[data_dir]


class ClipPanel:
    def __init__(self, store):
        self.store = store
        self._t0 = None

    def draw(self, c, zone, entry, transition):
        from display.modules import webcam
        x, y, w, h = zone
        # the render loop's own clock (the grid frames are drawn on), so each frame shows for the same time
        now = webcam.CLOCK.get("tick") or time.time()
        if transition or self._t0 is None:
            self._t0 = now
        clip = self.store.get(str(entry.get("clip") or ""))
        if clip is None:
            return "done" if now - self._t0 > 15 else None      # never came: let the rotation carry on
        meta, frames = clip
        fps = max(1.0, min(60.0, float(meta.get("fps", 30))))
        webcam.CLOCK["video_at"] = time.time()                   # the loop runs at the video rate while this is up
        fr = frames[int((now - self._t0) * fps + 0.25) % len(frames)]
        fh, fw = min(h, fr.shape[0]), min(w, fr.shape[1])
        c.Blit(fr[:fh, :fw], x + (w - fw) // 2, y + (h - fh) // 2)
        return None
