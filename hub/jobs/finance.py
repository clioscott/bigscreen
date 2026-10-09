"""Stock + crypto quotes via yfinance — one fetch per process.

v1 ran this in a single process for months and it grew to 8.7 GB (yfinance/curl
sessions leak). Running each fetch in a fresh process makes that impossible.

Output data/finance.json keeps the v1 shape:
    {SYMBOL: {price, change, prev_close, day_high, day_low, history:[{c}], min_v, max_v, rng,
              chart_type: "stock"|"crypto", _display: {title, width, icon}, _updated: t}}
Symbols that fail this run keep their previous values (with their old `_updated`),
so one bad quote never blanks the ticker.
"""

import math
import time
import warnings
from datetime import datetime

from hub import paths
from hub.jobs import _harness
from hub.util import atomic_write_json, read_json

CHART_POINTS = 85          # 170 px chart at 2 px per point
MARKET_MINUTES = 390       # 9:30–16:00 ET
CRYPTO_WINDOW = 288        # 24 h of 5-minute bars


def is_crypto(sym):
    return "-" in sym and ("USD" in sym or "CAD" in sym)


def normalize_symbols(raw):
    out = []
    for item in raw or []:
        if isinstance(item, str):
            item = {"symbol": item}
        sym = str(item.get("symbol", "")).strip()
        if not sym or item.get("enabled", True) is False:
            continue
        out.append({"symbol": sym, "title": item.get("title") or sym,
                    "width": item.get("width"), "icon": item.get("icon") or f"{sym}.png"})
    return out


def _finite(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _ticker_frame(hist, sym, multi):
    import pandas as pd
    if not multi:
        return hist
    if isinstance(hist.columns, pd.MultiIndex) and sym in hist.columns.levels[0]:
        return hist[sym]
    return None


def fetch_stocks(yf, symbols):
    import pandas as pd
    import pytz
    out = {}
    tickers = yf.Tickers(" ".join(symbols))
    for s in symbols:
        try:
            fi = tickers.tickers[s].fast_info
            price, prev = _finite(fi.last_price), _finite(fi.previous_close)
            if price is not None and prev:
                out[s] = {"price": price, "prev_close": prev, "change": (price - prev) / prev * 100.0}
                hi, lo = _finite(fi.day_high), _finite(fi.day_low)
                if hi is not None and lo is not None:
                    out[s]["day_high"], out[s]["day_low"] = hi, lo
        except Exception as e:
            print(f"quote {s}: {e}")

    hist = yf.download(symbols, period="1d", interval="5m", group_by="ticker", progress=False, threads=False)
    et = pytz.timezone("America/New_York")
    for s in symbols:
        try:
            df = _ticker_frame(hist, s, len(symbols) > 1)
            if df is None:
                continue
            closes = df["Close"].dropna()
            if closes.empty:
                continue
            if not isinstance(closes.index, pd.DatetimeIndex):
                closes.index = pd.to_datetime(closes.index)
            vals = [float(v) for v in closes.values.flatten()]
            min_v, max_v = min(vals), max(vals)
            rng = (max_v - min_v) or 1.0
            first = closes.index[0]
            first = et.localize(first) if first.tzinfo is None else first.astimezone(et)
            market_open = first.replace(hour=9, minute=30, second=0, microsecond=0)
            # time-based placement: the chart fills left→right as the trading day progresses
            slots = [None] * CHART_POINTS
            last_pos = 0
            for ts, v in zip(closes.index, vals):
                ts = et.localize(ts) if ts.tzinfo is None else ts.astimezone(et)
                minutes = max(0.0, (ts - market_open).total_seconds() / 60.0)
                pos = min(CHART_POINTS - 1, int(minutes / MARKET_MINUTES * CHART_POINTS))
                slots[pos] = {"c": (v - min_v) / rng}
                last_pos = max(last_pos, pos)
            prev = None
            for i in range(last_pos + 1):          # forward-fill gaps
                if slots[i] is not None:
                    prev = slots[i]
                elif prev is not None:
                    slots[i] = dict(prev)
            entry = out.setdefault(s, {})
            entry.update({"history": [p for p in slots[: last_pos + 1] if p is not None],
                          "min_v": min_v, "max_v": max_v, "rng": rng, "chart_type": "stock"})
            if "day_high" not in entry:
                entry["day_high"] = float(df["High"].max())
                entry["day_low"] = float(df["Low"].min())
        except Exception as e:
            print(f"history {s}: {e}")
    return out


def fetch_crypto(yf, symbols):
    out = {}
    hist = yf.download(symbols, period="2d", interval="5m", group_by="ticker", progress=False, threads=False)
    tickers = yf.Tickers(" ".join(symbols))
    for s in symbols:
        try:
            entry = {}
            try:
                p = _finite(tickers.tickers[s].fast_info.last_price)
                if p is not None:
                    entry["price"] = p
            except Exception:
                pass
            df = _ticker_frame(hist, s, len(symbols) > 1)
            if df is not None and len(df):
                df = df.dropna(subset=["Close"]).tail(CRYPTO_WINDOW)
                vals = [float(v) for v in df["Close"].values.flatten()]
                if vals:
                    ago, now = vals[0], vals[-1]
                    highs = [float(v) for v in df["High"].dropna().values.flatten()]
                    lows = [float(v) for v in df["Low"].dropna().values.flatten()]
                    min_v, max_v = min(vals), max(vals)
                    rng = (max_v - min_v) or 1.0
                    step = len(vals) / float(CHART_POINTS)
                    entry.update({
                        "change": (now - ago) / ago * 100.0 if ago else 0.0,   # rolling 24 h
                        "prev_close": ago,
                        "day_high": max(highs) if highs else now,
                        "day_low": min(lows) if lows else now,
                        "history": [{"c": (vals[int(i * step)] - min_v) / rng}
                                    for i in range(CHART_POINTS) if int(i * step) < len(vals)],
                        "min_v": min_v, "max_v": max_v, "rng": rng, "chart_type": "crypto",
                    })
                    entry.setdefault("price", now)
            if entry:
                out[s] = entry
        except Exception as e:
            print(f"crypto {s}: {e}")
    return out


def run(cfg):
    warnings.simplefilter("ignore", FutureWarning)
    import yfinance as yf

    tickers = normalize_symbols(cfg["modules"].get("finance", {}).get("symbols", []))
    symbols = [t["symbol"] for t in tickers]
    crypto = [s for s in symbols if is_crypto(s)]
    stocks = [s for s in symbols if s not in crypto]

    fresh = {}
    errors = []
    for label, fn, group in (("stocks", fetch_stocks, stocks), ("crypto", fetch_crypto, crypto)):
        if not group:
            continue
        try:
            fresh.update(fn(yf, group))
        except Exception as e:
            errors.append(f"{label}: {type(e).__name__}: {e}")

    now = time.time()
    previous = read_json(paths.DATA_DIR / "finance.json", {}) or {}
    data, stale = {}, []
    for t in tickers:                       # dict order == configured display order
        s = t["symbol"]
        entry = dict(previous.get(s, {}))
        if s in fresh and "price" in fresh[s]:
            entry.update(fresh[s])
            entry["_updated"] = now
        else:
            stale.append(s)
        if entry:
            entry["_display"] = {"title": t["title"], "width": t["width"], "icon": t["icon"]}
            data[s] = entry
    atomic_write_json(paths.DATA_DIR / "finance.json", data)

    ok_count = len(symbols) - len(stale)
    return {
        "ok": ok_count > 0,
        "message": f"{ok_count}/{len(symbols)} symbols updated"
                   + (f"; stale: {', '.join(stale[:6])}{'…' if len(stale) > 6 else ''}" if stale else ""),
        "stale": stale,
        "errors": errors,
        "at": datetime.now().isoformat(timespec="seconds"),
        "files": ["finance.json"],
    }


if __name__ == "__main__":
    _harness.main(run)
