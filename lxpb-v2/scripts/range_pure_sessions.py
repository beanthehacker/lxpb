"""Per-session PNG charts of patterns_pure/find_trading_range (ES M5, PT).

Orange = confirmed range (start -> end), dotted line at its confirm bar.
Grey dashed = candidate that never confirmed (window as last seen).
Sessions run 15:00 PT the day before -> 13:00 PT; 4 per PNG.

    python range_pure_sessions.py <bars.csv> <out_dir>   (writes _ranges_N.png)
"""
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Rectangle

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "patterns_pure"))
from find_trading_range import find_trading_range  # noqa: E402

PT = "America/Los_Angeles"
bars = pd.read_csv(sys.argv[1], index_col=0, parse_dates=True)
bars.index = pd.DatetimeIndex(bars.index).tz_convert(PT)
out_dir = sys.argv[2]
res = find_trading_range(bars, return_all=True)
ranges, cands = res["ranges"], res["candidates"]
dropped = cands[cands.outcome == "dropped"]

day = (bars.index + pd.Timedelta(hours=9)).normalize()
sessions = [g for _, g in bars.groupby(day) if len(g) > 20]
per_png = 4
for k in range(0, len(sessions), per_png):
    chunk = sessions[k:k + per_png]
    fig, axes = plt.subplots(len(chunk), 1, figsize=(20, 5.2 * len(chunk)), facecolor="black")
    axes = [axes] if len(chunk) == 1 else axes
    for ax, b in zip(axes, chunk):
        ax.set_facecolor("black")
        for i, (o, h, l, c) in enumerate(b[["Open", "High", "Low", "Close"]].itertuples(index=False)):
            ax.vlines(i, l, h, color="#bbb", lw=0.7)
            ax.add_patch(Rectangle((i - 0.3, min(o, c)), 0.6, max(abs(c - o), 0.05),
                                   color="#ddd" if c >= o else "#777"))
        t0, t1 = b.index[0], b.index[-1]

        def xs(s, e):
            return (int(b.index.searchsorted(s)),
                    int(b.index.searchsorted(e, side="right")) - 1)

        for _, c in dropped.iterrows():
            if c.last_seen < t0 or c.start_time > t1:
                continue
            x0, x1 = xs(c.start_time, c.last_seen)
            ax.add_patch(Rectangle((x0 - 0.5, c.low), x1 - x0 + 1, c.high - c.low, fill=False,
                                   ec="#888", lw=1, ls="--"))
        for _, r in ranges.iterrows():
            if r.end_time < t0 or r.start_time > t1:
                continue
            x0, x1 = xs(r.start_time, r.end_time)
            ax.add_patch(Rectangle((x0 - 0.5, r.low), x1 - x0 + 1, r.high - r.low,
                                   fc=(1, 0.69, 0, 0.12), ec="#ffb000", lw=2))
            if t0 <= r.confirm_time <= t1:
                xc = int(b.index.searchsorted(r.confirm_time))
                ax.vlines(xc, r.low, r.high, color="#ffb000", lw=1.2, ls=":")
            merged = f" (+{r.merged} merged)" if r.merged else ""
            ax.text(max(x0, 0), r.high + 0.8,
                    f"#{r.id} {r.start_time:%a %H:%M}-{r.end_time:%a %H:%M}  {r.low:g}-{r.high:g}"
                    f"  confirmed {r.confirm_time:%H:%M}{merged}", color="#ffb000", fontsize=10)
        ticks = [i for i, t in enumerate(b.index) if t.minute == 0 and t.hour % 2 == 0]
        ax.set_xticks(ticks, [f"{b.index[i]:%H:%M}" for i in ticks], color="#ccc", fontsize=8)
        ax.tick_params(axis="y", colors="#ccc", labelsize=8)
        ax.set_xlim(-1, len(b))
        ax.set_title(f"ES M5 PT, session {t1:%a %b %d}  ({t0:%a %H:%M} - {t1:%a %H:%M})",
                     color="#ccc", fontsize=11)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, f"_ranges_{k // per_png + 1}.png"), dpi=80,
                facecolor="black")
    plt.close(fig)
print(len(sessions), "sessions,", len(ranges), "ranges,", len(dropped), "dropped candidates")
