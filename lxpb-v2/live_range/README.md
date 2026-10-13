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

One trade per (box, direction, playbook) seat, and one order serves every box it qualifies for.

**Seat ranking** (`Params.seat_rank`, default `spike_first`, the reports' ranking made causal): when more
entries pass than there are working-order slots, a spike-P0 entry goes first, then the nearer level. A plain
entry is not placed while a spike entry (placeable or already resting) serves one of the same seats, and a
spike order that is placed pulls a resting plain order for the same seat (`superseded_by_spike`). The
reports hand a seat to a spike contender even when a plain one filled earlier; a live engine cannot undo a
fill, so this only decides which orders rest, never takes a seat back. `nearest` drops the spike preference.

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

**Fills are the report's pegged model** (`find_alt_fill`), on the tape's own records (price, bid, ask, aggressor
side): a buy fills when a seller prints at or below its price (a sell: a buyer at or above); a record that only
touches the price from the wrong side re-quotes the order one tick (`peg_step`) closer to market, up to
`peg_cap` (1 pt) in all, and the re-quote fills at the quote if it meets it. Every re-quote is judged by the
gates as a new order (`peg:weak_run_in` ... pulls it), and the stop and target are planned again at the fill
price. A level the tape has already traded through on the forming bar is never armed. `--no-peg` gives a
plain limit.

Stop: P0 candle of a spike level, else the thrust candle of the live same-type levels within 10 pt, widened to
3 pt (older P1) if tighter. Target: swing-extreme (latest confirmed zigzag pivot since the level's breakout,
0.25-50 pt away). Exits tick by tick; flat at 12:44 PT.

**Trade management** (`Params.manage`, `--no-manage` to switch off), the reports' two rules, run on the
trading-bar ledger and bars (the reports run `trade_management` on the M1/M2 ledger):

1. stop trail - when a trading bar closes past the highs (lows) of enough live same-type P0s that broke out on
   it (1 if any is a swing or spike, 2 if every P0 wick is at least 20% of its candle, else 3), the stop
   moves to one tick beyond that bar, toward the target only;
2. RR floor - at a bar close, remaining reward / remaining risk (against the trailed stop) at or below 0.2
   exits at market (the bid/ask of the next record).

R is always taken against the stop the trade started with.

## Run

    python scid_only/run_scid_only.py live_range/replay.py --from 2026-09-27T22:00 --warmup-days 4 --out /tmp/out
    python scid_only/run_scid_only.py live_range/check_boxes.py

## Known differences from the reports

* No liquidity or volume-spike tags and no opposite-zigzag target (the default rule is swing-extreme).
* The reports pick a cluster's entry among the retests that happened; the engine takes it from the levels
  live in its book, so the entry price differs on some trades (e.g. 4 Oct 15:02 LLPB 7785.75 in the report,
  7787.25 here) and the engine trades more seats than the reports' retest-driven pipeline.
* Forming-bar reads use the box edges as of the last closed bar; the batch read also lets the forming bar
  confirm zigzag pivots.
* No order latency or queue position beyond the report's peg model.
* A seat is kept by the first fill; the reports re-rank filled contenders afterwards.

## Results so far (28 Sep - 6 Oct 2026, .scid ticks only)

* Speed: 1.9M tape records (price / quote / side changes) replayed in ~280 s, about 6.8k records/s,
  warm-up included.
* Boxes: `check_boxes.py` - all boxes and all 201 episodes equal `find_range_box_zz`'s.
* Against the M1 range-box report's 22 trades, 15 appear in the engine's 52 at the same level and time
  (to the second on most). On those, the engine's *managed* result equals the report's managed one on 10
  (e.g. 30 Sep 04:10:41: three trails then the target, +1.38R; 30 Sep 12:37:46 and 1 Oct 21:00:28: RR-floor
  exits at +0.02R and -0.07R; 27 Sep 17:02:06: +0.05R); the other 5 enter at a different price (cluster
  extreme, or a chase of a different size). The other 37 trades the engine takes are seats the report's
  pipeline never traded.
* Not reproduced: 28 Sep 19:18:45 (swerve and a 48-minute wait), 2 Oct 00:05:13, 00:57:47, 08:17:58,
  4 Oct 23:15:52 and 5 Oct 01:34:14 - the cluster / swerve choice differs, not the fills or management.
* Pegging: 37 of 52 fills were chased (average 0.22 pt). With managed exits the 52 trades net -1.9R; with
  `--no-manage` 8.2R and with `--no-peg` -1.4R (49 trades): chasing and the RR floor cost R on this span.
* Seat ranking: `spike_first` pulled 3 plain orders in 15 days and changed 1 trade against `nearest`.
