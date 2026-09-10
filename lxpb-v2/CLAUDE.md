@AGENTS.md

# Data convention: TradingView continuous series only

**H1 and M5 bars come from TradingView's own continuous ES1! exports and from
nothing else. `.scid` data is NEVER resampled into H1 or M5 bars.** Not to
extend history further back, not to fill a hole mid-series, not "just for this
one range". Where the exports stop, the series stops, and that range is simply
absent — an empty range is a correct answer, a spliced-in second feed is not.

The two loaders are `render_labels_report._display_h1()` and `_display_m5()`
(merged from `DISPLAY_H1_PATHS` / `DISPLAY_M5_PATHS`). To extend or repair
coverage, add another TradingView export to those lists. That is the only
supported fix.

`lxpb_levels_cache.h1_levels()` runs the state machine once over the H1
series and `m5_levels()` once over `m5_bars_continuous()` (which is
`_display_m5()`). One ledger per timeframe, spanning every rollover; neither
takes a contract or `seg_idx`. Any new timeframe follows the same shape.

## `.scid` is for ticks, and the mapping is one-way

Per-contract `.scid` files remain the right source for second- and
tick-level work: real fills, exit resolution, the 1s/1min panes, bid/ask
volume, footprints. Use `render_labels_report._contract_index_for` to pick
whichever contract actually traded at a given instant.

Raw tick prices are mapped ONTO the continuous scale by adding
`render_labels_report._offset_for_ts`. **Never the reverse** — the continuous
series is never pushed back into raw per-contract terms to synthesise
structural bars. (Subtracting the same offset to compare a continuous price
against raw ticks inside a scan is fine; that is still the tick side being
read in its own terms.)

Those offsets are MEASURED per contract at load, against the display M5
export itself, over the span where that contract was genuinely front month
(`_measure_scid_offset` / `_front_month_start`). Do not reintroduce hardcoded
offset constants such as `TV_GROUND_TRUTH_OFFSETS`, and do not reintroduce a
"vintage delta" correction on top of them: a constant is only ever right for
the single export vintage it was measured against, and the delta existed
solely to patch that. Only the roll TIMING rule (`B26.roll_switch_utc`,
`B26.CONTRACTS`) is still taken from `build_es_h1_2026_backadjusted`.

## Validation, and what a failure means

Every continuous series is checked before any caller sees it:

- `_assert_one_vintage` — all exports in one list must agree to the tick
  wherever they overlap. A disagreement means one was re-exported against a
  different back-adjustment anchor; re-export both from the same anchor or
  drop one. Never paper over it with a correction factor.
- `_assert_no_roll_gaps` — no unexplained close-to-open jump at a roll
  instant. A jump there means the splice is wrong. Fix the splice; falling
  back to per-contract data is never the answer.
- `_assert_shares_h1_scale` — the M5 export must sit on the same scale as
  the H1 one.
- `_report_series_gaps` — prints (does not raise) any hole longer than a
  long holiday weekend. A hole is legal but never harmless: the state
  machine walks straight across it as though the two sides were adjacent
  bars. Close it by adding an export.
