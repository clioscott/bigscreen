"""Signing the hub in to YouTube: tools/youtube_signin.py reads BigScreen's Firefox profile and hands the session to
the hub, which passes it to yt-dlp (hub/webcams.py _yt_dlp) and says on the Overview when YouTube refuses it."""

import sqlite3
import types

from hub import webcams as wc
from tools import youtube_signin as ys


def _profile(tmp_path, rows):
    con = sqlite3.connect(str(tmp_path / "cookies.sqlite"))
    con.execute("CREATE TABLE moz_cookies (host TEXT, path TEXT, isSecure INT, expiry INT, name TEXT, value TEXT, "
                "originAttributes TEXT)")
    con.executemany("INSERT INTO moz_cookies VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    con.commit()
    con.close()
    return tmp_path


def test_the_profiles_youtube_and_google_cookies_become_a_cookies_txt(tmp_path):
    p = _profile(tmp_path, [
        (".youtube.com", "/", 1, 1_900_000_000, "SAPISID", "abc", ""),
        (".google.com", "/", 1, 1_900_000_000_000, "SID", "def", ""),            # (milliseconds, newer Firefox)
        ("accounts.google.com", "/", 1, 1_900_000_000, "LSID", "ghi", ""),
        (".example.com", "/", 0, 1_900_000_000, "x", "y", ""),                   # not YouTube's: left out
        (".youtube.com", "/", 1, 1_900_000_000, "PRIV", "z", "^privateBrowsingId=1"),   # a private window's
    ])
    rows = ys.read_cookies(p)
    assert sorted(r[4] for r in rows) == ["LSID", "SAPISID", "SID"]
    txt = ys.netscape(rows)
    assert txt.startswith("# Netscape HTTP Cookie File")
    lines = [l.split("\t") for l in txt.splitlines() if l and not l.startswith("#")]
    sid = next(l for l in lines if l[5] == "SID")
    assert sid == [".google.com", "TRUE", "/", "TRUE", "1900000000", "SID", "def"]
    lsid = next(l for l in lines if l[5] == "LSID")
    assert lsid[1] == "FALSE"                                                   # a host cookie: not for subdomains


def test_yt_dlp_gets_a_copy_of_the_session_when_there_is_one(monkeypatch, tmp_path):
    jar = tmp_path / "youtube-cookies.txt"
    seen = {}

    def run(cmd, **k):
        seen["cmd"] = cmd
        if "--cookies" in cmd:
            path = cmd[cmd.index("--cookies") + 1]
            seen["copy"] = open(path).read()
            seen["path"] = path
        return types.SimpleNamespace(returncode=0, stdout="{}", stderr="")
    monkeypatch.setattr(wc, "cookies_path", lambda: jar)
    monkeypatch.setattr(wc.subprocess, "run", run)
    wc._yt_dlp("-J", "u")
    assert "--cookies" not in seen["cmd"]                                       # not signed in: none
    jar.write_text("# Netscape HTTP Cookie File\n")
    wc._yt_dlp("-J", "u")
    assert seen["copy"].startswith("# Netscape") and seen["path"] != str(jar)   # a copy (yt-dlp writes it back)
    import os
    assert not os.path.exists(seen["path"])                                     # and gone afterwards


def test_signing_in_lets_the_hub_try_youtube_again_at_once(monkeypatch, tmp_path):
    monkeypatch.setattr(wc, "cookies_path", lambda: tmp_path / "youtube-cookies.txt")
    wc._yt_block.update(until=wc.time.time() + 600, why="Sign in to confirm you're not a bot")
    assert wc.youtube_state()["blocked_until"] is not None
    monkeypatch.setattr(wc, "lookup", lambda url, fresh=False: {"title": "Shibuya", "formats": [1, 2]})
    r = wc.youtube_check("https://www.youtube.com/watch?v=abcdefghijk")
    assert r["ok"] and r["title"] == "Shibuya" and r["blocked_until"] is None
