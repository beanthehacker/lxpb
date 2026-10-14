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

# Report regens: only when the user asks

**Never regenerate a report unless the user explicitly asks for a regen in
that request.** This covers every full or partial report run (the
ss_m5_confl2 reports and any other report under `public/reports/`), in any
checkout or worktree, including a regen "to verify" a code change or one
that would follow naturally from finishing a feature. A code change is done
when it is committed and smoke-tested; the regen waits for the user.

- A smoke test (`--max-rows`, written to a temp file, never to
  `public/reports/`) is still expected after a report-code change and does
  not need asking.
- Permission to regen covers only that request. It does not carry over to
  the next change.
- When a change needs a regen before its results show, say so and stop.

# Report charts: full width on any screen, readable labels (user preference)

**Standing preference (user, 2026-10-12, refined 2026-10-13): every report chart is as wide as the
screen it is opened on, in any orientation, laid out and tested for an 11-inch iPad Air (1180x820 CSS
px landscape, 820x1180 portrait) first.** Remember it for every chart you build or touch:

- The page carries `<meta name="viewport" content="width=device-width, initial-scale=1">`. Without
  it iPad Safari lays the page out at its own default width and scales it, so the charts end up
  half the page or zoomed.
- The chart stack is exactly as wide as what is on screen, sticky-left, whatever the browser did with the
  page. NOT `100vw`, NOT a fixed cap, and NOT `visualViewport.width * scale` (that is the device width,
  which left the charts at half the page when iPad Safari shrank the 1970px table to fit at ~0.6 zoom;
  caught from a Safari PDF 2026-10-13). A script measures the page's own starting zoom (`_baseScale`,
  after load and after a rotation) and sets `--stack-w` to `visualViewport.width * scale / _baseScale`
  minus a 16px gutter, so a fitted page gets a full-page-wide stack, a 1:1 page a device-wide one, and
  pinch-zooming changes nothing. In-chart and title text grows by 1/`_baseScale` (13px on screen,
  capped at 22 CSS px) so it stays readable on a shrunken page. Each pane is as wide as the stack, taller
  when the stack is wider (`clamp(340px, max(60vh, 0.3 x stack width), 760px)`), the M5 context pane
  above the trading pane.
- The candles fill the pane's width: the bar spacing follows the pane width and the number of bars
  (1px minimum, 20px maximum), not a fixed 6px.
- In-chart text is ~13px on screen (`chartFont()`): large enough to read on a tablet, small enough that one
  trade's tags fit. Never go back to 18px (tags pile on each other) or to 9px (unreadable). Axis tags
  show only the word naming the line (`stop`, `target`, `entry`, `level`, `range high`), since the tag
  prints the price itself. Axis tags and marker texts that would overlap are thinned (the most
  important one stays: entry, stop, target, level, range edges; result and exit markers before P0/P1
  markers), and a ray's full text shows on hover.
- Several range boxes on one trade are named A, B, ... in start order; chart labels stay short
  ("A 08:10" at its start, "A↓" at its breakout) and the ray hover text carries the times.
- Candles keep the default grey. Never recolour candles (blue, orange, ...) to show a range, a
  level or anything else; use rays, markers and price tags.
- A range box's candles, from its start to its breakout, are never compressed into a
  "[N bars skipped]" gap and never cut off at the window's left edge (`protect_spans` in
  `render_stop_target_report.build_m5_chart`, and the M5 pane reaches back to the earliest box).

Done for the `ss_m1_confl` / `ss_m2_confl` reports (their own chart JS and CSS in
`render_m1_confl_report.py`, which draws the panes itself rather than using the shared `_renderPane`).
The other reports keep their old layout until they are next touched; apply the same rules then.
Check a chart change with a mobile-emulated browser (Playwright `isMobile: true`) at 1180x820 and
820x1180, both with `width=device-width` and with `width=1967` (the shrink-to-fit case Safari shows), and
confirm the stack's width on screen (CSS width x scale) is the screen width minus the gutter.

# The M1 range-box report (`ss_m1_confl/`) is standalone

It started as a fork of `ss_m5_confl2` and shares nothing with it any more: no H1 input (no H1 P0
confluence, bias, trap or ATR-boundary columns or filters), no crest-refine, no D1/footprint/tick panes.
M5 appears only in the range boxes and the M5 context chart. The M5 names inside the file
(`m5_ledger`, `same_side_m5`, `LC.m5_levels`) are M1 levels: the script swaps M1 in behind the
shared "m5" level calls, because the shared modules (`render_stop_target_report`,
`render_ss_confl_finetune_report`, `trade_management`) still ask for them by that name. Chart dict keys
and DOM ids are `main` (the M1 trading pane) and `ctx` (the M5 context pane). Removed CLI flags
(`--range-box-only`, `--m5-charts-only`, `--atr-boundary-mult`, `--crest-refine*`) are accepted and
ignored so older regen commands still run. Range boxes on the M1 trading pane also carry
their zigzag pivots (`Cr1` crest, `Tr2` trough, ... numbered per box, `brk` on the one that broke it, `A:Cr1` when
a trade has several boxes): small squares above/below the M5 extreme's own M1 candle, never on the candles
(`range_box_tags.box_pivots` re-runs the detector's own zigzag; `_mark_range_pivots`). M1 pane only, not the M5 pane. The table's column positions are hard-coded in `SORT_COLS` and
the `nth-child` header rule: change them together with any column added or removed.

A fixed month of the fade playbook only (`sep_month_fade`) is the same script with
`--since-day 2026-09-01 --until-day 2026-09-30 --warmup-days 3 --playbook fade --range-lookback-days 60
--label "Sep 2026, whole month"`: trades are those of the Sep trading days; the range boxes behind them
may be up to `--range-lookback-days` old (the detector re-reads that much M5 before every touch, so it
costs seconds per touch; `--range-procs` forks it over 4 processes). `--playbook fade` leaves the go-with
playbook untagged, unlisted and without its filter chip. Every breakout-from-range trade is also tagged
`fade1` (it fades the range's first strong breakout) or `fade2` (the other edge had already broken out strongly,
price reversed, and this is the second-side breakout): `range_box_tags.expand_episodes` (`fade_no`, `prior_time`),
FADE 1 / FADE 2 badge, Exclude/Only chips, "1st"/"2nd" on the chart's breakout label and a "1st broke ..." line on
fade2 charts. A trade on several boxes is fade2 if any of them is. Run it through `scid_only/run_scid_only.py`
serially (`--workers 1`), like the others.

# Live range engine (`live_range/`): orders as close to real as possible (user preference)

**Standing preference (user, 2026-10-13): the streaming engine places orders the way a real account with limited
margin would, and judges every gate BEFORE the touch.** Remember it for every change to `live_range`:

- No ladders of resting orders: an order is placed only when price is within `arm_pts` of its level and
  pulled when price drifts beyond `cancel_pts`; at most `max_working_orders` rest at once and none while
  `max_open_positions` are open (a fill pulls the rest). The order set is updated on live data, tick by tick.
- Every filter is evaluated as if price were already on the order, and re-evaluated while it approaches: a
  weak run-in, a breakout that would not grade strong at that price, no plannable stop or target, a news or
  end-of-day blackout. An order that would fail is not placed, and is pulled the moment it would fail;
  it is never pulled at the touch because of what the touch showed. A target or stop that appears later in the
  run-in makes the order placeable then.
- A level that cannot be retested yet (the bar after its breakout has not closed) gets no order: touching it
  only consumes it.
- Fills follow the report's pegged model on the tape's own records (price, bid, ask, aggressor side): the right
  aggressor at or through the price fills, a wrong-side touch re-quotes the order a tick closer to market (up
  to 1 pt), each re-quote re-judged by the gates. Never fill an order just because a price printed at it.
- A plain-P0 is only an entry level with at least 1 pt of rejection wick on its formation candle (the M1/M2
  reports' rule); weaker ones end at their breakout but still serve as stop candidates while untouched.
- The engine follows the M1/M2 report's pipeline (`live_range/README.md`, "Matching the report"): the anchor
  (earliest-retested cluster member) that the range tag and the run-in are judged from, the box's break candle for the
  fade test, the liquidity and reopen gates. User decisions (2026-10-13): no cold-level mode, no 3-hour fill window, a
  level's own breakout need not be strong (`bo_range_atr` stays 0), boxes never expire, one order per seat that is never
  pulled for a later contender.
- Trade management (stop trail, RR floor) runs on the trading-bar ledger and bars, as in the reports; seat
  contenders are ranked spike entry first, then nearest (`seat_rank="spike_first"`).
  **User standing rule (2026-10-13): never change the seat-ranking default.** Offer an alternative as an option
  and let the user choose; do not flip `seat_rank` (or any other strategy default) on your own.

# Data convention: TradingView exports confirm, Sierra Chart ticks extend

**H1 and M5 history comes from TradingView's own continuous ES1! exports.
Past the exports' last bar, the series is extended with bars resampled from
the CURRENT front month's Sierra Chart `.scid` ticks, up to that contract's
roll and no further.** (User decision 2026-09-28: TradingView exports are
needed to confirm each roll's offset, not to supply every bar.)

The exports stay in charge of everything that needs them:

- **Confirming the offset.** The front month's offset is measured against the
  exports alone (`_tv_m5()`, never the extended series, which would confirm
  any offset). It needs >= 100 of that contract's front-month M5 bars in an
  export (`_SCID_OFFSET_MIN_OVERLAP`, ~8 trading hours).
- **History and holes.** Ticks never fill a hole inside the exported range,
  never reach back before the exports' last bar, and never cross a roll.
  Where no export reaches, that range is simply absent.

`_extend_with_scid` also: rebuilds the exports' last bar from ticks (an export
taken mid-bar leaves it partial); appends only bars that traded (no
forward-filled empty slots -- that is what made 70% of the old .scid M5
retests phantoms); leaves out the bar still forming at load time; and first
proves ticks rebuild the exports' own last 10 days of bars (same bar times,
opens/closes exact on >= 98%, every price within a tick on >= 99%), raising
otherwise. `LXPB_SCID_EXTEND=0` turns it off (the levels runtime, which has no
ticks, sets it).

The two loaders are `render_labels_report._display_h1()` and `_display_m5()`
(TradingView-only: `_tv_h1()` / `_tv_m5()`, merged from `DISPLAY_H1_PATHS` /
`DISPLAY_M5_PATHS`). To repair or deepen history, add another TradingView
export to those lists.

**Next roll: Z26 -> H27, Mon 2026-12-14 3:00 PM PT.** The tick extension stops
there and every load prints `stops at the EPZ26 roll`. From 7 days before it,
loads print a reminder. When either appears, or the current date is past the
roll, **ask the user for fresh post-roll TradingView exports (H1 and M5)
before relying on any bar after the roll**, then follow the rollover
checklist below. The same holds at every later roll.

**The export rule is repo-wide, not just `lxpb-v2`.** `label-review` and
`retest-vol-scalp` follow it too, through `../es_h1_display.py` — a smaller
loader over the same export list, for subprojects that deliberately avoid
importing lxpb-v2's report modules. Keep the two export lists in step. That
loader does NOT apply the tick extension yet: those two subprojects stop at
the H1 export's last bar.
`data/es-h1-continuous-backadjusted.csv` and its builder
`data/build_es_h1_continuous.py` are retired; nothing reads them.

`lxpb_levels_cache.h1_levels()` runs the state machine once over the H1
series and `m5_levels()` once over `m5_bars_continuous()` (which is
`_display_m5()`). One ledger per timeframe, spanning every rollover; neither
takes a contract or `seg_idx`. Any new timeframe follows the same shape.

# P0 kinds: spike-P0, swing-P0, plain-P0

**Every level price closes through is a P0**, and `../lxpb.py` tracks every
one of them to its retest. Its `p0_kind`, fixed at the breakout, says which
rule it met; there is no "valid P0" any more, only these names:

- **spike-P0** -- the formation candle is a spike, detector pairing (below).
- **swing-P0** -- a swing high/low AND consolidating at breakout (efficiency
  ratio < `ER_CONSOLIDATION_MAX`). A raw swing in a trending run is not one.
- **spike+swing-P0** -- both; it counts as either kind.
- **plain-P0** -- neither.

Before 2026-09-24 plain-P0s were dropped at their breakout (the old
"candidate gate", fate `gated_dropped`). Levels never interact, so keeping
them changed nothing about any other level. Every existing consumer still
reads the old population, and says so at the call site:

- `detect_lxpb_h1(bars, p0_kinds=...)` -- required: `ALL_P0_KINDS` or
  `SPIKE_OR_SWING_P0_KINDS`.
- `h1_levels(plain_p0=...)` / `m5_levels(plain_p0=...)` -- required:
  `PLAIN_P0_TRACKED` (full ledger) or `PLAIN_P0_UNTRACKED` (plain-P0s end at
  their own breakout, exactly the old ledger; they stay in, since P1 sibling
  counts, confluence and targets always counted them).

Letting a consumer see tracked plain-P0s is a strategy change: the user
decides it, one consumer at a time.

## `.scid` for ticks, and for extending the current front month

Apart from `_extend_with_scid` (above), `.scid` is only for tick-level work.

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
6. Update the "Next roll" line in the data convention above.

The new front month's ticks cannot be mapped until an export holds at least
100 of its front-month M5 bars (`_SCID_OFFSET_MIN_OVERLAP`, ~8 trading hours);
`_measure_scid_offset` raises before then. Once it does, the tick extension
takes over from that export's last bar until the following roll.

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
| Detector | hammer | shooting star | `../lxpb.py`'s `is_spike`, which decides which P0s are spike-P0s. So also the H1/M5 level caches and every reader of their `is_spike`: `ss_m5_confl2` (spike-P0 stop, H1 P0 kind), `trade_management.py`, `../retest-vol-scalp`. Also `../lxpb-spike` (own helper, same pairing). |
| Rejection | shooting star | hammer | `render_labels_report.is_spike_pp` (both copies) and its P0 Spike hint, `analyze_retest_cluster_selection.py`, `analyze_retest_features.py`, `../lxpb-spike-atr`. |

- Anything that reads a level's cached `is_spike` is on the detector pairing,
  whatever it calls the candle. Don't reinterpret it as the other pairing.
- New code that tests the formation candle itself must say which pairing it
  uses, in its docstring.
- Never switch a place from one pairing to the other, or merge them, without
  the user deciding it. It is a strategy change: for the detector it changes
  which P0s are spike-P0s, so every ledger and report changes.
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
  "bias-thrust", for the H1-bias expiry rule and the bias-served tag that
  shares it. It differs in exactly three ways -- it adds a flat row
  (`EXTRA_THRUST_TIER`, >= 0.60x range with a >= 75% body); it adds the
  repo's only SLIDING row (`sliding_thrust_body`: from >= 0.65x range
  needing a 50% body, easing half a point per point of extra range to a 42%
  floor reached at 0.81x, one-way, so extra body never buys back missing
  range); and it lets the reference candle be any bias candle in
  `h1_bias.BIAS_KINDS` -- an SFP today -- as well as a hammer/shooting star,
  so every bias expires the same way. All three are local: `patterns_pure/`
  is untouched and nothing outside that module sees any of them. The
  canonical rows are read from `SPIKE_THRUST_TIERS`, never rewritten, and
  `h1_bias._assert_matches_patterns_pure` checks on every load that the
  generalised code with both local rows OFF reproduces `find_spike_thrust`
  exactly on patterns-pure's own rows and reference candles. Adding another
  such extension, or another row, is a strategy change: the user decides it
  (the sliding row was added 2026-09-21, folding the old bias-served
  constants into the thrust definition).

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

# Trap variants (`trap_variants.py`)

The daily-analysis trapVariants (`E:\daily-analysis\trapVariants`, engine
`patterns-pure/find_trap.py`) ported ONE TIMEFRAME DOWN: the D1 variants
(V1, V2, V2.5, V3, V4) run entirely on H1, the H1 variants (V6-V9) run as M5
triggers under H1 wide/inside context against H1 levels. ATR is ATR(24) on H1
(the upstream LTF convention). Every detector is the vendored patterns-pure one
(`find_LP`, `find_eqh_eql`, `find_swings`, `find_inside_bar`,
`find_engulfing_*`, `find_targets`, hammer/shooting star); the module owns
only the variant cascade and the trap lifecycle, and its docstring lists every
local decision (confirmation on H1 too, target renames IHH/IHL/IDH/IDL, life
after confirmation, level-pool depth). Like `h1_bias.py`, its only consumer is
the ss_m5_confl2 Bias column -- a review aid, never a strategy input.

# Report layout: the trade table fits a 24" monitor (range_breakout)

**On a desktop 24" monitor (1920px wide) a range_breakout report's trade
table shows the whole row with NO horizontal scrollbar, however many columns
it has.** Adding or removing a column must never bring the scrollbar back.
The layout does this by itself, so a new column needs no width tuning:

- Desktop (mouse/trackpad, `(hover: hover) and (pointer: fine)`): the table is
  the window's width, and every header and cell may wrap between words but
  never inside one (times and prices stay on one line). The Notes box follows
  its column's width. If the row still doesn't fit, a small script steps the
  table's font down until it does (9px floor), and re-fits on window resize.
  Expanded chart rows take the table's width and never widen it.
- iPad / touch tablet (`(hover: none) and (pointer: coarse)`), and only there:
  the table scrolls inside its own window-sized box (pinned header row and
  Trade Id column), charts are pinned to the visible width with heights
  following the screen, portrait puts one chart per row, and tap targets are
  bigger.
- The Tags column is never wider than the Type column (every device): each
  tag badge shows a 2-3 letter code (VS volume spike, SC H1 spike
  confluence, FB fading bias, BS bias served, NT news/thin, GO Globex open,
  SW/SWB/CR swerve, R< below min R, EOD end-of-day flat, MG management),
  stacked one per line, with the full label and detail in its hover text
  and the legend on the Tags header. A new tag needs a code in that list.
- The Bias column is never wider than Type either: one code per line (H
  hammer, S shooting star, SFP swing failure, HoH / LoSS retest, V1..V9 trap variant;
  -N = H1 candles back, ✓ = trap confirmed), green bullish / red bearish /
  italic trap, full text in the cell's hover. A new bias kind needs a code.
- A new column's cell content must have break points: no long space-less
  strings (comma lists need a space or `<wbr>` after each comma), or it pins
  its column wide and forces the font down.
- After a layout change, check with headless Chrome at 1920x1080 that the
  page's scroll width equals the window width and the table font is still
  the default (no fallback shrink).

All of it lives in `range_breakout/report_layout.html` (CSS + script), which
`render_range_breakout_report.py` inserts just before `</body>` of every
report, plus a viewport meta tag. Change the layout there, never by hand in a
report. The 2026 report picks it up on its next regen.
