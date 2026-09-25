# LFG -- liquidity flush-n-grab  (WIP, parked 2026-09-24)

**Status: work in progress, parked.** A study page only: no report under
`public/reports/` reads it, and nothing else in the repo depends on it. The
rules below are what the user set on 2026-09-24. The open questions at the end
are still undecided, so settle them before building on this.

## The idea

An H1 tight range (patterns-pure `find_range`, see `../patterns_pure/`) holds
liquidity just outside its edges. When price flushes out of the range on M5
and runs into the nearest M5 P0 waiting beyond the edge, the retest of that P0
is traded back toward the range (mean reversion).

User's example: 18 Mar 2026, H1 range over the 00:00-03:00 PT candles (box
6932.00-6941.75, confirmed at 04:00 PT). The M5 04:00 PT candle wicks under the
box low and a long is taken on the retest of an M5 LHPB P0 just below it.

## Rules as settled

1. **Range.** Every `find_range` candidate on the H1 series, inner or outer,
   whatever its final status. It is tradable from the close of its confirm
   candle until the H1 candle that ends it closes. `find_range` runs on H1
   only: the box is the box as it stood at the last H1 close and never widens
   on M5. A wick on M5 only shows up in the next hour's box.
2. **Flush.** An M5 candle trades past the H1 box edge. A wick is enough.
3. **Entry level.** The nearest M5 P0 beyond that edge still waiting for its
   retest, of any kind (plain, swing or spike), however far away. An LHPB
   below the box low is a long; an LLPB above the box high is a short. The M5
   ledger is read with plain-P0s tracked.
4. **Void.** If price comes back to the box edge before touching the level,
   the bounce has already happened. There is no trade, and **that side of the
   range is finished for LFG**, with no later flush on it. Void rows are marked
   (red) on the page.
5. **Entry.** A limit order at the level, filled on the first touch.
6. **Stop.** ss_m5_confl2's own stop rule, called rather than restated: one
   tick beyond a spike-P0 entry level's own candle, else one tick beyond the
   most protective P1 thrust candle among same-side levels within 10pt of the
   fill, moved out to an older P1 if under 3pt.
7. **Target.** A **placeholder**: the most recently formed opposite-type M5 P0
   still waiting for its retest beyond the fill, taken at the entry candle's
   close. Target selection is still to be designed.
8. **Limit.** One trade per side per range.

Outcomes are resolved on M5 bars. A bar that hits both stop and target counts
as a loss. A bar that both returns to the edge and touches the level is
flagged ambiguous and treated as a void.

## Snapshot (2026, run 2026-09-24)

124 ranges gave 144 flushes:

| Outcome | Count |
|---|---|
| Filled trades | 28 (17 won, 11 lost), -6.66R total, -0.24R per trade |
| Void | 100, of which 62 are pokes that closed back inside on the same candle |
| Range ended before the level was reached | 8 |
| No P0 beyond the edge | 5 |
| Ambiguous | 2 |
| No stop | 1 |

The median planned reward is 0.26 of the risk, because the placeholder target
is usually a P0 formed a candle or two before the flush.

## Open questions

- **18 Mar example.** The user named the LHPB at 6930.25, but the
  "nearest P0" rule picks the spike-P0 LHPB at 6931.75 (hammer at 22:20 PT on
  17 Mar, 0.25pt below the box low). Is there a rule, such as a minimum
  distance from the edge, that should skip it?
- **Same-candle pokes.** 62 of the 100 voids are pokes of a fraction of a
  point that close back inside the range. Under the rules they end the side.
  The user rejected an M5 "widening limit" line: the box widens on H1 only.
- **Nested ranges.** Inner and outer ranges both trade, so one fill can count
  twice (11 Jun 2026, 22:45 PT). Decide whether only the outermost live range
  counts.
- **Target selection.** Still a placeholder.
- **Range ended mid-flush.** A flush still in progress when its H1 range ends
  is dropped ("range ended"). The flush may be what ended the range, so
  consider letting it run on to a touch or a void.
- **Resolution.** Everything is resolved on M5 bars; tick-level fills would
  remove the ambiguous and same-bar cases.

## Running it

```powershell
python lfg\lfg_study.py --start 2026-01-01 --out <path>.html   # add --max-ranges N for a quick check
```

Write the page to a scratch or temp path, never to `public/reports/`.
