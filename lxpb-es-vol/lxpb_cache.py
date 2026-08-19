"""
Disk cache for the two expensive, purely-deterministic LXPB pipeline
stages so repeated runs (e.g. re-rendering the dashboard, trying
different N_TICKS/STACK_THRESHOLD, scanning the same session twice)
never recompute them:

  1. build_h1_snapshots(H1_CSV)       -- ~100s over the full H1 history
     (2015-present), depends only on the H1 CSV file's contents and the
     MIN_HOURS_BEFORE_RETEST retest rule.
  2. build_level_intervals(df_1s, ...) -- the 1s-resolution consumption
     scan, depends on both the H1 CSV and the specific 1s CSV being
     scanned.

Cache keys are derived from each input file's (path, mtime, size) --
cheap to compute (no need to hash multi-hundred-MB file contents) and
correct for this workflow: any edit to a CSV changes its mtime, which
invalidates the cache automatically. Cached objects are pickled into
`.cache/` next to this file.
"""
import os
import pickle
import hashlib

import lxpb_confluence as C

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(_HERE, ".cache")


def _file_fingerprint(path):
    st = os.stat(path)
    return (os.path.abspath(path), st.st_mtime_ns, st.st_size)


def _cache_key(*parts):
    h = hashlib.sha1()
    for p in parts:
        h.update(repr(p).encode("utf-8"))
    return h.hexdigest()[:20]


def _load(cache_path):
    if os.path.exists(cache_path):
        with open(cache_path, "rb") as f:
            return pickle.load(f)
    return None


def _save(cache_path, obj):
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp_path = cache_path + ".tmp"
    with open(tmp_path, "wb") as f:
        pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp_path, cache_path)


def get_h1_snapshots(h1_csv_path, verbose=True):
    """Cached build_h1_snapshots. Returns (retests_df, snapshots, bar_times)."""
    key = _cache_key("h1_snapshots", _file_fingerprint(h1_csv_path), C.MIN_HOURS_BEFORE_RETEST)
    cache_path = os.path.join(CACHE_DIR, f"{key}.pkl")
    cached = _load(cache_path)
    if cached is not None:
        if verbose:
            print(f"  [cache hit] H1 LXPB snapshots ({cache_path})")
        return cached
    if verbose:
        print(f"  [cache miss] Building H1 LXPB snapshots from {h1_csv_path} ...")
    result = C.build_h1_snapshots(h1_csv_path)
    _save(cache_path, result)
    return result


def get_level_intervals(df_1s, csv_1s_path, h1_csv_path, verbose=True):
    """
    Cached build_level_intervals. `csv_1s_path` is used only for its file
    fingerprint (cache key) -- `df_1s` (already-loaded DataFrame) is what
    actually gets scanned on a cache miss.

    Returns (intervals_df, retests_df, snapshots, bar_times) -- the H1
    snapshot outputs are returned too since callers need `bar_times` for
    `_window_start_ts` burst-anchoring.
    """
    retests_df, snapshots, bar_times = get_h1_snapshots(h1_csv_path, verbose=verbose)

    key = _cache_key(
        "level_intervals",
        _file_fingerprint(csv_1s_path),
        _file_fingerprint(h1_csv_path),
        C.MIN_HOURS_BEFORE_RETEST,
    )
    cache_path = os.path.join(CACHE_DIR, f"{key}.pkl")
    cached = _load(cache_path)
    if cached is not None:
        if verbose:
            print(f"  [cache hit] 1s level-consumption intervals ({cache_path})")
        return cached, retests_df, snapshots, bar_times

    if verbose:
        print(f"  [cache miss] Building 1s level-consumption intervals for {csv_1s_path} ...")
    intervals_df = C.build_level_intervals(df_1s, snapshots, bar_times)
    _save(cache_path, intervals_df)
    return intervals_df, retests_df, snapshots, bar_times


def clear_cache():
    """Remove all cached pickles (use after changing H1/1s source data in
    a way that doesn't bump mtime, or after changing pipeline constants
    like MIN_HOURS_BEFORE_RETEST inside lxpb_confluence.py itself)."""
    if not os.path.isdir(CACHE_DIR):
        return
    for name in os.listdir(CACHE_DIR):
        os.remove(os.path.join(CACHE_DIR, name))
