@AGENTS.md

# Sessions: always work in a separate worktree

**Every new session starts by entering its own git worktree**, before any
file is edited, any script is run that writes output, or any branch is
switched. Use the `EnterWorktree` tool (or start the session with
`claude --worktree`). Do this even for a small task; if the session already
is in a worktree, stay in it.

- Never edit, commit or regen reports directly in the main checkout
  (`E:\lxpb\lxpb-v2`), so parallel sessions cannot trample each other's
  files, caches or report output.
- Read-only questions that touch no files need no worktree.
- Merge back to `main` only when the user asks.
- A worktree has no `acheron` / `scidReader` / `.scid` junctions, and
  gitignored caches and exports are absent too. Link or copy what the task
  needs from the main checkout (see the lxpb-v2 env setup memory) rather
  than regenerating or re-exporting.

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

# Spike candles (hammer / shooting star): patterns-pure only

**Every hammer or shooting-star test in this repo is patterns-pure's
`find_hammer` / `find_shooting_star`, from the vendored copy in
`patterns_pure/`, and nothing else.** Repo-wide, like the data rule above.
That definition is:

- body <= 35% of the bar's range, long wick > 50%, opposite wick <= 25%, AND
- a hammer closes at or above the PREVIOUS bar's low; a shooting star closes
  at or below the previous bar's high -- each with a 5% buffer, measured in the
  candidate candle's OWN range (`PREV_BAR_BUFFER`): a hammer may close up to
  5% of its range below the previous low, a shooting star up to 5% above the
  previous high. (Before 2026-09-21 the test was exact, no buffer.)

The wider 35% / 25% thresholds and the previous-bar close are both essential.
A single-bar version, or one with its own thresholds, is wrong even if it
looks close.

- Never restate the thresholds in code. Call the functions (they need the
  previous bar in the frame; see `render_labels_report.is_spike_pp`).
- `../lxpb.py`'s `is_spike` loads them by path; feed `advance_one_bar` only
  through `lxpb.iter_bars(state, bars)`, which attaches the per-bar verdict
  and seeds the previous bar from `state` on a resume.
- Never import from `D:\daily-analysis\patterns-pure` directly. To pick up an
  upstream change, refresh the vendored copy (`patterns_pure/README.md`); the
  level caches' rules fingerprint hashes those files and rebuilds on its own.
## Which pattern is the spike for each level type: two pairings, both deliberate

The definition above is shared; the pairing is not. Two pairings exist, and
mixing them up silently flips which P0s count as spikes:

| Pairing | LHPB (level = bar's high) | LLPB (level = bar's low) | Where |
|---|---|---|---|
| Detector | hammer | shooting star | `../lxpb.py`'s `is_spike`, which drives its candidate gate. So also the H1/M5 level caches and every reader of their `is_spike`: `ss_m5_confl2` (spike-P0 stop, H1 P0 kind), `trade_management.py`, `../retest-vol-scalp`. Also `../lxpb-spike` (own helper, same pairing). |
| Rejection | shooting star | hammer | `render_labels_report.is_spike_pp` (both copies) and its P0 Spike hint, `analyze_retest_cluster_selection.py`, `analyze_retest_features.py`, `../lxpb-spike-atr`. |

- Anything that reads a level's cached `is_spike` is on the detector pairing,
  whatever it calls the candle. Don't reinterpret it as the other pairing.
- New code that tests the formation candle itself must say which pairing it
  uses, in its docstring.
- Never switch a place from one pairing to the other, or merge them, without
  the user deciding it. It is a strategy change: for the detector it changes
  which levels pass the gate, so every ledger and report changes.
- Full table with file paths: `patterns_pure/README.md`.

## Spike-thrust candle

**A spike-thrust is the candle IMMEDIATELY after a spike** (a hammer or
shooting star by the definition above) that closes in the spike's direction
(up, close > open, after a hammer; down after a shooting star) AND meets at
least one of these four combinations -- the ONLY ones, nothing between or
beyond them:

| Thrust range vs spike candle's range | Thrust body vs its own range |
|---|---|
| >= 0.75x | >= 80% |
| >= 1.0x | >= 60% |
| >= 1.2x | >= 50% |
| >= 1.5x | >= 40% |

A candle between two rows needs the body of the row below it (1.4x needs 50%,
3x still needs 40%); nothing under 0.75x or under a 40% body qualifies.

It is patterns-pure's `find_spike_thrust` (vendored in `patterns_pure/`, rows
in its `SPIKE_THRUST_TIERS`) and nothing else, under the same rules as the
spike itself: never restate the rows or the close test inline, and change
them only in patterns-pure and then refresh the vendored copy.
Example: H1 20 Dec 2024 05:00 PT (spike = the 04:00 PT hammer; 1.76x, 48% body).

- It describes two consecutive candles and knows nothing about LHPB/LLPB. Code
  that ties it to a level must say which pairing (above) picks the spike.
- It is NOT the "thrust candle" of `ss_m5_confl2` / `trade_management.py`,
  which just means a level's P1 breakout candle, with no shape test at all.
- ONE deliberate local extension exists, and only one: `h1_bias.py`'s
  "bias-thrust", for the H1-bias expiry rule alone. It differs in exactly two
  ways -- it adds a single further row (`EXTRA_THRUST_TIER`, >= 0.60x range
  with a >= 75% body), and it lets the reference candle be an SFP as well as
  a hammer/shooting star, so an SFP bias expires the same way. Both are
  local: `patterns_pure/` is untouched and nothing outside that module sees
  either. The rows are read from `SPIKE_THRUST_TIERS`, never rewritten, and
  `h1_bias._assert_matches_patterns_pure` checks on every load that the
  generalised code reproduces `find_spike_thrust` exactly on patterns-pure's
  own rows and reference candles. Adding another such extension is a strategy
  change: the user decides it.

# H1 directional bias (`h1_bias.py`)

A bias is plain H1 candle context, NOT a level: a hammer is bullish, a
shooting star bearish (patterns-pure, read on the candle alone -- neither
LHPB/LLPB pairing above applies, since no level is involved), and an "sfp" is
patterns-pure's `find_sfp` -- a candle that sweeps an H1 swing low and closes
back above it (bullish) or sweeps a swing high and closes back below it
(bearish). `find_sfp` owns which swing that is: the most recent fractal pivot
that is both confirmed AND still UNTESTED, so a swing already traded through
is spent and yields no further SFP. Like every other pattern in the repo it
lives in `patterns_pure/` and is never restated at a call site.
Every bias is short-lived and `h1_bias.py` owns the expiries.
Its only consumer is `ss_m5_confl2`'s Bias column and its `fading_bias` dynamic
filter -- a review aid, never a strategy input.
