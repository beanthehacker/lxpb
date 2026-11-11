# ict_sfp: M5 sweep of an H1 swing (ICT-style entries), study scripts

Study code moved out of a scratch folder on 2026-11-10. Not a report yet; the rules are still under review.

| file | what it is |
|---|---|
| `ict.py` | shared helpers (1 s price path, H1 swing targets) and the first study: FVG / OB / displacement / OTE strategies on the H1 cross bar rows |
| `sweeps.py` | every H1 bar that trades past a confirmed, untested H1 swing (the population the cross bars are picked from) |
| `summ.py` | result tables with costs and bootstrap intervals for the first study |
| `algo.py` | first version of the sweep algo: M5 sweep, close back within 4 candles, stacked-liquidity skip, stop-entry over the M5 / M2 OB, pullback entry |
| `algo2.py` | current version: OB needs a body of 4 ticks (or a run of candles), stop-entry only while a gap is being left on M2 / M1 |
| `build_page.py`, `build_ex.py`, `build_jan19.py` | build the review pages (marks + step log) on top of a page rendered by `cross_bar/render_cross_bar_trades.py --at ...` |

The scripts read and write their working files (parquet, pickle, html) in the current directory: run them from a scratch folder,
with this folder on `PYTHONPATH`. `build_jan19.py` holds the Mon 2026-01-19 06:00 PT example with its marks written by hand.
