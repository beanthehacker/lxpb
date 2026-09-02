# Agent Instructions

## Background task polling — ALWAYS FAST, NEVER BREAK THIS RULE

When running long-running commands (report generation, tests, builds) in a
background/async shell, or waiting on a background agent:

- **Never wait an arbitrary long/fixed delay** (e.g. "wait 180 seconds")
  before checking on it. This wastes time.
- **Always poll aggressively** with short delays (5-15 seconds) and check
  again immediately, repeating until the task completes.
- Prefer `detach: true` for long-running background generation so it
  survives session interruptions, then poll it frequently rather than
  blocking with a long `initial_wait`.
- Every second counts — do not pad wait times "just in case."

This applies to all scripts/reports in this repo (e.g.
`lxpb-v2/render_labels_report.py`, `label-review/render_labels_report.py`,
`retest-vol-scalp/*.py`) which can take several minutes to run due to
real-tick `.scid` loading — always poll quickly rather than waiting long
fixed intervals.

## Fixed bug: pre-entry look-ahead in `_pin_exact_exit` (lxpb-v2)

`analyze_breakout_exits_1min.py`'s `_pin_exact_exit(...)` (used by both
`stop_target_grid_1min` in that module and `resolve_trades` in
`render_stop_target_report.py`) re-fetches the FULL clock-minute of real 1s
ticks for the resolving minute and scans from `:00`. When the resolving
minute is the SAME clock-minute as the trade's own touch/entry (touch mid-
minute, e.g. `07:42:13`), this let it credit a stop/target fill that
happened BEFORE the real entry (e.g. a naive hit at `07:42:00`, 13s before
entry) — a look-ahead bug that silently inflated win rates (caught via
`stop2_target2_trades_report.html` row 0/row 3: exit time shown earlier
than entry time; row 3 was even marked WIN when the 1s chart showed stop
hit first).

Fixed by adding a `not_before` param to `_pin_exact_exit` (pass the trade's
`touch_time`); ticks before it are dropped before scanning. Both call sites
updated. This changed `exit_analysis_report.html` and per-combo trade
reports meaningfully (e.g. stop=2/target=2: win rate 77.8% -> 64.6%, avg_R
0.56 -> 0.29) — both were regenerated after the fix (2026-08-25). If you
see an exit_time earlier than (or implausibly close to) entry/touch_time in
any of these reports again, suspect this same class of bug re-appearing
elsewhere (e.g. `_resolve_ambiguous_minute`, still used by
`analyze_fta_target.py`, was NOT patched and may have the same issue).

## Fixed bug: TradingView re-export re-anchors ALL history (lxpb-v2)

`ES1!` is not a real contract: TradingView splices the quarterly chain
(H26 -> M26 -> U26) into one series and shifts every OLDER segment by that
roll's spread so the chart has no gap. **Those spreads are recomputed on
every export**, so re-exporting silently moves the absolute price of
historical bars. Between the `es-h1-2015-14aug2026.csv` export and the
`24aug`/`1jan2026`/`2sep` exports, TradingView revised both 2026 roll
spreads (H26->M26 46.50 -> 50.50, M26->U26 68.00 -> 62.25), moving the
2026-01-20 15:00 close 7009.50 -> 7007.75 — a constant -1.75 across the
whole H26 segment, -5.75 across M26, 0.00 for the live U26 segment.

`build_es_h1_2026_backadjusted.TV_GROUND_TRUTH_OFFSETS` (which converts raw
`.scid` prices to chart scale) was measured against the *14aug* export, but
the reports display the newer exports — so every `.scid`-derived pane
(1s/1min/M5/footprint) was mis-scaled, AND, because the same offset converts
entry/stop/target into raw tick terms (`analyze_breakout_exits_1min.py`,
`render_stop_target_report.py`), Jan–Jun trades were **resolved at the wrong
price**. Jul–Aug reports sit in the U26 segment (delta 0.00) and were
unaffected, which is why this hid for so long.

Fixed (2026-09) by `render_labels_report._vintage_deltas()`: it *measures*
the per-segment shift between `OFFSET_CALIB_H1` (the export the constants
were calibrated against) and `DISPLAY_H1_PATHS` (what is actually rendered),
and adds it in `_offset_for_ts`. Same-vintage exports measure 0.00, so it is
a no-op until an export vintage actually changes. It **raises** if the
difference is not a dominant constant, so a mismatched/unadjusted file can
never again silently corrupt prices.

Rules when adding a new TradingView H1 export:
- Add it to `render_labels_report.DISPLAY_H1_PATHS` (oldest first); that is
  the single source of truth — `analyze_breakout_exits.DATA_PATHS` and
  `load_merged_h1()` both derive from it.
- **Never merge exports of different vintages.** Verify a new export against
  the existing ones first: the per-segment close difference must be a
  constant 0.00. (`1jan2026`, `24aug`, `1sep`, `2sep` are all one vintage;
  `es-h1-2015-14aug2026.csv` and `data/es-h1-continuous-backadjusted.csv`
  are the older one.)
- Sanity check after any data change: reconstruct an H1 bar from `.scid` and
  compare. **open and close must match 100% exactly**; high/low legitimately
  differ by up to one tick (0.25) because the raw Sierra tick feed includes
  spread-leg/block fills that TradingView filters out.

## Fixed bug: `detect_lxpb_h1` buckets are END-OF-DATA state, not as-of state (lxpb-v2)

`lxpb.detect_lxpb_h1(bars)` returns `(touch_lv0, touch_lv1, retests)` describing
the state machine's buckets **as of the LAST bar it was handed** — not as of any
particular moment inside the series:

- `touch_lv0` — formed, still never broken **at end of data**
- `touch_lv1` — broken, still awaiting retest **at end of data**
- `retests`   — levels that COMPLETED a retest, i.e. **consumed/dead**

Levels the machine discards silently (touched before `MIN_HOURS_BEFORE_RETEST`,
or broken-into-but-not-through) appear in none of the three.

`render_stop_target_report.build_m5_chart` used to run this over its *whole*
window — which extends past the trade to the exit plus padding — and then drew
levels from all three buckets, including `retests`. Because a completed retest
means the level is *gone*, this drew dead levels as though they were live: for
the Jan-21 `LHPB 6984.25` trade (row 59 of the full-year report) all **9** M5
levels drawn were stage `retested`, e.g. `6968.50` formed 2026-01-20 01:30 PT
and consumed 01-21 06:00–07:00 PT — over an hour *before* the 08:00 PT entry.

Fixed by feeding the machine only the bars strictly **before the entry bar**
(`as_of = resolved["touch_time"]`) and drawing only `lv1` (broken → live and
actionable, solid ray) and `lv0` (formed, unbroken, dashed ray). The `retests`
bucket is now never drawn. The entry bar itself is *excluded* rather than
included, so a level whose own M5 retest coincides with the H1 retest still
counts as live (that confluence is the point) and no post-entry price action can
retroactively kill a level. Row 59 went 9 dead levels → 4 live ones.

**Rule:** whenever these buckets are used to answer *"which levels existed at
time T?"*, do **not** call `detect_lxpb_h1` directly — use the level ledger
(`lxpb_levels_cache.levels_live_as_of`, see below), which records every level's
formation/breakout/death time once and answers as-of queries by interval test.
Only use the buckets directly when you want the trade population itself (a
completed retest IS the signal) — which is what `analyze_breakout_exits.py`,
`analyze_retest_features.py`, `analyze_m5_finetune_entries.py` etc.
legitimately do.

## Fixed bug: `M5_MAX_SPAN_DAYS` made live M5 levels unreachable (lxpb-v2)

`render_stop_target_report.build_m5_chart` used to clamp its M5 window to the
last `M5_MAX_SPAN_DAYS = 8` days before the retest, as a cost guard on `.scid`
loading. But an M5 level only qualifies for the pane if it **formed at or
before the H1 breakout bar** (`form_cutoff`). When breakout and retest are more
than 8 days apart, the window start lands *after* `form_cutoff`, so the
formation filter becomes unsatisfiable and the pane reports "no live M5 level"
**regardless of the data**.

Caught on row 1 of `stop2_target8_trades_report.html`: H1 `LLPB 7568.00` broke
2026-06-22 07:00 PT and retested 2026-07-01 08:00 PT — a 9-day gap. Six live M5
LLPB levels existed and all passed the cutoff (7569.25, 7569.50, 7576.00,
7576.50, 7579.25, 7584.00); the pane showed none. This was not rare: the
breakout→retest gap exceeds 7 days for **~17%** of Jul–Aug trades and **~15.5%**
of full-year trades (median 0.75 d, max 235 d).

Fixed by deleting `M5_MAX_SPAN_DAYS` entirely. The window is now bounded only by
the contract segment, and the cost guard it was standing in for is gone anyway:
M5 bars come from `lxpb_levels_cache.m5_bars_for_contract`, which builds the
whole contract's M5 OHLC once and caches it (first call ~6 s, then ~0.16 s —
*faster* than the old clamped path). When the breakout genuinely predates the
contract segment the title now says so (`breakout_out_of_reach`) instead of
silently claiming there were no levels.

**Rule:** never bound an LXPB level window by a fixed lookback. A level's
lifetime is unbounded — clamp only to the contract segment, or better, query
the ledger.

## LXPB level ledger cache (`lxpb-v2/lxpb_levels_cache.py`)

Re-running the state machine to ask "which levels were live at time T?" is both
slow and easy to get wrong (see the two bugs above). `lxpb_levels_cache.py`
records the **full lifecycle of every level, once**, and persists it to Parquet
so any report or backtest can answer as-of questions with an interval test.

Each row is one level: `type`, `price`, `formation_time`, `is_swing`,
`breakout_time` (NaT if never broken), `death_time` (NaT if still live at end of
data) and `fate` — one of `retested` (completed a retest; the trade signal),
`died_unbroken` / `died_broken` (the ~78% the machine discards silently), or
`alive_*`. A level is **live over `[formation_time, death_time)`** — one
consumed exactly at T is not live at T; `stage` is `broken` if
`breakout_time <= T`, else `formed`.

```python
import lxpb_levels_cache as LC
lv  = LC.h1_levels()                       # or LC.m5_levels(seg_idx) / LC.m5_levels_for_ts(ts)
live = LC.levels_live_as_of(lv, ts, type_="LLPB")   # DataFrame + `stage` column
sig  = LC.retests(lv)                      # the trade population
bars = LC.m5_bars_for_contract(seg_idx)    # whole-contract M5 OHLC, cached
```

CLI: `python lxpb_levels_cache.py --build-h1 --build-m5 all --stats`.
Cache lives in `lxpb-v2/data/levels_cache/`.

Two things about how it is built matter:

- It is a pure **external observer**. It never re-implements the LXPB rules; it
  steps `lxpb.advance_one_bar` and diffs the bucket lists to see which levels
  departed on which bar. `lxpb.py` is a synced copy of
  `D:\daily-analysis\lxpb-h1-apr2026\lxpb_h1_detect.py` and must stay untouched
  — the ledger exists precisely so it can.
- A `_rules_fingerprint()` (hash of `lxpb.py`'s bytes plus
  `MIN_HOURS_BEFORE_RETEST`) is stored in each cache's meta, so a re-sync of the
  rules invalidates every ledger automatically.

### Keeping the cache current — do NOT just rebuild

New data arrives in three different shapes and only one of them justifies a
rebuild. `_reconcile` picks the route:

- **Contract rollover / TradingView re-export** → `reanchor` (~10 ms). Every
  price shifts by a constant, but no market structure changes, and the state
  machine is **shift invariant** (every test is a price comparison or a ratio of
  price *differences*; there are no absolute thresholds). Verified empirically:
  shifting all input OHLC by a constant leaves every structural column —
  formation/breakout/retest/death times, `is_swing`, `is_spike`, `fate` —
  byte-identical and moves exactly the 12 `PRICE_COLS` by that constant. So the
  ledger is stored in the scale it was built in (`state_anchor` in the meta) and
  shifted onto the current scale on the way out of `h1_levels()` / `m5_levels()`.
- **New bars appended** → `extend` (~0.2 s vs 3–45 s). Terminal rows can never
  change again, so only still-open levels are recomputed: the machine state is
  pickled next to the ledger and resumed on the new bars. The state and the
  observer are pickled as **one object graph** — the observer tracks levels by
  object identity, so pickling them separately would break the identity walk and
  make every level look like it departed on the first resumed bar.
- **A bar revised inside the existing range** → `rebuild`. Everything downstream
  of it is invalid.

Rollover and append compose safely because the stored ledger and the persisted
state share one scale that never moves: the few new bars are shifted *into* the
state's scale, and the finished ledger is shifted *out* to the current one.

Detection uses `_bars_fingerprint` (exact) and `_shape_fingerprint` (shift
invariant). Both hash **every** bar. Do not "optimise" these back to sampling
head/tail + row count: that cannot see a mid-series revision and would serve a
ledger built from superseded prices with no indication anything was wrong. Full
hashing is ~2 ms (H1) / ~20 ms (M5) against a 3–45 s rebuild.

Cache filenames are **stable and overwritten in place**, not content-addressed —
content-addressed names would orphan a file per refresh and, worse, leave
`_reconcile` unable to find the previous ledger to extend or re-anchor, silently
degrading every update into a full rebuild.

Correctness is pinned by two scripts, both of which must pass:
`verify_levels_cache.py` (ledger ≡ direct as-of rerun: 29/29 H1 and 17/17 M5 cut
points exact, identical retest sets, plus the two `build_m5_chart` rows verified
by hand off the charts) and `verify_cache_updates.py` (every incremental route
— `hit`, `extend`, `reanchor`, `reanchor`+`extend`, forced `rebuild` — yields a
ledger identical to a from-scratch build). Two gotchas when comparing: ~120 H1
retests have `fta = NaN` (breakout and retest separated by a weekend gap, so no
bar updated `running_fta`) and NaN never equals itself, so stringify floats
before set-comparing.

## Parallelism — up to 4 cores available
Independent report/analysis runs (e.g. multiple `render_stop_target_report.py
--stop X --target Y` combos, or other per-parameter `.scid`-backed scripts in
this repo) can be run concurrently, up to 4 in parallel, instead of
sequentially one-at-a-time. Launch them as separate background/async
processes and poll each aggressively per the rule above, rather than waiting
for one to finish before starting the next.
