"""One-time: build ~/bigscreen-state from the v1 install. Run on the server:

    .venv/bin/python -m tools.migrate_v1 [--force]

- config: v1 server app-config.json, with system.display taken from the Pi's copy
  (the Pi's values are the ones actually tuned and running), plus the new
  headline categories and a `hub` section.
- secrets: token.pickle + credentials.json copied from ~/bigscreen-server.
- data: v1 data/*.json and favicons copied as a warm start.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from hub import config, paths

V1_SERVER = Path.home() / "bigscreen-server"
PI = "scott@192.168.1.100"
PI_V1 = "/home/scott/bigscreen-calendar"

G = "https://news.google.com/rss/search?hl=en-CA&gl=CA&ceid=CA:en&q="


def feeds(*pairs):
    return [{"name": n, "url": u, "enabled": True} for n, u in pairs]


CATEGORIES = [
    {"name": "Canada News", "enabled": True, "feeds": feeds(
        ("CBC", "https://www.cbc.ca/webfeed/rss/rss-canada"),
        ("Globe and Mail", "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/canada/"),
        ("Global News", "https://globalnews.ca/canada/feed/"),
        ("Toronto Star", "https://www.thestar.com/search/?f=rss&t=article&c=news/canada*&l=50&s=start_time&sd=desc"),
    )},
    {"name": "Local News", "enabled": True, "feeds": feeds(
        ("CBC British Columbia", "https://www.cbc.ca/webfeed/rss/rss-canada-britishcolumbia"),
        ("Vancouver Sun", "https://vancouversun.com/feed"),
        ("Daily Hive", "https://dailyhive.com/feed/vancouver"),
        ("Global BC", "https://globalnews.ca/bc/feed/"),
        ("Globe and Mail BC", "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/canada/british-columbia/"),
    )},
    {"name": "World News", "enabled": True, "feeds": feeds(
        ("BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("Guardian", "https://www.theguardian.com/world/rss"),
        ("CBC World", "https://www.cbc.ca/webfeed/rss/rss-world"),
        ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
        ("Globe and Mail World", "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/world/"),
        ("New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
    )},
    {"name": "US News", "enabled": True, "feeds": feeds(
        ("Politico", "https://rss.politico.com/politics-news.xml"),
        ("CNN (via Google News)", G + "site:cnn.com+when:1d"),
        ("MS NOW", "https://www.ms.now/rss"),
        ("The Atlantic", "https://www.theatlantic.com/feed/channel/politics/"),
        ("New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml"),
        ("Washington Post", "https://feeds.washingtonpost.com/rss/politics"),
        ("NPR", "https://feeds.npr.org/1014/rss.xml"),
        ("Guardian US", "https://www.theguardian.com/us-news/us-politics/rss"),
        ("New Yorker", "https://www.newyorker.com/feed/news"),
    )},
    {"name": "Business & Economics", "enabled": True, "feeds": feeds(
        ("Globe and Mail Business", "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/business/"),
        ("CBC Business", "https://www.cbc.ca/webfeed/rss/rss-business"),
        ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
        ("Guardian Business", "https://www.theguardian.com/business/rss"),
        ("New York Times Business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    )},
    {"name": "Technology", "enabled": True, "feeds": feeds(
        ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
        ("The Verge", "https://www.theverge.com/rss/index.xml"),
        ("TechCrunch", "https://techcrunch.com/feed/"),
        ("404 Media", "https://www.404media.co/rss/"),
        ("Hacker News", "https://hnrss.org/frontpage?points=150"),
    )},
    {"name": "Satire", "enabled": True, "feeds": feeds(
        ("The Beaverton", "https://www.thebeaverton.com/feed/"),
        ("The Onion", "https://theonion.com/feed/"),
        ("Borowitz Report", "https://www.borowitzreport.com/feed"),
    )},
]


def pi_config():
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", PI, f"cat {PI_V1}/config/app-config.json"],
                       capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        raise SystemExit(f"could not read Pi config: {p.stderr}")
    return json.loads(p.stdout)


def main():
    force = "--force" in sys.argv
    paths.ensure_dirs()
    if paths.CONFIG_PATH.exists() and not force:
        raise SystemExit(f"{paths.CONFIG_PATH} exists; pass --force to rebuild it")

    cfg = json.loads((V1_SERVER / "config" / "app-config.json").read_text())
    cfg["system"]["display"] = pi_config()["system"]["display"]
    cfg["version"] = 2
    cfg["hub"] = {
        "panel_port": 8800,
        "pi": {"host": "192.168.1.100", "user": "scott", "data_dir": "/dev/shm/bigscreen",
               "v1_dir": PI_V1, "display_service": "bigscreen-client"},
    }
    h = cfg["modules"]["headlines"]
    h.update({"source_type": "rss_categories", "categories": CATEGORIES, "items_per_category": 12,
              "max_per_source": 3, "max_age_hours": 36, "refresh_interval_sec": 900})
    h.pop("subreddits", None)          # derived from categories when pushed to the v1 display
    w = cfg["modules"]["weather"]
    w.setdefault("citypage_refresh_interval_sec", w.get("refresh_interval_sec", 900))
    w.setdefault("swob_refresh_interval_sec", 120)
    for t in cfg["modules"]["finance"]["symbols"]:
        t.setdefault("enabled", True)
    config.save(cfg, reason="migrated from v1")
    print(f"wrote {paths.CONFIG_PATH}")

    for name in ("token.pickle", "credentials.json"):
        src = V1_SERVER / name
        if src.exists():
            shutil.copy2(src, paths.SECRETS_DIR / name)
            (paths.SECRETS_DIR / name).chmod(0o600)
            print(f"copied {name}")

    for src in (V1_SERVER / "data").glob("*.json"):
        if src.name != "headlines.json" and not (paths.DATA_DIR / src.name).exists():
            shutil.copy2(src, paths.DATA_DIR / src.name)
    for src in (V1_SERVER / "data" / "icons").glob("*_16.png"):
        if not (paths.ICONS_DIR / src.name).exists():
            shutil.copy2(src, paths.ICONS_DIR / src.name)
    print("seeded data/")


if __name__ == "__main__":
    main()
