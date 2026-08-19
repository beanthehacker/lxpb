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
| `--data` | `data/es-h1-continuous.csv` | H1 OHLC CSV to run `detect_lxpb_h1` against -- own locally-built, non-back-adjusted continuous series (2024-08-19 through present; see "ES H1 data" below) |
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
- `data/es-h1-continuous.csv` -- default input dataset, built by
  `data/build_es_h1_continuous.py`. Covers 2024-08-19 through present.

## ES H1 data

`../data/es-h1-2015-14aug2026.csv` (used elsewhere in this monorepo) is a
TradingView "ES1!" **back-adjusted** continuous contract: every re-export
recalculates all historical bars relative to whichever contract is
currently front-month, so old absolute price levels are not real traded
prices and drift over time. Verified by diffing two export vintages ~16
months apart for identical timestamps: average +280pt / up to +412pt
difference on the same historical bar. This makes any absolute-price-level
logic (S/R zones, LXPB levels) non-reproducible and historically
inaccurate the further back you go (near-zero drift right at the export's
anchor date, growing to hundreds of points a couple of years back).

For **label-review only** (this fix is intentionally scoped here and does
not touch `../data/es-h1-2015-14aug2026.csv` or anything else in the
monorepo, so other tools/tests that depend on it are unaffected),
`data/build_es_h1_continuous.py` builds a real, non-back-adjusted,
contract-tagged continuous H1 series instead, by splicing:

1. **Recent window** (real front-month tick data): local Sierra Chart
   `.scid` files (`D:\SC\Data\F.US.EP{H26,M26,U26}.scid`), read via
   `D:\acheron\AcheronUtils\scidReader.py`, restricted to each contract's
   real front-month window per CME's standard quarterly roll calendar
   (switch ~8 calendar days before the expiring contract's own 3rd-Friday
   expiry) -- verified byte-exact against TradingView's own current-quarter
   bars.
2. **Older window** (back to 2024-08-19, the oldest free intraday data
   available with no back-adjustment): Yahoo Finance's public,
   unauthenticated chart API for `ES=F` (continuous front-month, never
   back-adjusted) -- verified against known real historical prints
   elsewhere (e.g. the actual March 2020 COVID-crash low).

Every row is tagged with its `contract`/`source` for provenance, and
re-running the build script reproduces identical historical bars every
time (unlike the back-adjusted TradingView export). **Known limitation**:
freely-available, unadjusted *hourly* data only goes back to 2024-08-19
(Yahoo's intraday history cap) -- extending real unadjusted H1 further back
would need a paid tick-data vendor, so this dataset intentionally does not
cover 2015-2024.

