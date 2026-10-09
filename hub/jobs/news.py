"""Headlines from publisher RSS/Atom feeds, grouped into categories.

Config (modules.headlines):
    categories: [{name, enabled, feeds: [{name, url}]}]
    items_per_category      max items shown per category (default 12)
    max_per_source          cap per outlet so the list stays mixed (default 3)
    max_age_hours           drop older items (default 36)

Writes data/headlines.json in the shape the v1 display already reads:
    {"last_updated_utc": t, "<category>": [{title, source, domain, created_utc}], "_meta": {...}}
and a 16x16 favicon per domain into data/icons/<domain>_16.png.
"""

import email.utils
import html
import io
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from PIL import Image

from hub import paths
from hub.jobs import _harness
from hub.util import atomic_write_json

UA = "Mozilla/5.0 (X11; Linux x86_64) BigScreen/2.0 (personal LED display)"
ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/elements/1.1/}"
FEED_TIMEOUT = 15
FAVICON_MAX_AGE = 30 * 86400


def _parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


# The display's BDF fonts are ASCII/Latin-1; typographic punctuation would render as nothing.
_ASCII_PUNCT = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "–": "-", "—": "-", "―": "-", "−": "-",
    "…": "...", " ": " ", " ": " ", " ": " ", " ": " ", "​": "",
})


def _clean(text):
    text = re.sub(r"<[^>]+>", "", text or "")
    text = html.unescape(text).translate(_ASCII_PUNCT)
    return re.sub(r"\s+", " ", text).strip()


def _domain(url):
    host = urlparse(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def parse_feed(body, feed_name):
    """Return items [{title, link, domain, source, created_utc}] from RSS 2.0 or Atom bytes."""
    root = ET.fromstring(body)
    entries = root.findall(".//item") or root.findall(f".//{ATOM}entry")
    items = []
    for e in entries:
        title = _clean(e.findtext("title") or e.findtext(f"{ATOM}title"))
        link = (e.findtext("link") or "").strip()
        if not link:
            le = e.find(f"{ATOM}link")
            link = le.get("href", "") if le is not None else ""
        created = _parse_date(e.findtext("pubDate") or e.findtext(f"{DC}date")
                              or e.findtext(f"{ATOM}published") or e.findtext(f"{ATOM}updated"))
        source, domain = feed_name, _domain(link)
        # Google News wraps links in news.google.com redirects; the real outlet is in <source url=...>
        # and is also appended to the title as " - Outlet".
        src_el = e.find("source")
        if src_el is not None and src_el.get("url"):
            domain = _domain(src_el.get("url"))
            source = (src_el.text or source).strip()
            suffix = f" - {source}"
            if title.endswith(suffix):
                title = title[: -len(suffix)].rstrip()
        if title:
            items.append({"title": title, "link": link, "domain": domain,
                          "source": source, "created_utc": created or 0})
    return items


def fetch_feed(session, feed):
    t0 = time.time()
    status = {"name": feed.get("name") or _domain(feed["url"]), "url": feed["url"]}
    try:
        r = session.get(feed["url"], timeout=FEED_TIMEOUT)
        status["http"] = r.status_code
        r.raise_for_status()
        items = parse_feed(r.content, status["name"])
        status["items"] = len(items)
        newest = max((i["created_utc"] for i in items), default=0)
        status["newest_age_h"] = round((time.time() - newest) / 3600, 1) if newest else None
        status["ok"] = bool(items)
        if not items:
            status["error"] = "feed parsed but had no items"
    except Exception as e:
        items = []
        status["ok"] = False
        status["error"] = f"{type(e).__name__}: {e}"[:200]
    status["ms"] = int((time.time() - t0) * 1000)
    return items, status


def _norm_title(t):
    return re.sub(r"[^a-z0-9]+", "", t.lower())[:80]


def select_items(items, limit, per_source, max_age_h):
    """Newest first, de-duplicated, at most `per_source` per outlet, so the list stays mixed."""
    cutoff = time.time() - max_age_h * 3600
    seen, per, out = set(), {}, []
    for it in sorted(items, key=lambda i: i["created_utc"], reverse=True):
        if it["created_utc"] and it["created_utc"] < cutoff:
            continue
        key = _norm_title(it["title"])
        if key in seen:
            continue
        if per.get(it["domain"], 0) >= per_source:
            continue
        seen.add(key)
        per[it["domain"]] = per.get(it["domain"], 0) + 1
        out.append(it)
        if len(out) >= limit:
            break
    return out


def ensure_favicon(session, domain):
    """16x16 RGB PNG, same convention the v1 display reads (icons/<domain>_16.png)."""
    if not domain:
        return False
    path = paths.ICONS_DIR / f"{domain}_16.png"
    if path.exists() and time.time() - path.stat().st_mtime < FAVICON_MAX_AGE:
        return True
    try:
        r = session.get("https://www.google.com/s2/favicons", params={"domain": domain, "sz": 32}, timeout=8)
        if r.status_code != 200:
            return path.exists()
        img = Image.open(io.BytesIO(r.content)).convert("RGBA")
        bg = Image.new("RGBA", img.size, (0, 0, 0, 255))
        img = Image.alpha_composite(bg, img).convert("RGB")
        img.thumbnail((16, 16), Image.LANCZOS)
        tmp = path.with_suffix(".tmp.png")
        img.save(tmp)
        tmp.replace(path)
        return True
    except Exception:
        return path.exists()


def run(cfg):
    hcfg = cfg["modules"].get("headlines", {})
    cats = [c for c in hcfg.get("categories", []) if c.get("enabled", True)]
    limit = int(hcfg.get("items_per_category", 12))
    per_source = int(hcfg.get("max_per_source", 3))
    max_age_h = float(hcfg.get("max_age_hours", 36))

    session = requests.Session()
    session.headers.update({"User-Agent": UA,
                            "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"})

    jobs = [(c["name"], f) for c in cats for f in c.get("feeds", []) if f.get("enabled", True)]
    done = [0]
    lock = threading.Lock()

    def fetch(cf):
        r = (cf[0], *fetch_feed(session, cf[1]))
        with lock:
            done[0] += 1
            _harness.progress(done[0], len(jobs), f"fetched {cf[1].get('name') or cf[1].get('url')}")
        st = r[2]
        print(f"{cf[0]} · {st.get('name')}: " + (f"{st.get('items')} items" if st.get("ok") else f"FAILED {st.get('error')}"),
              flush=True)
        return r

    _harness.progress(0, len(jobs), "fetching feeds")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fetch, jobs))

    payload = {"last_updated_utc": time.time()}
    feed_status = {}
    domains = set()
    counts = {}
    for c in cats:
        name = c["name"]
        pooled = [it for cat, items, _ in results if cat == name for it in items]
        chosen = select_items(pooled, limit, per_source, max_age_h)
        payload[name] = [{k: it[k] for k in ("title", "source", "domain", "created_utc")} for it in chosen]
        counts[name] = len(chosen)
        domains.update(it["domain"] for it in chosen)
        feed_status[name] = [st for cat, _, st in results if cat == name]

    with ThreadPoolExecutor(max_workers=8) as pool:
        icons_ok = sum(pool.map(lambda d: ensure_favicon(session, d), sorted(domains)))

    payload["_meta"] = {"categories": [c["name"] for c in cats]}
    feeds_failed = [f"{cat}: {st['name']}" for cat, sts in feed_status.items() for st in sts if not st["ok"]]
    empty = [n for n, k in counts.items() if k == 0]
    if any(counts.values()):
        atomic_write_json(paths.DATA_DIR / "headlines.json", payload)
    atomic_write_json(paths.CACHE_DIR / "news_feed_status.json",
                      {"checked_at": time.time(), "categories": feed_status})

    return {
        "ok": bool(any(counts.values())),
        "message": f"{sum(counts.values())} headlines in {len(cats)} categories"
                   + (f"; {len(feeds_failed)} feed(s) failing" if feeds_failed else ""),
        "counts": counts,
        "feeds_failed": feeds_failed,
        "empty_categories": empty,
        "favicons": f"{icons_ok}/{len(domains)}",
        "files": ["headlines.json", "icons/"],
    }


if __name__ == "__main__":
    _harness.main(run)
