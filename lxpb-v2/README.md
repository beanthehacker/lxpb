# LXPB Hand-Labeling Review Tool (label-review)

A standalone HTML report for hand-labeling completed LXPB (LHPB/LLPB)
retests, used to fine-tune `../lxpb.py`'s detection logic by human review.
Each row is one fully completed level lifecycle -- **Phase 0** (formation
bar), **Phase 1** (breakout bar), **Phase 2** (retest bar) -- shown on an
expandable H1 candlestick chart (same dark, TradingView
lightweight-charts@4 style as `../lxpb-es-vol/render_report.py`) with
dots/arrows marking each phase, plus checkbox columns for human-judged
features.

## Usage

```
python render_labels_report.py [options]
```

| Option | Default | Meaning |
|---|---|---|
| `--data` | `../data/es-h1-continuous-backadjusted.csv` | H1 OHLC CSV to run `detect_lxpb_h1` against -- whole-monorepo canonical, back-adjusted, jump-free continuous series (2015-present; see "ES H1 data" below) |
| `--output` | `lxpb_labels_report.html` | Output HTML path |
| `--title` | auto | Report `<h1>` title |
| `--n-ticks` | 20 | Confluence radius (ticks) for the "nearby broken-out levels" hint/overlay |
| `--tick-size` | 0.25 | Tick size used to convert `--n-ticks` to price points |
| `--start` / `--end` | `2026-01-01` / none | Optional **retest-time** date filter (`YYYY-MM-DD`) before applying `--limit` -- formation/breakout may predate `--start`; only the retest itself must fall in range. Default shows only 2026 retests |
| `--limit` | 300 | Max rows to render (there are ~14,300 completed retests total in the bundled dataset, ~993 in 2026 alone -- far too many to label in one file; use `--limit 0` for no cap) |
| `--order` | `desc` | `desc` = most recent formations first, `asc` = oldest first |

Workflow:

1. Run the script to generate an HTML file (adjust `--start`/`--end`/
   `--limit`/`--order` to pick which batch of levels to review).
2. Open the HTML in a browser. Every row is **pre-labeled with computed
   defaults** (see "Valid column" below) -- tick/untick "Reviewed", flip
   any feature checkbox or the overall "Valid" checkbox that the reviewer
   disagrees with, add misc notes, and click a row (or its ▶ button) to
   expand its chart.
3. Labels autosave to the browser's `localStorage` (keyed by
   `type|price|formation_epoch`, so re-running the script with different
   `--limit`/`--start`/`--end` won't collide with previously saved labels
   for the same underlying levels).
4. Use **Export labels CSV** any time to save a durable copy (survives
   browser data clearing / moving machines); **Import labels CSV** merges
   a previously exported CSV back into `localStorage` (e.g. to resume
   labeling on a fresh machine, or to combine labels collected across
   multiple report files/batches).
5. Use the Type/Status/Validity filter chips and the summary counters at
   the top to track review progress.

## Report contents

- **Only completed retests** (`state["retests"]` from `detect_lxpb_h1`,
  i.e. formation -> breakout -> retest all happened) become rows -- a full
  0/1/2 lifecycle is required to show a meaningful chart. Still-open,
  broken-out-but-never-retested levels (`touch_lv1`) are not rows, but
  **are** included as confluence overlay lines/hints on nearby rows'
  charts, same as `lxpb-es-vol`'s established confluence rule.
- **Chart markers**: gold circle = Phase 0 (formation), colored
  arrow = Phase 1 (breakout, up/down by direction), purple circle =
  Phase 2 (retest).
- **Chart window**: starts `BARS_BEFORE` (8) bars before Phase 0 and ends
  `BARS_AFTER` (8) bars after Phase 2. Some levels sit unbroken for months
  before breaking out, and/or take months to get retested -- to keep
  charts readable and file size small, large gaps (> `MAX_MERGE_GAP` = 15
  bars) around any phase transition are compressed out of the chart
  (small context kept on each side, e.g. `CONTEXT_BARS_AFTER_BREAKOUT`),
  with a grey square marker + "`[N bars skipped]`" annotation marking
  where bars were omitted. A large skipped-bar count is itself evidence
  *against* "fast retest, no drift" for that row.
- **Fixed candle width**: each candle renders at a fixed, deliberately
  tight pixel width (`FIXED_BAR_SPACING` = 6px) regardless of how many
  candles are in the window -- charts with few candles show blank space
  rather than stretching candles to fill the row. `autoSize` is disabled;
  instead each chart is given its own `ResizeObserver` on its container,
  which calls `chart.resize()` + reapplies `barSpacing` only when the
  container itself actually resizes (row opened, window resized). This
  avoids the two problems with letting lightweight-charts manage sizing:
  its built-in `autoSize` resize-observer re-fits the visible range to the
  container width asynchronously (overriding any `barSpacing` set at
  chart-creation time), and a naive fix of re-asserting `barSpacing` on
  every *visible-range* change would also revert the reviewer's own
  manual drag/scroll-zoom on the time axis. Reviewers can freely drag or
  scroll-wheel-zoom any chart after it renders -- that interaction is
  never fought or reset.
- **Price lines**: this row's own level (gold, solid); other same-type
  levels broken out and within `--n-ticks` (blue for LHPB / red for LLPB,
  dashed) -- the same confluence-cluster visual aid used across this repo.
- **Valid/Invalid column**: an overall verdict checkbox, pre-checked
  ("Valid" by default) whenever *any* of the feature defaults below is
  true, per the rule "whichever LXPB has any of these qualities, mark
  valid by default." Rows that default to Invalid (no feature hints true)
  are highlighted with a red-tinted row background. As with feature
  checkboxes, the reviewer's tick is the ground truth -- flip it either
  direction as needed. Export/import CSV and the Validity filter chips
  (Valid/Invalid) include this column.
- **Feature columns** (`FEATURES` list in the script -- add one entry
  there to add a new hand-eval column everywhere, table + JS + CSV
  export/import all stay in sync automatically). Each is **pre-checked
  from a computed default** (not just an unchecked hint) -- the grey text
  below each checkbox explains the underlying number, but the checkbox
  state itself is the default; reviewer ticks/unticks are what get saved:
  - **P0 Spike** -- default: detected via `D:\daily-analysis\patterns-pure`
    (`find_shooting_star`/`find_hammer` for LHPB/LLPB respectively).
  - **P1 Wide Breakout** -- default: breakout bar range >= `WIDE_BREAKOUT_RATIO_THRESHOLD`
    (2.0x) trailing 20-bar average range.
  - **Confluence Cluster** -- default: count of other same-type broken-out
    levels within `--n-ticks` >= `CONFLUENCE_MIN_COUNT` (3).
  - **Large Wick** -- default: formation bar's opposite-side wick >= 40%
    of bar range, via `patterns-pure`'s `has_large_upper_wick`/
    `has_large_lower_wick`.
  - **Fast Retest** -- default: H1 bar count from breakout to retest <=
    `FAST_RETEST_MAX_BARS` (6).
  - **Misc notes** -- free-text textbox (not a checkbox, no default).
  Threshold constants were chosen from empirical percentile analysis of
  the bundled dataset's hint distributions (see module docstring/code
  comments for the exact percentiles) and are easy to retune at the top
  of the script.

## Files

- `render_labels_report.py` -- the entire tool (data pipeline + HTML/JS
  template, self-contained, no external Python deps beyond pandas/numpy
  already used by `../lxpb.py`; imports spike/wick helpers from
  `D:\daily-analysis\patterns-pure` via `sys.path`).
- `lxpb_labels_report.html` -- example generated output (most recent 300
  completed 2026 retests from the bundled dataset, default args).
  Regenerate any time; this file is a disposable build artifact, not
  source of truth (labels live in each browser's `localStorage` / your
  exported CSV, not in this HTML).
- `../data/es-h1-continuous-backadjusted.csv` -- default input dataset,
  the whole-monorepo canonical ES H1 series, built by
  `../data/build_es_h1_continuous.py`. Covers 2015-01-01 through present.

## ES H1 data

This repo previously had (and label-review previously defaulted to) its
own locally-built, *non*-back-adjusted continuous splice, separate from
what the rest of the monorepo used. That has been retired: label-review
and every other tool in this monorepo (`../lxpb.py`, `../lxpb-es-vol/*`)
now share **one canonical, back-adjusted, jump-free** continuous ES H1
series: `../data/es-h1-continuous-backadjusted.csv`, built by
`../data/build_es_h1_continuous.py`.

Background on why back-adjustment was originally a problem, and how it's
now handled correctly:

`../data/es-h1-2015-14aug2026.csv` is a TradingView "ES1!"
**back-adjusted** continuous contract export: every *re-export*
recalculates all historical bars relative to whichever contract is
currently front-month, so re-exporting it periodically and swapping the
file makes old absolute price levels silently drift over time (verified
by diffing two export vintages ~16 months apart for identical
timestamps: average +280pt / up to +412pt difference on the same bar).
That drift-on-re-export behavior was the actual bug -- **not** the fact
that the data is back-adjusted (back-adjustment itself is normal/correct
for strategy backtesting: it trades absolute historical price accuracy
for zero artificial jumps at each contract roll).

The fix: treat that TradingView export as a **frozen, one-time snapshot**
(internally self-consistent -- every roll in that single file is
adjusted relative to the same anchor date) rather than something to
re-export and swap out, and only ever *extend* it forward with fresh,
**real, unadjusted** front-month `.scid` data (which needs no
back-adjustment math at all, since the current/front contract always
carries a +0 offset by definition). `../data/build_es_h1_continuous.py`
does exactly this:

1. Takes `../data/es-h1-2015-14aug2026.csv` as-is for all history through
   its own last bar (2026-08-14).
2. Appends real H1 bars built from the local Sierra Chart
   `F.US.EPU26.scid` file (current front contract, read via
   `D:\acheron\AcheronUtils\scidReader.py`) for every bar after that.
3. Re-run any time new `.scid` data lands to keep the tail current; once
   the front contract itself rolls (U26->Z26 in Sep 2026), add the new
   quarter's symbol to `FRONT_CONTRACTS` in that script (see its
   docstring for the reverse-engineered TradingView roll-timing rule --
   3 business days before 3rd-Friday expiry, 17:00 CT session open --
   confirmed bar-for-bar against real `.scid` overlap data for both 2026
   rolls).

### 2026-only reconstruction (`build_es_h1_2026_backadjusted.py`)

A separate, narrower script/dataset --
`data/build_es_h1_2026_backadjusted.py` / `data/es-h1-2026-backadjusted.csv`
-- independently rebuilds a 2026-only back-adjusted series entirely from
local `.scid` contract data (H26/M26/U26), without relying on the frozen
TradingView export at all. This is what originally reverse-engineered
TradingView's roll rule (documented in its module docstring) and is kept
because `render_lxpb_retest_1s_report.py` (below) imports it directly for
its roll-timing logic (`roll_switch_utc`) to pick the correct real
front-month contract's 1-second ticks per retest. Validated result:
Open/Close match the TradingView export almost exactly (mean diff
~0.002-0.003pt, 100% of bars within 1pt across all three 2026 contract
segments); High/Low differ by up to ~1-1.5pt on ~40% of bars from
ordinary cross-vendor tick noise (acceptable/expected, not a bug). No
artificial jumps at either roll boundary.

## 2026 retest report: H1 + 1s bid/ask volume (`render_lxpb_retest_1s_report.py`)

A second, non-hand-labeling HTML report: one expandable row per completed
2026 `detect_lxpb_h1` retest (run on `data/es-h1-2026-backadjusted.csv`,
which only contains 2026 bars, so every retest found on it is already a
2026 retest), **excluding gap instances** from the rendered rows (see
below), showing on expand:

1. **H1 context chart** -- formation -> breakout -> retest, reusing
   `render_labels_report.build_row_chart` unchanged (same gap-compressed
   window, markers, confluence price-lines), centered horizontally (fixed
   candle width via `barSpacing`, not `fitContent()` -- blank space is
   split evenly left/right instead of stretching a handful of bars to
   fill a wide monitor).
2. **1s candles + Bid Volume + Ask Volume** (left column), synced pan/zoom
   + crosshair (same lightweight-charts@4 engine as
   `../lxpb-es-vol/render_report.py`), built from REAL ticks read directly
   from the local Sierra Chart `F.US.EP{H26,M26,U26}.scid` files
   (`D:\SC\Data`), choosing whichever contract was actually front-month at
   that retest using the exact same roll-switch instants
   (`roll_switch_utc`) as `build_es_h1_2026_backadjusted.py` -- so the
   bid/ask volume always comes from the real traded contract, never a
   fixed/wrong one. Because an H1 bar only pins a retest to a whole hour,
   this script first finds the actual 1-second bar within that hour where
   price really touched (or gapped past) the level -- lxpb.py's Phase 3
   touched/gap_over rule, applied at 1s resolution -- and centers the
   chart on that instant (`--pad-seconds`, default +/-45s -- tight enough
   that the retesting candle itself is clearly visible).
3. **1min candle chart** (right column), standalone (own crosshair legend,
   not pan/zoom-synced to the 1s trio) -- +/-`--one-min-pad-minutes`
   (default 20) around the retest instant, resampled from the same 1s
   ticks (no second `.scid` fetch), giving broader before/after context
   than the tight 1s window; other nearby same-type LXPB levels are drawn
   as unlabeled dashed background lines (same confluence tolerance/colors
   as the H1 chart), but formation/breakout markers are NOT shown here --
   it's a zoomed-in view of the retest itself, not the level's history.

All price labels/lines are in ADJUSTED (back-adjusted continuous) terms
across all three charts; the RAW price actually traded (and which
contract) is shown once per row in the results table's "Raw (Contract)"
column rather than repeated on every chart panel.

**Gap exclusion**: retests are excluded from the rendered rows (though
`lxpb.py`'s own consuming/invalidation logic in `advance_one_bar` is
untouched -- this is purely a report-display filter applied after
detection) if either:
- **gap breakout** -- the level's breakout bar opened/stayed entirely
  beyond the level (`breakout_low > price` for LHPB, `breakout_high <
  price` for LLPB) -- lxpb.py Phase 2's gap-breakout branch, or
- **gap retest** (gap-over) -- the retest bar gapped clean past the level
  instead of actually touching it, i.e. `NOT(retest_low <= entry_price <=
  retest_high)` -- lxpb.py Phase 3's gap-over branch.

A **"Vol @ Retest" filter** (checkbox chips, default both checked) flags
whether the same-side volume -- Bid Volume for LHPB/LONG retests, Ask
Volume for LLPB/SHORT retests -- on the EXACT 1s bar the retest touched on
exceeds `--vol-threshold` (default 300); the results table also shows the
raw Bid/Ask volume at that instant as columns.

```
python render_lxpb_retest_1s_report.py [--limit 0] [--order desc] [--pad-seconds 45] [--one-min-pad-minutes 20] [--vol-threshold 300]
```

`--limit 0` (default) renders every non-gap 2026 retest (859 as of the
latest `es-h1-2026-backadjusted.csv`, out of 922 total -- 63 gap
instances excluded); `--order` controls most-recent-first vs.
oldest-first. Each row embeds its own 1s candle/bid/ask + 1min JSON
(unlike the H1-only labeling report above), so the full-2026 output is
large (~23MB) -- acceptable, but pass `--limit N` for a quicker/smaller
build while iterating. Output: `lxpb_retest_1s_report.html` (disposable
build artifact, regenerate any time).



## Which level to take when a retest sweeps several at once (`analyze_retest_cluster_selection.py` + `render_cluster_selection_report.py`)

A single retest move -- one large H1 bar, or a run of 2-3 consecutive bars
going the same way -- often trades back through **several** pending LXPB
levels at the same time. These two scripts exist to answer: *out of those
nearby levels, which one should you actually take?*

### The measurement trap this is built around

The deepest level swept is, mechanically, the one sitting at the move's
reversal point, so ex-post it will almost always show the best bounce.
That does not make "always take the deepest" a strategy: if price turns
at a shallower level in front of it, the deeper resting order **never
fills** and the trade is simply missed.

So the candidate set is **not** "levels that were touched". For every
event the scripts replay `lxpb.py`'s state machine and take a
point-in-time snapshot of `state["touch_lv1"]` *as of the bar before the
event starts* -- i.e. every level that had already broken out and was
still waiting for its retest. That snapshot is then clipped to the move's
own price band **plus `--miss-band-pts` (default 10) beyond the move's
extreme**, so levels price never reached are carried as real candidates
with outcome `MISSED`. Every selection rule is scored with **a missed
pick worth 0 R**, which is what makes fill rate and average R directly
comparable across rules.

### The four features under test

| # | Feature | How it is computed |
|---|---------|--------------------|
| a | **Recency to breakout** | `recency_bars` = H1 bars from the level's **formation** bar to its **breakout** bar (LXPB = "last high *pre-breakout*"). The alternative reading, breakout -> retest, is also computed and shown in grey as `bars_breakout_to_event` -- see "Assumptions" below. |
| b | **Swing level** | `find_swings` from `patterns_pure/` (ATR ZigZag, `atr_mult 0.75`, ATR-21) run over the trailing 750 bars **ending at the event's first bar**, so no future data. The level counts as a swing only if the pivot's own high/low equals the level price. `lxpb.py`'s cruder 3-bar `is_swing` is carried alongside as `is_swing_lxpb`. |
| c | **Spike candle** | `find_shooting_star` for an LHPB, `find_hammer` for an LLPB, from `patterns_pure/`. **Note:** `lxpb.py` uses the opposite mapping (LHPB -> hammer); that is carried as `is_spike_lxpb` for comparison and `lxpb.py` was left untouched. |
| d | **Large wick** | `candle_utils.has_large_upper_wick` for LHPB / `has_large_lower_wick` for LLPB (40% of range), with the raw `wick_pct` shown too. |

Recency is min-max normalised **within each cluster** (1.0 = the level
that formed closest to its own breakout, relative to its peers in that
same event) and added to 0/1 for swing, spike and wick -- all at weight
1.0 by default (`--weights`). One level per event wins; ties break on
lower `recency_bars`, then shallower.

### 1-second ground truth

Each event pulls one real tick slice from the `F.US.EP*.scid` files
(same roll-switch and back-adjustment rules as everything else here) and
every candidate is resolved by indexing **forward from its own fill tick**
with numpy -- a clock-minute is never re-fetched and re-scanned, so this
is structurally immune to the pre-entry look-ahead bug documented in
`../AGENTS.md`. Fill realism matches `analyze_breakout_exits_1min.py`
(long entry needs `BidVolume>0`, long target needs `AskVolume>0`, stops
fill on either side, a same-tick stop/target tie resolves to the **stop**).

Ticks are fetched 30 minutes *before* the event purely so the 1min
context chart has "before" bars; fill scanning still starts at the
event's own first bar.

Per candidate you get `immediate_bounce` (points gained before price
trades `--break-pts`, default 2, through the level -- the comparable "did
*this* level react" number), `mfe`/`mae`, `held`, and a stop/target
outcome in R.

### Report

`render_cluster_selection_report.py` writes
`lxpb_cluster_selection_report.html`, laid out like
`stop2_target2_trades_report.html`: one expandable row per candidate,
grouped by event, opening into an **H1 chart with the features drawn on
it** -- the P0 marker spells out the feature values, swing pivots are
marked `sh`/`sl`, and every level in the cluster is a labelled price line
carrying its depth rank and outcome (`MISSED` included) -- plus the real
1s trio (candles + bid/ask volume) and a 1min context chart. Candidates
that were never reached centre their tick charts on the second of
**closest approach**, annotated with how many points short the move
stopped.

Each row has hand-check boxes (`recency_ok`, `swing_ok`, `spike_ok`,
`wick_ok`, `pick_ok`) and a notes field, persisted to `localStorage` and
exportable to CSV so the verdicts can be handed back to correct the
feature definitions.

```
python render_cluster_selection_report.py [--limit 0] [--start 2026-07-01] [--end 2026-08-31] \
    [--move-bars 3] [--miss-band-pts 10] [--min-cluster-size 2] [--min-retested 2] \
    [--stop 2] [--target 2] [--horizon-hours 6] [--bounce-seconds 300] [--break-pts 2] \
    [--weights recency=1,swing=1,spike=1,wick=1] [--output lxpb_cluster_selection_report.html]
```

`analyze_retest_cluster_selection.py` takes the same analysis flags and
prints the stat tables without building any HTML (add `--no-ticks` for a
fast structure-only pass, which skips the `.scid` load entirely and
reports feature base rates instead of outcomes). It also writes a
per-candidate CSV. Pass `--limit N` for a quick smaller build -- the full
default window is 73 events / 268 candidates / ~7.7MB.

### Assumptions to confirm

These were judgement calls; the report states them in a box at the top
and the hand-check columns exist to correct them:

1. **Recency** is read as formation -> breakout, not breakout -> retest.
2. **Spike** uses the strict patterns-pure definition, which only fires on
   ~7% of candidates -- possibly too strict.
3. All four features carry **equal weight**, pending the exported labels.
4. Defaults `--move-bars 3`, `--miss-band-pts 10`, `--break-pts 2`,
   `--bounce-seconds 300`, stop/target 2/2.

### `patterns_pure/`

Byte-identical copies of `find_ATR.py`, `find_swings.py`, `find_hammer.py`,
`find_shooting_star.py` and `candle_utils.py` from
`D:\daily-analysis\patterns-pure`, vendored so this repo is
self-contained. Imported via `sys.path`, not as a package. See
`patterns_pure/README.md` for provenance and the refresh command.

## Same-side confluence fine-tuning (`render_ss_confl_finetune_report.py`)

The SS1/SS2 reports filter strong H1 retests by same-side H1 confluence,
merge connected qualifying levels into one trade, then select the highest
eligible H1/M5 price for LLPB shorts or the lowest for LHPB longs.

The **Merged H1 levels** column lists the distinct H1 member prices, highest
first for LLPB shorts and lowest first for LHPB longs. A single-member trade
lists its own H1 price. This replaces the post-merge external SS count and
its numeric table filter; `--ss-confl-min` still qualifies candidates before
merging. M5 entry levels and external H1 support are not cluster members.

The historical **Confl.** column and its numeric filter are no longer
generated. The generator does not aggregate historical external-level
counts; same-side confluence remains in use for SS qualification,
clustering, entry selection and the blue H1 chart overlays.

The H1 radius is **+/-5.25 points** by default. It applies throughout SS
qualification, clustering and H1 entry selection, not just deduplication.
The M5 entry search remains **+/-5 points** around each cluster member.
The original stop/target reports retain their own +/-2.5-point H1 radius.

The 5.25-point default is the smallest H1 link that joins the Jan-20
full-year SS1 groups previously displayed as rows 54 and 56: their nearest
members are 7014.25 and 7009.00. This is a structural threshold, not a
profit-optimized one. Widening it admits additional candidates as well as
merging existing ones. Connections are transitive, so a cluster's total
price span can exceed the radius and its members can have different
breakout/retest bars. Existing level-liveness and fill rules still apply.

The **3-hour entry window starts at the refined H1 level's own retest
candle start**, not the original cluster anchor's retest or the refined
level's exact intrabar touch. Select the H1 reference from the existing
entry pool first, then use that level's actual retest/consumption bar from
its lifecycle, not a clipped chart endpoint. If it has not yet been
retested/consumed, the window has not started and the order stays UNFILLED;
there is no fallback to the original anchor. The interval includes its
start and excludes its end (`--max-alt-fill-hours`, default 3).

The **Refined H1 retest** column shows this window start; its tooltip
retains the original H1 retest and the window end. For SS2 full-year row 0,
the window is January 1, 2026 **18:00-21:00 PT**, rather than 15:00-18:00.
Original entry qualification, cluster identity and the fixed 2/8 baseline
still use the original H1 setup.

Execution charts (1s candles, bid/ask volume, 1-minute context and footprints)
load around the exact filled-entry timestamp, even when the order fills
after the original H1 retest hour. Unfilled orders instead show the full
refined-H1 entry-search window in the M5 and 1-minute panes; they do not
have execution-centered charts or footprints. No fill-window pane is
fabricated when the refined H1 level has not yet been retested.

The **target** is the most recently formed **P0**, not the nearest price or
the latest P1: a live opposite-type M5 level on the favourable side of the
actual fill (LHPB below an LLPB short, LLPB above an LHPB long), 1-20 points
away inclusive. Its breakout candle must have broken at least **two distinct
same-type P0 levels**. Historical peers still count if subsequently consumed,
but the target itself must remain live. With no eligible target, the fixed
fallback remains 8 points (`--fallback-target`).

The **stop** comes from live same-side M5 levels whose prices are within
**+/-10 points of the actual fine-tuned fill**. For an LLPB short, use the
highest high of their breakout candles **plus one tick (0.25)**; for an LHPB
long, use the lowest low **minus one tick**. This search does not require a
shared breakout candle. The radius applies to level prices, not candle
extremes or stop width. If no protective candle stop qualifies, use a
**4-point fallback** (`--fallback-stop`; `--stop` remains an alias).

Both exit searches use the ledger state immediately before the fill's M5
bar, so breakout candles must already be complete and current-bar OHLC or
later lifecycle events cannot influence the bracket. Neither search has a
fixed formation lookback. Source badges identify dynamic and fallback
exits; hover for the selected P0/P1 details. Reward:risk, realized R and
excursion percentiles in R all use each trade's own stop distance. The
fixed 2/8 baseline is unchanged.

```powershell
python render_ss_confl_finetune_report.py --ss-confl-min 1
python render_ss_confl_finetune_report.py --ss-confl-min 2 --full-year
python render_ss_confl_finetune_report.py --fallback-stop 4 --fallback-target 8
python render_ss_confl_finetune_report.py --h1-confluence-points 2.5 --output previous_zone_report.html
```

Each report displays both radii and the resulting cluster count. Use
`--h1-confluence-points` to reproduce or compare another H1 zone without
changing the M5 entry search.

## LXPB level ledger cache (`lxpb_levels_cache.py`)

`lxpb.detect_lxpb_h1(bars)` answers *"what does the machine hold at the end of
this series?"* — it returns `(touch_lv0, touch_lv1, retests)` as of the **last
bar handed to it**, where `retests` means *consumed / dead*. It does not answer
*"which levels were live at time T?"*, and every attempt to make it do so by
re-running over `bars[:T]` is slow and has, twice, produced silently wrong
charts (see `../AGENTS.md`).

`lxpb_levels_cache.py` records the **full lifecycle of every level, once**, and
persists it to Parquet. Reports and backtests then answer as-of questions with a
cheap interval test instead of a replay.

### The ledger

One row per level:

| column | meaning |
| --- | --- |
| `type` | `LHPB` / `LLPB` |
| `price` | level price |
| `formation_time` | bar that registered it |
| `is_swing` | finalized on the following bar |
| `breakout_time` | close through the level, `NaT` if never broken |
| `death_time` | when it left the machine, `NaT` if still live at end of data |
| `fate` | see below |
| `retest_time`, `fta`, … | populated for `fate == "retested"` |

`fate` is one of:

- `retested` — completed a retest. **This is the trade signal**, and the only
  fate the existing analysis scripts ever saw.
- `died_unbroken` — traded into the level but never closed through; discarded.
- `died_broken` — broke out, then was touched again before
  `MIN_HOURS_BEFORE_RETEST`; silently consumed.
- `alive_formed` / `alive_broken` — still held when data ran out.

The two `died_*` fates are the point of the ledger. Of 22,518 H1 levels,
**17,676 (78%) die silently** and appear in none of `detect_lxpb_h1`'s three
buckets — so any code that reconstructs history from those buckets is
reconstructing it from the 22% that happened to survive.

### Query API

```python
import lxpb_levels_cache as LC

lv   = LC.h1_levels()                    # whole H1 series
lv   = LC.m5_levels(seg_idx)             # per contract segment
lv   = LC.m5_levels_for_ts(ts)           # segment resolved from a timestamp

live = LC.levels_live_as_of(lv, ts, type_="LLPB")
sig  = LC.retests(lv)                    # the trade population
bars = LC.m5_bars_for_contract(seg_idx)  # whole-contract M5 OHLC, cached
```

`levels_live_as_of` treats a level as live over **`[formation_time,
death_time)`** — one consumed exactly at `ts` is *not* live at `ts` — and adds a
`stage` column, `broken` if `breakout_time <= ts` else `formed`.

Build/refresh everything and print a summary:

```
python lxpb_levels_cache.py --build-h1 --build-m5 all --stats
```

Cache files land in `data/levels_cache/`. H1 is ~0.7 MB / ~3 s to build; M5 is
~0.7–2.4 MB per contract and ~4–45 s. After that, loads are effectively free.

### How it stays honest

It is a pure **external observer**: it steps `lxpb.advance_one_bar` and diffs
the bucket lists between bars to see which levels departed and when. It never
re-implements a rule, so it cannot drift from the strategy definition. This
matters because `../lxpb.py` is a synced copy of
`D:\daily-analysis\lxpb-h1-apr2026\lxpb_h1_detect.py` and must stay untouched.

### Keeping it current

Source data changes in three ways, and only one of them needs a rebuild.
`_reconcile` compares the incoming bars against the cached meta and picks the
cheapest correct route:

| Situation | Route | Cost |
| --- | --- | --- |
| Same bars | `hit` | instant |
| **Contract rollover / TradingView re-export** — every price shifts by a constant | `reanchor` | ~10 ms |
| **New bars appended** — open levels may now break, retest or die | `extend` | ~0.2 s |
| A bar revised or backfilled *inside* the existing range | `rebuild` | 3–45 s |
| `lxpb.py` rules or `ALGO_VERSION` changed | `rebuild` | 3–45 s |

**Rollover.** Back-adjustment moves every price by a constant but changes no
market structure, and the state machine is *shift invariant* — every test in it
is a price comparison or a ratio of price differences, with no absolute
thresholds. So the entire lifecycle is provably unchanged and only the 12
`PRICE_COLS` move. The ledger is stored in the scale it was built in
(`state_anchor`), and `h1_levels()` / `m5_levels()` shift it onto the current
scale on the way out, so callers always get prices matching today's charts. A
rollover is therefore a metadata event, not a recompute.

**Append.** Rows that reached a terminal fate can never change again, so only
the still-open ones are recomputed: the machine's state is pickled beside the
ledger and *resumed* on the new bars. The state and observer are pickled as one
object graph on purpose — the observer tracks levels by object identity, so
pickling them separately would break the identity walk and make every level look
like it departed on the first resumed bar.

Because the stored ledger and the persisted state share one scale that never
moves, a rollover and an append arriving *together* compose safely: the handful
of new bars is shifted into the state's scale, and the finished ledger is
shifted out to the current one.

Detection relies on `_bars_fingerprint` (exact content) and `_shape_fingerprint`
(shift-invariant). Both hash **every** bar rather than sampling head/tail plus a
row count — sampling is O(1) but cannot see a mid-series revision, and would
serve a ledger built from superseded prices with no sign anything was wrong.
Full hashing costs ~2 ms (H1) / ~20 ms (M5) against a 3–45 s rebuild.

Cache files have **stable names** (`h1_levels.parquet`, `m5_levels_EPH26.*`) and
are overwritten in place. Content-addressed names would orphan a file per
refresh and, worse, leave `_reconcile` unable to find the previous ledger to
extend or re-anchor — every update would silently degrade into a full rebuild.

### Verification

Two scripts, both of which should pass before trusting the cache:

- `verify_levels_cache.py` — the ledger equals a direct as-of rerun of the state
  machine (29/29 H1 and 17/17 M5 cut points exact, identical retest sets), and
  the two `build_m5_chart` rows that exposed the M5 bugs still render the levels
  a human verified off the charts.
- `verify_cache_updates.py` — every incremental route (`hit`, `extend`,
  `reanchor`, `reanchor`+`extend`, and a forced `rebuild` after a mid-series
  revision) produces a ledger **identical** to a from-scratch build.

Two gotchas if you write your own comparison — ~120 H1 retests have `fta = NaN`
(breakout and retest split by a weekend gap, so no intervening bar updated
`running_fta`), and NaN never equals itself, so stringify floats before
set-comparing.

### Consumers

`render_stop_target_report.build_m5_chart` uses it for both the M5 bars and the
M5 level rays it overlays on the H1 level being traded. New backtests should
prefer it over calling `detect_lxpb_h1` directly for anything historical.


## MAE / MFE excursion columns (`render_stop_target_report._compute_excursion`)

The trades report carries two excursion columns, both measured in points and
both only ever populated for one outcome:

- **MAE (win)** -- how far a *winning* trade went against the position before it
  reached target. Blank (`-`) on losses.
- **MFE (loss)** -- how far a *losing* trade went in favour of the position
  before it was stopped. Blank (`-`) on wins.

### The window is `[touch_time, exit_time]`, strictly

The excursion is measured over the exact life of the trade: from the touch
(entry) timestamp through the pinned exit timestamp, both inclusive, at
1-second resolution on real ticks. Interior whole minutes are read from the
cached 1-minute bars; the **first and last minutes are re-fetched at 1s and
clipped** to the trade's own bounds, because a minute bar that merely
*contains* the entry also contains price action from before it.

Do **not** relax the clip -- e.g. flooring `touch_time` to the second to pick up
a few more ticks. That re-introduces pre-entry movement and is the same class
of look-ahead bug as the `_pin_exact_exit` `not_before` fix. One real example:
a trade whose strict window holds 39 ticks (MFE 1.00) grows to 146 ticks
(MFE 5.25) if the entry bound is floored -- a 5x inflation that is entirely
price the trade never actually saw.

### Why the naive version was wrong

The original implementation selected minute *bars* with
`bars.index >= touch_time & bars.index <= exit_time`. Because a bar is indexed
by its **start**, this had two independent failures:

1. A trade that opened and closed inside a single clock-minute selected **no
   bars at all** and reported `-`. This silently blanked 136 of 413 losing rows
   in the 2026 full-year report.
2. The entry's own partial minute was **always** dropped (its start precedes
   `touch_time`), so every scan began at the next minute boundary and discarded
   the most volatile part of the trade.

Both are fixed; the function's docstring records the reasoning so the
bar-selection shortcut does not come back.

### Excursions are clamped at 0 by seeding with the entry price

The position is *at* the entry price at t=0, so neither excursion can be
negative by definition. The scanned high/low range is therefore seeded with
`raw_entry` before the excursions are derived. For a normally-filled trade this
is a strict no-op.

### Gapped entries (the warning marker)

Entries are modeled at the level's own price with **no slippage**. Occasionally
a tick jumps clean through the level, so the entry price is never actually
traded between touch and exit -- the fill was not available in reality. That is
detected as `raw_entry` falling outside the scanned range *before* seeding, and
is surfaced two ways:

- a warning glyph next to the entry price in the trade row, with a tooltip, and
- a **gapped entry** count in the summary boxes.

In the 2026 full-year run this affects 5 of 502 trades. The most extreme has
`touch_time == exit_time` to the microsecond: a single tick, ~12 points beyond
the level, blowing through both the level and the stop in one print.
