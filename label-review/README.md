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

