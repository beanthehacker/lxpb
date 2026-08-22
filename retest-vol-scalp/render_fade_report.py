"""
LXPB fade/mean-reversion research -- HTML explainer report.

Builds a single narrative HTML page (lxpb_fade_report.html) that:
  1. Defines every engineered feature in plain language (glossary).
  2. Shows the aggregate findings (bucket tables from STEP 3) per feature.
  3. Illustrates each feature with 1-2 REAL annotated trade examples, each
     rendered as an H1 context chart (formation -> breakout -> retest,
     same marker convention as ../label-review/render_labels_report.py)
     plus a 1-second candle + Bid Volume + Ask Volume trio (same dark
     TradingView lightweight-charts@4 look as ../lxpb-es-vol/render_report.py),
     with markers annotating the touch/entry instant (feature values in the
     marker text), the start of the specific lookback window being
     illustrated, and the 30-minute MFE peak.

Data: lxpb_fade_features.csv (STEP 2 output), lxpb_fade_bucket_analysis.csv
(STEP 3 output), ../data/es-h1-continuous-backadjusted.csv (H1 context),
../lxpb-es-vol/ES_full_1s.csv (1s candles/bid/ask for the example windows).

Run: python render_fade_report.py
Output: lxpb_fade_report.html
"""
import os
import sys
import json
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _REPO_ROOT)
import lxpb as L  # noqa: E402

H1_CSV = os.path.join(_REPO_ROOT, "data", "es-h1-continuous-backadjusted.csv")
CSV_1S = os.path.join(_REPO_ROOT, "lxpb-es-vol", "ES_full_1s.csv")
FEATURES_CSV = os.path.join(_HERE, "lxpb_fade_features.csv")
BUCKETS_CSV = os.path.join(_HERE, "lxpb_fade_bucket_analysis.csv")
OUT_HTML = os.path.join(_HERE, "lxpb_fade_report.html")

TICK = 0.25
H1_BARS_BEFORE = 6
H1_BARS_AFTER = 15
ONE_S_LOOKBACK_BUFFER_S = 60     # extra context before the widest feature window
ONE_S_HORIZON_S = 1800           # 30 min forward, matches quality_30m label

LEVEL_COLOR = "#fcd34d"
BID_COLOR = "#f87171"
ASK_COLOR = "#4ade80"
CANDLE_UP = "#DDDDD0"
CANDLE_DOWN = "#888888"
TOUCH_COLOR = "#60a5fa"
MFE_COLOR = "#4ade80"
WINDOW_COLOR = "#fbbf24"
PT_TZ = ZoneInfo("America/Los_Angeles")
DEFAULT_POST_TOUCH_FOCUS_S = 300  # how much post-entry reaction to show by default (rest is zoomable)

# ---------------------------------------------------------------------------
# Example selection -- real rows from lxpb_fade_features.csv (rid = row
# index in that file), picked by direct query (see session notes) to
# clearly illustrate each named feature, with a contrasting failure example
# wherever the bucket analysis showed a clean effect.
# ---------------------------------------------------------------------------
EXAMPLES = [
    {"rid": 82, "cat": "breakout_strength", "tag": "good", "window_s": None,
     "caption": "Wide, impulsive breakout bar (4.6x avg range) -- clean 19.75pt fade"},
    {"rid": 226, "cat": "breakout_strength", "tag": "bad", "window_s": None,
     "caption": "Weak, narrow breakout bar (0.6x avg range) -- level fails, -25.75pt"},

    {"rid": 108, "cat": "momentum", "tag": "good", "window_s": 120,
     "caption": "Sharp flush into the level (-14.75pt/120s) -- 70.5pt textbook fade"},
    {"rid": 179, "cat": "momentum", "tag": "bad", "window_s": 120,
     "caption": "Weak, drifting approach (-2.0pt/120s) -- no real reaction, -12.25pt"},

    {"rid": 211, "cat": "orderflow", "tag": "good", "window_s": 120,
     "caption": "Heavy one-sided aggressor flow absorbed at the level (cumΔ120s=-1783) -- 32.75pt"},

    {"rid": 194, "cat": "activity", "tag": "good", "window_s": 300,
     "caption": "Busy retest: 35,281 trades in the preceding 5 minutes -- 15.75pt"},
    {"rid": 3, "cat": "activity", "tag": "bad", "window_s": 300,
     "caption": "Dead-quiet retest: only 5 trades in the preceding 5 minutes -- -5.0pt"},

    {"rid": 77, "cat": "volume_spike", "tag": "good", "window_s": 60,
     "caption": "Moderate volume spike (z=0.79, the 'sweet spot') -- 42.0pt"},
    {"rid": 11, "cat": "volume_spike", "tag": "bad", "window_s": 60,
     "caption": "Extreme volume spike (z=17.6) -- one-sided but chaotic, -8.25pt"},

    {"rid": 55, "cat": "dwell_time", "tag": "good", "window_s": 30,
     "caption": "Fast tag: price spent 3 of the last 30s near the level -- 28.75pt"},
    {"rid": 64, "cat": "dwell_time", "tag": "bad", "window_s": 30,
     "caption": "Long loiter: price spent 28 of the last 30s already at the level -- -3.0pt"},

    {"rid": 44, "cat": "level_age", "tag": "good", "window_s": None,
     "caption": "Seasoned level, retested 844h (~35 days) after breakout -- 23.25pt"},
    {"rid": 8, "cat": "level_age", "tag": "bad", "window_s": None,
     "caption": "Awkward middle-age retest, 33h after breakout -- -13.25pt"},

    {"rid": 246, "cat": "confluence", "tag": "good", "window_s": None,
     "caption": "A second same-type broken-out level sits within 20 ticks -- 37.25pt"},

    {"rid": 213, "cat": "prior_touches", "tag": "high", "window_s": None,
     "caption": "Price already revisited this exact level 148 times in 2yr -- 27.25pt (still works)"},
    {"rid": 243, "cat": "prior_touches", "tag": "low", "window_s": None,
     "caption": "Brand-new price zone, never touched before (0 prior touches) -- 23.5pt (works too)"},

    {"rid": 36, "cat": "is_swing", "tag": "good", "window_s": None,
     "caption": "Formed as a genuine 3-bar swing pivot (not just a spike) -- 54.75pt"},
]


def load_data():
    print("Loading H1 data ...")
    h1 = L.load_ohlc_data(H1_CSV)
    print(f"  {len(h1)} bars")
    print("Loading 1s data (this can take ~40-60s) ...")
    df1s = pd.read_csv(CSV_1S, parse_dates=["Time_PT"])
    df1s["time_utc"] = df1s["Time_PT"].dt.tz_convert("UTC").dt.tz_localize(None)
    df1s = df1s.sort_values("time_utc").reset_index(drop=True)
    print(f"  {len(df1s)} 1s bars")
    feats = pd.read_csv(FEATURES_CSV, parse_dates=["formation_time", "breakout_time", "retest_time", "touch_time_utc"])
    buckets = pd.read_csv(BUCKETS_CSV)
    return h1, df1s, feats, buckets


def _epoch(ts):
    return int(pd.Timestamp(ts).tz_localize("UTC").timestamp())


def build_h1_chart(h1, row):
    idx = h1.index
    i_form = int(idx.searchsorted(row["formation_time"]))
    i_retest = int(idx.searchsorted(row["retest_time"]))
    i0 = max(0, i_form - H1_BARS_BEFORE)
    i1 = min(len(h1), i_retest + H1_BARS_AFTER + 1)
    win = h1.iloc[i0:i1]

    candles = [{"time": _epoch(t), "open": r.open, "high": r.high, "low": r.low, "close": r.close}
               for t, r in win.iterrows()]

    markers = []
    if row["formation_time"] in win.index:
        markers.append({"time": _epoch(row["formation_time"]), "position": "aboveBar", "color": LEVEL_COLOR,
                         "shape": "circle", "text": "Formation"})
    if row["breakout_time"] in win.index:
        up = row["type"] == "LHPB"
        markers.append({"time": _epoch(row["breakout_time"]),
                         "position": "aboveBar" if up else "belowBar",
                         "color": "#4ade80" if up else "#f87171",
                         "shape": "arrowUp" if up else "arrowDown", "text": "Breakout"})
    if row["retest_time"] in win.index:
        markers.append({"time": _epoch(row["retest_time"]), "position": "belowBar", "color": "#a78bfa",
                         "shape": "circle", "text": "Retest"})
    markers.sort(key=lambda m: m["time"])

    price_lines = [{"price": float(row["price"]), "color": LEVEL_COLOR, "lineWidth": 1, "lineStyle": 2,
                     "title": f"{row['type']} {row['price']:.2f}"}]
    return {"candles": candles, "markers": markers, "priceLines": price_lines,
            "title": f"H1 context -- {row['type']} @ {row['price']:.2f}"}


def build_1s_chart(df1s, row, window_s, mfe_label="mfe_30m", mae_label="mae_before_peak_30m"):
    touch = row["touch_time_utc"]
    lookback = (window_s or 30) + ONE_S_LOOKBACK_BUFFER_S
    t0 = touch - pd.Timedelta(seconds=lookback)
    t1 = touch + pd.Timedelta(seconds=ONE_S_HORIZON_S)
    tcol = df1s["time_utc"]
    i0 = int(tcol.searchsorted(t0))
    i1 = int(tcol.searchsorted(t1))
    win = df1s.iloc[i0:i1]
    if win.empty:
        return None

    candles, bidvol, askvol = [], [], []
    for _, r in win.iterrows():
        ep = _epoch(r["time_utc"])
        candles.append({"time": ep, "open": r["Open"], "high": r["High"], "low": r["Low"], "close": r["Close"]})
        bidvol.append({"time": ep, "value": float(r["BidVolume"]), "color": BID_COLOR})
        askvol.append({"time": ep, "value": float(r["AskVolume"]), "color": ASK_COLOR})

    entry = float(row["entry_price"])
    direction = int(row["direction"])
    touch_ep = _epoch(touch)
    touch_pt_str = (pd.Timestamp(touch).tz_localize("UTC").tz_convert(PT_TZ)
                     .strftime("%H:%M:%S PT"))

    feat_bits = []
    if window_s:
        mom = row.get(f"momentum_pts_{window_s}s")
        cd = row.get(f"cum_delta_{window_s}s")
        vz = row.get(f"vol_spike_z_{window_s}s")
        tc = row.get(f"trades_count_{window_s}s")
        if pd.notna(mom):
            feat_bits.append(f"mom{window_s}s={mom:.2f}pt")
        if pd.notna(cd):
            feat_bits.append(f"cumΔ{window_s}s={cd:.0f}")
        if pd.notna(vz):
            feat_bits.append(f"vol_z{window_s}s={vz:.2f}")
        if pd.notna(tc):
            feat_bits.append(f"trades{window_s}s={tc:.0f}")
    pcs = row.get("preconsolidation_secs_30s")
    if pd.notna(pcs) and (window_s == 30 or window_s is None):
        feat_bits.append(f"preconsol30s={int(pcs)}s")

    entry_label = f"ENTRY {touch_pt_str}"
    if feat_bits:
        entry_label += " " + " ".join(feat_bits)
    markers = [{
        "time": touch_ep, "position": "belowBar" if direction == 1 else "aboveBar",
        "color": TOUCH_COLOR, "shape": "arrowUp" if direction == 1 else "arrowDown",
        "text": entry_label, "size": 2,
    }]
    if window_s:
        w0 = touch - pd.Timedelta(seconds=window_s)
        if w0 >= win["time_utc"].iloc[0]:
            markers.append({"time": _epoch(w0), "position": "aboveBar", "color": WINDOW_COLOR,
                             "shape": "circle", "text": f"<- {window_s}s window start", "size": 1.5})

    t2peak_col = "time_to_peak_s_30m"
    mfe = row.get(mfe_label)
    mae = row.get(mae_label)
    t2peak = row.get(t2peak_col)
    peak_ts = None
    if pd.notna(mfe) and pd.notna(t2peak):
        peak_ts = touch + pd.Timedelta(seconds=int(t2peak))
        markers.append({"time": _epoch(peak_ts), "position": "aboveBar" if direction == 1 else "belowBar",
                         "color": MFE_COLOR, "shape": "circle", "text": f"MFE +{mfe:.2f}pt", "size": 1.5})
    markers.sort(key=lambda m: m["time"])

    price_lines = [{"price": entry, "color": LEVEL_COLOR, "lineWidth": 2, "lineStyle": 2,
                     "title": f"{row['type']} entry {entry:.2f}"}]

    # Default zoom: the pre-touch lookback window is only ~30-360s while the full
    # forward horizon is 30min, so fitting the *entire* series by default squeezes
    # the touch/entry instant (the whole point of the chart) into a sliver a few
    # percent from the left edge. Instead default to lookback -> entry + a modest
    # reaction window (extended to cover the MFE peak marker if it lands sooner
    # than that), and let the user scroll/zoom out to see the rest of the 30min.
    focus_end = touch + pd.Timedelta(seconds=max(DEFAULT_POST_TOUCH_FOCUS_S, lookback * 2))
    focus_end = min(focus_end, win["time_utc"].iloc[-1])
    focus_range = {"from": _epoch(t0), "to": _epoch(focus_end)}

    meta = {
        "type": row["type"], "direction": "LONG" if direction == 1 else "SHORT",
        "entry": round(entry, 2), "retest_time": str(row["retest_time"]),
        "touch_time_pt": touch_pt_str,
        "mfe_30m": None if pd.isna(mfe) else round(float(mfe), 2),
        "mae_30m": None if pd.isna(mae) else round(float(mae), 2),
        "quality_30m": None if pd.isna(row.get("quality_30m")) else round(float(row["quality_30m"]), 2),
    }
    return {"candles": candles, "bidvol": bidvol, "askvol": askvol, "markers": markers,
            "priceLines": price_lines, "focusRange": focus_range,
            "title": f"1s -- {row['type']} touch {touch}", "meta": meta}


# ---------------------------------------------------------------------------
# Glossary / feature definitions
# ---------------------------------------------------------------------------
GLOSSARY = [
    ("Direction convention", "CONTINUATION: an LHPB retest (old swing high, broken out upward, "
     "now price pulls back down to it) is traded LONG -- betting the old resistance now holds as "
     "support and price bounces/scalps back in the breakout's direction. An LLPB retest is traded "
     "SHORT, symmetrically. This is the same convention already baked into lxpb.py's own FTA/stop-"
     "loss columns -- 'fading the pullback', not fading the level's own directional bias."),
    ("Breakout strength (breakout_range_ratio, breakout_body_ratio)",
     "How forceful was the ORIGINAL breakout bar that created this level's broken-out state? "
     "breakout_range_ratio = that bar's high-low range divided by the trailing 20-bar average "
     "range (>1 = wider/more impulsive than normal). breakout_body_ratio = the fraction of that "
     "range that was real body (close vs open) rather than wick."),
    ("Momentum into the level (momentum_pts_Ws)",
     "Signed price change over the W seconds immediately before the touch, projected onto the "
     "trade's direction. A large NEGATIVE number means price plunged/spiked hard on its way INTO "
     "the level (as expected for an approach into support/resistance) -- the magnitude measures "
     "how sharp/climactic that approach was, not just its existence."),
    ("Order flow / cumulative delta (cum_delta_Ws, same_side_vol_ratio_Ws)",
     "Delta = AskVolume-BidVolume per second (aggressor imbalance) from the real scid tape. "
     "cum_delta_Ws sums Delta over the preceding W seconds, projected onto trade direction: a large "
     "negative value for a LONG means heavy net aggressor SELLING pushed price down into the level "
     "-- exactly the flow an 'absorption' setup needs to see fail to break the level. "
     "same_side_vol_ratio_Ws is the fraction of volume already flowing WITH the trade direction "
     "before the touch."),
    ("Trade / tick activity (trades_count_Ws, upticks_Ws, downticks_Ws)",
     "trades_count_Ws = number of actual executions (scid Trades column, not contracts) in the "
     "preceding W seconds -- a direct measure of participation, distinct from raw volume (few, "
     "large trades vs many, small ones). upticks/downticks = count of 1-second bars with positive/"
     "negative Delta -- the closest tick-direction proxy available from bid/ask volume."),
    ("Volume spike (vol_spike_z_Ws)",
     "Z-score of the preceding W-second volume against a rolling 600s baseline of same-length "
     "chunks -- how anomalous the volume coming into the retest is vs. that session's normal "
     "activity."),
    ("Volatility compression (range_compression_Ws)",
     "Mean 1-second bar range in the preceding W seconds, divided by the same average over a "
     "longer 600s baseline. <1 = price action tightened up (quiet, coiling) right before the "
     "touch; >1 = choppy/expanding range into the level."),
    ("Dwell time / preconsolidation (preconsolidation_secs_30s)",
     "Of the 30 seconds strictly BEFORE the official touch, how many were already within 6 ticks "
     "of the level -- i.e. had price already been hovering near this price, or did it arrive fresh? "
     "Deliberately pre-touch only (no lookahead) to avoid leaking the outcome into the feature."),
    ("Level age / seasoning (level_age_hours, hours_breakout_to_retest)",
     "level_age_hours = formation to retest. hours_breakout_to_retest = time the level spent "
     "broken-out and awaiting retest (this is what lxpb.py's own 4-hour MIN_HOURS_BEFORE_RETEST "
     "rule already gates on the low end)."),
    ("Historical inflection strength (prior_touches_2yr / prior_touches_all)",
     "How many H1 bars in the 2-year (or full-history) lookback BEFORE this level even formed "
     "already traded within 4 ticks of this exact price -- i.e. was this already a well-worn price "
     "zone, or a fresh one?"),
    ("Confluence count", "Number of OTHER same-type (LHPB/LLPB) levels, still broken-out and "
     "awaiting their own retest, within 20 ticks of this level at the moment of this retest -- "
     "captured via a live snapshot of lxpb.py's internal touch_lv1 state (not just the final "
     "end-of-history state)."),
    ("is_spike / is_swing", "Single-bar hammer/shooting-star pattern (is_spike) vs. a genuine "
     "3-bar swing pivot -- both from lxpb.py's own formation-bar classification."),
    ("Outcome labels (mfe_30m, mae_before_peak_30m, quality_30m)",
     "MFE = maximum favorable excursion (biggest paper profit reached, no stop/target) within 30 "
     "minutes forward. MAE-before-peak = the worst drawdown endured on the way to that peak (i.e. "
     "the stop size actually needed to survive to it). quality_30m = MFE - MAE-before-peak, the "
     "continuous 'how good was this trade' regression target used throughout."),
]

CAT_TITLES = {
    "breakout_strength": "Breakout strength -- was the original break a real impulse?",
    "momentum": "Momentum into the level",
    "orderflow": "Order flow / cumulative delta (absorption)",
    "activity": "Trade activity (no. of trades, upticks/downticks)",
    "volume_spike": "Volume spike coming into the retest",
    "dwell_time": "Time spent at the level (dwell / preconsolidation)",
    "level_age": "Level age / seasoning",
    "confluence": "Confluence (stacked levels)",
    "prior_touches": "Historical inflection strength (prior touches)",
    "is_swing": "Formation quality (spike vs. genuine swing)",
}

CAT_FINDINGS = {
    "breakout_strength": "Weak/narrow breakout bars are the worst bucket (mean quality 2.9); "
        "wide, impulsive breakout bars roughly triple it (8.0-8.5). A real impulse leaves a level "
        "worth respecting later.",
    "momentum": "Sharper, more climactic pre-touch momentum (bigger, not just present) produces "
        "meaningfully bigger winners, even at a slightly lower hit rate -- a fast flush/spike into "
        "the level beats a lazy drift.",
    "orderflow": "The most one-sided cumulative delta AGAINST the trade direction (heaviest "
        "aggressor pressure trying to break the level) is both the best hit-rate (89%) and best "
        "payout bucket -- the core 'absorption' thesis.",
    "activity": "Monotonic across every window: more trades and more up/downticks = better win "
        "rate AND better payout. Busy retests beat quiet ones consistently.",
    "volume_spike": "Non-monotonic: the middle tercile (moderately elevated volume) outperforms "
        "the most extreme volume-spike bucket -- too much one-sided volume can mean the level is "
        "about to break, not get absorbed.",
    "dwell_time": "Levels tagged FAST (little prior loitering) produce the biggest winners; "
        "levels price has already ground on for 20-30s pre-touch are the worst quality bucket, "
        "though still a decent hit rate -- the level looks 'used up'.",
    "level_age": "Non-monotonic: very fresh (4-7h) is decent, medium age (~1-1.5 days) is the "
        "WORST bucket, and old/seasoned levels (weeks old) are the best -- momentum carries the "
        "fast ones, and only genuinely major levels survive to be retested weeks later.",
    "confluence": "Having >=1 other broken-out same-type level within 20 ticks jumps win rate to "
        "96% and quality to 10.7 vs. 6.1/78% with none -- but n=25, so suggestive not proven.",
    "prior_touches": "No clean signal either way -- low, medium and high prior-touch levels "
        "perform similarly in this sample.",
    "is_swing": "Swing-formed levels notably outperform non-swing ones (quality 8.3 vs 6.0); "
        "is_spike showed no edge (small n=21).",
}


def bucket_html(buckets, feature):
    sub = buckets[(buckets["feature"] == feature) & (buckets["group"] == "ALL")]
    if sub.empty:
        return ""
    rows = "".join(
        f"<tr><td class='left'>{r['bucket']}</td><td>{r['n']}</td>"
        f"<td>{r['mean_quality_30m']:.2f}</td><td>{r['win_rate']:.0%}</td>"
        f"<td>{r['mean_mfe_30m']:.2f}</td><td>{r['mean_mae_30m']:.2f}</td></tr>"
        for _, r in sub.iterrows()
    )
    return (f"<table class='bucket-table'><thead><tr><th class='left'>{feature} bucket</th>"
            f"<th>n</th><th>mean quality_30m</th><th>win rate</th><th>mean MFE</th><th>mean MAE</th>"
            f"</tr></thead><tbody>{rows}</tbody></table>")


CSS = """
<style>
:root { --bg:#111316; --surface:#1c1f24; --surface2:#22262d; --border:#2e333b;
        --text:#d4d8df; --text-dim:#8b93a1; --bull:#4ade80; --bear:#f87171; --accent:#60a5fa; }
* { box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       padding:20px 24px 60px; max-width:1180px; margin:0 auto;
       background:var(--bg); color:var(--text); }
h1 { font-size:1.5em; margin:0 0 6px; color:#e8eaed; }
h2 { font-size:1.2em; margin:38px 0 6px; color:#e8eaed; border-bottom:1px solid var(--border); padding-bottom:6px; }
h3 { font-size:1.0em; margin:18px 0 4px; color:#cbd5e1; }
p.lead { color:var(--text-dim); font-size:0.92em; line-height:1.5; }
p, li { line-height:1.55; font-size:0.92em; }
.caveat { background:#2a1f14; border:1px solid #5c4324; border-radius:6px; padding:10px 14px; font-size:0.88em; color:#e2c08d; }
dl.glossary dt { font-weight:600; color:#e8eaed; margin-top:12px; }
dl.glossary dd { margin:2px 0 0; color:var(--text-dim); font-size:0.9em; }
table.bucket-table { border-collapse:collapse; font-size:0.82em; margin:10px 0 18px; background:var(--surface);
       border:1px solid var(--border); border-radius:6px; overflow:hidden; }
table.bucket-table th, table.bucket-table td { border-bottom:1px solid var(--border); padding:5px 10px; text-align:right; }
table.bucket-table th { background:var(--surface2); color:var(--text-dim); }
td.left, th.left { text-align:left; }
.finding-box { background:#122417; border:1px solid #1f4c2b; border-radius:6px; padding:10px 14px; font-size:0.88em; color:#bdf3cd; margin:8px 0 14px; }
.example { margin:14px 0 30px; background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:12px 14px; }
.example.tag-bad { border-color:#4c2323; }
.example-caption { font-size:0.9em; margin-bottom:8px; }
.example-caption .tag { font-size:0.72em; padding:1px 8px; border-radius:10px; margin-right:8px; }
.tag-good .tag { background:#123a1e; color:var(--bull); }
.tag-bad .tag { background:#3a1414; color:var(--bear); }
.tag-high .tag, .tag-low .tag { background:#1e2a3a; color:var(--accent); }
.chart-grid { display:grid; grid-template-columns: 1fr 1.3fr; gap:10px; }
.chart-col { display:grid; grid-template-rows: 260px 90px 90px; gap:6px; }
.chart-cell { background:#000; border:1px solid var(--border); border-radius:5px; overflow:hidden; }
.chart-title { color:#cccccc; padding:4px 8px; font-size:0.72em; font-family:ui-monospace,monospace;
       background:#0a0a0a; border-bottom:1px solid #1f1f1f; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.chart-ph { height:calc(100% - 22px); width:100%; }
.h1-cell { grid-row: span 3; }
.meta-row { font-size:0.78em; color:var(--text-dim); margin-top:8px; }
.meta-row b { color:#e8eaed; }
.toc { columns:2; font-size:0.88em; }
.toc a { color:var(--accent); }
</style>
"""

JS_TEMPLATE = """
<script>
const EXAMPLES = __EXAMPLES_JSON__;
const PT_TZ = 'America/Los_Angeles';
const timeFmt = new Intl.DateTimeFormat('en-US', { timeZone: PT_TZ,
  hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false });
const timeFmtH1 = new Intl.DateTimeFormat('en-US', { timeZone: PT_TZ,
  month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false });

function _baseOpts(tickFmt) {
  return {
    autoSize: true,
    layout: { background:{type:'solid', color:'#000000'}, textColor:'#cccccc',
              fontFamily:"'Courier New', monospace", fontSize:9 },
    grid: { vertLines:{visible:false}, horzLines:{visible:false} },
    crosshair: { mode: 0 },
    rightPriceScale: { borderColor:'#333', scaleMargins:{top:0.1, bottom:0.1} },
    timeScale: { borderColor:'#333', timeVisible:true, secondsVisible:true,
      tickMarkFormatter: (t) => tickFmt.format(new Date(t * 1000)) },
    localization: { timeFormatter: (t) => tickFmt.format(new Date(t * 1000)) },
  };
}
function _addCandles(chart) {
  return chart.addCandlestickSeries({
    upColor:'#DDDDD0', downColor:'#888888', borderUpColor:'#DDDDD0', borderDownColor:'#888888',
    wickUpColor:'#DDDDD0', wickDownColor:'#888888',
    priceFormat: { type:'price', precision:2, minMove:0.25 },
    lastValueVisible:false, priceLineVisible:false,
  });
}
function _addHist(chart, color) {
  return chart.addHistogramSeries({ color: color, priceFormat:{type:'volume'} });
}

function renderH1(key, cd) {
  const el = document.getElementById('h1-' + key);
  if (!cd || !cd.candles || !cd.candles.length) { el.textContent = 'no H1 data'; return; }
  const chart = LightweightCharts.createChart(el, _baseOpts(timeFmtH1));
  const s = _addCandles(chart);
  s.setData(cd.candles);
  if (cd.markers && cd.markers.length) s.setMarkers(cd.markers);
  (cd.priceLines || []).forEach(pl => s.createPriceLine(pl));
  chart.timeScale().fitContent();
}

function renderTrio(key, cd) {
  const elC = document.getElementById('c-' + key), elB = document.getElementById('b-' + key), elA = document.getElementById('a-' + key);
  if (!cd || !cd.candles || !cd.candles.length) { elC.textContent = 'no 1s data'; return; }
  const chartC = LightweightCharts.createChart(elC, _baseOpts(timeFmt));
  const sC = _addCandles(chartC);
  sC.setData(cd.candles);
  if (cd.markers && cd.markers.length) sC.setMarkers(cd.markers);
  (cd.priceLines || []).forEach(pl => sC.createPriceLine(pl));

  const chartB = LightweightCharts.createChart(elB, _baseOpts(timeFmt));
  const sB = _addHist(chartB, '#f87171'); sB.setData(cd.bidvol);
  const chartA = LightweightCharts.createChart(elA, _baseOpts(timeFmt));
  const sA = _addHist(chartA, '#4ade80'); sA.setData(cd.askvol);

  const charts = [chartC, chartB, chartA];
  let syncing = false;
  charts.forEach(c => c.timeScale().subscribeVisibleLogicalRangeChange(range => {
    if (syncing || !range) return;
    syncing = true;
    charts.forEach(o => { if (o !== c) o.timeScale().setVisibleLogicalRange(range); });
    syncing = false;
  }));
  if (cd.focusRange) {
    charts.forEach(c => c.timeScale().setVisibleRange(cd.focusRange));
  } else {
    charts.forEach(c => c.timeScale().fitContent());
  }
}

Object.keys(EXAMPLES).forEach(key => {
  renderH1(key, EXAMPLES[key].h1);
  renderTrio(key, EXAMPLES[key].trio);
});
</script>
"""


def main():
    h1, df1s, feats, buckets = load_data()

    chart_map = {}
    example_blocks = []
    for ex in EXAMPLES:
        row = feats.iloc[ex["rid"]]
        key = f"{ex['cat']}_{ex['tag']}_{ex['rid']}"
        h1cd = build_h1_chart(h1, row)
        trio = build_1s_chart(df1s, row, ex["window_s"])
        if trio is None:
            print(f"WARNING: no 1s data for rid={ex['rid']}, skipping example")
            continue
        chart_map[key] = {"h1": h1cd, "trio": trio}
        m = trio["meta"]
        example_blocks.append({
            "cat": ex["cat"], "key": key,
            "html": f"""
<div class="example tag-{ex['tag']}">
  <div class="example-caption"><span class="tag">{ex['tag'].upper()}</span><strong>{ex['caption']}</strong></div>
  <div class="chart-grid">
    <div class="h1-cell chart-cell"><div class="chart-title">H1 context</div><div class="chart-ph" id="h1-{key}"></div></div>
    <div class="chart-col">
      <div class="chart-cell"><div class="chart-title">1s candles ({row['type']}, entry {m['entry']})</div><div class="chart-ph" id="c-{key}"></div></div>
      <div class="chart-cell"><div class="chart-title">Bid Volume</div><div class="chart-ph" id="b-{key}"></div></div>
      <div class="chart-cell"><div class="chart-title">Ask Volume</div><div class="chart-ph" id="a-{key}"></div></div>
    </div>
  </div>
  <div class="meta-row">
    <b>{m['direction']}</b> {m['type']} retest H1 bar {m['retest_time']}, exact 1s touch
    <b>{m['touch_time_pt']}</b> (blue arrow below) &nbsp;|&nbsp;
    MFE(30m) <b>{m['mfe_30m']}</b>pt &nbsp; MAE-before-peak(30m) <b>{m['mae_30m']}</b>pt &nbsp;
    quality_30m <b>{m['quality_30m']}</b>pt &nbsp;|&nbsp;
    <i>1s chart defaults to a zoomed-in view around the touch -- scroll/drag to see the full 30min.</i>
  </div>
</div>
""",
        })

    glossary_html = "".join(f"<dt>{name}</dt><dd>{desc}</dd>" for name, desc in GLOSSARY)

    sections = []
    for cat, title in CAT_TITLES.items():
        blocks = [b["html"] for b in example_blocks if b["cat"] == cat]
        if not blocks:
            continue
        # bucket table: use the first named feature window mentioned for this cat, else the base column
        feat_col_map = {
            "breakout_strength": "breakout_range_ratio", "momentum": "momentum_pts_120s",
            "orderflow": "cum_delta_120s", "activity": "trades_count_300s",
            "volume_spike": "vol_spike_z_60s", "dwell_time": "preconsolidation_secs_30s",
            "level_age": "hours_breakout_to_retest", "confluence": "confluence_count",
            "prior_touches": "prior_touches_2yr", "is_swing": "is_swing",
        }
        btable = bucket_html(buckets, feat_col_map[cat])
        sections.append(f"""
<h2 id="{cat}">{title}</h2>
<div class="finding-box">{CAT_FINDINGS[cat]}</div>
{btable}
{''.join(blocks)}
""")

    n_rows = len(feats)
    win_rate_all = (feats["quality_30m"] > 0).mean()

    header = f"""
<h1>Which LXPB retests make good fade/mean-reversion scalps?</h1>
<p class="lead">Continuation-direction retest trades (LHPB retest = LONG, LLPB retest = SHORT)
on ES, built from {n_rows} real completed H1 LXPB retests (2026-05-28 to 2026-08-17, the window
with genuine scid-derived 1-second bid/ask/trade data) and every-30-minute-forward MFE/MAE
outcomes. Every chart below is a real trade from the data, not a synthetic illustration.
Overall: {win_rate_all:.0%} of all {n_rows} retests are net-positive on the quality_30m metric
(MFE &minus; MAE-before-peak within 30 minutes).</p>
<div class="caveat"><b>Caveats:</b> n={n_rows}, one instrument, one ~2.5-month regime.
A 5-fold cross-validated LightGBM model predicting quality_30m from all features together
reaches only R&sup2;=0.166 (correlation 0.41) out-of-fold -- the individual feature effects
below are real but noisy, directional evidence, not a finished, guaranteed edge.</div>
<h2>Contents</h2>
<div class="toc">{''.join(f'<a href="#{c}">{t}</a><br>' for c, t in CAT_TITLES.items())}</div>
<h2>Glossary</h2>
<dl class="glossary">{glossary_html}</dl>
<h2>How to read the bucket tables</h2>
<p class="lead">Every category below splits that feature into <b>terciles</b> -- three
roughly-equal-count groups (low / mid / high, computed with pandas <code>qcut</code> across
all {n_rows} retests, so group edges are wherever the data falls, not round numbers). Columns:
<b>n</b> = retests in that group. <b>mean quality_30m</b> = average of MFE &minus;
MAE-before-peak (points) for the group -- the main outcome score. <b>win rate</b> = % of that
group's retests with quality_30m &gt; 0, i.e. the eventual favorable move outran the drawdown
endured to reach it (a directional proxy, since no fixed stop/target was assumed). <b>mean
MFE</b> / <b>mean MAE</b> are the average favorable/adverse excursions (points) in isolation.
Some categories (e.g. is_swing, confluence) use 2 natural groups instead of 3 when the raw
feature only takes a couple of distinct values.</p>
"""

    charts_json = json.dumps(chart_map).replace("</", "<\\/")
    js = JS_TEMPLATE.replace("__EXAMPLES_JSON__", charts_json)

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LXPB Fade Research -- Which Levels Work</title>
<script src="https://unpkg.com/lightweight-charts@4/dist/lightweight-charts.standalone.production.js"></script>
{CSS}
</head><body>
{header}
{''.join(sections)}
{js}
</body></html>
"""
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nSaved -> {OUT_HTML}  ({len(chart_map)} examples)")


if __name__ == "__main__":
    main()
