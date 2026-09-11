# Resume notes: M5 target rules restricted to the level's own P1..P2 window

Captured 2026-09-11 from an in-progress, uncommitted working tree (found
mid-edit while doing unrelated Vercel-deployment work in this repo) and
pushed here as-is so it isn't lost. Not reviewed or completed by that other
work -- just preserved with a description of what's here and what's still
broken.

## What this branch contains

`m5_structure.consolidation_target` and
`render_m5_confl2_report._opposite_m5_target`/`_consolidation_target` are
being changed so a trade's target-source structure (a consolidation area, or
an opposite-type M5 P0) must lie **entirely inside that level's own P1..P2
window** -- strictly after its breakout candle, strictly before its retest
bar -- instead of just "previous" (any time before the fill/cutoff).
Rationale is in the updated docstrings/comments in both files: structure
older than P1 belongs to a move the level had no part in; structure after P2
isn't there yet to aim at.

Alongside that, `_pick_target` (singular) is being replaced by
**`_pick_targets`** (plural) + **`_active_mode`**: both target rules
(`consolidation`, `opposite-m5`) are now always computed, keyed in a dict,
so the report's in-browser mode toggle can switch between them live without
a Python regen (`_active_mode` picks whichever enabled rule's target is
farthest from the fill).

## Known bug -- fixed

This note originally flagged `_resolve_raw_retest` as still calling a
dangling singular `_pick_target(...)`, which would have raised `NameError:
name '_pick_target' is not defined`. That was true of the uncommitted tree
this file was captured from, but not of what actually landed in this
commit: `_resolve_raw_retest` already calls `_pick_targets(...)` then
`_active_mode(targets, price, enabled=args.default_target_modes)`, same as
`process_cluster`'s own trade path. A handful of docstrings/comments still
named the old singular `_pick_target` in prose (no behavior effect); those
are fixed too. Verified with a 20-row smoke test (`--max-rows 20`) that
exercises `_resolve_raw_retest` via the `p1_reacted` reaction-cutoff scan
without hitting the old name.

## Regenerated report file

`public/reports/ss_m5_confl2/ss_m5_confl2_report.html` in this commit is
much smaller than the last full regen (~3600 fewer lines) -- almost
certainly a `--max-rows` smoke-test regen, not a full one (see repo memory:
always smoke-test before a full regen). **Do not treat this HTML as
current/authoritative** -- a full regen is still pending (tracked
separately, alongside further target-rule and report-layout work already
sitting on top of this one in the same working tree).

## Other unreconciled work sitting elsewhere (as of this branch's creation)

Found while trying to get `main` to a clean state; none of this has been
merged into `main` (`842304e` at the time this branch was cut) or into each
other:

- **Branch `nimod/tmp1`** (local + `origin/nimod/tmp1`) has one commit,
  `de0b380` ("Regen ss_m5_confl2 report with p1_reacted now always-on
  dynamic filter"), that is **not** an ancestor of `main` -- a divergent
  line that predates the P1..P2 window work here.
- **`git stash list` -> `stash@{0}`** ("wip on nimod/tmp1 before switching
  to main"), taken 2026-09-10, holds a *different* set of uncommitted
  changes: `lxpb_levels_cache.py` (new `FATE_GATED_DROPPED` fate, `ALGO_VERSION`
  1->2, `apply_finalized_swing`), `lxpb.py`, `render_stop_target_report.py`,
  `ss_m5_confl2/render_m5_confl2_report.py` (an earlier revision than this
  branch's), a rebuilt `m5_levels_EPU26` cache, and two new raw tick CSVs
  under `../data/`. This predates and is superseded by (but not identical
  to) both `de0b380` and this branch -- needs manual reconciliation, not a
  blind pop.

None of the three (this branch, `de0b380`, the stash) are confirmed
duplicates or supersets of each other -- read each one's diff before
deciding what to keep.
