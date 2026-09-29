"""Nightly refresh: bring the level ledgers up to the session that just closed.

    python nightly_refresh.py [--no-commit]

Run by Windows Task Scheduler every weekday after the ES session changes
(nightly_refresh.bat, task "lxpb nightly refresh"; see "Nightly refresh" in
CLAUDE.md). Nothing here reads data of its own -- it loads the display series
the way every report does, so Sierra Chart's new ticks reach the ledgers through
the one sanctioned path, `_extend_with_scid`, with all of its checks:

  1. Sierra Chart freshness: the front month's last recorded tick must reach
     the last session close, else the ledgers would silently stop short.
  2. Roll status: within a week of the next roll, or past it, ask for a
     post-roll TradingView export (a Windows pop-up as well as the log).
  3. H1 and M5 level ledgers: reconciled (normally the cheap `extend` route).
  4. Commit the refreshed caches, only when run from a clean `main` checkout
     with nothing else staged. Never pushed.

Exit code 0 = refreshed; 1 = something needs attention (see the log).
"""
import argparse
import os
import subprocess
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import pandas as pd  # noqa: E402

PT = "America/Los_Angeles"
SESSION_CLOSE_PT = 14            # 14:00 PT, Mon-Fri
TICK_STALE_SLACK = pd.Timedelta(minutes=15)
CACHE_PATHS = ("lxpb-v2/data/levels_cache", "lxpb-v2/data/scid_offsets.json")


def log(msg):
    print(f"[{pd.Timestamp.now(tz=PT):%Y-%m-%d %H:%M:%S} PT] {msg}", flush=True)


def popup(msg):
    """Best effort: a Windows message box, so an ask doesn't sit unread in a log."""
    try:
        subprocess.Popen(["msg", os.environ.get("USERNAME", "*"), "/TIME:0", f"lxpb nightly: {msg}"])
    except OSError:
        pass


def last_session_close(now_pt):
    """The most recent weekday 14:00 PT at or before now (holidays not excluded)."""
    t = now_pt.normalize() + pd.Timedelta(hours=SESSION_CLOSE_PT)
    if t > now_pt:
        t -= pd.Timedelta(days=1)
    while t.weekday() >= 5:
        t -= pd.Timedelta(days=1)
    return t


def git(*args):
    return subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True)


def commit_caches(last_bar_pt):
    if git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != "main":
        log("caches not committed: this checkout is not on main")
        return
    gitdir = git("rev-parse", "--git-dir").stdout.strip()
    if any(os.path.exists(os.path.join(HERE, gitdir, f))
           for f in ("MERGE_HEAD", "REBASE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD")):
        log("caches not committed: a merge/rebase is in progress")
        return
    if git("diff", "--cached", "--quiet").returncode != 0:
        log("caches not committed: other changes are already staged")
        return
    top = git("rev-parse", "--show-toplevel").stdout.strip()
    paths = [os.path.join(top, p) for p in CACHE_PATHS]
    git("add", "--", *paths)
    if git("diff", "--cached", "--quiet").returncode == 0:
        log("caches unchanged, nothing to commit")
        return
    r = git("commit", "-m", f"nightly: level caches through {last_bar_pt:%Y-%m-%d %H:%M} PT")
    log("committed refreshed caches" if r.returncode == 0 else f"commit failed: {r.stderr.strip()}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-commit", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    attention = []
    log("nightly refresh starting")

    import render_labels_report as R
    import lxpb_levels_cache as LC

    now = pd.Timestamp.now(tz="UTC")
    i = R._contract_index_for(now)
    sym = R.CONTRACTS[i][0]

    # CONTRACTS ends at the newest contract an export has confirmed, so after its
    # roll `sym` is no longer front month: its ticks are only wanted up to the roll.
    roll = R._own_roll()[i]
    rolled = roll is not None and now >= roll

    # 1. Sierra Chart freshness
    close = last_session_close(now.tz_convert(PT))
    if rolled:
        close = min(close, roll.tz_convert(PT))
    bounds = R._scid_bounds(sym)
    if bounds is None:
        attention.append(f"no readable {sym} tick file in {R.SCID_DIR}")
    else:
        last_tick = bounds[1].tz_convert(PT)
        log(f"{sym}: last tick {last_tick:%a %Y-%m-%d %H:%M:%S} PT; last session close {close:%a %m-%d %H:%M} PT")
        if last_tick < close - TICK_STALE_SLACK:
            attention.append(f"Sierra Chart has {sym} ticks only to {last_tick:%a %m-%d %H:%M} PT but the "
                             f"session closed {close:%a %m-%d %H:%M} PT -- open Sierra Chart, let it "
                             "download the gap, then rerun nightly_refresh.bat (a market holiday "
                             "also triggers this)")

    # 2. roll status
    if rolled:
        attention.append(f"{sym} rolled {roll.tz_convert(PT):%a %Y-%m-%d %H:%M} PT and no post-roll "
                         "TradingView export has been added -- H1/M5 bars stop at the roll. Export fresh "
                         "ES1! H1 and M5 files and follow the rollover checklist in lxpb-v2/CLAUDE.md")
    elif roll is not None and roll - now <= R._SCID_EXT_ROLL_WARN:
        attention.append(f"{sym} rolls {roll.tz_convert(PT):%a %Y-%m-%d %H:%M} PT -- after it, export "
                         "fresh post-roll TradingView ES1! H1 and M5 files (rollover checklist in "
                         "lxpb-v2/CLAUDE.md)")

    # 3. level ledgers (reconcile: extend / reanchor / rebuild as needed)
    h1 = LC.h1_levels(plain_p0=LC.PLAIN_P0_TRACKED, verbose=True)
    m5 = LC.m5_levels(plain_p0=LC.PLAIN_P0_TRACKED, verbose=True)
    h1_last, m5_last = R._display_h1().index[-1], R._display_m5().index[-1]
    log(f"H1 ledger {len(h1):,} levels, bars to {h1_last.tz_convert(PT):%a %m-%d %H:%M} PT; "
        f"M5 ledger {len(m5):,} levels, bars to {m5_last.tz_convert(PT):%a %m-%d %H:%M} PT")

    # 4. commit
    if not a.no_commit:
        commit_caches(m5_last.tz_convert(PT))

    for msg in attention:
        log("ATTENTION: " + msg)
        popup(msg)
    log(f"done in {time.time() - t0:.0f}s" + (f", {len(attention)} item(s) need attention" if attention else ""))
    return 1 if attention else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log("FAILED:\n" + traceback.format_exc())
        popup("refresh FAILED -- see lxpb-v2/data/nightly_refresh.log")
        sys.exit(2)
