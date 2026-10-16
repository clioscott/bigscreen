"""Alert Ready live feed: the NAAD System's TCP stream (Pelmorex, free, no login).

NAAD (National Alert Aggregation & Dissemination) is the feed behind Canada's broadcast and phone alerts.
Every CAP-CP message arrives on ``streaming1|2.naad-adna.pelmorex.com:8080`` as XML, plus a heartbeat once a
minute that lists the last ten alerts — any we missed (hub restart, dropped socket) are fetched from the
archive (``capcp1|2.naad-adna.pelmorex.com/<date>/<sent>I<identifier>.xml``).

This thread only *receives*: each message is saved under cache/naad/ and the ``naad`` job (a short subprocess,
like every other fetch) is asked to run, which filters, routes and publishes. Earthquake and tsunami messages
also wake their own jobs (they show them on their own screens).
"""

from __future__ import annotations

import hashlib
import re
import socket
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

from hub import paths
from hub.util import atomic_write_json, read_json

STREAMS = [("streaming1.naad-adna.pelmorex.com", 8080), ("streaming2.naad-adna.pelmorex.com", 8080)]
ARCHIVES = ["http://capcp1.naad-adna.pelmorex.com", "http://capcp2.naad-adna.pelmorex.com"]
DIR = paths.CACHE_DIR / "naad"
INDEX = paths.CACHE_DIR / "naad_index.json"
UA = "BigScreen/2.0 (personal LED display, Vancouver)"

state = {"connected": None, "since": None, "last_heartbeat": None, "last_alert": None, "received": 0,
         "backfilled": 0, "errors": []}
_lock = threading.Lock()
_notify = None


def log(msg):
    print(f"[naad] {msg}\n", end="", flush=True)


def _err(msg):
    with _lock:
        state["errors"] = ([f"{datetime.now():%H:%M:%S} {msg[:160]}"] + state["errors"])[:10]


def _tag(e):
    return e.tag.split("}", 1)[-1]


def _find(root, name):
    for e in root:
        if _tag(e) == name:
            return (e.text or "").strip()
    return ""


def key_of(sender, ident):
    return hashlib.sha1(f"{sender}|{ident}".encode()).hexdigest()[:20]


def archive_path(ident, sent):
    return f"/{sent[:10]}/{re.sub(r'[-:+]', '_', sent)}I{re.sub(r'[-:]', '_', ident)}.xml"


def _index():
    return read_json(INDEX, {}) or {}


def store(xml_bytes, source="stream"):
    """Save one CAP message (idempotent). Returns (key, routes) when new, else None."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        _err(f"bad XML from {source}: {e}")
        return None
    sender, ident, sent = _find(root, "sender"), _find(root, "identifier"), _find(root, "sent")
    if sender == "NAADS-Heartbeat":
        return None
    k = key_of(sender, ident)
    path = DIR / f"{k}.xml"
    with _lock:
        if path.exists():
            return None
        DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(xml_bytes)
        idx = _index()
        idx[k] = {"sender": sender, "id": ident, "sent": sent, "received": time.time(), "source": source}
        atomic_write_json(INDEX, idx)
        state["received"] += 1
        state["last_alert"] = time.time()
    text = xml_bytes[:6000].decode("utf8", "replace").lower()
    routes = ["naad"]
    if "nrcan" in sender.lower() or "<value>earthquake</value>" in text or "eew" in ident.lower():
        routes.append("quakes")
    if "tsunami" in text:
        routes.append("tsunami")
    log(f"{source}: {sender} {ident} ({', '.join(routes)})")
    return k, routes


def _heartbeat(root):
    state["last_heartbeat"] = time.time()
    refs = _find(root, "references").split()
    known = _index()
    todo = []
    for ref in refs:
        try:
            sender, ident, sent = ref.split(",")
        except ValueError:
            continue
        if key_of(sender, ident) not in known:
            todo.append((sender, ident, sent))
    if todo:
        threading.Thread(target=_backfill, args=(todo,), name="naad-backfill", daemon=True).start()


def _backfill(todo):
    routes = set()
    for sender, ident, sent in todo:
        for base in ARCHIVES:
            try:
                r = requests.get(base + archive_path(ident, sent), timeout=20, headers={"User-Agent": UA})
                if r.status_code == 200 and b"<alert" in r.content:
                    got = store(r.content, "archive")
                    if got:
                        state["backfilled"] += 1
                        routes.update(got[1])
                    break
            except Exception as e:
                _err(f"archive {ident}: {e}")
    if routes:
        _wake(routes)


def _wake(routes):
    if _notify:
        try:
            _notify(sorted(set(routes)))
        except Exception as e:
            _err(f"notify: {e}")


def _on_message(xml_bytes):
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return
    if _find(root, "sender") == "NAADS-Heartbeat":
        _heartbeat(root)
        return
    got = store(xml_bytes)
    if got:
        _wake(got[1])


def _run():
    i, backoff = 0, 5
    while True:
        host = STREAMS[i % len(STREAMS)]
        try:
            s = socket.create_connection(host, timeout=20)
            s.settimeout(180)                                   # a heartbeat comes every minute
            with _lock:
                state.update(connected=f"{host[0]}:{host[1]}", since=time.time())
            log(f"connected to {host[0]}")
            backoff = 5
            buf = b""
            while True:
                d = s.recv(65536)
                if not d:
                    raise ConnectionError("stream closed")
                buf += d
                while b"</alert>" in buf:
                    msg, buf = buf.split(b"</alert>", 1)
                    msg += b"</alert>"
                    start = msg.find(b"<?xml")
                    start = start if start >= 0 else msg.find(b"<alert")
                    if start >= 0:
                        _on_message(msg[start:])
                if len(buf) > 4_000_000:                       # never grow without bound on garbage
                    buf = b""
        except Exception as e:
            with _lock:
                state["connected"] = None
            _err(f"{host[0]}: {e}")
            try:
                s.close()
            except Exception:
                pass
        i += 1
        time.sleep(backoff)
        backoff = min(120, backoff * 2)


def prune(max_age_days=4):
    """Drop stored messages older than a few days (the job only shows unexpired ones)."""
    cutoff = time.time() - max_age_days * 86400
    with _lock:
        idx = _index()
        for k, v in list(idx.items()):
            if v.get("received", 0) < cutoff:
                (DIR / f"{k}.xml").unlink(missing_ok=True)
                idx.pop(k, None)
        atomic_write_json(INDEX, idx)


def start(notify=None):
    global _notify
    _notify = notify
    DIR.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=_run, name="naad-listener", daemon=True).start()
