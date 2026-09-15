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

**This rule is repo-wide, not just `lxpb-v2`.** `label-review` and
`retest-vol-scalp` follow it too, through `../es_h1_display.py` — a smaller
loader over the same export list, for subprojects that deliberately avoid
importing lxpb-v2's report modules. Keep the two export lists in step.
`data/es-h1-continuous-backadjusted.csv` and its builder
`data/build_es_h1_continuous.py` are retired; nothing reads them.

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
solely to patch that. (Re-anchoring pre-roll M5 exports, below, is different:
its offset is measured from the exports' own overlap on every load and never
stored.) Only the roll TIMING rule (`B26.roll_switch_utc`) is still
taken from `build_es_h1_2026_backadjusted`; the contract list itself is
`render_labels_report.CONTRACTS` (U23 onward, wrapping `B26.CONTRACTS`). The roll rule's
3-business-day count skips market holidays, and a holiday expiry Friday moves back a
day first: TradingView rolled one session early in Jun 2024, Jun 2025 and Jun 2026
(Juneteenth inside the count, or on the expiry Friday itself), and a weekday-only
count read the wrong contract's ticks for that session.

## Validation, and what a failure means

Every continuous series is checked before any caller sees it:

- `_assert_one_vintage` (H1) — the H1 list is a single vintage: its exports
  must agree to the tick wherever they overlap. The H1 export is the genuine
  post-roll reference the M5 series is checked against, so it is never
  shifted; replace it with a fresh export instead.
- `_merge_vintages` (M5) — old M5 history cannot be re-exported after a
  roll, so `DISPLAY_M5_PATHS` holds one inner list per roll vintage and all
  older history is shifted onto the next vintage by an offset MEASURED at load
  on their overlap. Each inner list must itself be one vintage
  (`_assert_one_vintage`). It raises unless the overlap is at least 1,000 bars, the
  difference there is one constant on >= 99% of them and the same constant in
  every contract segment the overlap spans, and a roll lies between the two
  vintages' last bars (a shift with no roll is a revised export, not a
  re-anchor). Never store that offset as a constant, and never shift an
  export that fails these checks — drop it.
- `_assert_no_roll_gaps` — no unexplained close-to-open jump at a roll
  instant. A jump there means the splice is wrong. Fix the splice; falling
  back to per-contract data is never the answer.
  A roll that lands on a weekend/holiday REOPEN is skipped with a printed
  note, since its bar jump includes the real closure gap (Jun 2025: 29pt,
  matching U25's own ticks); `_measure_scid_offset` still checks the splice.
- `_assert_shares_h1_scale` — every contract segment of the M5 series must
  agree with the H1 export (>= 99% of shared bars). This is what verifies the
  re-anchored history outside the export overlap, segment by segment, rather
  than assuming a roll moves every segment alike.
- `_report_series_gaps` — prints (does not raise) any hole longer than a
  long holiday weekend. A hole is legal but never harmless: the state
  machine walks straight across it as though the two sides were adjacent
  bars. Close it by adding an export.

## Rollover checklist

A quarterly roll re-anchors TradingView's whole back-adjusted history: every
bar of every segment moves by that roll's spread (U26->Z26 on 2026-09-14:
exactly +67.75pt on every bar back to Jul 2023). So after each roll:

1. Export one fresh H1 file reaching back as far as TradingView allows and
   make it the ONLY entry in `DISPLAY_H1_PATHS` and `../es_h1_display.py`. It
   should reach back at least as far as the M5 history, since it is the
   per-segment check on the re-anchored M5 bars.
2. Export the latest M5 range post-roll, starting weeks before the roll so it
   overlaps the newest pre-roll M5 export by thousands of bars, and put it in
   a NEW inner list at the end of `DISPLAY_M5_PATHS`. Leave every pre-roll
   list as it is: the loader re-anchors all of it and prints the offset it
   measured. Later exports made before the next roll go into that same new list.
3. Append the new front month to `render_labels_report.CONTRACTS`.
4. Check the roll instant on ticks: bars in the last session before
   `B26.roll_switch_utc` must match the outgoing contract's ticks at one
   constant offset, bars after it the incoming contract's at +0.00.
5. Level caches need no manual step: a pure re-anchor is shifted in place,
   anything else (new depth, revised last bar) rebuilds on first use.

The new front month's ticks cannot be mapped until an export holds at least
100 of its front-month M5 bars (`_SCID_OFFSET_MIN_OVERLAP`, ~8 trading hours);
`_measure_scid_offset` raises before then.
