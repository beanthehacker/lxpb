# LXPB ledger-cache build performance — exploration notes (2026-09)

Scope: why `lxpb_levels_cache.build_ledger()` (the thing `h1_levels()` /
`m5_levels()` run once over the whole continuous series) is slow on M5, what
was fixed here, what wasn't, and how to pick this back up.

This branch is a **performance-only** exploration. It does not touch the M5/H1
data-loading pipeline (that work landed separately, see `git log --oneline
-- lxpb-v2/render_labels_report.py`) and does not change any LXPB rule or
output — every change here was validated to produce a byte-identical ledger
against the unmodified code before being kept.

## Current state (read this first)

As of this branch, a full M5 continuous-series rebuild takes **14.46s**
(down from **25.72s** unmodified, down from **2,338s / ~39 min** on the
old pre-TV-only-fix series — see "Important" note below). Given that, the
Phase 2 vectorization described under "What wasn't done" is no longer an
urgent problem to solve — 14-26s for a full rebuild is a reasonable cost for
something that only needs to run on a cache miss/extend. Revisit it if the
M5 series grows enough (more years of history, or a lower timeframe) that
build time becomes a real friction point again; `scripts/bench_ledger_cache.py`
will show if/when the per-bar cost starts degrading again the way it did on
the old series.

## The problem

`build_ledger()`'s cost does not scale linearly with bar count. Historically
(before the M5 TV-only-bars fix landed on `main`, when the continuous M5
series was 169,137 bars, mixing TradingView exports with `.scid`-derived
fallback bars), a full rebuild took **2,338s (~39 minutes)** — see
`data/levels_cache/m5_levels_continuous.json`'s `built_seconds` from that
vintage. H1 (11,259 bars) takes **3.1s**. That is not proportional: M5 has
~15x more bars than H1 but took ~750x longer.

A scaling sweep (`scripts/bench_ledger_cache.py`, run against that older
169k-bar series) showed *why* — the per-bar cost itself degrades as the
series gets longer:

| bars | seconds | bars/s |
|---|---|---|
| 5,000 | 9.9 | 507 |
| 10,000 | 21.4 | 468 |
| 20,000 | 123.9 | 161 |
| 30,000 | 317.6 | 95 |
| 45,000 | 583.8 | 77 |

## Root cause (cProfile, 15,000-bar M5 slice, 122s total)

```
advance_one_bar          66.1s  (54%)  -- lxpb.py's own Phase 2/4 scans
_LedgerObserver._departed 28.8s  (24%)  -- lxpb_levels_cache.py bookkeeping
_LedgerObserver.observe   18.1s  (15%)  -- lxpb_levels_cache.py bookkeeping
```

`touch_lv0` (levels formed but not yet broken out) grows into the thousands
over a multi-year M5 history — the module's own docstring already flagged
this ("touch_lv0 can hold thousands of long-dormant levels on a big M5
history"). Two **independent** O(bars × |touch_lv0|) costs stack on top of
each other, both fully rescanning `touch_lv0`/`touch_lv1` every single bar:

1. **`lxpb.advance_one_bar`'s own Phase 2** (breakout check) and **Phase 4**
   (running-FTA update) — real rule-evaluation logic, ~54% of total time.
   **Not changed by this branch** — see "What wasn't done" below for why.
2. **`lxpb_levels_cache._LedgerObserver`** re-deriving "what changed this
   bar" from scratch every bar: `observe()` copied the full `touch_lv0`/
   `touch_lv1` lists (`list(lv0)`, `list(lv1)`) and `_departed()` diffed
   them against the previous bar's snapshot via a two-pointer walk — ~40%
   of total time, pure bookkeeping, no rule logic. **This is what got
   fixed.**

## The fix (this branch)

`advance_one_bar` already computes, internally, exactly which levels left
`touch_lv0`/`touch_lv1` on this bar and why (Phase 2's `keep` vs
`gated_dropped` vs the silent "discard" branch; Phase 3's `keep` vs the
silent "consumed early" branch) — it just threw that information away before
returning. Made it **additive**: it now also returns the three lists it
already builds (`consumed_early`, `discarded_no_close`,
`broke_out_promoted`), so `_LedgerObserver.observe()` consumes them directly
instead of paying O(|touch_lv0|) / O(|touch_lv1|) every bar to rediscover
them by diffing full-list snapshots.

Also: `observe()`'s "newly registered levels" step used to scan all of
`touch_lv0` looking for `formation_time == bar_time`. Since `advance_one_bar`
Phase 1 always appends exactly that bar's LHPB then LLPB to the **end** of
`touch_lv0`, unconditionally, every bar, this is now `touch_lv0[-2:]` —
O(1) instead of O(|touch_lv0|).

No rule, decision, or output field changed. `advance_one_bar`'s docstring
carries the exact contract for the three new return values.

### Files changed

- `lxpb.py` — `advance_one_bar`'s return signature: 2-tuple → 5-tuple,
  additive only.
- `lxpb-v2/lxpb_levels_cache.py` — `_LedgerObserver.observe()` rewritten;
  `build_ledger()`'s call site updated to match the new unpacking.

### Validation

Compared against the unmodified originals (`git show <base-commit>:...`) on
real continuous M5 data, `pd.testing.assert_frame_equal(df_base, df_opt,
check_exact=True)` after sorting both by `(formation_time, type)`:

| bars | original | optimized | speedup | output |
|---|---|---|---|---|
| 5,000 | 8.7s | 5.0s | 1.7x | identical |
| 20,000 | 97.4s | 63.2s | 1.5x | identical |
| 30,000 | 271.1s | 179.7s | 1.5x | identical |
| 3,000 (post TV-only-fix series) | 0.20s | 0.14s | 1.4x | identical |
| 15,000 (post TV-only-fix series) | 1.39s | 0.94s | 1.5x | identical |
| **124,146 — full current series** | **25.72s** | **14.46s** | **1.78x** | **identical** |

Consistent ~1.4–1.8x speedup, byte-identical ledger at every size tested,
including a full end-to-end run of the entire current continuous M5 series.
This removes the OBSERVER's ~40% share of the cost; it does not touch the
state machine's own ~54% (Phase 2/4), so the net win is bounded to roughly
that ~1.5-1.8x, not the 10-50x a full fix (see "What wasn't done" below)
would need.

**Important:** the M5 TV-only-bars fix that landed on `main` separately, in
parallel with this exploration, changed the continuous M5 series length
from 169,137 to 124,146 bars (dropping `.scid`-derived phantom weekend/
holiday bars) — and, far more significantly, appears to have also removed
whatever was driving `touch_lv0`'s pathological growth on the old series:
the SAME unmodified code that took 2,338s (~39 min) on the old 169k-bar
series takes only **25.7s** on the new, clean 124k-bar series — a ~90x
improvement that has nothing to do with this branch. The original "way too
long" complaint that motivated this exploration looks to have already been
mostly resolved as a side effect of that other fix. This branch's 1.78x is
real but now a much smaller piece of the picture; the per-bar profiling
numbers above (from the old series) still correctly characterize the
algorithmic O(bars × |touch_lv0|) shape, just not today's absolute
wall-clock time.

## What wasn't done: the bigger lever

Phase 2's own `touch_lv0` scan (~54% of total time, per the cProfile
breakdown above) is the dominant **remaining** cost, and this branch does
not touch it. Each level's breakout test (`bar.high < price` /
`bar.low > price` and friends) is simple, level-local, and independent of
every other pending level — in principle a strong vectorization candidate:
compare the bar's OHLC against a numpy array of all pending levels' prices
in one shot instead of a Python `for lv in touch_lv0` loop.

This was **deliberately not attempted** here, because it's a materially
bigger and riskier change than the one in this branch:

- It requires restructuring `touch_lv0`/`touch_lv1`'s representation inside
  `lxpb.py` itself (list-of-dicts → parallel numpy arrays or similar), not
  just handing back information the function already had.
- `lxpb.py`'s own module docstring says it is **"a synced copy of a
  canonical file kept in `D:\daily-analysis`"** (specifically
  `D:\daily-analysis\lxpb-h1-apr2026\lxpb_h1_detect.py`) — not present on
  this machine, so it could not be verified against the true source. Per
  `lxpb_levels_cache.py`'s own docstring, forking `lxpb.py`'s rules
  elsewhere is the exact mistake `lxpb_fade_research_step1_levels.py`
  already made once.
- A deeper rewrite of the state representation carries real risk of subtle
  behavioral drift that a shallow read can miss: the exact-tag "inconclusive
  breakout" handling, the ER-based candidate gate, `is_swing`'s next-bar
  lookahead, gap-over handling on both breakout and retest, and
  `running_fta`'s incremental min/max all interact with `touch_lv0`/
  `touch_lv1`'s exact shape.

### Suggested approach if this is resumed

1. Re-profile Phase 2 alone (line-level — `line_profiler` or manual
   timing — cProfile's function-level granularity isn't enough) on a large
   M5 slice to get a real breakdown of the breakout-check loop vs. Phase 4's
   running-FTA update, both currently folded into `advance_one_bar`'s single
   66s/54% cProfile line.
2. Prototype `touch_lv0` as parallel numpy arrays (price, type, is_spike,
   is_swing, formation_time as int64, a padded/ragged `er_closes`
   structure) living in `lxpb.py`'s `state` dict **alongside** the existing
   list-of-dicts representation at first, so the vectorized breakout test
   can be cross-checked bar-by-bar against the existing Python loop while
   developing it, before removing the old path.
3. Validate with the **same methodology as this branch**:
   `pd.testing.assert_frame_equal(df_base, df_opt, check_exact=True)`
   against the unmodified state machine, at multiple bar-slice sizes,
   *before* trusting any speed number. `scripts/bench_ledger_cache.py`
   covers the speed side; the parity side needs a temporary copy of the
   pre-change code (`git show <commit>:lxpb.py > /tmp/lxpb_baseline.py`,
   same trick used to validate this branch — see git history on this file
   for the exact throwaway script).
4. `_reconcile`'s resume-from-blob path pickles the machine `state` (see
   `_resume_blob`) — a changed internal representation for `touch_lv0`/
   `touch_lv1` means bumping `ALGO_VERSION` so old cached `.state.pkl`
   blobs get rejected (forcing a rebuild) instead of being unpickled into a
   shape the new code doesn't expect.
5. Coordinate with whoever maintains the true canonical source at
   `D:\daily-analysis\lxpb-h1-apr2026\lxpb_h1_detect.py` before merging
   anything that changes `lxpb.py`'s internals, even non-behaviorally —
   per this repo's own convention (see `lxpb_levels_cache.py`'s module
   docstring), this file must not silently drift from that source.

## Other observations from profiling (not acted on)

- `_resume_blob` still pickles `_prev_lv0`/`_prev_lv1` on the observer even
  though the rewritten `observe()` no longer reads them — harmless dead
  weight, worth removing in a follow-up cleanup pass along with the now-
  unused `_departed` helper.
- `list.append` alone accounted for ~117 million calls / 7.7s in the
  15,000-bar cProfile run — a symptom of `advance_one_bar`'s
  rebuild-the-list-every-phase pattern (`keep = []; ...; keep.append(lv)`
  in Phases 2 and 3), not something addressed here.

## Reproducing this exploration

```
python scripts/bench_ledger_cache.py                  # scaling sweep, current code
python scripts/bench_ledger_cache.py 5000 20000 50000  # custom sizes
```

To re-validate parity against a specific earlier commit:

```python
# from lxpb-v2/, after: git show <commit>:lxpb.py > /tmp/lxpb_baseline.py
#                        git show <commit>:lxpb-v2/lxpb_levels_cache.py > /tmp/lxpb_levels_cache_baseline.py
import sys, pandas as pd
sys.path.insert(0, "/tmp")
import lxpb_baseline as L_base
import lxpb_levels_cache_baseline as LC_base
LC_base.L = L_base          # point the baseline ledger-cache at the baseline state machine

import lxpb_levels_cache as LC_opt
bars = LC_opt.m5_bars_continuous().iloc[:20_000]
df_base, _ = LC_base.build_ledger(bars, "M5")
df_opt, _ = LC_opt.build_ledger(bars, "M5")
pd.testing.assert_frame_equal(
    df_base.sort_values(["formation_time", "type"]).reset_index(drop=True),
    df_opt.sort_values(["formation_time", "type"]).reset_index(drop=True),
    check_exact=True,
)
```
