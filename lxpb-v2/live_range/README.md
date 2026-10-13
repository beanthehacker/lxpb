# live_range: the range-box playbooks as a streaming engine

The `ss_m1_confl` / `ss_m2_confl` reports find trades first and tag them with range boxes
afterwards (exploration only). This package is the other way round: an incremental, tick-driven
engine that keeps a ledger of range boxes and LXPB levels alive, works out *before* the retest
which level it will trade for the fade and the go-with playbook, rests an order there, and
pulls it as new data arrives.

Nothing re-reads history. Per tick it touches a few numbers; per closed bar it advances small
state machines.

```
ticks ──► Engine.on_tick
            ├─ M1 / trading-bar / M5 bars (forming + closed)
            ├─ LevelBook  (lxpb.advance_one_bar, M5 and trading bars)  levels waiting for a retest
            ├─ BoxBook    (incremental find_range_box_zz)               boxes + episodes
            ├─ StructZigzag / swings on the trading bars                targets, run-in, swerve
            ├─ order book  (arm / pull / amend)                         one order per level cluster
            └─ positions   (fill, stop, target, end-of-day flat)
```

## Files

| file | what |
|---|---|
| `series.py` | growing OHLC series, Wilder ATR kept up to date |
| `zigzag.py` | `BoxZigzag` (ATR zigzag of the boxes), `StructZigzag` (3/5/10 pt zigzag of the targets) |
| `levels.py` | `LevelBook`: lxpb state, what happened to each level, which levels a bar would break |
| `boxes.py` | `BoxBook`: boxes, one `Reader` per box (episodes), forming-bar read |
| `engine.py` | `Engine`: orders, gates, stop/target planning, fills, exits |
| `replay.py` | streams `.scid` ticks through the engine; events.jsonl, trades.csv, summary |
| `check_boxes.py` | the incremental box ledger against `find_range_box_zz` over the same bars |

## Boxes and episodes

`BoxBook` is `find_range_box_zz` made incremental: the ATR zigzag on M5 bars (order inside a bar
from the M1 bars), the box state machine fed one pivot at a time, and a `Reader` per box that
walks it bar by bar. A bar that closes beyond an edge and grades *strong* on the M1 bars opens
an **episode** for that edge (one per edge, never invalidated; only a retest of one of the box's
M5 levels, or those levels all disappearing, ends it).
`check_boxes.py` replays 8,000 M1 bars: all boxes and all 201 episodes equal the batch
detector's, same times.

The M5 bar still forming is read as if it closed at the last price, once a second and only when
the price is beyond some box edge, on a copy of each reader: provisional episodes, which arm
orders and are withdrawn if the bar ends up not breaking out. A box counts as a range once it
has two pivots at each edge, as in the reports.

## Orders

Entries: same-type live levels within 2 pt chain into a cluster, ordered at the extreme price (the reports'
rule); a confirmed swing within 1 pt of that price moves it to the nearest other live level within 10 pt, or
blocks it (swerve). Levels that cannot be retested yet get none.

For every episode waiting for its retest, an entry qualifies as:

* **fade** - a level beyond the breakout edge (LLPB above an up breakout, LHPB below a down one);
* **go-with** - a level inside the box or within 2 pt beyond the breakout-side edge (LHPB after an up
  breakout, LLPB after a down one).

One trade per (box, direction, playbook) seat; the first fill takes it, and one order serves every box it
qualifies for.

**Orders are placed like an account with limited margin would place them, and every gate is judged before
the touch.** On every tick:

1. an entry within `arm_pts` (4 pt) of price is evaluated as if price were already on it: the breakouts
   waiting for their retest *including a forming M5 bar read as if it closed at that price*, the run-in into
   the level (must be strong: 4x the baseline bar range), for a fade that the breakout is the run-in's own,
   a stop and a target that can be planned, and no blackout (news pull, end-of-day). Pass -> an order is placed
   with that stop and target; fail -> none (and it is placed later if it starts to pass);
2. a resting order is re-evaluated the same way and pulled the moment it would fail, or when price is
   more than `cancel_pts` (6 pt) away, the level is spent, or a swing now sits on it;
3. at most `max_working_orders` (2) rest at once, nearest first, and none while `max_open_positions` (1) is open
   (a fill pulls the others).

At the touch it simply fills at its price on the plan made on the previous tick.

Stop: P0 candle of a spike level, else the thrust candle of the live same-type levels within 10 pt, widened to
3 pt (older P1) if tighter. Target: swing-extreme (latest confirmed zigzag pivot since the level's breakout,
0.25-50 pt away). Exits tick by tick; flat at 12:44 PT.

## Run

    python scid_only/run_scid_only.py live_range/replay.py --from 2026-09-27T22:00 --warmup-days 4 --out /tmp/out
    python scid_only/run_scid_only.py live_range/check_boxes.py

## Known differences from the reports

* No trade management (stop trail, RR floor), no pegged/chasing entry, no liquidity or
  volume-spike tags, no opposite-zigzag target (the default rule is swing-extreme).
* Forming-bar reads use the box edges as of the last closed bar; the batch read also lets the forming bar
  confirm zigzag pivots.
* No order latency or queue position: a resting order fills when a tick trades at or through its price.
* The first fill takes a seat, where the reports ranked contenders (spike entry first, else
  earliest touch).

## Results so far (28 Sep - 6 Oct 2026, .scid ticks only)

* Speed: 768k ticks (price changes) replayed in ~210 s, about 3.7k ticks/s, warm-up included.
* Boxes: `check_boxes.py` - all boxes and all 201 episodes equal `find_range_box_zz`'s.
* Against the M1 range-box report's 22 trades: 17 appear in the engine's 61 trades at the same level and
  time (several to the second, e.g. 28 Sep 10:11:29 LLPB 7768.75, 29 Sep 04:49:38 LLPB 7768.00 and 11:00:34
  LLPB 7727.50). The engine's 61 trades are every box seat it could fill, not only the retests the old
  pipeline happened to trade, so it has more of them (39 fades, 8 fade+go-with, 14 go-with).
* Not reproduced: 28 Sep 19:18:45 (the 7740.00 go-with, filled after a swerve and a 48-minute wait), 2 Oct
  00:05:13 and 4 Oct 23:15:52, plus differences in entry price on three more (cluster / seat order).
