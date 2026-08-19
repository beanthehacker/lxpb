# LXPB

`lxpb.py` — H1 LXPB (Last High/Low Pre-Breakout) detector: streams H1 OHLC
bars through a small state machine and outputs each completed retest with
its entry price, FTA (First Target Available) profit target, stop loss,
and a spike/swing classification.

This logic is a synced port of the canonical implementation at
`D:\daily-analysis\lxpb-h1-apr2026\lxpb_h1_detect.py` (kept there as the
source of truth; ported here so this repo's downstream scripts and tests
can use it directly). It replaces the previous `lxpb.py` and
`LxPB-ES-D1-H1-M5-tradingview.py`, whose H1 detection logic had three
confirmed bugs relative to the corrected version (see below).

## Other scripts

- `lxpb-m1.py`, `m5-retest-analysis.py`, `join_es_nq_retests.py`,
  `process_m5_data.py`, `simulate-joined-retests.py` — unrelated
  downstream M1/M5 retest/analysis pipeline, unaffected by this change.
- The old D1-level logic, D1↔H1 confluence, and trade-simulation pipeline
  (previously in `LxPB-ES-D1-H1-M5-tradingview.py`) were removed along
  with the buggy H1 detector and have no replacement here yet.
- `lxpb-es-vol/` — a separate strategy: LXPB stacked-level confluence +
  1-second order-flow absorption detection for ES, ported wholesale from
  `D:\daily-analysis\lxpb-volume-strat\` and wrapping this repo's
  `lxpb.py` as its H1 detector. See `lxpb-es-vol/README.md` for details
  and porting notes.

## Usage

```bash
python lxpb.py --data data/es-h1-continuous-backadjusted.csv
python lxpb.py --data data/nq-h1-4apr2021-11apr2025.csv --output retests.csv
```

## Algorithm — step by step

Three lists are maintained across the full bar history:

| List | Contents |
|------|----------|
| `touch_lv0` | Zero-touch levels: formed, not yet broken out |
| `touch_lv1` | One-touch levels: broken out, awaiting retest |
| `retests` | Completed retests: one-touch level returned to after ≥4 hours |

Per-bar processing order: **Phase 0** (finalize pending swing classification)
→ **Phase 3** (retest check) → **Phase 2** (breakout check) → **Phase 1**
(register new levels from this bar's high/low) → **Phase 4** (update
running FTA).

### Breakout (Phase 2)

A zero-touch level breaks out when the bar's body crosses it:
- **LHPB**: bar opens below the level and closes above it (or the bar
  gaps up entirely above the level).
- **LLPB**: bar opens above the level and closes below it (or gaps down
  entirely below it).

A bar whose range merely touches the level without the close crossing it
does **not** break the level out — but the level *is* consumed either way
(removed from `touch_lv0`) once a bar's range overlaps it.

### Retest (Phase 3)

A one-touch level is retested when a later bar's range overlaps the level
(or gaps cleanly past it) **and** at least `MIN_HOURS_BEFORE_RETEST` (4h)
have elapsed since breakout. **No directional open/close condition is
required** — any touch (or gap-over) after the wait qualifies. The level
is consumed on any touch regardless of whether the wait was satisfied.

### Gap handling

A bar entirely on the far side of a level (never actually trading at the
level's price) still counts as a breakout or retest — the level has been
passed, even without a wick touching it. For gap-over retests, `entry_price`
is the (synthetic) level price, not the bar's own OHLC; detect this case
via `not (retest_low <= entry_price <= retest_high)`.

### Spike / swing classification

Every level is classified when formed:
- **`is_spike`**: the formation bar itself is a hammer (LHPB) or shooting
  star (LLPB) — single-bar pattern, finalized immediately.
- **`is_swing`**: the formation bar's high/low is more extreme than both
  the bar immediately before and immediately after it. Since the "after"
  bar isn't known until the next iteration, this is finalized one bar
  later, in Phase 0 — always before that level's earliest possible
  breakout.

### FTA (First Target Available) & stop loss

| Type | FTA | Stop loss |
|------|-----|-----------|
| LHPB (long)  | `min(low)` of bars strictly between breakout and retest | `low` of the breakout bar |
| LLPB (short) | `max(high)` of bars strictly between breakout and retest | `high` of the breakout bar |

## Output columns

`type`, `formation_time`, `price`, `is_spike`, `is_swing`, `breakout_time`,
`breakout_open/high/low/close`, `retest_time`, `retest_open/high/low/close`,
`entry_price`, `fta`, `stop_loss`.

## Fixed vs. the old logic (confirmed bugs)

1. **Retest required a directional open/close condition** — the old
   version only counted a retest as valid if the bar opened on the far
   side and closed back across the level. Real retests are just a touch
   after the wait period; direction isn't required.
2. **No gap handling** — a bar that gapped clean through a level (never
   trading at that price) was silently ignored instead of counting as a
   breakout/retest.
3. **No spike/swing classification** — now added (see above).
4. **No FTA / stop loss on output** — now computed and included on every
   retest.

## Tests

`tests/test_lxpb.py` — unit tests for each of the four fixes above, plus
golden-file regression tests (`tests/fixtures/*_golden.csv`) run against
real recent H1 data in `dataTest/` (ES, NQ, RTY). Run repeatedly to catch
any future behavior change:

```bash
python -m pytest tests/test_lxpb.py -v
```

Only regenerate fixtures after verifying a logic change is intentional:

```bash
python tests/generate_golden.py
```
