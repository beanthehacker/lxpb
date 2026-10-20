"""Check patterns_pure/find_trading_range against the hand-drawn ES M5 range
of Oct 15-16 2026 (PT), read off a screenshot of that chart.

    python range_pure_check.py <bars.csv> ['{"param": value, ...}'] [--png out.png]

<bars.csv> comes from scid_bars.py (EPZ26 ticks, 5-min, PT), e.g.
    python scid_bars.py D:/SierraChart-daytrading/Data/F.US.EPZ26.scid "2026-10-12 15:00" "2026-10-16 13:00"
"""
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "patterns_pure"))
import find_trading_range as ftr  # noqa: E402

PT = "America/Los_Angeles"
WINDOW = ("2026-10-15 07:00", "2026-10-16 08:30")
# The one range in WINDOW (user, 2026-10-19): the overnight box drawn by eye,
# (name, start, end, low, high). The 08:00-12:00 and 12:20-15:50 boxes also
# drawn then are NOT ranges, so any other box found in WINDOW is a miss.
TARGETS = [
    ("C", "2026-10-15 20:42", "2026-10-16 06:32", 7792.5, 7809.5),
]


def load(path):
    b = pd.read_csv(path, index_col=0, parse_dates=True)
    b.index = pd.DatetimeIndex(b.index).tz_convert(PT)
    return b


def _iou(a0, a1, b0, b1):
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    union = max(a1, b1) - min(a0, b0)
    return inter / union if union > 0 else 0.0


def _in_window(found):
    w0, w1 = (pd.Timestamp(x, tz=PT) for x in WINDOW)
    return found[(found.end_time >= w0) & (found.start_time <= w1)].reset_index(drop=True)


def compare(found):
    found = _in_window(found)
    used, score = set(), []
    for name, s, e, lo, hi in TARGETS:
        s, e = pd.Timestamp(s, tz=PT), pd.Timestamp(e, tz=PT)
        best, bi = 0.0, None
        for i, r in found.iterrows():
            t = _iou(s.value, e.value, r.start_time.value, (r.end_time + pd.Timedelta("5min")).value)
            if t > best:
                best, bi = t, i
        if bi is None:
            print(f"{name}: MISSED")
            score.append(0.0)
            continue
        r = found.loc[bi]
        used.add(bi)
        p = _iou(lo, hi, r.low, r.high)
        print(f"{name}: box {bi} time-IoU {best:.2f} price-IoU {p:.2f}  "
              f"{r.start_time:%m-%d %H:%M}-{r.end_time:%m-%d %H:%M} {r.low}-{r.high}")
        score.append(min(best, p))
    extra = [i for i in range(len(found)) if i not in used]
    for i in extra:
        r = found.loc[i]
        print(f"extra box {i}: {r.start_time:%m-%d %H:%M}-{r.end_time:%m-%d %H:%M} "
              f"{r.low}-{r.high} ({r.n_bars} bars)")
    print(f"score {sum(score) / len(score):.2f}, extra {len(extra)}")


def plot(bars, found, out):
    """WINDOW's candles (gaps closed up), found ranges solid blue, target dashed."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    w0, w1 = (pd.Timestamp(x, tz=PT) for x in WINDOW)
    b = bars[(bars.index >= w0) & (bars.index <= w1)]

    def xi(t):
        return int(b.index.searchsorted(t))

    fig, ax = plt.subplots(figsize=(20, 8), facecolor="black")
    ax.set_facecolor("black")
    for i, (o, h, l, c) in enumerate(b[["Open", "High", "Low", "Close"]].itertuples(index=False)):
        ax.vlines(i, l, h, color="#bbb", lw=0.8)
        ax.add_patch(Rectangle((i - 0.3, min(o, c)), 0.6, max(abs(c - o), 0.05),
                               color="#ddd" if c >= o else "#777"))
    for r in _in_window(found).itertuples():
        x0, x1 = xi(r.start_time), xi(r.end_time)
        ax.add_patch(Rectangle((x0 - 0.5, r.low), x1 - x0 + 1, r.high - r.low,
                               fc=(0, 0.67, 1, 0.15), ec="#00beff", lw=2))
        ax.text(x0, r.high + 0.8, f"{r.start_time:%H:%M}-{r.end_time:%H:%M}  {r.low}-{r.high}",
                color="#00beff", fontsize=11)
    for _, s, e, lo, hi in TARGETS:
        x0, x1 = xi(pd.Timestamp(s, tz=PT)), xi(pd.Timestamp(e, tz=PT))
        ax.add_patch(Rectangle((x0 - 0.5, lo), x1 - x0, hi - lo, fill=False, ec="#ffb000",
                               lw=1.5, ls="--"))
    ticks = [i for i, t in enumerate(b.index) if t.minute == 0 and t.hour % 2 == 0]
    ax.set_xticks(ticks, [f"{b.index[i]:%d %H:%M}" for i in ticks], color="#ccc", fontsize=9)
    ax.tick_params(axis="y", colors="#ccc")
    ax.set_xlim(-1, len(b))
    ax.set_title("find_trading_range (blue) vs hand-drawn range (orange dashed), ES M5, PT",
                 color="#ccc")
    fig.tight_layout()
    fig.savefig(out, dpi=100, facecolor="black")


if __name__ == "__main__":
    bars = load(sys.argv[1])
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].startswith("{") else {}
    pd.set_option("display.width", 250)
    found = ftr.find_trading_range(bars, **params)
    print(found.to_string())
    compare(found)
    if "--png" in sys.argv:
        plot(bars, found, sys.argv[sys.argv.index("--png") + 1])
