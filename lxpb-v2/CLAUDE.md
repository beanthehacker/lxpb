@AGENTS.md

# patterns_pure is a git submodule (user, 2026-10-20)

`lxpb-v2/patterns_pure` is the `beanthehacker/patterns_pure` repo, mounted as a submodule (it is shared with
daily-analysis and swing_playbooks; nothing is vendored any more). A fresh clone or a new worktree needs
`git submodule update --init` before anything runs (the repo's post-checkout hook does it on `git worktree add`).
Change a detector in the patterns_pure repo, push it there, then bump the submodule here in its own commit.
lxpb's fractal SFP is `find_sfp_fractal.py` (`from find_sfp_fractal import find_sfp`); `find_SFP.py` is
daily-analysis's different SFP.

# Sessions: always work in a separate worktree

**Every new session starts by entering its own git worktree**, before any
file is edited, any script is run that writes output, or any branch is
switched. Use the `EnterWorktree` tool (or start the session with
`claude --worktree`). Do this even for a small task; if the session already
is in a worktree, stay in it.

- Never edit, commit or regen reports directly in the main checkout
  (`E:\lxpb\lxpb-v2`), so parallel sessions cannot trample each other's
  files, caches or report output.
- Read-only questions that touch no files need no worktree.
- Merge back to `main` only when the user asks.
- A worktree has no `acheron` / `scidReader` / `.scid` junctions, and
  gitignored caches and exports are absent too. Link or copy what the task
  needs from the main checkout (see the lxpb-v2 env setup memory) rather
  than regenerating or re-exporting.

# Times: always PT, never UTC (user, 2026-10-20)

**Every time stated to the user is in Pacific Time** (America/Los_Angeles, PST or PDT as in force on that date), labelled
"PT": replies, tables, charts, HTML reports, docstrings, notes, commit messages. Never state a time in UTC, even for data
stored in UTC (the FX H1 files in E:/daily-analysis/data): convert it first. Code, data and CLI arguments defined in UTC may
stay UTC internally.

# Opening .html files on this Windows machine: Chrome only, no links (user, 2026-10-16)

**Whenever a `.html` file (a report, a smoke-test output, a chart page) is generated or updated for the
user to look at, open it straight from disk in the default Chrome**, e.g. PowerShell
`Start-Process "C:\Program Files\Google\Chrome\Application\chrome.exe" -ArgumentList "file:///<abs path, forward slashes, spaces as %20>"`.

- **Never give links to it** in the reply (no `file:///` markdown link, no relative-path link), and never
  show it in Claude Code's own browser: no built-in browser pane (`mcp__Claude_Browser__*`,
  `preview_start`), no `show_pane`, no `SendUserFile` with `display: "render"`. Name the file in plain
  text (its path) after opening it, nothing clickable.
- No local server for it: Chrome opens the file from disk. Reports that load `/js/row-store.js` by site
  path need it inlined into the local copy first, and review notes will not persist from disk (no
  `/api/rows`); say so.
- This overrides the general "link the file" convention of the app's system prompt for `.html` files here.

# Report regens: only when the user asks

**Never regenerate a report unless the user explicitly asks for a regen in
that request.** This covers every full or partial report run (the
ss_m5_confl2 reports and any other report under `public/reports/`), in any
checkout or worktree, including a regen "to verify" a code change or one
that would follow naturally from finishing a feature. A code change is done
when it is committed and smoke-tested; the regen waits for the user.

- A smoke test (`--max-rows`, written to a temp file, never to
  `public/reports/`) is still expected after a report-code change and does
  not need asking.
- Permission to regen covers only that request. It does not carry over to
  the next change.
- When a change needs a regen before its results show, say so and stop.

# Report charts: full width on any screen, readable labels (user preference)

**Standing preference (user, 2026-10-12, refined 2026-10-13): every report chart is as wide as the
screen it is opened on, in any orientation, laid out and tested for an 11-inch iPad Air (1180x820 CSS
px landscape, 820x1180 portrait) first.** Remember it for every chart you build or touch:

- The page carries `<meta name="viewport" content="width=device-width, initial-scale=1">`. Without
  it iPad Safari lays the page out at its own default width and scales it, so the charts end up
  half the page or zoomed.
- The chart stack is exactly as wide as what is on screen, sticky-left, whatever the browser did with the
  page. NOT `100vw`, NOT a fixed cap, and NOT `visualViewport.width * scale` (that is the device width,
  which left the charts at half the page when iPad Safari shrank the 1970px table to fit at ~0.6 zoom;
  caught from a Safari PDF 2026-10-13). A script measures the page's own starting zoom (`_baseScale`,
  after load and after a rotation) and sets `--stack-w` to `visualViewport.width * scale / _baseScale`
  minus a 16px gutter, so a fitted page gets a full-page-wide stack, a 1:1 page a device-wide one, and
  pinch-zooming changes nothing. In-chart and title text grows by 1/`_baseScale` (13px on screen,
  capped at 22 CSS px) so it stays readable on a shrunken page. Each pane is as wide as the stack, taller
  when the stack is wider (`clamp(340px, max(60vh, 0.3 x stack width), 760px)`). Order (user, 2026-10-14):
  the M1 trading pane on top, the M5 context pane under it (both full width), then D1 and H1 side by side
  at half width each (one column only when the screen is under ~740px).
- The candles fill the pane's width: the bar spacing follows the pane width and the number of bars
  (1px minimum, 20px maximum), not a fixed 6px.
- In-chart text is ~13px on screen (`chartFont()`): large enough to read on a tablet, small enough that one
  trade's tags fit. Never go back to 18px (tags pile on each other) or to 9px (unreadable). Axis tags
  show only the word naming the line (`stop`, `target`, `entry`, `level`, `range high`), since the tag
  prints the price itself. Axis tags and marker texts that would overlap are thinned (the most
  important one stays: entry, stop, target, level, range edges; result and exit markers before P0/P1
  markers), and a ray's full text shows on hover.
- Several range boxes on one trade are named A, B, ... in start order; chart labels stay short
  ("A 08:10" at its start, "A↓" at its breakout) and the ray hover text carries the times.
- Every pane has a full-screen button (top right of its title bar): the browser's fullscreen on a
  mouse device, only the fixed full-window layer on a touch device (Safari's own fullscreen ends on a
  scroll / swipe); Esc or the same button leaves.
- Touch price axis (`_touchPriceAxis`): the chart library reacts to a mouse only on the right price
  axis, so a finger there would scroll the page. A one-finger vertical drag on the axis strip now
  stretches / squeezes the price range (down = taller candles), a double tap on it returns to auto-fit.
- A pane never skips the market structure the trade is decided on. M1 and M5 panes skip bars only in
  between: a range box is kept whole for its last 36 h (M1) / 4 days (M5), an older one as its start and
  every crest/trough of the box plus the stretch before its breakout, and any bar that reached the
  entry, stop or target price is kept (`_structure_keep`, `touch_prices`). D1 and H1 show every bar and
  reach back to the earliest box, with NO labels, lines or text: just one small arrow on the bar the trade
  is taken on (user, 2026-10-14). Range rays and start/breakout lines are on the M1 and M5 panes. The M1
  pane does not draw the other live M1 LXPB levels (the blue rays above and below); only the trade's own.
- Candles keep the default grey. Never recolour candles (blue, orange, ...) to show a range, a
  level or anything else; use rays, markers and price tags.
- A range box's candles, from its start to its breakout, are never compressed into a
  "[N bars skipped]" gap and never cut off at the window's left edge (`protect_spans` in
  `render_stop_target_report.build_m5_chart`, and the M5 pane reaches back to the earliest box).

Done for the `ss_m1_confl` / `ss_m2_confl` reports (their own chart JS and CSS in
`render_m1_confl_report.py`, which draws the panes itself rather than using the shared `_renderPane`).
The other reports keep their old layout until they are next touched; apply the same rules then.
Check a chart change with a mobile-emulated browser (Playwright `isMobile: true`) at 1180x820 and
820x1180, both with `width=device-width` and with `width=1967` (the shrink-to-fit case Safari shows), and
confirm the stack's width on screen (CSS width x scale) is the screen width minus the gutter.

# M1 fade playbook: the ENGINE decides the trades, not the report (user, 2026-10-16)

**The M1 fade report is the FADE playbook only; go-with is not part of it (user, 2026-10-16).** (Two engine rules below.)

**Engine rule 1, H1-backed range merge (user, 2026-10-16, `Params.box_merge_overlap` = 0.5, 0 = off, replay `--box-merge-overlap`).**
Two M5 range boxes whose price ranges overlap by at least 50% of the SMALLER box AND that both sit inside one known, still
unbroken H1 range box (patterns-pure `find_range_box_zz` on the CLOSED H1 bars, M5 as lower data, re-read each time an H1 bar
closes; `box_merge_tol` 2 pt lets an M5 edge poke out of the H1 box) are ONE range: they share their seats, so only one trade is
taken per merged range, direction and playbook. Judged point in time when both boxes have a breakout waiting (nothing needs the
other box to have broken out in advance), links never come apart, seat ranking is unchanged (first order placed keeps it).
The replay seeds `--h1-days` (25) of H1/M5 history from before the stream (`Engine.seed_h1`).
**The merged range IS the H1 box (user, 2026-10-16: "only one range box is considered, whatever the timeframe").** Once M5 boxes are
merged, the range they form has the edges of the tightest known open H1 range box that holds every member (`Engine.range_edges`;
e.g. 7792.50-7809.50, not the M5 box's 7798.50-7808.25). Those edges decide whether a fade level is "beyond the edge" (the level's own
price must be below the low / above the high), they are what the report draws (start = the H1 box's start; the pivot squares are
still the M5 box's), and the M5 member box still supplies the strong breakout and its timing. A box on its own, or one no single
open H1 box holds, keeps its M5 edges.  Side study:
`box_merge_study/h1_merge_engine.py` (merge off vs on); `fill_candidates.py` lists the LXPB candidates and range edges per fill.

**Engine rule 3, NO CLUSTER ANCHOR (user, 2026-10-16).** Each LXPB level is judged independently as a fade (its OWN price outside the
range edge, its own P1 run-in, its own stop / target). The cluster (same-type levels chained within 2 pt) is used only afterwards, to
look for a BETTER entry price (more extreme) among the level's own members, and that better price must itself qualify as a fade on
its own. Do not reintroduce an anchor / earliest-retested member / "classify by the cluster's first level". The old swerve move (to
the next level) is gone for the same reason; a level with a swing on it is simply skipped. Oct 16 2026 (fade 2 off): the only fade
trade is the 06:35:56 PT long at 7790.25 (+3.50 pt), the level that is the extreme of the 7790.25 / 7791.50 cluster.

**Engine rule 4, TARGET = RANGE EDGE (user, 2026-10-17, `Params.target_mode` = "range_edge", replay / report flag
`--fade-target zigzag` brings back the old most-recent-zigzag-pivot target).** A fade's target is the edge of the range it fades: a
long (LHPB below the range) targets the range LOW, a short (LLPB above it) the range HIGH, using the merged range's H1 edges when
merged (`range_edges`), the nearest edge past the fill when the trade serves several ranges; still 0.25-50 pt from the fill or the
level gets no order (`no_target`). Go-with keeps the zigzag target. Oct 16 2026: the 06:35:56 PT long's target is 7792.50 (H1 low).

**Engine rule 2, FADE 2 IS OFF (user, 2026-10-16, `Params.fade2` = False, replay / report flag `--fade2` brings it back).** A fade
of a box's second-side breakout (its OTHER edge had already broken out strongly before) is not traded, tagged or drawn; only
fade 1 (the box's first strong breakout) is. A trade whose boxes were mixed keeps only its fade-1 boxes. In the M1 report the
fade-2 boxes are dropped before the seats are handed out, so a fade-2 trade has no breakout-from-range tag and is not listed,
and the charts draw fade-1 boxes only. Oct 16 2026: the engine's 06:38:45 and 09:02:32 PT trades are fade 2 and disappear. Never count go-with trades
(engine `playbooks` = go_with, report 'retest-into-range') in its results, totals or comparisons, and drop them from engine
replays before judging the fade playbook. The engine still takes go-with trades and they use the single open-position slot
(noted in `live_range/engine.py`, `live_range/replay.py` and the head of `render_m1_confl_report.py`).

**Box source flag (user, 2026-10-19, `Params.box_detector`, replay / report `--box-detector`; default `"zz"`).** `"trading_range"`
takes the M5 range boxes from `patterns_pure/find_trading_range` (`live_range/trbook.py`) instead of the zigzag boxes; episodes,
seats, both playbooks and the H1 merge are the same code. A box that the detector merges with a later one is RENEWED: a new
box id (new seats, fresh episode Reader on the merged edges), and the H1 merge never links two generations of one range. Not
the default: switching it is a strategy change the user decides (see `live_range/README.md`, "The other box source").

**Standing rule (user, 2026-10-19): range-box regression cases.** After ANY change to `patterns_pure/find_trading_range.py`,
`find_zigzag.py`, `find_ATR.py`, `find_range.py` (the strong-breakout grader, `_atr_series`) or `live_range/trbook.py`, run
`python patterns_pure/tests/test_find_trading_range_cases.py` and report the result. It pins two ranges the user signed off,
on bars frozen in `patterns_pure/tests/fixtures/`: USDJPY H1 Apr 21 10:00 PT - Apr 29 03:00 PT 2026, 158.958-159.847, failed breakout up Apr 29 04:00 PT, exact (and
159.093-159.847 live as of Apr 27 19:00 PT), and AUDUSD H1 Jan 13 08:00 - Jan 19 06:00 PT 2026, 0.66671-0.67108, exact; plus no look-ahead on both. A failing
case means the change broke a signed-off range: do not edit the expected values or commit the change without the user's OK.

**Standing rule: for the M1 range-fade playbook, a trade exists only if the live engine (`live_range/`) takes it.** The
algorithm is range first: a range box with a strong breakout exists, THEN the LXPB ledger is read only as a cache lookup
for "which level can be traded for the fade seat of that box". Questions like "did the fade trades occur today", "why was
level X not traded", "is this a valid fade trade" are answered by replaying ticks through the engine
(`python scid_only/run_scid_only.py live_range/replay.py --from <day 22:00 UTC prior> --warmup-days 5 --out <dir>`,
events.jsonl shows every order armed / amended / cancelled with its reason). The `ss_m1_confl` report now shows exactly the
engine's fade trades (it is engine only, see the next section), so its rows and the replay agree; the older retest-first pipeline
(clusters over hours, a 3 h fill window, `unfilled_within_window` / `weak_run_in` rows, tags read afterwards) no longer exists and
must not be used to explain a trade. Ledger rows (`lxpb_levels_cache.m1_levels`, wick filter, fate) are the lookup the engine reads.

# The M1 fade report (`ss_m1_confl/`) is ENGINE ONLY (user, 2026-10-16)

**The older retest-first pipeline of this report was DELETED.** `render_m1_confl_report.py` no longer selects candidates,
clusters confluence, finds fills, picks stops or targets, tags range boxes afterwards or hands out seats: all of that is the
live engine (`live_range/`, see the sections above). The report replays the window's ticks through the engine
(`live_range.replay.build_engine` / `feed`, decisions once a second, H1-backed range merge, fade 2 off) and
`ss_m1_confl/engine_rows.py` turns each FILLED fade trade (go-with dropped) into the row dict the page and charts read; the M1
ledger (`LC.m5_levels`, swapped to M1 behind the shared "m5" calls) is only a lookup for the chart code. Consequences:

- Live target-rule and management toggles (user, 2026-10-17, like the M2 report): at every fill the engine also follows WHAT-IF
  brackets (`Engine._views`, report only, never read by a decision): the same fill and stop with each of the old M1/M2
  reports' THREE target rules (`Engine._target`: `range_edge`, `zigzag` = swing-extreme, `opposite_zz` = the farthest standing
  opposite-type P0 formed since the leg into the retest began), plain (stop / target / EOD flat) and managed (stop trail + RR
  floor). `engine_rows` ships them as the row's modes (`range-edge`, `swing-extreme`, `opposite-zz`, the old data-mode names and
  labels), so the "Target rules" checkboxes and the "Apply thrust-trail + RR-floor management" box swap targets, outcomes and
  stats in place; several rules ticked = the farther target per row. Defaults on load, as the old reports: only the engine's
  rule ticked (`--fade-target`, default range_edge per the user), management UNTICKED (old reports: unticked). The engine's
  managed bracket of its own rule IS the real trade (checked identical, Sep 28-Oct 1).
  The trade SET is the engine's (entries, seats and the one position slot follow the engine's own target); another rule only
  changes how the same trades would have ended. No unfilled / NO TRADE rows, no go-with, no news-spike badge. A WIN is
  `r > 0`, whatever ended it. The console's "N trades taken, win rate ..." line is the plain (unmanaged) bracket.
- Flags: `--since-day --until-day --warmup-days --label --bar-minutes --output --min-r` plus `--decision-s`,
  `--box-merge-overlap`, `--box-merge-tol`, `--h1-days`, `--fade2`. Every flag of the deleted pipeline (`--ss-confl-min`,
  `--p0-kinds`, `--target-mode`, `--swerve*`, `--peg*`, `--workers`, `--playbook`, `--range-lookback-days`, `--news-*` ...) is in
  `REMOVED_FLAGS`: accepted and ignored so older regen commands still run. Run it through `scid_only/run_scid_only.py` where
  there are no TradingView exports; no `--workers`, no fork pool, no 8 GB peak any more (the engine replay of a month is minutes).
- `range_box_tags.py` is only the tag names now. Range boxes on the M1 trading pane carry their zigzag pivots (`Cr1` crest, `Tr2`
  trough, ... numbered per box, `brk` on the one that broke it, `A:Cr1` when a trade has several boxes) from the engine's own box
  ledger (`Engine._box_snapshot`); small squares above/below the M5 extreme's own M1 candle (`_mark_range_pivots`), M1 pane only.
- Chart dict keys and DOM ids are `main` (the M1 trading pane) and `ctx` (the M5 context pane). The table's column positions are
  hard-coded in `SORT_COLS` and the `nth-child` header rule: change them together with any column added or removed.
- A fixed month of the fade playbook (`sep_month_fade`) is `--since-day 2026-09-01 --until-day 2026-09-30 --warmup-days 5
  --label "Sep 2026, whole month"`. Regenerated with the engine on 2026-10-17 (no anchor, H1 edges, fade 1, range-edge
  target): 19 fade trades, 57.9% win rate, avg R +0.11, total +2.1R. The output goes to the scratchpad and is gzipped into
  `public/reports/ss_m1_confl/sep_month_fade.html.gz` (the report writes plain .html).
- `sep_oct_fade` (2026-10-18, user): the same report over Sep 1 - Oct 16 2026 (last .scid tick), `--since-day 2026-09-01
  --until-day 2026-10-16 --warmup-days 5 --label "Sep 1 - Oct 16 2026"`, gzipped into `public/reports/ss_m1_confl/sep_oct_fade.html.gz`.
  Regenerated 2026-10-18 with the 6-M5-candle range rule: 23 fade trades. On load (range edge, management off): 69.6% win,
  avg R +0.15, +3.4R (was 25 trades, 72.0%, +4.8R before the rule). NOT public (not in `proxy.ts`).
  Regenerated 2026-10-26 with the "Entry level" filters (user): each row's ENTRY level (`engine_rows._entry_kind`) is marked
  spike (spike-P0 / spike+swing-P0), swing (swing-P0 / spike+swing-P0) and its P0 candle's rejection wick in pt (upper for
  LHPB, lower for LLPB, as the plain-P0 wick rule); badges SPK / SWG / W<pts>; filters any/yes/no each, wick threshold
  (default 1 pt = `min_plain_wick`), combined ALL (and) or ANY (or), stats recompute live.
- The engine's tags on a row: `plain_p0`, `no_confl` (cluster of one), `swerved`, `breakout-from-range`, `fade1` (`fade2` /
  `fade-mixed` only with `--fade2`), `r_below_min`, `eod_flat`.

No news-spike logic in the M1 fade playbook (user, 2026-10-16): the news-spike tag and the engine's news-spike gate (`news_gate`,
`ShockTape`) are REMOVED. Do not add a shock-second / wide-spread check back. `liquidity.py` stays only because `ss_m5_confl2`
still imports it (its own tag, untouched). The scheduled 05:29:55-05:30:40 PT news-pull blackout is a different rule and stays.

Sign-in (user request 2026-10-15): `/login` takes Google OR email + password (NextAuth Credentials in `auth.ts`). Both give the
session email `ALLOWED_EMAIL`, and the review state in `row_state` is keyed by report/row only, so it is one data set for either
way in. The password is the `LOGIN_PASSWORD` env var on the Vercel project; it is never written into this public repo (a hash of a
short password would not protect it either).

`sep_month_fade` is PUBLIC (no sign-in): `proxy.ts` excludes `reports/ss_m1_confl/sep_month_fade.html` and
`js/row-store.js` from the auth matcher (a literal: Next reads it at build time). Nothing else is public, and
`/api/rows` stays gated, so the report's Valid/Notes cannot be read or written by a visitor. Publishing another
report means adding its path to that matcher; do not do it without the user asking.

# Live range engine (`live_range/`): orders as close to real as possible (user preference)

**Standing preference (user, 2026-10-13): the streaming engine places orders the way a real account with limited
margin would, and judges every gate BEFORE the touch.** Remember it for every change to `live_range`:

- **PRODUCTION DATA LIMIT (user, 2026-10-16): in production the algo only gets data once per SECOND, not per tick.**
  Every decision of the engine (arm / pull / re-quote an order, gates, run-in, box and level reads, RR-floor exit) is
  made from a 1-second snapshot and must never use information finer than that (an individual tick, the aggressor flag
  of a single print, the intra-second order of highs and lows). Implemented (`Params.decision_s` = 1.0, replay flag
  `--decision-s`, 0 = old per-record behaviour for comparison): the first record of a new second lets the algo decide
  on what came before it (`Engine._decide`: RR-floor exit, peg re-quotes, then `_orders_tick`). What stays per record,
  because the exchange does it, not the algo: fills of resting orders, stop / target hits, bar building. Bar-close
  work (stop trail, entry rebuild) still runs on the first record of the new bar, which is never earlier than a 1 s
  snapshot could do it. Known simplifications: a fill pulls the other orders at once (a live account would cancel them
  at its next snapshot, with a double-fill risk), and the end-of-day flat fires on the first record at or after its time.
  Never write a rule that needs tick-level reaction.
- No ladders of resting orders: an order is placed only when price is within `arm_pts` of its level and
  pulled when price drifts beyond `cancel_pts`; at most `max_working_orders` rest at once and none while
  `max_open_positions` are open (a fill pulls the rest). The order set is updated on live data, tick by tick.
- Every filter is evaluated as if price were already on the order, and re-evaluated while it approaches: a
  weak run-in, a breakout that would not grade strong at that price, no plannable stop or target, a news or
  end-of-day blackout. An order that would fail is not placed, and is pulled the moment it would fail;
  it is never pulled at the touch because of what the touch showed. A target or stop that appears later in the
  run-in makes the order placeable then.
- A level that cannot be retested yet (the bar after its breakout has not closed) gets no order: touching it
  only consumes it.
- Fills follow the report's pegged model on the tape's own records (price, bid, ask, aggressor side): the right
  aggressor at or through the price fills, a wrong-side touch re-quotes the order a tick closer to market (up
  to 1 pt), each re-quote re-judged by the gates. Never fill an order just because a price printed at it.
- A plain-P0 is only an entry level with at least 1 pt of rejection wick on its formation candle (the M1/M2
  reports' rule); weaker ones end at their breakout but still serve as stop candidates while untouched.
- The engine was built to match the old M1/M2 report's pipeline (`live_range/README.md`, "Matching the report", history only): the anchor
  (earliest-retested cluster member) that the range tag and the run-in are judged from, the box's break candle for the
  fade test, the reopen gate (no news-spike gate). User decisions (2026-10-13): no cold-level mode, no 3-hour fill window, a
  level's own breakout need not be strong (`bo_range_atr` stays 0), boxes never expire, one order per seat that is never
  pulled for a later contender.
- Trade management (stop trail, RR floor) runs on the trading-bar ledger and bars, as in the reports; seat
  contenders are ranked spike entry first, then nearest (`seat_rank="spike_first"`).
  **User standing rule (2026-10-13): never change the seat-ranking default.** Offer an alternative as an option
  and let the user choose; do not flip `seat_rank` (or any other strategy default) on your own.

# Data convention: TradingView exports confirm, Sierra Chart ticks extend

**H1 and M5 history comes from TradingView's own continuous ES1! exports.
Past the exports' last bar, the series is extended with bars resampled from
the CURRENT front month's Sierra Chart `.scid` ticks, up to that contract's
roll and no further.** (User decision 2026-09-28: TradingView exports are
needed to confirm each roll's offset, not to supply every bar.)

The exports stay in charge of everything that needs them:

- **Confirming the offset.** The front month's offset is measured against the
  exports alone (`_tv_m5()`, never the extended series, which would confirm
  any offset). It needs >= 100 of that contract's front-month M5 bars in an
  export (`_SCID_OFFSET_MIN_OVERLAP`, ~8 trading hours).
- **History and holes.** Ticks never fill a hole inside the exported range,
  never reach back before the exports' last bar, and never cross a roll.
  Where no export reaches, that range is simply absent.

`_extend_with_scid` also: rebuilds the exports' last bar from ticks (an export
taken mid-bar leaves it partial); appends only bars that traded (no
forward-filled empty slots -- that is what made 70% of the old .scid M5
retests phantoms); leaves out the bar still forming at load time; and first
proves ticks rebuild the exports' own last 10 days of bars (same bar times,
opens/closes exact on >= 98%, every price within a tick on >= 99%), raising
otherwise. `LXPB_SCID_EXTEND=0` turns it off (the levels runtime, which has no
ticks, sets it).

The two loaders are `render_labels_report._display_h1()` and `_display_m5()`
(TradingView-only: `_tv_h1()` / `_tv_m5()`, merged from `DISPLAY_H1_PATHS` /
`DISPLAY_M5_PATHS`). To repair or deepen history, add another TradingView
export to those lists.

**Next roll: Z26 -> H27, Mon 2026-12-14 3:00 PM PT.** The tick extension stops
there and every load prints `stops at the EPZ26 roll`. From 7 days before it,
loads print a reminder. When either appears, or the current date is past the
roll, **ask the user for fresh post-roll TradingView exports (H1 and M5)
before relying on any bar after the roll**, then follow the rollover
checklist below. The same holds at every later roll.

**The export rule is repo-wide, not just `lxpb-v2`.** `label-review` and
`retest-vol-scalp` follow it too, through `../es_h1_display.py` — a smaller
loader over the same export list, for subprojects that deliberately avoid
importing lxpb-v2's report modules. Keep the two export lists in step. That
loader does NOT apply the tick extension yet: those two subprojects stop at
the H1 export's last bar.
`data/es-h1-continuous-backadjusted.csv` and its builder
`data/build_es_h1_continuous.py` are retired; nothing reads them.

`lxpb_levels_cache.h1_levels()` runs the state machine once over the H1
series and `m5_levels()` once over `m5_bars_continuous()` (which is
`_display_m5()`). One ledger per timeframe, spanning every rollover; neither
takes a contract or `seg_idx`. Any new timeframe follows the same shape.

# P0 kinds: spike-P0, swing-P0, plain-P0

**Every level price closes through is a P0**, and `../lxpb.py` tracks every
one of them to its retest. Its `p0_kind`, fixed at the breakout, says which
rule it met; there is no "valid P0" any more, only these names:

- **spike-P0** -- the formation candle is a spike, detector pairing (below).
- **swing-P0** -- a swing high/low AND consolidating at breakout (efficiency
  ratio < `ER_CONSOLIDATION_MAX`). A raw swing in a trending run is not one.
- **spike+swing-P0** -- both; it counts as either kind.
- **plain-P0** -- neither.

Before 2026-09-24 plain-P0s were dropped at their breakout (the old
"candidate gate", fate `gated_dropped`). Levels never interact, so keeping
them changed nothing about any other level. Every existing consumer still
reads the old population, and says so at the call site:

- `detect_lxpb_h1(bars, p0_kinds=...)` -- required: `ALL_P0_KINDS` or
  `SPIKE_OR_SWING_P0_KINDS`.
- `h1_levels(plain_p0=...)` / `m5_levels(plain_p0=...)` -- required:
  `PLAIN_P0_TRACKED` (full ledger) or `PLAIN_P0_UNTRACKED` (plain-P0s end at
  their own breakout, exactly the old ledger; they stay in, since P1 sibling
  counts, confluence and targets always counted them).

Letting a consumer see tracked plain-P0s is a strategy change: the user
decides it, one consumer at a time.

## `.scid` for ticks, and for extending the current front month

Apart from `_extend_with_scid` (above), `.scid` is only for tick-level work.

Per-contract `.scid` files remain the right source for second- and
tick-level work: real fills, exit resolution, the 1s/1min panes, bid/ask
volume, footprints. Use `render_labels_report._contract_index_for` to pick
whichever contract actually traded at a given instant.

Raw tick prices are mapped ONTO the continuous scale by adding
`render_labels_report._offset_for_ts`. **Never the reverse** — the continuous
series is never pushed back into raw per-contract terms to synthesise
structural bars. (Subtracting the same offset to compare a continuous price
against raw ticks inside a scan is fine; that is still the tick side being
read in its own terms.)

Those offsets are MEASURED per contract at load, against the display M5
export itself, over the span where that contract was genuinely front month
(`_measure_scid_offset` / `_front_month_start`). Do not reintroduce hardcoded
offset constants such as `TV_GROUND_TRUTH_OFFSETS`, and do not reintroduce a
"vintage delta" correction on top of them: a constant is only ever right for
the single export vintage it was measured against, and the delta existed
solely to patch that. (Re-anchoring pre-roll M5 exports, below, is different:
its offset is measured from the exports' own overlap on every load and never
stored.) Only the roll TIMING rule (`B26.roll_switch_utc`) is still
taken from `build_es_h1_2026_backadjusted`; the contract list itself is
`render_labels_report.CONTRACTS` (U23 onward, wrapping `B26.CONTRACTS`). The roll rule's
3-business-day count skips market holidays, and a holiday expiry Friday moves back a
day first: TradingView rolled one session early in Jun 2024, Jun 2025 and Jun 2026
(Juneteenth inside the count, or on the expiry Friday itself), and a weekday-only
count read the wrong contract's ticks for that session.

## Validation, and what a failure means

Every continuous series is checked before any caller sees it:

- `_assert_one_vintage` (H1) — the H1 list is a single vintage: its exports
  must agree to the tick wherever they overlap. The H1 export is the genuine
  post-roll reference the M5 series is checked against, so it is never
  shifted; replace it with a fresh export instead.
- `_merge_vintages` (M5) — old M5 history cannot be re-exported after a
  roll, so `DISPLAY_M5_PATHS` holds one inner list per roll vintage and all
  older history is shifted onto the next vintage by an offset MEASURED at load
  on their overlap. Each inner list must itself be one vintage
  (`_assert_one_vintage`). It raises unless the overlap is at least 1,000 bars, the
  difference there is one constant on >= 99% of them and the same constant in
  every contract segment the overlap spans, and a roll lies between the two
  vintages' last bars (a shift with no roll is a revised export, not a
  re-anchor). Never store that offset as a constant, and never shift an
  export that fails these checks — drop it.
- `_assert_no_roll_gaps` — no unexplained close-to-open jump at a roll
  instant. A jump there means the splice is wrong. Fix the splice; falling
  back to per-contract data is never the answer.
  A roll that lands on a weekend/holiday REOPEN is skipped with a printed
  note, since its bar jump includes the real closure gap (Jun 2025: 29pt,
  matching U25's own ticks); `_measure_scid_offset` still checks the splice.
- `_assert_shares_h1_scale` — every contract segment of the M5 series must
  agree with the H1 export (>= 99% of shared bars). This is what verifies the
  re-anchored history outside the export overlap, segment by segment, rather
  than assuming a roll moves every segment alike.
- `_report_series_gaps` — prints (does not raise) any hole longer than a
  long holiday weekend. A hole is legal but never harmless: the state
  machine walks straight across it as though the two sides were adjacent
  bars. Close it by adding an export.

## Rollover checklist

A quarterly roll re-anchors TradingView's whole back-adjusted history: every
bar of every segment moves by that roll's spread (U26->Z26 on 2026-09-14:
exactly +67.75pt on every bar back to Jul 2023). So after each roll:

1. Export one fresh H1 file reaching back as far as TradingView allows and
   make it the ONLY entry in `DISPLAY_H1_PATHS` and `../es_h1_display.py`. It
   should reach back at least as far as the M5 history, since it is the
   per-segment check on the re-anchored M5 bars.
2. Export the latest M5 range post-roll, starting weeks before the roll so it
   overlaps the newest pre-roll M5 export by thousands of bars, and put it in
   a NEW inner list at the end of `DISPLAY_M5_PATHS`. Leave every pre-roll
   list as it is: the loader re-anchors all of it and prints the offset it
   measured. Later exports made before the next roll go into that same new list.
3. Append the new front month to `render_labels_report.CONTRACTS`.
4. Check the roll instant on ticks: bars in the last session before
   `B26.roll_switch_utc` must match the outgoing contract's ticks at one
   constant offset, bars after it the incoming contract's at +0.00.
5. Level caches need no manual step: a pure re-anchor is shifted in place,
   anything else (new depth, revised last bar) rebuilds on first use.
6. Update the "Next roll" line in the data convention above.

The new front month's ticks cannot be mapped until an export holds at least
100 of its front-month M5 bars (`_SCID_OFFSET_MIN_OVERLAP`, ~8 trading hours);
`_measure_scid_offset` raises before then. Once it does, the tick extension
takes over from that export's last bar until the following roll.

# Spike candles (hammer / shooting star): patterns-pure only

**Every hammer or shooting-star test in this repo is patterns-pure's
`find_hammer` / `find_shooting_star`, from the vendored copy in
`patterns_pure/`, and nothing else.** Repo-wide, like the data rule above.
That definition is:

- body <= 35% of the bar's range, long wick > 50%, opposite wick <= 25%, AND
- a hammer closes at or above the PREVIOUS bar's low; a shooting star closes
  at or below the previous bar's high -- each with a 5% buffer, measured in the
  candidate candle's OWN range (`PREV_BAR_BUFFER`): a hammer may close up to
  5% of its range below the previous low, a shooting star up to 5% above the
  previous high. (Before 2026-09-21 the test was exact, no buffer.)

The wider 35% / 25% thresholds and the previous-bar close are both essential.
A single-bar version, or one with its own thresholds, is wrong even if it
looks close.

- Never restate the thresholds in code. Call the functions (they need the
  previous bar in the frame; see `render_labels_report.is_spike_pp`).
- `../lxpb.py`'s `is_spike` loads them by path; feed `advance_one_bar` only
  through `lxpb.iter_bars(state, bars)`, which attaches the per-bar verdict
  and seeds the previous bar from `state` on a resume.
- Never import from `D:\daily-analysis\patterns-pure` directly. To pick up a
  change, bump the `patterns_pure` submodule; the level caches' rules
  fingerprint hashes those files and rebuilds on its own.
## Which pattern is the spike for each level type: two pairings, both deliberate

The definition above is shared; the pairing is not. Two pairings exist, and
mixing them up silently flips which P0s count as spikes:

| Pairing | LHPB (level = bar's high) | LLPB (level = bar's low) | Where |
|---|---|---|---|
| Detector | hammer | shooting star | `../lxpb.py`'s `is_spike`, which decides which P0s are spike-P0s. So also the H1/M5 level caches and every reader of their `is_spike`: `ss_m5_confl2` (spike-P0 stop, H1 P0 kind), `trade_management.py`, `../retest-vol-scalp`. Also `../lxpb-spike` (own helper, same pairing). |
| Rejection | shooting star | hammer | `render_labels_report.is_spike_pp` (both copies) and its P0 Spike hint, `analyze_retest_cluster_selection.py`, `analyze_retest_features.py`, `../lxpb-spike-atr`. |

- Anything that reads a level's cached `is_spike` is on the detector pairing,
  whatever it calls the candle. Don't reinterpret it as the other pairing.
- New code that tests the formation candle itself must say which pairing it
  uses, in its docstring.
- Never switch a place from one pairing to the other, or merge them, without
  the user deciding it. It is a strategy change: for the detector it changes
  which P0s are spike-P0s, so every ledger and report changes.
- Full table with file paths: `patterns_pure/README.md`.

## Spike-thrust candle

**A spike-thrust is the candle IMMEDIATELY after a spike** (a hammer or
shooting star by the definition above) that closes in the spike's direction
(up, close > open, after a hammer; down after a shooting star) AND meets at
least one of these four combinations -- the ONLY ones, nothing between or
beyond them:

| Thrust range vs spike candle's range | Thrust body vs its own range |
|---|---|
| >= 0.75x | >= 80% |
| >= 1.0x | >= 60% |
| >= 1.2x | >= 50% |
| >= 1.5x | >= 40% |

A candle between two rows needs the body of the row below it (1.4x needs 50%,
3x still needs 40%); nothing under 0.75x or under a 40% body qualifies.

It is patterns-pure's `find_spike_thrust` (vendored in `patterns_pure/`, rows
in its `SPIKE_THRUST_TIERS`) and nothing else, under the same rules as the
spike itself: never restate the rows or the close test inline, and change
them only in the patterns_pure repo and then bump the submodule.
Example: H1 20 Dec 2024 05:00 PT (spike = the 04:00 PT hammer; 1.76x, 48% body).

- It describes two consecutive candles and knows nothing about LHPB/LLPB. Code
  that ties it to a level must say which pairing (above) picks the spike.
- It is NOT the "thrust candle" of `ss_m5_confl2` / `trade_management.py`,
  which just means a level's P1 breakout candle, with no shape test at all.
- ONE deliberate local extension exists, and only one: `h1_bias.py`'s
  "bias-thrust", for the H1-bias expiry rule and the bias-served tag that
  shares it. It differs in exactly three ways -- it adds a flat row
  (`EXTRA_THRUST_TIER`, >= 0.60x range with a >= 75% body); it adds the
  repo's only SLIDING row (`sliding_thrust_body`: from >= 0.65x range
  needing a 50% body, easing half a point per point of extra range to a 42%
  floor reached at 0.81x, one-way, so extra body never buys back missing
  range); and it lets the reference candle be any bias candle in
  `h1_bias.BIAS_KINDS` -- an SFP today -- as well as a hammer/shooting star,
  so every bias expires the same way. All three are local: `patterns_pure/`
  is untouched and nothing outside that module sees any of them. The
  canonical rows are read from `SPIKE_THRUST_TIERS`, never rewritten, and
  `h1_bias._assert_matches_patterns_pure` checks on every load that the
  generalised code with both local rows OFF reproduces `find_spike_thrust`
  exactly on patterns-pure's own rows and reference candles. Adding another
  such extension, or another row, is a strategy change: the user decides it
  (the sliding row was added 2026-09-21, folding the old bias-served
  constants into the thrust definition).

# Zigzag: patterns-pure `find_zigzag` only

**Every zigzag in this repo is `patterns_pure/find_zigzag.py`, and nothing else.**
Repo-wide, like the spike-candle and data rules. New code never writes a zigzag of
its own: no "running high / reversal" loop, no fixed-point pivot finder, no second
copy "to keep it incremental". It calls the detector. User decision 2026-10-14: the
two old zigzags (the ATR one behind the range boxes, and `m5_structure`'s 3 pt / 3 bars,
5 pt / 2, 10 pt / 1 one behind the targets and run-in) were merged into this one.

The rule, all of it in `find_zigzag.py`'s docstring:

- **ATR based, no hard-coded points.** A leg is confirmed when price reverses
  `0.75 x ATR(21)` off its extreme (`ATR_MULT`, `ATR_PERIOD`), ATR read as of the
  confirming bar, so every pivot is point-in-time. Nothing is confirmed before the
  ATR has 21 bars.
- **At least `MIN_BARS` = 1 bar** between the extreme's bar and the confirming bar
  (user decision 2026-10-15; it was 2 from 2026-10-14). The extreme's own bar counts as evidence
  of the reversal ONLY when `lower_data` proves the order inside it: the bar made its extreme first
  and fell (rose) afterwards, so a bar can then confirm its own extreme. With the order unknown (no
  `lower_data`, no lower bars in the bar, both extremes in one lower bar) or the other way round
  (low first, then the high), it never counts. A new extreme restarts the count. The minimum is in
  bars of the series the zigzag runs on. Still at most one pivot per bar, so a bounce and the next
  swing inside one bar confirm on that bar and the next. The range boxes pass M1 as `lower_data`;
  `m5_structure.zigzag_pivots` (targets, run-in) passes none, so there only `MIN_BARS` = 1 applies.
- **A leg's starting bar counts its own far side** (user decision 2026-10-16): when the lower-timeframe order proves a bar made its
  extreme first and moved away afterwards, that move is evidence for the next leg even if the same bar just confirmed the previous
  pivot (before, it was lost: M5 Oct 16 2026 05:50 PT high 7802.75 then low 7798.50 missed the 7798.50 trough). A crest and the
  trough right after it can share one bar. Changed without any regression run; every report that reads the zigzag needs a regen.
- **Range box extension grace** (user decision 2026-10-16, `find_range_box_zz.extends_box`, mirrored in `live_range/boxes.py`): a pivot past
  the box's allowable edge (starting edge + 20% of the starting height) by LESS than 1 pt (measured from the farther of that limit and the
  box's current edge, so it repeats with no overall cap) still WIDENS the box instead of breaking it, when the pivot's own candle is mostly
  inside the box as it stood: >= 75% of its range inside, or >= 50% inside with the close inside. The next candle is not read. Written
  and wired into both the batch detector and the live book with NO test or replay run; `check_boxes.py` (live vs batch) not run yet.
  Every report and the engine need a regen / replay to show it.
- **Range box minimum length** (user decision 2026-10-18, `find_range_box_zz` `min_range_minutes` = `MIN_RANGE_MINUTES` 30, mirrored in
  `live_range/boxes.py` `BoxBook.long_enough`): at least 6 M5 candles must be part of a range. A broken box is kept only if the bars
  from its first pivot's bar up to, not including, its break candle number >= 6 (`n_bars` column; open / merged boxes not filtered),
  and a strong breakout (episode) counts only if the box had >= 6 bars before its breakout bar (a younger one is dropped from
  `episodes`; the live engine does not trade it). Expressed in minutes and rounded up to bars of the series, so H1 boxes (1 bar) are
  unaffected. `check_boxes.py` Sep 28 - Oct 6 2026: 0 box mismatches, episodes batch 161 = live 161. Engine replays and every report
  that reads the boxes need a regen to show it.
- **Reversal is cumulative** (the deepest adverse price since the extreme), the next
  leg starts at the most adverse bar of the one just confirmed, at most one pivot is
  confirmed per bar, and the last still-extending leg is never a pivot.
- **Order inside a bar** (a bar that makes a new high and also falls far enough:
  low first confirms the old crest, high first only moves it) comes from
  `lower_data` via `intrabar_order`; unknown means the extreme in the direction of
  travel came first.

| Need | Use |
|---|---|
| a whole series | `find_zigzag(data, atr_mult, atr_period, min_bars, lower_data)` -> frame `time, price, kind ("crest"/"trough"), confirm_time, bar, confirm_bar` |
| bar by bar (live) | `ZigZag(...).step(high, low, atr, order, t)` -> `Pivot(kind, i, price, j)`; `find_zigzag` is this same machine, so live and batch cannot disagree |
| arrays | `zigzag_pivots(high, low, atr, order, ...)` |
| "which pivot was known at T" | `most_recent_pivot(frame, kind, cutoff, after)` / `ZigZag.most_recent(...)`: formed AND confirmed strictly before `cutoff` (never use `time` alone) |
| the continuous M5 / M1 series, memoised | `m5_structure.zigzag_pivots(bars=None, ...)` (a loader, not a zigzag) |

Callers today: `find_range_box_zz` (boxes), `live_range/boxes.py` (live boxes) and
`live_range/engine.py` (targets, run-in), `ss_m1_confl/range_box_tags.py` (chart pivots),
and the target / run-in rules of `ss_m5_confl2`, `ss_m1_confl` and `range_breakout`
(through `m5_structure`). The reports' knobs are `--zz-atr-mult` (0.75) and `--zz-min-bars`
(1); `--zz-threshold-pts` is gone and accepted-and-ignored so older regen commands run.
Pivot kinds are `"crest"` / `"trough"` everywhere (the old `"high"` / `"low"` are gone).

Rules:

- Change the zigzag in `find_zigzag.py` only, and every consumer follows. It changes
  which pivots, boxes, targets and run-ins exist, so it is a strategy change: the user
  decides it, and reports regenerate only when the user asks.
- One parameter set for every caller. A caller that wants different `atr_mult` /
  `min_bars` says so at the call site, and the user decided it.
- Not zigzags, left alone: `m5_structure.swing_pivots` (a k-bar fractal) and the
  vendored `find_swings` (H1 swing levels for the cluster-selection report,
  `trap_variants`, `find_LP`, `find_eqh_eql`, 3DF). `find_swings` is an ATR zigzag too
  but a byte-identical upstream copy with a local-extreme filter and one scalar ATR;
  it has NOT been moved onto `find_zigzag` (open decision for the user).
- Regens pending as of 2026-10-14: every report that reads the range boxes or the
  zigzag targets / run-in (`ss_m5_confl2`, `ss_m1_confl` incl. `sep_month_fade`,
  `range_breakout`) still carries the old pivots until regenerated.

# H1 directional bias (`h1_bias.py`)

A bias is plain H1 candle context, NOT a level: a hammer is bullish, a
shooting star bearish (patterns-pure, read on the candle alone -- neither
LHPB/LLPB pairing above applies, since no level is involved), and an "sfp" is
patterns-pure's `find_sfp` (module `find_sfp_fractal`) -- a candle that sweeps an H1 swing low and closes
back above it (bullish) or sweeps a swing high and closes back below it
(bearish). `find_sfp` owns which swing that is: the most recent fractal pivot
that is both confirmed AND still UNTESTED, so a swing already traded through
is spent and yields no further SFP. Like every other pattern in the repo it
lives in `patterns_pure/` and is never restated at a call site.
Every bias is short-lived and `h1_bias.py` owns the expiries.
Its only consumer is `ss_m5_confl2`'s Bias column and its `fading_bias` dynamic
filter -- a review aid, never a strategy input.

# Trap variants (`trap_variants.py`)

The daily-analysis trapVariants (`E:\daily-analysis\trapVariants`, engine
`patterns-pure/find_trap.py`) ported ONE TIMEFRAME DOWN: the D1 variants
(V1, V2, V2.5, V3, V4) run entirely on H1, the H1 variants (V6-V9) run as M5
triggers under H1 wide/inside context against H1 levels. ATR is ATR(24) on H1
(the upstream LTF convention). Every detector is the vendored patterns-pure one
(`find_LP`, `find_eqh_eql`, `find_swings`, `find_inside_bar`,
`find_engulfing_*`, `find_targets`, hammer/shooting star); the module owns
only the variant cascade and the trap lifecycle, and its docstring lists every
local decision (confirmation on H1 too, target renames IHH/IHL/IDH/IDL, life
after confirmation, level-pool depth). Like `h1_bias.py`, its only consumer is
the ss_m5_confl2 Bias column -- a review aid, never a strategy input.

# Report layout: the trade table fits a 24" monitor (range_breakout)

**On a desktop 24" monitor (1920px wide) a range_breakout report's trade
table shows the whole row with NO horizontal scrollbar, however many columns
it has.** Adding or removing a column must never bring the scrollbar back.
The layout does this by itself, so a new column needs no width tuning:

- Desktop (mouse/trackpad, `(hover: hover) and (pointer: fine)`): the table is
  the window's width, and every header and cell may wrap between words but
  never inside one (times and prices stay on one line). The Notes box follows
  its column's width. If the row still doesn't fit, a small script steps the
  table's font down until it does (9px floor), and re-fits on window resize.
  Expanded chart rows take the table's width and never widen it.
- iPad / touch tablet (`(hover: none) and (pointer: coarse)`), and only there:
  the table scrolls inside its own window-sized box (pinned header row and
  Trade Id column), charts are pinned to the visible width with heights
  following the screen, portrait puts one chart per row, and tap targets are
  bigger.
- The Tags column is never wider than the Type column (every device): each
  tag badge shows a 2-3 letter code (VS volume spike, SC H1 spike
  confluence, FB fading bias, BS bias served, NS news spike, GO Globex open,
  SW/SWB/CR swerve, R< below min R, EOD end-of-day flat, MG management),
  stacked one per line, with the full label and detail in its hover text
  and the legend on the Tags header. A new tag needs a code in that list.
- The Bias column is never wider than Type either: one code per line (H
  hammer, S shooting star, SFP swing failure, HoH / LoSS retest, V1..V9 trap variant;
  -N = H1 candles back, ✓ = trap confirmed), green bullish / red bearish /
  italic trap, full text in the cell's hover. A new bias kind needs a code.
- A new column's cell content must have break points: no long space-less
  strings (comma lists need a space or `<wbr>` after each comma), or it pins
  its column wide and forces the font down.
- After a layout change, check with headless Chrome at 1920x1080 that the
  page's scroll width equals the window width and the table font is still
  the default (no fallback shrink).

All of it lives in `range_breakout/report_layout.html` (CSS + script), which
`render_range_breakout_report.py` inserts just before `</body>` of every
report, plus a viewport meta tag. Change the layout there, never by hand in a
report. The 2026 report picks it up on its next regen.
