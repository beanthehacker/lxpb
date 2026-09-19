# /levels -- upcoming M5 levels

`https://<site>/levels` lists the M5 LXPB levels that have broken out (P1) but not been retested (P2),
within ±N pts (1-200, default 50) of the latest completed ES M5 close, prepared as the `ss_m5_confl2`
report would trade them: same-side confluence >= 1, clustering, fine-tuned entry (with the original
entry shown beside it), swerve rule, spike-P0 / thrust stop. No target (the retest is ahead), no tick
data. Each row expands to M5 and H1 charts with original entry (gold), refined entry (blue) and stop
(red dashed) as rays from their source candles. It looks like the report because it uses the report's own
CSS and chart renderer (`public/levels/assets.*`, lifted by `sync_runtime.py`).

## How it runs -- nothing on a schedule

Opening the page calls `GET /api/levels`. If the stored state is from a previous hour (or there is none) the
page then calls `POST /api/levels`, which fetches TradingView, rebuilds and returns the fresh state; an open
page notices the hour change and does the same. Whoever opens the page first in an hour pays for the refresh
(~7 s warm, ~1 min the first time ever). The whole app stays behind `proxy.ts`' Google login.

| Piece | File |
|---|---|
| Page (static, rewritten from `/levels`) | `public/levels/index.html` |
| Data function (Vercel Python) | `api/levels.py` -> spawns `levels_runtime/refresh.py` |
| TradingView websocket fetch (last ~5000 M5/H1 bars) | `tv_feed.py` |
| Rules: report's own functions, retest assumed on the next bar | `compute.py`, `charts.py` |
| Persistence (Neon `levels_kv`, or files in dev) | `store.py` |
| Path/stub setup so report modules import without the parent dir or tick data | `bootstrap.py`, `vendor/` |

TradingView is fetched server-side because its data socket only accepts a tradingview.com Origin, which a
browser can't send. The fetched bars matched the repo's exports to the tick on their overlap.

### Data

* History: `seed/es_m5_history.csv.gz` (the exports, merged and validated by the repo's loaders) seeds an
  empty store once. After that every refresh joins the live tail to the stored history with
  `_merge_vintages` (measured re-anchor offset after a roll) and writes the merged bars back, so the exports
  are never needed again. Refresh at least every ~2 weeks: the tail is 5000 bars (~17 days) and must overlap
  the stored history by 1000+ bars.
* The level ledger's cache files are stored too, so a refresh extends the ledger by the new bars.
* After a quarterly roll, append the new front month to `render_labels_report.CONTRACTS` (as in CLAUDE.md's
  rollover checklist); the H1 export list is not used here (live H1 only).

## Deploying

Needs the Neon database from the main README (`NEON_DB_DATABASE_URL`); the table is created on first use.
`requirements.txt` + `vercel.json` (maxDuration 300, bundle exclusions) at the project root configure the Python
function. Re-run `python levels_runtime/sync_runtime.py` and commit whenever `lxpb.py`,
`build_es_h1_2026_backadjusted.py`, the exports or the report CSS/chart JS change.

## Local

    python levels_runtime/dev_server.py           # http://127.0.0.1:8770/levels  (no login, files in .dev_store)
    LEVELS_PY_DEV=http://127.0.0.1:8770 npm run dev   # or under next dev, behind the app's login

`?open=N` on the page expands the first N rows.
