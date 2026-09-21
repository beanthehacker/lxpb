# `patterns_pure/` — vendored copy of `D:\daily-analysis\patterns-pure`

Byte-identical copies of the handful of helpers this repo needs from the
canonical pattern library at `D:\daily-analysis\patterns-pure`, vendored
here (2026-08-26) so `lxpb-v2`'s cluster-selection analysis is
self-contained and does not depend on that external folder being present.

| File | Used for |
|---|---|
| `find_swings.py` | `find_swings()` — ATR ZigZag swing-pivot detection (feature **b**, "is this LXPB level a real swing level?"), plus `is_local_swing_high/low` |
| `find_ATR.py` | `findATR()` — Wilder ATR(21), the threshold input `find_swings` needs |
| `find_shooting_star.py` | spike detection for **LHPB** formation bars (feature **c**); `../../lxpb.py`'s `is_spike` for **LLPB** |
| `find_hammer.py` | spike detection for **LLPB** formation bars (feature **c**); `../../lxpb.py`'s `is_spike` for **LHPB** |
| `find_candle_utils` → `candle_utils.py` | `has_large_upper_wick` / `has_large_lower_wick` (feature **d**) |
| `find_spike_thrust.py` | `find_spike_thrust()` — the **spike-thrust** candle definition (see "Spike candles" in `../CLAUDE.md`); `../h1_bias.py`'s bias-thrust builds on it |
| `find_sfp.py` | `find_sfp()` — the **SFP** (swing failure) definition: a candle that sweeps a confirmed, still-**untested** fractal swing and closes back on the near side of it. Used by `../h1_bias.py` |
| `find_three_drive_failure.py` | `find_three_drive_failure()` — the **3DF** (Three-Drive Failure) structure: three consecutive swing lows/highs, each further out than the last and each one taken straight back; not yet used by any report |

`find_hammer.py` / `find_shooting_star.py` are the **only** hammer /
shooting-star definition allowed anywhere in the repo -- see "Spike candles"
in `../CLAUDE.md`.

## Conventions kept from upstream

- Files are **unmodified** so they can be diffed against / refreshed from
  upstream with a straight copy. That means they use flat imports
  (`from find_ATR import findATR`), so this directory is added to
  `sys.path` rather than imported as a package — see
  `../analyze_retest_cluster_selection.py`.
- ATR period is **21** project-wide; don't pass `period=` without writing
  down why.

## Spike-side mapping (important)

The definition is shared; **which pattern counts as the spike for each level
type is not**. There are two pairings in the repo, each deliberate. Use the
one belonging to the code you're in, and never swap one for the other without
the user deciding it -- see "Spike candles" in `../CLAUDE.md`.

| Pairing | LHPB (level = a bar's high) | LLPB (level = a bar's low) | Used by |
|---|---|---|---|
| **Detector** | `find_hammer` | `find_shooting_star` | `../../lxpb.py`'s `is_spike` (its candidate gate), so the H1/M5 level caches and everything reading their `is_spike`: `ss_m5_confl2` (spike-P0 stop, H1 P0 kind), `trade_management.py`, `../../retest-vol-scalp`; also `../../lxpb-spike` (its own helper, same pairing) |
| **Rejection** | `find_shooting_star` (long upper wick) | `find_hammer` (long lower wick) | `render_labels_report.is_spike_pp` (both copies) and the P0 Spike hint, `../analyze_retest_cluster_selection.py` feature **c**, `../analyze_retest_features.py`, `../../lxpb-spike-atr` |

The rejection pairing reads the level as price rejected beyond it, which is
consistent with feature **d** ("LHPB formation candle has a large *upper*
wick, LLPB a large *lower* wick"). The cluster-selection report carries the
detector's flag alongside as `is_spike_lxpb`, so the two are never confused.

## Refreshing

```powershell
Copy-Item D:\daily-analysis\patterns-pure\{find_ATR,find_swings,find_hammer,find_shooting_star,find_spike_thrust,find_sfp,find_three_drive_failure,candle_utils}.py `
          D:\lxpb\lxpb-v2\patterns_pure\ -Force
```

A changed `find_hammer.py` / `find_shooting_star.py` changes the level caches'
rules fingerprint, so every H1/M5 ledger rebuilds on next use.

## Three-Drive Failure (3DF)

`find_three_drive_failure.py` is a STRUCTURE detector, not a candle one: it
reads three consecutive ZigZag pivots rather than one or two bars.

**3DF-bullish** = three drives down to a low, all three bought straight back:

1. three consecutive swing lows from `find_swings` (so each recovery between
   them is a real bounce of at least `atr_mult * ATR`, not noise),
2. each drive's low strictly below the previous drive's low, and
3. drives 2 and 3 each RECLAIM the previous drive's low -- some bar within
   `recover_bars` closes back above it. The drive candle itself counts, so an
   immediate rejection reclaims at once, but a couple of closes below the old
   low followed by a sharp move back up qualifies just as well.

`recover_bars` defaults to 2: a couple of closes beyond, then the reclaim.
Stretching it is what turns an ordinary pullback after an impulse into a
"failed drive" -- on 2026 H1, 6 bars finds three times as many patterns as 2.

**3DF-bearish** is the exact mirror (three higher swing highs, each of drives 2
and 3 closing back below the previous drive's high within `recover_bars`).

The drive candles are deliberately NOT shape-tested: a drive is judged by what
price does with the new extreme, not by how the one candle that made it looks.
The first drive gets no reclaim test -- there is no previous drive to take back,
and the ZigZag bounce off it is the evidence it was rejected.

The pattern is complete at drive 3's reclaim bar and is indexed there
(`confirm_time`, at or after `drive3_time`). The ZigZag only confirms drive 3 as
a pivot once price has bounced `atr_mult * ATR` off it, which can be later
still, so a caller walking trailing windows (the repo uses 750 H1 bars) sees
each pattern on the first window that reveals it. Feed it a trailing window for
that reason anyway: `findATR` measures one ATR at the end of whatever frame it
is given.

Example (H1, PT): 2026-06-30 19:00 / 2026-07-01 00:00 / 2026-07-01 06:00 --
lows 7585.25, 7578.25, 7573.50, bounces to 7602.50 and 7607.50, then a rally
to 7646.75.
