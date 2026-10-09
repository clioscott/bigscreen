"""Small HTTP control server on the display (default :8765), used by the hub.

    GET  /status                 fps, draw time, panels, config revision, preview state
    GET  /frame.png?scale=2      the current frame (nearest-neighbour upscaled)
    GET  /panels                 panel names the cycle can use right now
    POST /override               {"side": "left"|"right", "panel": "...", "duration_sec": 60, ...entry fields}
    POST /override/clear         {"side": "left"|"right"} or {} for both
    POST /preview                {"overlay": {...partial config...}, "ttl_sec": 120}
    POST /preview/clear
    POST /restart                exit; systemd restarts the service
"""

import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np
from PIL import Image


def start_control_server(app, port):
    handler = _make_handler(app)
    srv = ThreadingHTTPServer(("0.0.0.0", port), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, name="control", daemon=True).start()
    return srv


def _make_handler(app):
    png_cache = {"seq": -1, "scale": 0, "data": b""}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n))
            except ValueError:
                return None

        def do_GET(self):
            u = urlparse(self.path)
            if u.path in ("/", "/status"):
                return self._json(app.stats())
            if u.path == "/panels":
                s = app.screen
                return self._json({"zone": sorted(s.panels), "full": sorted(s.full_panels)})
            if u.path == "/frame.png":
                q = parse_qs(u.query)
                scale = max(1, min(8, int((q.get("scale") or ["1"])[0])))
                frame, seq = app.snapshot_frame()
                if png_cache["seq"] != seq or png_cache["scale"] != scale:
                    arr = np.repeat(np.repeat(frame, scale, axis=0), scale, axis=1) if scale > 1 else frame
                    bio = io.BytesIO()
                    Image.fromarray(arr, "RGB").save(bio, "PNG", compress_level=1)
                    png_cache.update(seq=seq, scale=scale, data=bio.getvalue())
                data = png_cache["data"]
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)
                return
            self._json({"error": "not found"}, 404)

        def do_POST(self):
            u = urlparse(self.path)
            body = self._body()
            if body is None:
                return self._json({"error": "bad JSON"}, 400)
            now = time.time()
            if u.path == "/override":
                side = body.get("side", "right")
                eng = app.screen.engines.get(side)
                if not eng or not body.get("panel"):
                    return self._json({"error": "need side (left|right) and panel"}, 400)
                if body["panel"] not in app.screen.panels and body["panel"] not in app.screen.full_panels:
                    return self._json({"error": f"panel {body['panel']!r} isn't available on this display"}, 400)
                eng.set_override(body["panel"], body.get("duration_sec"))
                return self._json({"ok": True})
            if u.path == "/override/clear":
                sides = [body["side"]] if body.get("side") else ["left", "right"]
                for s in sides:
                    app.screen.engines[s].clear_override()
                return self._json({"ok": True})
            if u.path == "/preview":
                overlay = body.get("overlay")
                if not isinstance(overlay, dict):
                    return self._json({"error": "overlay must be an object"}, 400)
                app.source.set_preview(overlay, float(body.get("ttl_sec", 120)), now)
                return self._json({"ok": True, "expires_in": body.get("ttl_sec", 120)})
            if u.path == "/preview/clear":
                return self._json({"ok": True, "cleared": app.source.clear_preview()})
            if u.path == "/restart":
                app.restart_requested = True
                return self._json({"ok": True})
            self._json({"error": "not found"}, 404)

    return H
