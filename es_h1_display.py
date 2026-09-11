"""The canonical continuous ES H1 series, for the subprojects outside lxpb-v2.

See "TradingView continuous series only" in lxpb-v2/CLAUDE.md for the rule this
implements. In short: H1 and M5 bars come from TradingView's own continuous
ES1! exports and from nothing else. `.scid` data is never resampled into H1 or
M5 bars, and where the exports stop, the series stops.

This module exists because `data/es-h1-continuous-backadjusted.csv` -- which
label-review and retest-vol-scalp used to read -- is retired. That file took a
frozen TradingView export as its historical base and *extended* it with
resampled front-month `.scid` bars, joining two vendors' feeds at an arbitrary
date. Its builder, `data/build_es_h1_continuous.py`, is retired with it.

lxpb-v2 has its own richer loader (`render_labels_report._display_h1`) which
additionally validates roll splices, checks the M5 series against this scale
and measures `.scid` offsets against it. That one is not reused here for the
same reason those subprojects reimplement their tick splicing standalone:
importing it drags in scidReader, the patterns-pure library and a chain of
report modules. The export list and the merge rule are identical; keep them in
step if either changes.

    import es_h1_display
    h1 = es_h1_display.load()   # naive-UTC index, open/high/low/close
"""
import os

import pandas as pd

import lxpb as L

_HERE = os.path.dirname(os.path.abspath(__file__))

# Oldest first; merged newest-wins so a later export supersedes an earlier one
# wherever they overlap. Must match render_labels_report.DISPLAY_H1_PATHS.
DISPLAY_H1_PATHS = [
    os.path.join(_HERE, "lxpb-v2", "data", "1jan2026-CME_MINI_ES1!, 60.csv"),
    os.path.join(_HERE, "lxpb-v2", "data", "24aug-CME_MINI_ES1!, 60.csv"),
    os.path.join(_HERE, "lxpb-v2", "data", "2sep-CME_MINI_ES1!, 60.csv"),
]

# Two exports of the same back-adjustment vintage agree to the tick on every
# shared bar. Allow a handful of single-bar revisions, but no more: a real
# vintage difference moves a whole contract segment at once, and merging two
# vintages would plant a step change in history that no roll explains.
_MIN_AGREE_SHARE = 0.99

_CACHE = None


def load():
    """The merged continuous H1 series, naive-UTC indexed like
    `lxpb.load_ohlc_data` returns, cached per process."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    frames = {p: L.load_ohlc_data(p) for p in DISPLAY_H1_PATHS if os.path.exists(p)}
    if not frames:
        raise RuntimeError(f"no display H1 exports exist: {DISPLAY_H1_PATHS}")
    items = list(frames.items())
    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            (pa, fa), (pb, fb) = items[a], items[b]
            common = fa.index.intersection(fb.index)
            if len(common) == 0:
                continue
            share = float((fa.loc[common, "close"].round(4) ==
                           fb.loc[common, "close"].round(4)).mean())
            if share < _MIN_AGREE_SHARE:
                raise RuntimeError(
                    f"{os.path.basename(pa)} and {os.path.basename(pb)} disagree on "
                    f"{(1 - share):.1%} of their {len(common)} shared bars -- they are "
                    "different back-adjustment vintages and cannot be merged. Re-export "
                    "both from the same anchor, or drop one.")
    merged = pd.concat(frames.values()) if len(frames) > 1 else next(iter(frames.values()))
    _CACHE = merged[~merged.index.duplicated(keep="last")].sort_index()
    return _CACHE
