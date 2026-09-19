# Upcoming M5 levels dashboard

Live table of the M5 LXPB levels that are broken out (P1) but not yet retested (P2), within ±N pts of the latest completed M5 close
of the latest ES close, prepared the way the `ss_m5_confl2` report would trade them: same-side
confluence >= 1, clustering, fine-tuned entry, swerve rule, spike-P0 / thrust stop. No target
(the retest is still ahead), no `.scid`/tick data. Each row shows H1 (left) and M5 (right) charts
with the original entry (gold), refined entry (blue) and stop (red dashed) as rays from their source candles.
The page reuses the ss_m5_confl2 report's own CSS, chart renderer and chart-spec format (lifted by `build.py`
into `data/assets.*`), so it looks and behaves like the report. `?open=N` expands the first N rows on load.

    python m5_levels_dashboard/serve.py        # http://127.0.0.1:8765

- Filters: side (LHPB long / LLPB short), N = 1..200 pts (default 50), confluence >= 1, swerve-blocked.
- Refresh: hourly at hh:00:45 (and "Refresh now"). `serve.py` runs `build.py` in a fresh process;
  the page polls `/api/status` and reloads its data when a build lands.
- Why not fetched in the browser: TradingView's data socket only accepts a tradingview.com Origin,
  which a page can't send. `tv_feed.py` does it server-side and is verified tick-identical to the
  repo's exports on their overlap.
- Data: the repo's TradingView exports plus the fetched tail (last 5000 M5 / H1 bars) as one more
  vintage, through the repo's own loaders and validators. Level cache lives in `data/` here, never
  the shared one. After a roll the older history is re-anchored automatically by the measured
  overlap offset; the H1 export falls back to live H1 alone if it is an older vintage.
- The retest is assumed on the next M5 bar: `build.py` sets that stand-in `retest_time` and calls
  `render_m5_confl2_report`'s own functions; no rule is restated.
