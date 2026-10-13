"""Small HTTP control server on the display (default :8765), used by the hub.

    GET  /status                 fps, draw time, panels, config revision, preview state
    GET  /frame.png?scale=2      the current frame (nearest-neighbour upscaled)
    GET  /logs?after=N           console lines after cursor N (in-memory ring, see common/logring.py)
    GET  /panels                 panel names the cycle can use right now
    POST /play                   {"side": "left"|"right", "entry": {...rotation entry}, "hold": false, "ttl_sec": 3600}
                                 show an entry now: once (by its own rules, then the rotation resumes) or held
    POST /rotation               {"side": "left"|"right" (omit: both), "action": "next"|"prev"|"stop"|"hold"|"release"}
    POST /override               {"side": …, "panel": "...", "duration_sec": 60, "once": false, ...entry fields}
                                 older form of /play: held for duration_sec (or played once with "once")
    POST /override/clear         {"side": "left"|"right"} or {} for both: stop previews
    POST /preview                {"overlay": {...partial config...}, "ttl_sec": 120}
    POST /preview/clear
    POST /headlines/jump         {"delta": 1|-1} or {"index": n}, optional "hold_sec"; if headlines aren't showing,
                                 plays that one category page and then the rotation resumes (or pins them for hold_sec)
    POST /quake                  {"event": {...}, "duration_sec": 150}: earthquake early warning, shown at once
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
            if u.path == "/logs":
                from common import logring
                q = parse_qs(u.query)
                return self._json(logring.ring.since(int((q.get("after") or ["0"])[0] or 0)))
            if u.path == "/panels":
                s = app.screen
                return self._json({"zone": sorted(s.panels), "full": sorted(s.full_panels), "takeover": sorted(s.takeover_panels)})
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
                s = app.screen
                if body["panel"] not in s.panels and body["panel"] not in s.full_panels and body["panel"] not in s.takeover_panels:
                    return self._json({"error": f"panel {body['panel']!r} isn't available on this display"}, 400)
                extra = {k: v for k, v in body.items() if k not in ("side", "panel", "duration_sec")}
                if body["panel"] == "font_specimen":           # parse the fonts here, not in the render loop
                    from display.modules import font_specimen
                    for rel in (extra.get("fonts") or [])[:12]:
                        try:
                            font_specimen.font(rel)
                        except Exception as e:
                            return self._json({"error": f"{rel}: {e}"}, 400)
                extra.pop("once", None)
                eng.play({**extra, "panel": body["panel"]}, hold=not body.get("once"), ttl_sec=body.get("duration_sec"))
                return self._json({"ok": True})
            if u.path == "/override/clear":
                sides = [body["side"]] if body.get("side") else ["left", "right"]
                for s in sides:
                    app.screen.engines[s].stop()
                return self._json({"ok": True})
            if u.path == "/play":
                side = body.get("side", "right")
                eng = app.screen.engines.get(side)
                entry = body.get("entry") or {}
                s = app.screen
                if not eng or not isinstance(entry, dict) or not entry.get("panel"):
                    return self._json({"error": "need side (left|right) and entry.panel"}, 400)
                if entry["panel"] not in s.panels and entry["panel"] not in s.full_panels and entry["panel"] not in s.takeover_panels:
                    return self._json({"error": f"panel {entry['panel']!r} isn't available on this display"}, 400)
                ttl = body.get("ttl_sec")
                eng.play(entry, hold=bool(body.get("hold")), ttl_sec=ttl if ttl else (3600 if body.get("hold") else None))
                return self._json({"ok": True})
            if u.path == "/rotation":
                act = body.get("action")
                sides = [body["side"]] if body.get("side") else ["left", "right"]
                if act not in ("next", "prev", "stop", "hold", "release") or any(x not in app.screen.engines for x in sides):
                    return self._json({"error": "need action next|prev|stop|hold|release and side left|right"}, 400)
                for x in sides:
                    eng = app.screen.engines[x]
                    {"next": eng.next, "prev": eng.prev, "stop": eng.stop,
                     "hold": lambda: eng.hold(True, float(body.get("ttl_sec") or 3600)), "release": lambda: eng.hold(False)}[act]()
                return self._json({"ok": True, "panels": app.screen.describe()})
            if u.path == "/preview":
                overlay = body.get("overlay")
                if not isinstance(overlay, dict):
                    return self._json({"error": "overlay must be an object"}, 400)
                app.source.set_preview(overlay, float(body.get("ttl_sec", 120)), now)
                return self._json({"ok": True, "expires_in": body.get("ttl_sec", 120)})
            if u.path == "/preview/clear":
                return self._json({"ok": True, "cleared": app.source.clear_preview()})
            if u.path == "/headlines/jump":
                r = app.screen.headlines_jump(index=body.get("index"), delta=int(body.get("delta") or 0),
                                              hold_sec=float(body.get("hold_sec") or 0) or None)
                return self._json(r, 400 if "error" in r else 200)
            if u.path == "/quake":
                ev = body.get("event")
                if not isinstance(ev, dict):
                    return self._json({"error": "event must be an object"}, 400)
                app.screen.quake.set_event(ev)
                if ev.get("cancel"):
                    app.screen.engines["right"].play({"panel": "quake"}, hold=True, ttl_sec=20)
                else:
                    app.screen.engines["right"].play({"panel": "quake"}, hold=True, ttl_sec=float(body.get("duration_sec") or 150))
                return self._json({"ok": True})
            if u.path == "/quake_obs":
                app.screen.quake.set_obs(body.get("v") or "", float(body.get("t") or 0))
                return self._json({"ok": True})
            if u.path == "/restart":
                app.restart_requested = True
                return self._json({"ok": True})
            self._json({"error": "not found"}, 404)

    return H
