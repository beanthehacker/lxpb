"""
refresh.py -- one refresh of the upcoming-levels state, run when someone opens
/levels and the stored state is from a previous hour.

    python levels_runtime/refresh.py [--force]      (api/levels.py spawns exactly this)

  1. take the lock (one refresh at a time; a crashed one frees itself);
  2. skip if the state is already from this hour (unless --force);
  3. history: the store's merged M5 bars, or on first use the committed seed;
  4. fetch the live tail from TradingView -- last ~5000 M5 and H1 bars -- and
     join it to the history with the repo's own loaders, as one more vintage
     (a same-vintage tail joins at offset 0; after a roll `_merge_vintages`
     re-anchors the history by an offset measured on the overlap);
  5. write the merged bars back, so the history keeps itself current and never
     needs the exports again;
  6. extend the level ledger by the new bars (its cache files live in the store),
     compute the page state (compute.py), store it.

Runs in its own interpreter every time so no module-level cache from a previous
hour can leak into this one. Only TradingView continuous ES1! bars are used
(see "continuous contracts only" in CLAUDE.md); no .scid data is touched.
"""
import argparse
import gzip
import json
import os
import sys
import tempfile
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bootstrap  # noqa: E402
bootstrap.setup()

import pandas as pd  # noqa: E402

import tv_feed as TV  # noqa: E402
import store as ST  # noqa: E402
from store import is_stale  # noqa: E402

WORK = os.environ.get("LEVELS_WORK_DIR") or os.path.join(tempfile.gettempdir(), "levels_work")
LC_FILES = ("m5_levels_continuous.parquet", "m5_levels_continuous.json",
            "m5_levels_continuous.state.pkl")


def _log(msg):
    print(f"[refresh {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _gz_csv(bars):
    out = bars.reset_index()
    out["time"] = (out["time"] - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)   # unit-independent (pandas 3 is not ns)
    return gzip.compress(out[["time", "open", "high", "low", "close"]].to_csv(index=False).encode(), 6)


def _do(store):
    os.makedirs(WORK, exist_ok=True)
    hist_path = os.path.join(WORK, "history_M5.csv")
    live_m5, live_h1 = os.path.join(WORK, "live_M5.csv"), os.path.join(WORK, "live_H1.csv")

    blob = store.get("m5_bars")
    if blob is None:
        _log("no stored history -- seeding from the committed export merge")
        with open(bootstrap.SEED_M5, "rb") as f:
            blob = f.read()
    with open(hist_path, "wb") as f:
        f.write(gzip.decompress(blob))

    m5_live, h1_live = TV.fetch_bars("5"), TV.fetch_bars("60")
    now = pd.Timestamp.now(tz="UTC")
    TV.write_export(TV.completed(m5_live, 5, now), live_m5)
    TV.write_export(TV.completed(h1_live, 60, now), live_h1)
    _log(f"fetched M5 {len(m5_live)} bars to {m5_live.index[-1]}, "
         f"H1 {len(h1_live)} bars to {h1_live.index[-1]}")

    import render_labels_report as R
    import lxpb_levels_cache as LC
    R.DISPLAY_M5_PATHS = [[hist_path], [live_m5]]
    R.DISPLAY_H1_PATHS = [live_h1]
    R._DISPLAY_H1_CACHE = R._DISPLAY_M5_CACHE = None
    m5 = R._display_m5()          # validated: one vintage per list, roll splices, H1 scale
    h1 = R._display_h1()
    store.put("m5_bars", _gz_csv(m5))
    _log(f"M5 series {m5.index[0]} -> {m5.index[-1]} ({len(m5):,} bars); "
         f"latest close {float(m5['close'].iloc[-1])}")

    LC.CACHE_DIR = os.path.join(WORK, "levels_cache")
    os.makedirs(LC.CACHE_DIR, exist_ok=True)
    for name in LC_FILES:
        data = store.get("lc/" + name)
        if data is not None:
            with open(os.path.join(LC.CACHE_DIR, name), "wb") as f:
                f.write(data)
    before = {n: _mtime(os.path.join(LC.CACHE_DIR, n)) for n in LC_FILES}
    ledger = LC.m5_levels(verbose=True, plain_p0=LC.PLAIN_P0_UNTRACKED)
    for name in LC_FILES:
        path = os.path.join(LC.CACHE_DIR, name)
        if _mtime(path) != before[name]:
            with open(path, "rb") as f:
                store.put("lc/" + name, f.read())

    import compute
    state = compute.compute_state(ledger, m5, h1)
    state["built_at"] = int(time.time())
    store.put("state", json.dumps(state, separators=(",", ":")).encode())
    _log(f"stored state: {len(state['rows'])} rows, {state['n_no_stop']} dropped for no valid stop")


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def refresh(store, force=False):
    """Returns 'done', 'fresh' (nothing to do) or 'busy' (another refresh runs)."""
    if not store.acquire():
        return "busy"
    try:
        if not force and not is_stale(store.updated_at("state")):
            return "fresh"
        try:
            _do(store)
            store.delete("error")
        except Exception as e:
            store.put("error", f"{type(e).__name__}: {e}"[:500].encode())
            raise
        return "done"
    finally:
        store.release()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    try:
        print(refresh(ST.default_store(), force=args.force))
    except Exception:
        traceback.print_exc()
        sys.exit(1)
