"""
tv_feed.py -- pull TradingView's own continuous ES1! bars over its chart
websocket, for the M5 levels dashboard's live tail.

Why this runs in Python and not in the browser: TradingView's data socket
only accepts connections carrying an `Origin: https://www.tradingview.com`
header, which a page served from anywhere else cannot set (and the site's
REST endpoints send no CORS headers). A local process can. The bars are the
same continuous, back-adjusted ES1! series the exports in `data/` are made
of -- this is a TradingView source, so it stays inside the repo's
"TradingView continuous series only" convention (lxpb-v2/CLAUDE.md).

Only the last `n_bars` (TradingView caps an anonymous session at ~5000) are
fetched; the deep history stays in the exports. `live_export_path` writes the
fetch in the same epoch-seconds OHLC CSV format an export has, so it can join
`DISPLAY_M5_PATHS` / `DISPLAY_H1_PATHS` as one more export (see build.py).
"""
import json
import os
import random
import re
import string
import time

import pandas as pd
import websocket

SYMBOL = "CME_MINI:ES1!"
WS_URL = "wss://data.tradingview.com/socket.io/websocket"
ORIGIN = "https://www.tradingview.com"
MAX_BARS = 5000
TIMEOUT_S = 40

_SPLIT = re.compile(r"~m~\d+~m~")


def _sid(prefix):
    return prefix + "".join(random.choice(string.ascii_lowercase) for _ in range(12))


def _frame(obj):
    s = obj if isinstance(obj, str) else json.dumps(obj, separators=(",", ":"))
    return f"~m~{len(s)}~m~{s}"


def _call(func, params):
    return _frame({"m": func, "p": params})


def fetch_bars(timeframe, n_bars=MAX_BARS, symbol=SYMBOL):
    """Last `n_bars` bars of `symbol` at `timeframe` ("5", "60") as a
    UTC-indexed OHLC frame, the still-forming bar included (the caller
    decides what to do with it -- see `completed`).

    Extended-hours session and TradingView's default futures back-adjustment,
    which is what the repo's exports were made with. Raises RuntimeError on a
    symbol/series error or when the series never completes."""
    ws = websocket.create_connection(WS_URL, header=[f"Origin: {ORIGIN}"], timeout=TIMEOUT_S)
    try:
        cs = _sid("cs_")
        ws.send(_call("set_auth_token", ["unauthorized_user_token"]))
        ws.send(_call("chart_create_session", [cs, ""]))
        ws.send(_call("resolve_symbol", [cs, "sds_sym_1", "=" + json.dumps(
            {"symbol": symbol, "adjustment": "splits", "backadjustment": "default",
             "session": "extended"})]))
        ws.send(_call("create_series", [cs, "sds_1", "s1", "sds_sym_1", str(timeframe),
                                        int(n_bars), ""]))
        bars, done, deadline = None, False, time.time() + TIMEOUT_S
        while not done and time.time() < deadline:
            raw = ws.recv()
            for part in filter(None, _SPLIT.split(raw)):
                if part.startswith("~h~"):          # heartbeat: echo it back
                    ws.send(_frame(part))
                    continue
                try:
                    msg = json.loads(part)
                except ValueError:
                    continue
                kind = msg.get("m")
                if kind in ("symbol_error", "critical_error", "series_error"):
                    raise RuntimeError(f"TradingView {kind}: {msg.get('p')}")
                if kind == "timescale_update":
                    series = msg["p"][1].get("sds_1")
                    if series:
                        bars = series["s"]
                elif kind == "series_completed":
                    done = True
        if not done or not bars:
            raise RuntimeError("TradingView series did not complete in time")
    finally:
        ws.close()
    df = pd.DataFrame([b["v"][:5] for b in bars],
                      columns=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df.set_index("time").sort_index()


def completed(bars, period_minutes, now=None):
    """`bars` without the bar still forming at `now` (a bar labelled T is
    complete once T + period has passed)."""
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    return bars[bars.index + pd.Timedelta(minutes=period_minutes) <= now]


def write_export(bars, path):
    """Write `bars` as an export-format CSV (epoch seconds + OHLC), atomically
    so a reader never sees a half-written file."""
    out = bars.reset_index()
    out["time"] = (out["time"] - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)   # unit-independent (pandas 3 is not ns)
    tmp = path + ".tmp"
    out[["time", "open", "high", "low", "close"]].to_csv(tmp, index=False)
    os.replace(tmp, path)
