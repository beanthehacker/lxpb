# lxpb-spike-atr — the *spike-only-beyond-ATR* LXPB strategy

A fully structural (no grid search, no free parameters to fit) LXPB strategy
plus an HTML report, built on top of the shared machinery in
[`../lxpb-v2`](../lxpb-v2) and the canonical pattern/session helpers in
`D:\daily-analysis`.

```
python analyze_spike_atr_strategy.py     # funnel + trade list on stdout
python render_spike_atr_report.py        # -> spike_atr_trades_report.html
python render_spike_atr_report.py --all-spikes \
       --output spike_atr_all_spikes_report.html \
       --cache data\1min_all_spikes.csv   # every spike retest, PASS/FAIL shown
```

## The rules

| Leg | Rule |
| --- | --- |
| **Level** | A completed LXPB retest (`lxpb.detect_lxpb_h1` on H1). LHPB = *Last High Pre-Breakout* → broke up, retested from above → **long**. LLPB → **short**. |
| **Setup filter** | The **formation bar must be a spike**, per `D:\daily-analysis\patterns-pure`: `find_shooting_star` for LHPB, `find_hammer` for LLPB (via `render_labels_report.is_spike_pp`). |
| **Entry** | The level price itself, on the Phase‑2 retest bar. |
| **Stop** | **Beyond the breakout candle** — `breakout_low` for LHPB, `breakout_high` for LLPB. This is exactly `lxpb.stop_loss`. |
| **Target** | The **FTA** (first trouble area) — the running `min(low)`/`max(high)` of the bars strictly *between* breakout and retest (`lxpb.running_fta`). |
| **Gate** | **Price must be beyond the moving daily‑ATR boundary** — either already at the entry, or by the time it would reach the stop. |

### The moving ATR boundary

D1 candles are built from the H1 series by `helpers.calculateD1W1` (NY
session, 17:00 ET close, DST-aware). `ATR(21)` is `patterns-pure`'s
`findATR` over the D1 bars that closed **before** the trade's own trading day.
Then, at the instant of the trade:

```
lower boundary = high of the trading day so far  −  mult × ATR      (LHPB / long)
upper boundary = low  of the trading day so far  +  mult × ATR      (LLPB / short)
```

The level is traded only if:

```
LHPB (long)    entry < lower boundary   OR   stop < lower boundary
LLPB (short)   entry > upper boundary   OR   stop > upper boundary
```

i.e. price is *already* stretched a full daily ATR past the extreme the
session has printed, **or** it would have to be by the time it stopped the
trade out. Either way, getting stopped requires the day to exceed a full
average daily range measured from the extreme already on the board.

Two consequences worth knowing:

* **The stop leg subsumes the entry leg for any normal bracket.** The stop
  is by construction further from the boundary's anchor than the entry is,
  so `entry beyond ⟹ stop beyond` and the OR collapses to the stop test.
  The report still shows both margins and a `BEYOND @entry` / `BEYOND @stop`
  pill so you can see which leg carried it. The entry leg only becomes
  load-bearing on a degenerate row where a gap puts the stop on the *target*
  side of the entry — and `select_trades` rejects those outright as
  inverted brackets (1 such row exists across the 291 Jul–Aug retests; it
  is not a spike).
* **The boundary does not drift in the direction that matters.** Reaching a
  long's stop takes price *down*, which cannot raise the day high the lower
  boundary is anchored to (and symmetrically for a short), so testing the
  stop against the entry-time boundary is exact rather than an
  approximation.

"Day so far" is strictly **point-in-time**: per-trading-day `cummax`/`cummin`
shifted one bar, combined with the retest bar's *open* and the level price.
The retest bar's own high/low are deliberately excluded — using them would be
look-ahead. (Verified on this sample: including them flips no verdict.)

## Results — Jul–Aug 2026 retests (ES, H1)

Funnel on `../lxpb-v2/data/24aug-CME_MINI_ES1!, 60.csv`, `--atr-mult 1.0`:

```
completed retests in window ...... 291   (+123 gap rows dropped repo-wide)
spike formation (patterns-pure) .. 24
has FTA target + structural stop . 24
bracket >= 1 tick on both legs ... 24
stop/target correctly oriented ... 24   (0 inverted dropped)
beyond the ATR boundary .......... 1    (at entry, or by the time price reaches the stop)
```

* **The one qualifying trade:** LLPB, retest 2026‑07‑29 15:11 PT, entry
  7371.75, stop 110.25 pt, target 13.75 pt → **WIN, +0.125R**.
* **All 24 spike retests** (ignoring the ATR gate): 21 W / 3 L, avg **+0.081R**.

Two things fall out of this and are worth stating plainly:

1. **The ATR gate is extremely restrictive here.** Daily ATR(21) ran ≈83–101
   pts in Jul–Aug 2026 while a typical intraday day-range-so-far at retest
   time was ≈46 pts, so the boundary usually sits far outside the day's
   action. Relaxing `--atr-mult`: `0.8` → 2 trades, `0.75` → 4, `0.7` → 5,
   `0.6` → 6, `0.5` → 8. (Across *all* retests, spike or not, 20 pass at
   `1.0`.)
2. **The canonical FTA target is tiny relative to a beyond-the-breakout-bar
   stop.** On these spike rows the FTA is a mean of 3.65 pts (median 1.5,
   min 0.25, max 20) against stops averaging 16.8 pts (median 11.1, max
   110.25) — so R:R is often ≈0.1. That is *why* the win rate is ~88% while
   avg R is only +0.08: the payoff structure, not the edge, is doing the
   talking. Any expectancy read on this strategy has to be in R, never in
   hit rate.

## Files

| File | What |
| --- | --- |
| `analyze_spike_atr_strategy.py` | Strategy core: load → spike flag → ATR boundary → select → resolve → summarize. Also a CLI that prints the funnel and the trade table. |
| `render_spike_atr_report.py` | The HTML report. |
| `data/1min_*.csv` | Auto-generated 1-minute bar caches (rebuilt on demand; safe to delete). |
| `spike_atr_trades_report.html` | Qualifying trades only. |
| `spike_atr_all_spikes_report.html` | Every spike retest with a BEYOND/within ATR verdict, so the near-misses are visible. |
## The report

`render_spike_atr_report.py` is deliberately a **skin over
`../lxpb-v2/render_labels_report.py`** — it reuses that module's `CSS`,
`JS_TEMPLATE`, `FEATURES`, `compute_hints` and `build_1s_trio_chart`
verbatim, so the page is the same one as
`../lxpb-v2/lxpb_strong_breakout_report.html`: the same expandable H1 + 1s
trio (price/bid/ask) + 1‑min + footprint chart stack, the same crosshair
legends, the same **PT** (`America/Los_Angeles`) tooltips and axis labels,
the same localStorage hand-labeling with CSV export/import, and the same
filter chips.

What differs:

* The H1 chart runs past the retest **to the resolved exit bar** and carries
  the trade's own price lines — gold = level/entry, green dashed = FTA
  target, red dashed = structural stop, **orange dotted = the ATR boundary**,
  grey dotted = the day extreme it's measured from — on top of the
  blue/red confluence lines the labeling report already draws.
* The 1s trio is centred on the **actual fill instant** (`touch_time` from
  the 1‑minute walk-forward), not the naive any-side H1 touch.
* Extra columns: entry fill time, stop/FTA prices, stop/target points, R:R,
  ATR(21), day extreme, ATR boundary, stop margin, BEYOND/within verdict,
  outcome, R and exit time.
* Two extra filter chip rows (ATR verdict, outcome), spliced into the
  inherited `applyFilters` by `build_js()`. That splice **asserts** on its
  anchors, so if `render_labels_report.JS_TEMPLATE` is edited upstream this
  fails loudly instead of silently shipping dead filters.

Labels are keyed `type|price|formation_epoch`, so hand-labels made in the
lxpb-v2 reports carry over for the same levels.

## CLI flags

Shared (from `analyze_spike_atr_strategy._add_cli_args`):

| Flag | Default | Meaning |
| --- | --- | --- |
| `--data` | `../lxpb-v2/data/24aug-CME_MINI_ES1!, 60.csv` | H1 OHLC CSV |
| `--symbol` | `ES1!` | used for D1/session construction |
| `--start` / `--end` | `2026-07-01` / `2026-08-31` | filters on **retest** date |
| `--atr-mult` | `1.0` | boundary = day extreme ∓ this many ATRs |
| `--atr-d1-window` | `0` | `0` = ATR(21) over *all* closed D1 bars (converged Wilder RMA ≡ TradingView `ta.atr(21)`); `N` = only the last N D1 bars, matching `mean-reversion-playbook`'s `tail(21)` convention. The two differ materially (e.g. 2026‑07‑15: 92.6 vs 106.4). |
| `--include-gaps` | off | keep levels whose formation/breakout/retest only happened via a price gap |

Report-only: `--output`, `--title`, `--all-spikes`, `--n-ticks`,
`--tick-size`, `--pad-seconds`, `--one-min-pad-minutes`, `--no-footprint`,
`--no-ticks` (fast H1-only smoke test), `--cache`.

## Gotchas worth knowing

* **Spike polarity.** `lxpb.py`'s own `is_spike` maps LHPB→hammer,
  LLPB→shooting-star — the *opposite* of the repo convention. This strategy
  uses `render_labels_report.is_spike_pp` (LHPB→shooting‑star,
  LLPB→hammer), which is the correct mapping; see
  `../lxpb-v2/patterns_pure/README.md`.
* **`sys.path` ordering.** `D:\daily-analysis` contains an `lxpb` *package*
  that shadows `D:\lxpb\lxpb.py`. Always `sys.path.append` it, never
  `insert(0, ...)`.
* **1-minute cache keys are index-based** (`f"{i:04d}_{retest_time}"`), so
  they are only unique *within one fixed trade list*. Any run with a
  different selection must pass its own `--cache` path.
* **Don't mix H1 files.** Two back-adjustment vintages differ by up to 5.75
  pts in their overlap, so merging them plants a step change no roll
  explains. The `24aug-…` file is the default here, so results are
  apples-to-apples with `../lxpb-v2`'s exit reports. The old
  `es-h1-continuous-backadjusted.csv` is retired outright -- it spliced
  resampled `.scid` bars onto a TradingView base; see "TradingView
  continuous series only" in `../lxpb-v2/CLAUDE.md`.
* **Exit resolution is look-ahead free.** `_pin_exact_exit` is always called
  with `not_before=touch_time`; see `../AGENTS.md` for the history of that
  bug. If a report ever shows an exit time at/before its entry, suspect a
  regression of exactly that class.

## Upstream edits this required

Two small, backwards-compatible changes in `../lxpb-v2`:

* `analyze_breakout_exits_1min.build_or_load_1min_series(..., cache_path=None)`
  — lets a distinct trade selection use its own cache file.
* `render_stop_target_report.resolve_trades(..., stop=None, target=None)` —
  when the scalars are `None`, the per-trade `stop_dist`/`target_dist` are
  used, which is what a structural (variable) bracket needs.
