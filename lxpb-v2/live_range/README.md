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
            ├─ ZigZag (find_zigzag) / swings on the trading bars         targets, run-in, swerve
            ├─ order book  (arm / pull / amend)                         one order per level cluster
            └─ positions   (fill, stop, target, end-of-day flat)
```

## Files

| file | what |
|---|---|
| `series.py` | growing OHLC series, Wilder ATR kept up to date |
| `levels.py` | `LevelBook`: lxpb state, what happened to each level, which levels a bar would break |
| `boxes.py` | `BoxBook`: boxes, one `Reader` per box (episodes), forming-bar read |
| `engine.py` | `Engine`: orders, gates, stop/target planning, fills, exits |
| `replay.py` | streams `.scid` ticks through the engine; events.jsonl, trades.csv, summary |
| `check_boxes.py` | the incremental box ledger against `find_range_box_zz` over the same bars |

## The zigzag

There is exactly one, `patterns_pure/find_zigzag.py` (`ZigZag` here, `find_zigzag` in batch): 0.75 x ATR(21) reversal,
confirmed at least 1 bar after the extreme (on the extreme's own bar when the M1 order proves the reversal came after it), same parameters for the boxes (M5 bars) and for the targets / run-in
(trading bars). This directory has no zigzag code of its own and must not grow one (`../CLAUDE.md`, "Zigzag").

## Boxes and episodes

`BoxBook` is `find_range_box_zz` made incremental: `patterns_pure/find_zigzag.ZigZag` on M5 bars (order inside a bar
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

The engine follows the M1/M2 report's pipeline (`render_m1_confl_report.py`), one rule at a time; see "Matching the report" below.

**Entry and cluster.** Same-type live levels within 2 pt of each other chain into a cluster, ordered at the extreme
price (the reports' rule). Only levels the report would trade are candidates: a plain-P0 needs 1 pt of rejection wick
(`min_plain_wick`). A confirmed swing within 1 pt of
the price moves the order to the nearest other live level within 10 pt, or blocks it (swerve). Levels that cannot be
retested yet get none.

**Anchor.** A cluster's anchor is its earliest-retested member (the nearest to price while nothing is retested, and
kept after it is retested and leaves the book). The reports classify with the anchor's price and measure the run-in
from the anchor's P1, so the engine does too; the entry price (the extreme, or the swerved level) only decides where
the order rests. A swerved cluster keeps its order alive after its anchor is gone. Two clusters that end on the same
price share one order.

**Playbooks.** For every strong breakout of a range box (a box that has two pivots at each edge; a box never expires)
waiting for its retest:

* **fade** - an LLPB whose anchor lies above the top edge of a box that broke UP (an LHPB below the bottom edge of a
  box that broke DOWN), and the breakout is the run-in's own: the box's break candle (for the edge the zigzag broke
  through, else the strong-breakout bar) ends after the run-in leg began;
* **go-with** - an LHPB whose anchor lies inside the box or within 2 pt beyond its top edge after an UP breakout (an
  LLPB at the bottom edge after a DOWN breakout).

The breakout must be *strong*: a bar that closes beyond an edge and grades strong on the M1 bars (1.5 x ATR, body at
least half), read on the closed M5 bars plus the M5 bar still forming as if it closed at the order price. A forming bar
that would retest one of the box's waiting M5 levels ends the wait at once (the reports see it that way at the touch).
Episodes already retested, weak or failed breakouts give nothing.

One trade per (box, direction, playbook) seat, and one order serves every box it qualifies for.

**Seat ranking** (`Params.seat_rank`, default `spike_first`; the default is fixed by the user and is not to be changed
without being asked). Before any order exists, every entry that passes the gates is a candidate: they are ranked (a
spike-P0 entry first, then the nearer level) and a plain entry is not placed while a spike candidate serves one of
the same seats. Once an order is resting for a seat it stands: it is never pulled for a contender that shows up later,
and no second order is put on that seat. The reports rank filled contenders after the fact; a live engine cannot undo
a fill. `--seat-rank nearest` drops the spike preference (an option only).

**Orders are placed like an account with limited margin would place them, and every gate is judged before
the touch.** On every tick:

1. an entry within `arm_pts` (4 pt) of price is evaluated as if price were already on it: the breakouts
   waiting for their retest, the run-in into the level (strong: 4x the median bar range, measured from the anchor's P1
   to the planned entry, not the chased price), for a fade that the breakout is the run-in's own, a stop and a
   target that can be planned, and no blackout (news pull, end-of-day, the 15:00-15:05 PT reopen) and no
   thin tape (`liquidity.py`: 20% or more of the last 5 minutes' quotes wider than 1 tick). Pass -> an order is placed
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

## Matching the report

What the engine takes from `render_m1_confl_report.py` (M1; the M2 report is the same with 2-minute bars), and where it
cannot:

| report | engine |
|---|---|
| ledger: plain-P0 needs >= 1 pt wick (`--plain-p0-min-wick-pts`) | same; weaker ones are stop candidates only |
| ledger: strong-breakout gate on every level's own P1 (`--bo-range-atr 1.5 --bo-body 0.5 --bo-atr-period 30`) | implemented but OFF (`--bo-range-atr 1.5` turns it on): a level's own breakout need not be strong |
| cluster = confluence of retested levels, entry = extreme, anchor = earliest retest | live chain of levels, extreme, anchor kept after its retest |
| range tag read at the touch with the touch's own M5 bar "closed right there" | forming bar read as if closed; a retest by that bar so far ends the wait |
| tag price = anchor price; run-in from the anchor's P1 to the planned entry | same |
| fade: box break candle (zigzag `break_time`) vs the run-in start | same (`break_t` is checked against `find_range_box_zz`) |
| news pull 05:29:55-05:30:40 PT, EOD entry block 12:45-15:00 PT, flat 12:44 PT | same |
| liquidity gate (tag `low_liquidity`, excluded by default), 15:00-15:05 PT reopen (tag `globex_eth_open`, excluded by default) | gates, switchable (`liquidity_gate`, `globex_block`) |
| peg: 0.25 pt step, 1 pt cap, bid/ask and aggressor side | same |
| stop (spike P0 else thrust, widened to 3 pt), target (swing extreme 0.25-50 pt), stop trail, RR floor 0.2 | same |
| `r_below_min` (planned R < 1), `plain_p0`, `no_confl`, `swerved`, `eod_flat` tags | recorded on every trade (`tags`); `min_r` makes the R tag a gate (off) |
| boxes from the last 5 days of M5 history per touch (a limit of how the report runs its detector, not a rule) | no expiry; `box_lookback_days=5` copies the report - on the test span it changes nothing |

Where the engine differs because it is live, or because the report cannot be copied:

* The report's order waits up to 3 h anywhere after its anchor's retest; the engine has no such limit (a cluster's order
  lives as long as its entry level), only rests an order within `arm_pts` of price, as the margin preference demands,
  and holds one position at a time (the report lets trades overlap).
* The report builds its M1/M2 level list from its window's first bar, so levels formed earlier do not exist there; the
  engine's levels run from its warm-up, so it can trade levels the report never had.
* The report applies the strong-breakout gate above; the engine does not, so it can trade levels the report dropped.
* The report reads the touch's own M5 bar in full: a retest of a box level later in that bar, after the touch, already
  counts as done. The engine can only see the bar so far.
* A cluster's entry is the extreme of the retested members and their confluence pools, and members retested after the
  anchor extend it; the engine takes the extreme of the live chain.
* Seats: the reports rank filled contenders afterwards; the engine's first fill keeps a seat.
* Not applied (review tags in the report, nothing trades on them): volume spike, H1 bias, trap variants, H1 spike
  confluence, ATR boundary, `fading_bias`; the opposite-zigzag target is off by default in the report too.
* Fill latency and queue position are not modelled beyond the report's peg model.

## Run

    python scid_only/run_scid_only.py live_range/replay.py --from 2026-09-27T22:00 --warmup-days 5 --out /tmp/out
    python scid_only/run_scid_only.py live_range/check_boxes.py

Options worth knowing: `--seat-rank nearest`, `--no-peg`, `--no-manage`, `--bo-range-atr 1.5`, `--box-lookback-days 5`.

## Results (27 Sep 22:00 UTC - 6 Oct 2026, `.scid` ticks only, defaults)

* Speed: 2.0M tape records replayed in ~450 s (4.5k records/s), warm-up included.
* Boxes: `check_boxes.py` - all boxes, their break candles and all 201 episodes equal `find_range_box_zz`'s.
* Against the M1 range-box report (22 range-tagged trades, 107 trades in all) the engine takes 30 trades: 24 have a
  report trade at the same level and time (20 range-tagged there, 4 not) and 6 have none (levels that formed before the
  report's window, and levels the report's strong-breakout gate dropped). 18 of the report's 22 range trades appear
  (to the second on most); on 12 of those the engine's managed result equals the report's (30 Sep 04:10:41 three trails
  then the target, +1.38R; 1 Oct 21:00:28 RR-floor exit -0.07R; 4 Oct 23:15:52 trailed stop +0.21R).
* Not reproduced: 2 Oct 01:08:05 (one position at a time: the previous trade was still open), 2 Oct 03:11:35 (a swing
  pulled the order), 2 Oct 08:17:58 and 4 Oct 15:02:11 (no order was armed; not traced).
* With `--bo-range-atr 1.5` the engine takes 29 trades, 25 with a report counterpart: the gate removes only one of the
  six unmatched ones.
* Net over the span, managed and chasing: -0.4R on 30 trades (planned R >= 1 only: -0.3R on 8).
