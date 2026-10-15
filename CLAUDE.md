# Cloud sessions (Claude Code on the web)

Read this first when running in a cloud container (paths under `/home/user/`). Everything else
about the code lives in `lxpb-v2/CLAUDE.md`: read it before working in `lxpb-v2/`. Its rules apply
here too, except where this section says otherwise.

## What the container has

- **This repo** at `/home/user/lxpb`.
- **`scidReader`** comes from the user's other repo, `beanthehacker/acheron`, checked out at
  `/home/user/acheron` when the session was started with it selected. The reader is
  `/home/user/acheron/AcheronUtils/scidReader.py`. `render_labels_report.py` (and the `data/`
  builders) put the Windows path `D:\acheron\AcheronUtils` on `sys.path`, which does not exist
  here: `scid_only/run_scid_only.py` adds `/home/user/acheron/AcheronUtils` first
  (`LXPB_SCID_READER` overrides it). Any other script that imports `render_labels_report`, or a
  module that imports it (`m5_structure`, the report modules), must do
  `sys.path.insert(0, "/home/user/acheron/AcheronUtils")` itself before that import.
  If `/home/user/acheron` is missing, attach the repo (`add_repo`, if the session has it) or ask the
  user to start the session with `acheron` selected. Never write a replacement reader.
- **Ticks**: Sierra Chart `.scid` files in `/home/user/scid` (`LXPB_SCID_DIR` overrides):

  | file | front month | offset onto the continuous scale |
  |---|---|---|
  | `F.US.EPU26.scid` | 2026-06-14 22:00 UTC to the roll, 2026-09-14 22:00 UTC | +67.75 |
  | `F.US.EPZ26.scid` | from 2026-09-14 22:00 UTC; the current upload ends ~2026-10-06 17:30 UTC | 0.00 |

  The environment's setup script (the user's cloud environment settings) downloads them from the
  user's Google Drive; the file IDs live there, not in this repo. If the files are missing or
  truncated, the setup script did not run or failed: tell the user, and do not stand in for the
  data. Newer ticks arrive only when the user uploads a newer `.scid`.
- **No TradingView exports, and none are needed here.** Run every report through
  `lxpb-v2/scid_only/run_scid_only.py`, which builds the H1 and M5 series from the ticks at the
  offsets stored in `lxpb-v2/data/scid_offsets.json`. Consequences (see its docstring):
  history starts 2026-06-14, so older levels and boxes do not exist; the offsets are reused, not
  re-measured; level ledgers are cached in a scratch dir (`LXPB_SCID_CACHE`, default
  `/tmp/lxpb_scid_only_cache`), never in `data/levels_cache/`. Do not ask the user for exports in a
  cloud session. The exception is the next roll (EPZ26 -> EPH27, Mon 2026-12-14 15:00 PT): past it
  there is neither a `.scid` nor a stored offset for EPH27, so ask the user.
- **Tools**: the reports need Python 3 with pandas, numpy and pyarrow; research scripts have also
  used scipy, scikit-learn and matplotlib. The setup script installs them. If a module is missing, `pip install` it
  for the session and tell the user to add it to the setup script. Chromium for Playwright is at
  `/opt/pw-browsers` and the Node Playwright package at `/opt/node-tools/node_modules/playwright`,
  for checking report pages (the iPad layout rules in `lxpb-v2/CLAUDE.md`).
- **The worktree rule** in `lxpb-v2/CLAUDE.md` and its `E:\` paths are for the user's Windows
  checkout. A cloud container is already isolated per session: work on the session's own branch.

## Running reports

```bash
cd /home/user/lxpb/lxpb-v2
python3 scid_only/run_scid_only.py ss_m1_confl/render_m1_confl_report.py \
    --since-day 2026-09-01 --until-day 2026-09-30 --warmup-days 5 --bar-minutes 1 \
    --playbook fade --range-lookback-days 60 --label "Sep 2026, whole month" \
    --workers 1 --output /path/in/the/scratchpad/sep.html
```

- Serial only (`--workers 1`): spawned workers would skip the runner's patches.
  `--range-procs N` (forked, default 4) is fine.
- Output to the scratchpad unless the user asked for a regen (the repo's regen rule still holds).
- **Memory: 4 CPUs, 16 GB, no swap.** One month of `render_m1_confl_report` grows to ~8 GB, so
  never run two report processes at once; the second gets killed (exit 137). A month of the fade
  report takes ~35-45 minutes, most of it reading the range boxes before each touch.
- With `--since-day` earlier than about 2026-08-15, the default 60-day box lookback reaches before
  the first tick file and the run stops with `AssertionError: file not found` (scidReader looking
  for EPM26). Shorten `--range-lookback-days` so the lookback starts on or after 2026-06-14.

## Pitfalls already paid for

- **Ledger times are bar starts.** `breakout_time`, `retest_time` and `death_time` label the bar
  the event happened in. "Was this level live at time T" must require its breakout bar to have
  CLOSED (`breakout_time + bar length <= T`) and must settle an end stamped on the bar that holds
  T from the M1 bars before T (dead only if price had already reached the level). Reading
  `retest_time > T` alone dropped levels that price reached later in the same H1 bar and produced
  a fake 67% vs 50% "H1 level ahead" effect in the Oct 2026 range-fade study.
- **Never `np.searchsorted` the memory-mapped `.scid` `Time` field**: it copies the whole strided
  column (~500 MB) on every call. Use `render_labels_report._scid_first_at_or_after` (binary
  search on the memmap) or `_scid_window`.
- `render_m1_confl_report._boxes_for_touches` forks a `multiprocessing` pool that pickles
  `_boxes_of_one_touch` by module name. Code that execs the report into a namespace must register
  that namespace in `sys.modules`, or the pool fails and the read silently falls back to serial.
