"""Sign the hub in to YouTube, from your own PC: stops YouTube's "confirm you're not a bot" refusals.

    python tools/youtube_signin.py              # opens Firefox: sign in to YouTube, then close that window
    python tools/youtube_signin.py --no-browser # send the session again, without opening Firefox

Firefox opens in a profile of its own (BigScreen's, kept on this PC), on Google's sign-in page for YouTube. Sign in,
let YouTube load, then close the window. The script reads that profile's YouTube session (Firefox keeps cookies in a
plain SQLite file), copies it to the hub over SSH into its secrets folder (never git), and asks the hub to try
YouTube again at once. Nothing else uses that profile, so YouTube never rotates the session away from the hub's copy.

Use a spare Google account if you can: YouTube may restrict an account used with yt-dlp (rarely, at the hub's few
lookups an hour, but your main account carries everything else). Run it again whenever the control panel's Overview
says YouTube is refusing the hub.

Standard library only (Python 3.8+); needs Firefox and ssh to the hub (key login).
"""

import argparse
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HOST = "scott@192.168.1.111"
HUB = "http://192.168.1.111:8800"
REMOTE = "~/bigscreen-state/secrets/youtube-cookies.txt"
SIGN_IN = "https://accounts.google.com/ServiceLogin?service=youtube&continue=https%3A%2F%2Fwww.youtube.com%2F"
DOMAINS = ("youtube.com", "google.com")
SIGNED_IN = {"SAPISID", "__Secure-3PAPISID", "SID", "__Secure-3PSID", "LOGIN_INFO"}


def profile_dir() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path.home() / ".local" / "share"
    return base / "BigScreen" / "firefox-youtube"


def firefox() -> str:
    cands = []
    if platform.system() == "Windows":
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if os.environ.get(env):
                cands.append(Path(os.environ[env]) / "Mozilla Firefox" / "firefox.exe")
    elif platform.system() == "Darwin":
        cands.append(Path("/Applications/Firefox.app/Contents/MacOS/firefox"))
    found = next((str(c) for c in cands if c.is_file()), None) or shutil.which("firefox")
    if not found:
        sys.exit("Firefox isn't installed here (it's what the sign-in window opens in).")
    return found


def open_sign_in(profile: Path) -> None:
    profile.mkdir(parents=True, exist_ok=True)
    print("A Firefox window is opening on Google's sign-in page for YouTube.")
    print("Sign in, wait for YouTube to load, then close that window (this script waits for it).")
    # -wait-for-browser: on Windows firefox.exe is a launcher that exits at once without it
    subprocess.run([firefox(), "-no-remote", "-wait-for-browser", "-profile", str(profile), SIGN_IN])


def read_cookies(profile: Path) -> list:
    db = profile / "cookies.sqlite"
    if not db.is_file():
        sys.exit("No cookies in BigScreen's Firefox profile yet: run it without --no-browser and sign in.")
    with tempfile.TemporaryDirectory() as tmp:               # a copy: Firefox may still hold the file
        for name in ("cookies.sqlite", "cookies.sqlite-wal", "cookies.sqlite-shm"):
            if (profile / name).is_file():
                shutil.copy2(profile / name, Path(tmp) / name)
        con = sqlite3.connect(str(Path(tmp) / "cookies.sqlite"))
        try:
            rows = con.execute("SELECT host, path, isSecure, expiry, name, value FROM moz_cookies "
                               "WHERE originAttributes = ''").fetchall()
        finally:
            con.close()
    return [r for r in rows if any(r[0].lstrip(".") == d or r[0].endswith("." + d) for d in DOMAINS)]


def netscape(rows) -> str:
    """cookies.txt, as yt-dlp reads it."""
    out = ["# Netscape HTTP Cookie File", "# BigScreen hub's YouTube session (tools/youtube_signin.py)", ""]
    for host, path, secure, expiry, name, value in rows:
        expiry = int(expiry or 0)
        if expiry > 10 ** 11:                                  # newer Firefox keeps milliseconds
            expiry //= 1000
        out.append("\t".join([host, "TRUE" if host.startswith(".") else "FALSE", path or "/",
                              "TRUE" if secure else "FALSE", str(expiry), name, value]))
    return "\n".join(out) + "\n"


def upload(text: str, host: str) -> None:
    cmd = (f"umask 077; mkdir -p ~/bigscreen-state/secrets && cat > {REMOTE}.new && mv {REMOTE}.new {REMOTE}")
    r = subprocess.run(["ssh", host, cmd], input=text.encode("utf-8"), capture_output=True)
    if r.returncode:
        sys.exit(f"Couldn't copy it to the hub over ssh: {r.stderr.decode('utf-8', 'replace').strip()}")


def check(hub: str) -> dict:
    req = urllib.request.Request(hub + "/api/webcams/youtube/check", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--no-browser", action="store_true", help="send the session again without opening Firefox")
    ap.add_argument("--host", default=HOST, help=f"ssh to the hub (default {HOST})")
    ap.add_argument("--hub", default=HUB, help=f"the hub's control panel (default {HUB})")
    a = ap.parse_args()
    profile = profile_dir()
    if not a.no_browser:
        open_sign_in(profile)
        time.sleep(1.0)                                        # Firefox finishing its writes
    rows = read_cookies(profile)
    names = {r[4] for r in rows if r[0].endswith("youtube.com")}
    if not names & SIGNED_IN:
        sys.exit("That profile isn't signed in to YouTube (no session cookies). Run it again and sign in before "
                 "closing the window.")
    upload(netscape(rows), a.host)
    print(f"Sent the YouTube session ({len(rows)} cookies) to the hub. Asking it to try YouTube now…")
    try:
        r = check(a.hub)
    except Exception as e:                                     # the file is there; the hub uses it from the next try
        print(f"The hub didn't answer ({e}); it will use the session from its next YouTube lookup.")
        return
    if r.get("ok"):
        print(f"Signed in: OK. YouTube answered the hub (\"{r.get('title', '')[:60]}\").")
    else:
        print(f"YouTube still refused the hub: {r.get('error', '?')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
