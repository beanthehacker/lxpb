# `patterns_pure/` — vendored copy of `D:\daily-analysis\patterns-pure`

Byte-identical copies of the handful of helpers this repo needs from the
canonical pattern library at `D:\daily-analysis\patterns-pure`, vendored
here (2026-08-26) so `lxpb-v2`'s cluster-selection analysis is
self-contained and does not depend on that external folder being present.

| File | Used for |
|---|---|
| `find_swings.py` | `find_swings()` — ATR ZigZag swing-pivot detection (feature **b**, "is this LXPB level a real swing level?"), plus `is_local_swing_high/low` |
| `find_ATR.py` | `findATR()` — Wilder ATR(21), the threshold input `find_swings` needs |
| `find_shooting_star.py` | spike detection for **LHPB** formation bars (feature **c**) |
| `find_hammer.py` | spike detection for **LLPB** formation bars (feature **c**) |
| `find_candle_utils` → `candle_utils.py` | `has_large_upper_wick` / `has_large_lower_wick` (feature **d**) |

## Conventions kept from upstream

- Files are **unmodified** so they can be diffed against / refreshed from
  upstream with a straight copy. That means they use flat imports
  (`from find_ATR import findATR`), so this directory is added to
  `sys.path` rather than imported as a package — see
  `../analyze_retest_cluster_selection.py`.
- ATR period is **21** project-wide; don't pass `period=` without writing
  down why.

## Spike-side mapping (important)

`lxpb.py`'s own `is_spike` maps LHPB→`is_hammer` and LLPB→`is_shootingstar`.
That is the **opposite** of what this repo uses everywhere else
(`render_labels_report.is_spike_pp` and
`../analyze_retest_cluster_selection.py`), which maps:

- **LHPB** (level = a bar's *high*, rejection of higher prices) →
  `find_shooting_star` (long **upper** wick)
- **LLPB** (level = a bar's *low*, rejection of lower prices) →
  `find_hammer` (long **lower** wick)

The patterns-pure mapping is the one consistent with feature **d** ("LHPB
formation candle has a large *upper* wick, LLPB a large *lower* wick"), so
it is what the cluster-selection features use. `lxpb.py` itself is left
untouched (it is a synced copy of the canonical detector).

## Refreshing

```powershell
Copy-Item D:\daily-analysis\patterns-pure\{find_ATR,find_swings,find_hammer,find_shooting_star,candle_utils}.py `
          D:\lxpb\lxpb-v2\patterns_pure\ -Force
```
