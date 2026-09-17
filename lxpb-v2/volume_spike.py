"""
volume_spike.py
=================
Flags an "unusual volume spike" in the SAME-SIDE 1s tape around a trade's
entry touch/fill instant -- BidVolume for an LHPB (long) fill, AskVolume for
an LLPB (short) one, the aggressor side that would be running stops through
the level.

MEASURED, not a flat cutoff: a single absolute number means something very
different overnight than during RTH, so instead each fill gets its own
local yardstick. Two windows, both centered on the fill:

    CORE window (+/-`core_window_seconds`, default 30s) -- every second in
    here is a spike CANDIDATE.

    BASELINE window (+/-`baseline_window_seconds`, default 600s/10min),
    WITH the core window cut back out of it -- this trade's own "normal"
    same-side tape right around the same fill, so a busy RTH session and a
    quiet overnight one each get compared against their own recent selves,
    not a shared constant.

A candidate second QUALIFIES when it is both at least `ratio_threshold`
times the baseline's mean same-side volume per second (default 8x) AND
strictly more than `min_peak` contracts outright (default 50) -- the ratio
alone would flag a 3-contract second against a near-zero overnight baseline
as "unusual", which it is statistically but not practically.

MULTIPLE seconds in the core window can qualify (a stop run is rarely one
clean print). Every qualifying second's offset from the fill (whole
seconds, negative = before, positive = after, 0 = the exact fill second)
is kept, not just the single busiest one -- see `format_offsets`, which
collapses a consecutive run into a range (`+12s-+15s`) and separates
non-consecutive ones with commas (`+12s,+17s`).

Example: trade 4122 (2026 ss_m5_confl2, LLPB) filled at 2026-08-31 01:01:05
PT; the tape prints AskVolume 748 at 01:01:18 PT (offset +12), ~400x the
+/-10min baseline mean -- an ask-side stop run right through the level.

Using it
--------
`detect(touch_time, level_type)` returns (spiked, metrics). metrics carries
every qualifying second's offset (`offsets`) plus the single busiest one's
own value/time/offset for the headline description, even when spiked is
False (an empty `offsets` list) so a near-miss can still be reviewed in a
row's tooltip; metrics itself is None only when no tick data covers the
window at all.
"""
import pandas as pd

import render_labels_report as R  # noqa: E402

CORE_WINDOW_SECONDS_DEFAULT = 30
BASELINE_WINDOW_SECONDS_DEFAULT = 600
RATIO_THRESHOLD_DEFAULT = 8.0
MIN_PEAK_CONTRACTS_DEFAULT = 50
# Baseline mean is floored to this many contracts/sec before dividing, so a
# near-empty baseline (overnight, a few contracts total) can't turn an
# ordinary handful of contracts into an arbitrarily huge ratio.
BASELINE_FLOOR_DEFAULT = 0.5


def _as_utc(ts):
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def format_offsets(offsets):
    """Sorted signed-second offsets -> a compact label: a consecutive run
    collapses into `<start>-<end>` (e.g. `+12s-+15s`), and separate runs are
    comma-joined (e.g. `+12s,+17s`). A single offset is just `<n>s`; 0 is
    always written bare (`0s`), never `+0s`."""
    def lbl(n):
        return "0s" if n == 0 else f"{n:+d}s"
    offs = sorted(offsets)
    runs = []
    start = prev = offs[0]
    for o in offs[1:]:
        if o == prev + 1:
            prev = o
            continue
        runs.append((start, prev))
        start = prev = o
    runs.append((start, prev))
    return ",".join(lbl(a) if a == b else f"{lbl(a)}-{lbl(b)}" for a, b in runs)


def _core_and_baseline(touch_time, level_type, core_window_seconds, baseline_window_seconds):
    """(side, core_frame, baseline_mean) or None if no tick data covers the
    (wider) baseline window at all."""
    side = "BidVolume" if level_type == "LHPB" else "AskVolume"
    core_lo = touch_time - pd.Timedelta(seconds=core_window_seconds)
    core_hi = touch_time + pd.Timedelta(seconds=core_window_seconds)
    base_lo = touch_time - pd.Timedelta(seconds=baseline_window_seconds)
    base_hi = touch_time + pd.Timedelta(seconds=baseline_window_seconds)
    ticks = R._ticks_for_window(base_lo, base_hi)
    if ticks is None or ticks.empty:
        return None
    bars = R._resample_1s(ticks)
    bars = bars.loc[(bars.index >= base_lo) & (bars.index <= base_hi)]
    if bars.empty:
        return None
    core = bars.loc[(bars.index >= core_lo) & (bars.index <= core_hi)]
    if core.empty:
        return None
    baseline = bars.loc[(bars.index < core_lo) | (bars.index > core_hi)]
    baseline_mean = float(baseline[side].mean()) if not baseline.empty else 0.0
    return side, core, baseline_mean


def detect(touch_time, level_type,
           core_window_seconds=CORE_WINDOW_SECONDS_DEFAULT,
           baseline_window_seconds=BASELINE_WINDOW_SECONDS_DEFAULT,
           ratio_threshold=RATIO_THRESHOLD_DEFAULT,
           min_peak=MIN_PEAK_CONTRACTS_DEFAULT,
           baseline_floor=BASELINE_FLOOR_DEFAULT):
    """(spiked, metrics) for one fill. metrics is None only when the
    baseline window has no tick data at all."""
    touch_time = _as_utc(touch_time)
    found = _core_and_baseline(touch_time, level_type, core_window_seconds,
                               baseline_window_seconds)
    if found is None:
        return False, None
    side, core, baseline_mean = found
    denom = max(baseline_mean, baseline_floor)
    vols = core[side]
    qualifies = (vols > min_peak) & (vols / denom >= ratio_threshold)
    qualifying = core.loc[qualifies]
    # The busiest core second overall -- always the single most informative
    # one, and (since qualifying is a fixed volume/ratio cutoff applied to
    # every second against the SAME baseline) it is necessarily itself
    # qualifying whenever anything qualifies at all.
    peak_pos = int(vols.to_numpy().argmax())
    peak_value = float(vols.iloc[peak_pos])
    peak_time = core.index[peak_pos]
    offsets = sorted(int(round((t - touch_time).total_seconds())) for t in qualifying.index)
    metrics = {
        "side": side, "peak_value": peak_value, "peak_time": peak_time,
        "offset_seconds": int(round((peak_time - touch_time).total_seconds())),
        "offsets": offsets, "offsets_label": format_offsets(offsets) if offsets else "",
        "baseline_mean": baseline_mean, "ratio": peak_value / denom,
        "core_window_seconds": core_window_seconds,
        "baseline_window_seconds": baseline_window_seconds,
        "ratio_threshold": ratio_threshold, "min_peak": min_peak,
    }
    return bool(offsets), metrics


def describe(metrics):
    """One-line human summary of a metrics dict, for a row tooltip."""
    if not metrics:
        return "no tape in the volume-spike window"
    pt = pd.Timestamp(metrics["peak_time"]).tz_convert("America/Los_Angeles")
    off = metrics["offset_seconds"]
    off_str = "0s (exactly at the fill)" if off == 0 else f"{off:+d}s from the fill"
    n = len(metrics.get("offsets") or [])
    extra = (f"; {n} qualifying second(s) at {metrics['offsets_label']}" if n > 1 else "")
    return (f"peak {metrics['side']} {metrics['peak_value']:.0f} at "
            f"{pt.strftime('%Y-%m-%d %H:%M:%S')} PT ({off_str}), "
            f"{metrics['ratio']:.1f}x the +/-{metrics['baseline_window_seconds']:g}s baseline "
            f"mean ({metrics['baseline_mean']:.1f}/s), threshold {metrics['ratio_threshold']:g}x "
            f"/ >{metrics['min_peak']:.0f} contracts{extra}")
