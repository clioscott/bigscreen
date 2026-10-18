"""Talking to the hub (``/api/display/*``) and making sense of what it reports."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

# hub/web/ui/core.js PANEL_TYPES and WEATHER_SCREENS (labels only)
PANEL_LABELS = {
    "calendar": "Calendar", "client_calls_upcoming": "Upcoming client calls", "client_calls_past": "Past client calls",
    "headlines": "Headlines", "weather": "Weather info", "weather_full": "Weather info (full width)",
    "map": "Weather map", "map_full": "Weather map (full width)", "weather_alert": "Weather alert (full screen)",
    "blank": "Blank", "quake": "Earthquake warning", "font_specimen": "Font specimen",
    "webcam": "Webcam", "webcam_full": "Webcam (full width)", "webcam_wall": "Webcam (whole wall)",
    "bridges": "Bridges now", "bridges_full": "Bridges now (full width)", "bridges_wall": "Bridges now (whole wall)",
}
WEATHER_SCREENS = {"current": "Current conditions", "hourly": "Hourly forecast", "forecast": "7-day forecast",
                   "warning": "Warnings"}
SIDES = ("left", "right")
ENTRY_KEYS = ("panel", "track", "weather_screen", "cam")          # what identifies an entry (display/rotation.py:entry_key)


class HubError(Exception):
    pass


class Hub:
    def __init__(self, base_url: str, timeout: float = 4.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _call(self, method, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(self.base_url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", "User-Agent": "BigScreenTray"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read()).get("detail")
            except Exception:
                detail = None
            raise HubError(detail or f"{e.code} {e.reason}") from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise HubError(f"can't reach {self.base_url} ({getattr(e, 'reason', e)})") from None

    def get(self, path):
        return self._call("GET", path)

    def post(self, path, payload=None):
        return self._call("POST", path, payload or {})

    def display_status(self) -> dict:
        """The display's own /status (raises HubError if the hub or the display can't be reached)."""
        r = self.get("/api/display/status")
        if not r.get("reachable") or not isinstance(r.get("status"), dict):
            raise HubError(f"the hub can't reach the display ({r.get('error') or 'no status'})")
        return r["status"]

    def track_labels(self) -> dict:
        tracks = ((self.get("/api/config").get("modules") or {}).get("weather_maps") or {}).get("tracks") or {}
        return {k: v.get("label") for k, v in tracks.items() if isinstance(v, dict)}


def entry_key(e: dict) -> dict:
    return {k: e[k] for k in ENTRY_KEYS if e.get(k)}


class Wall:
    """A snapshot of the display: what each side is playing, its rotation, the headline categories."""

    def __init__(self, status: dict | None = None, track_labels: dict | None = None):
        self.status = status or {}
        self.track_labels = track_labels or {}

    @property
    def known(self) -> bool:
        return bool(self.status)

    def title(self, e: dict | None) -> str:
        if not e:
            return "nothing"
        p = e.get("panel", "")
        if p.startswith("map"):
            return self.track_labels.get(e.get("track")) or f"Map: {e.get('track') or '?'}"
        if p in ("weather", "weather_full"):
            return WEATHER_SCREENS.get(e.get("weather_screen") or "current") or PANEL_LABELS[p]
        return PANEL_LABELS.get(p) or p.replace("_", " ").capitalize()

    def side(self, side: str) -> dict:
        return (self.status.get("panels") or {}).get(side) or {}

    def entries(self, side: str) -> list[dict]:
        """The side's rotation entries, each once (by identity)."""
        out, seen = [], set()
        for e in self.side(side).get("cycle") or []:
            k = json.dumps(entry_key(e), sort_keys=True)
            if e.get("panel") and k not in seen:
                seen.add(k)
                out.append(e)
        return out

    def playing(self, side: str) -> dict | None:
        return (self.side(side).get("playing") or {}).get("entry")

    def is_preview(self, side: str) -> bool:
        return bool(self.side(side).get("preview"))

    def is_held(self, side: str) -> bool:
        return bool((self.side(side).get("preview") or {}).get("hold"))

    @property
    def headlines(self) -> dict:
        return self.status.get("headlines") or {}

    @property
    def categories(self) -> list[str]:
        return list(self.headlines.get("categories") or [])

    def describe(self, side: str) -> str:
        e = self.playing(side)
        s = self.title(e)
        if e and e.get("panel") == "headlines" and self.headlines.get("current"):
            s += f" — {self.headlines['current']}"
        if self.is_held(side):
            s += " (held)"
        elif self.is_preview(side):
            s += " (manual)"
        return s

    def to_cache(self) -> dict:
        return {"cycles": {s: self.entries(s) for s in SIDES}, "categories": self.categories,
                "track_labels": self.track_labels}

    @classmethod
    def from_cache(cls, c: dict) -> "Wall":
        """An offline stand-in built from the cache: rotation and categories only, nothing playing."""
        panels = {s: {"cycle": (c.get("cycles") or {}).get(s) or []} for s in SIDES}
        return cls({"panels": panels, "headlines": {"categories": c.get("categories") or []}}
                   if c else {}, c.get("track_labels") or {})
