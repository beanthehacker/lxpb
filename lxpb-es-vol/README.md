# LXPB + Volume-Absorption Strategy (lxpb-es-vol)

Ported wholesale from `D:\daily-analysis\lxpb-volume-strat\` (the
"originating" repo/session where this strategy was developed). All logic,
file layout, and comments are preserved as-is; only cross-repo import
paths and hardcoded absolute paths were adapted to work standalone inside
`D:\lxpb`. See "Changes made during the port" below for the exact diffs.

Combines a 1-second order-flow absorption detector with the H1 LXPB
(Last High/Low Pre-Breakout) stacked-level confluence filter from
`../lxpb.py` (this repo's canonical H1 detector), to flag high-conviction
reversal setups: aggressive, one-sided volume that gets absorbed right at
a cluster of previously broken-out H1 support/resistance levels.

Originated from an ad-hoc analysis of ES on 2026-08-13 08:00-09:30 PT; the
daily-analysis version documents the full research history in its own
README.

## Pipeline

```
export_es_1s_pt.py       -> ES_20260813_0800-0930_PT_1s.csv   (1s OHLCV + Bid/AskVolume from .scid,
                              hardcoded to the original 08:00-09:30 PT session)
export_es_1s_range.py     -> generalized version of the above: CLI args (start_pt, end_pt, out_csv)
                              for arbitrary date/time ranges. Used to export the full-August dataset.
absorption_detector.py    -> ES_20260813_absorption_events.csv (session-wide z-score absorption scan)
absorption_backtest.py    -> build_features()/flag_events() (local-window absorption detector +
                              toy fixed-R backtest), ES_20260813_absorption_backtest_trades.csv
lxpb_confluence.py         -> H1 LXPB state machine wrapper (2h retest rule, stacked-level counter,
                              1s-resolution intra-hour consumption tracking), now wrapping
                              ../lxpb.py instead of daily-analysis's lxpb_h1_detect.py. Current
                              default scorer is the fast interval-based build_level_intervals()/
                              level_intervals_confluence_at() pair (see "Caching & performance"
                              below); the older per-bar dict approach is kept as LEGACY reference.
lxpb_cache.py              -> disk cache (pickle, keyed on input-file mtime/size) wrapping the two
                              expensive steps (H1 snapshot build, 1s level-interval build) so
                              repeated scans against the same CSVs recompute nothing.
combine_and_scan.py        -> scan(csv_1s_path, out_csv_path, verbose) (zone-first scan: LXPB
                               confluence is checked for candidate bars first; the order-flow
                               absorption pattern gates which bars are candidates. Output =
                               confirmed signals only.) Defaults to the original 08:00-09:30 PT
                               session but can be pointed at any 1s CSV.
run_august_scan.py         -> driver script: runs scan() against the full-August 1s dataset,
                              -> lxpb_volume_strat_triggers_august.csv
render_report.py           -> lxpb_volume_strat_report.html  (v25-style dark dashboard: summary,
                               filters, table, per-row expandable H1 context + 1s candles +
                               bid/ask volume charts rendered with TradingView
                               lightweight-charts@4)
render_report_august.py    -> lxpb_volume_strat_report_august.html (same renderer, pointed at
                               the full-August dataset/triggers)
render_report_full.py      -> lxpb_volume_strat_report_full.html (same renderer, pointed at the
                               FULL .scid-history dataset/triggers)
consumption_impact_diagnostic.py -> compares the strict "currently active only" confluence
                              definition against a "level ever existed nearby" history-aware
                              definition, to gauge how much the base LXPB consume-on-touch rule
                              suppresses legitimate confluence signal.
lxpb_stop_target_optimization.py -> MFE/MAE profiling, stop x target grid search, stop-reference
                              (impulse extreme vs cluster extreme) comparison, and volume/
                              follow-through feature analysis against the full-history signal set.
plot_es_candles_volume.py  -> standalone Plotly 1s candlestick + bid/ask volume chart with
                              absorption-event markers, for a given time-of-day window.
```

Run in order: `export_es_1s_pt.py`/`export_es_1s_range.py` (only needed once
per date range) → `combine_and_scan.py` (or `run_august_scan.py` for the
full-month scan) → `render_report.py` (or `render_report_august.py` /
`render_report_full.py`).

For the full strategy rationale (dashboard chart layout, confluence rules,
absorption-pattern definition, level-test gate, backtest results, stop/
target optimization findings, caching/performance notes, known
limitations) see `D:\daily-analysis\lxpb-volume-strat\README.md` — that
narrative is unchanged by this port and is not duplicated here to avoid
drift between two copies.

## Changes made during the port

- **`lxpb_confluence.py`**: now imports `../lxpb.py` (`import lxpb as L`)
  instead of daily-analysis's `../lxpb-h1-apr2026/lxpb_h1_detect.py` +
  `helpers/load_ohlc_data.py`. Behavior is unchanged — this repo's
  `lxpb.py` is a self-contained port of the same corrected H1 detection
  logic (see `D:\lxpb\README.md`), and `L.MIN_HOURS_BEFORE_RETEST = 2` is
  still applied as a module-global override the same way.
- **`combine_and_scan.py`**, **`consumption_impact_diagnostic.py`**,
  **`render_report.py`**: `H1_CSV` now points at this monorepo's shared
  canonical `../data/es-h1-continuous-backadjusted.csv` — a single,
  back-adjusted, jump-free ES H1 series covering 2015-01-01 through
  present (built by `../data/build_es_h1_continuous.py`; see
  `../label-review/README.md`'s "ES H1 data" section for how it's built
  and kept up to date). This supersedes an earlier local
  `../data/es-h1-4apr2021-11apr2025.csv` (2021-04-04 through 2025-04-11
  only, still used by the unrelated `../simulate-joined-retests.py`
  legacy pipeline), which did not cover the bundled example 1s session's
  date (2026-08-13); the new canonical file's data range does (verified
  directly against the CSV: it has H1 bars for 2026-08-13). Note: this
  repo currently has a separate, pre-existing issue unrelated to this H1
  data change -- `absorption_backtest.py` (imported by `combine_and_scan.py`)
  is missing from disk though still tracked in git -- so an actual
  end-to-end run currently cannot be smoke-tested here until that's
  resolved.
- **`combine_and_scan.py`**: added a defensive column-typed empty
  `DataFrame` fallback in `scan()` so a zero-signal run (see caveat above)
  still writes a valid (empty) output CSV instead of raising `KeyError` on
  `sort_values("time_pt")`.
- **`run_august_scan.py`**: paths now relative to this folder instead of
  hardcoded to `D:\daily-analysis\lxpb-volume-strat\...`.
- **`export_es_1s_pt.py`/`export_es_1s_range.py`**: unchanged — the
  `.scid` reader dependency (`D:\acheron\AcheronUtils\scidReader.py`) and
  data file (`F.US.EPU26.scid`) are absolute external paths independent of
  which repo this code lives in, and were confirmed present on this
  machine.
- **Not ported**: `ES_202608_full_1s.csv` (~88MB) and `ES_full_1s.csv`
  (~472MB) — the full-August and full-`.scid`-history 1-second exports.
  Too large to duplicate into this repo; re-export via
  `export_es_1s_range.py` if needed, or copy manually from
  `D:\daily-analysis\lxpb-volume-strat\` if still present there. All
  smaller reference/output CSVs and HTML reports generated from them
  (e.g. `lxpb_volume_strat_triggers_full.csv`,
  `lxpb_volume_strat_report_full.html`) *are* included for reference.

## Verified after porting

- `lxpb_confluence.py` imports cleanly and picks up `../lxpb.py` with
  `MIN_HOURS_BEFORE_RETEST = 2`.
- `combine_and_scan.py` runs end-to-end against the bundled
  `ES_20260813_0800-0930_PT_1s.csv` + local H1 CSV (0 signals, per the
  date-coverage caveat above — pipeline completes without error, caching
  via `lxpb_cache.py` confirmed working on a second run).
- `absorption_detector.py` and `absorption_backtest.py` run standalone
  against the bundled session CSV and reproduce the same output shape as
  the original (61 backtest trades, 37.7% win rate).
- `lxpb_strategy_backtest.py`'s default CLI args point at the (not
  ported) full-August dataset, same as the original script — pass
  explicit `triggers_csv`/`csv_1s` args pointing at any available data to
  run it against something smaller.
