# Cloud sessions (Claude Code on the web)

Read this first when running in a cloud container (paths under `/home/user/`). Everything else
about the code lives in `lxpb-v2/CLAUDE.md`: read it before working in `lxpb-v2/`. Its rules apply
here too, except where this section says otherwise.

## Setup is automatic

`.claude/hooks/session-start.sh` (registered in `.claude/settings.json`) runs at the start of every
cloud session and needs nothing from the user except ONE environment variable, set once in the cloud
environment's settings (Environment variables):

    LXPB_SCID_DRIVE_IDS=EPU26=<google drive file id>,EPZ26=<google drive file id>

The IDs are for the user's own Drive copies of the `.scid` files (shared "anyone with the link"). This
repo is public, so the IDs never go in it. The hook (idempotent, about 2 minutes on a fresh container,
seconds after that):

1. installs the Python packages that are missing (pyarrow, tzdata, scipy, scikit-learn, matplotlib,
   gdown; pandas 3.0.5 / numpy 2.5.3 come with the image and are what the cloud reports were checked with);
2. makes `scidReader` importable from any script, through a `.pth` file in site-packages;
3. downloads each listed `F.US.<SYM>.scid` into `/home/user/scid` unless a whole copy from the same
   Drive ID is already there (`F.US.<SYM>.scid.driveid` records the ID). A new Drive ID re-downloads.

Its printout opens the session: the reader in use and each `.scid` file with its first and last tick.
If it says `LXPB_SCID_DRIVE_IDS is not set` or a download failed, tell the user exactly that, and do
not stand in for the data.

## What the container has

- **This repo** at `/home/user/lxpb`.
- **`scidReader`**: the user's private `beanthehacker/acheron` repo has the original
  (`AcheronUtils/scidReader.py`), used when the session was started with that repo selected
  (`/home/user/acheron`). Without it, `lxpb-v2/scid_only/scidReader.py` is used: written from Sierra
  Chart's published `.scid` layout and checked to give identical frames on whole files. Never copy
  acheron's code into this public repo. `render_labels_report.py` (and the `data/` builders) only
  put the Windows path `D:\acheron\AcheronUtils` on `sys.path`; in the cloud the hook's `.pth` makes
  the import work anyway, and `scid_only/run_scid_only.py` also puts the reader folder first
  (`LXPB_SCID_READER` overrides it).
- **Ticks**: Sierra Chart `.scid` files in `/home/user/scid` (`LXPB_SCID_DIR` overrides):

  | file | front month | offset onto the continuous scale |
  |---|---|---|
  | `F.US.EPU26.scid` | 2026-06-14 22:00 UTC to the roll, 2026-09-14 22:00 UTC | +67.75 |
  | `F.US.EPZ26.scid` | from 2026-09-14 22:00 UTC; the upload of 2026-10-06 ends ~17:30 UTC that day | 0.00 |

  Newer ticks arrive only when the user uploads a newer `.scid` (as a new Drive file with its ID in
  `LXPB_SCID_DRIVE_IDS`, so the hook downloads it again).
- **No TradingView exports, and none are needed here.** Run every report through
  `lxpb-v2/scid_only/run_scid_only.py`, which builds the H1 and M5 series from the ticks at the
  offsets stored in `lxpb-v2/data/scid_offsets.json`. Consequences (see its docstring):
  history starts 2026-06-14, so older levels and boxes do not exist; the offsets are reused, not
  re-measured; level ledgers are cached in a scratch dir (`LXPB_SCID_CACHE`, default
  `/tmp/lxpb_scid_only_cache`), never in `data/levels_cache/`. Do not ask the user for exports in a
  cloud session. The exception is the next roll (EPZ26 -> EPH27, Mon 2026-12-14 15:00 PT): past it
  there is neither a `.scid` nor a stored offset for EPH27, so ask the user.
- **Tools**: everything the reports and research scripts use is installed by the hook. If a script
  needs another package, `pip install` it for the session and add it to the hook's list. Chromium for Playwright is at
  `/opt/pw-browsers` and the Node Playwright package at `/opt/node-tools/node_modules/playwright`,
  for checking report pages (the iPad layout rules in `lxpb-v2/CLAUDE.md`).
- **The worktree rule** in `lxpb-v2/CLAUDE.md` and its `E:\` paths are for the user's Windows
  checkout. A cloud container is already isolated per session: work on the session's own branch.

## Running reports

```bash
cd /home/user/lxpb/lxpb-v2
python3 scid_only/run_scid_only.py ss_m1_confl/render_m1_confl_report.py     --since-day 2026-09-01 --until-day 2026-09-30 --warmup-days 5 --bar-minutes 1     --label "Sep 2026, whole month" --output /path/in/the/scratchpad/sep.html
```

- The M1 fade report is ENGINE ONLY since 2026-10-16 (`lxpb-v2/CLAUDE.md`, "The M1 fade report is ENGINE ONLY"): it replays the
  ticks through `live_range` and lists the engine's fade trades. The flags of the old pipeline (`--workers`, `--playbook`,
  `--range-lookback-days`, `--range-procs` ...) are accepted and ignored; there is no worker pool any more.
- Output to the scratchpad unless the user asked for a regen (the repo's regen rule still holds).
- **Memory: 4 CPUs, 16 GB, no swap.** Do not run two report / replay processes at once. A month of the engine replay takes minutes,
  not the 35-45 minutes of the old range-box reads.
- The engine also seeds `--h1-days` (25) of H1 / M5 history before the window; with `--since-day` earlier than about 2026-07-10 that
  reaches before the first tick file (2026-06-14) and the run stops with `AssertionError: file not found`: lower `--h1-days`.

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
