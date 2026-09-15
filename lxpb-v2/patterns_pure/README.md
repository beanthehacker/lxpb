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
| `find_spike_thrust.py` | `find_spike_thrust()` — the **spike-thrust** candle definition (see "Spike candles" in `../CLAUDE.md`); not yet used by any report |

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
Copy-Item D:\daily-analysis\patterns-pure\{find_ATR,find_swings,find_hammer,find_shooting_star,find_spike_thrust,candle_utils}.py `
          D:\lxpb\lxpb-v2\patterns_pure\ -Force
```

A changed `find_hammer.py` / `find_shooting_star.py` changes the level caches'
rules fingerprint, so every H1/M5 ledger rebuilds on next use.
