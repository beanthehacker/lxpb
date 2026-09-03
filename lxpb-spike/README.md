# lxpb-spike — hammer/shooting-star + strong-breakout LXPB strategy

A thin strategy layer over [`../lxpb-v2`](../lxpb-v2) (level detection,
gap filtering, tick-accurate exit resolution) plus the canonical
candlestick-pattern detectors in `D:\daily-analysis\patterns-pure`.

```
python analyze_hammer_star_strategy.py            # funnel + trade table on stdout
python render_hammer_star_report.py               # -> stop3_target9_trades_report.html
```

## The rules

| Leg | Rule |
| --- | --- |
| **Level** | A completed LXPB retest (`lxpb.detect_lxpb_h1` on H1). LHPB → long, LLPB → short. |
| **Formation filter** | **LHPB is valid only if its formation bar is a HAMMER; LLPB is valid only if its formation bar is a SHOOTING STAR** — all other formations are dropped. Judged by `D:\daily-analysis\patterns-pure`'s `find_hammer` / `find_shooting_star` (not lxpb.py's own simplified inline patterns, and note this is the *opposite* polarity from `render_labels_report.is_spike_pp`, which maps LHPB→shooting-star/LLPB→hammer — this strategy instead matches lxpb.py's own native `is_spike` polarity, just computed with the more rigorous patterns-pure logic). |
| **Breakout-strength filter** | The level's original breakout bar must be a strong impulse: `breakout_range_ratio` (breakout bar's high-low range ÷ its own trailing 20-bar average range) ≥ `WIDE_BREAKOUT_RATIO_THRESHOLD` (2.0) — the same definition used in `../retest-vol-scalp/lxpb_fade_report.html`'s "Breakout strength" section and `../lxpb-v2`'s own "strong breakout" convention. |
| **Entry** | The level's own retest price (`entry_price`), anchored to the real 1s aggressor-side fill instant within the retest hour. |
| **Stop / Target** | Fixed 3pt stop / 9pt target (3R), resolved with the same tick-accurate `_pin_exact_exit` machinery (look-ahead free — see `../AGENTS.md`). |

## Results — Jul–Aug 2026 retests (ES, H1, `../lxpb-v2/data/24aug-CME_MINI_ES1!, 60.csv`)

```
completed retests in window ......... 291   (+123 gap rows dropped repo-wide)
valid formation (hammer/star) ........ 29
strong breakout (>= 2.0x 20-bar avg) .. 3
```

Only 3 trades qualify on this sample (both filters are individually
restrictive; combined they leave very few). All 3 were losses (0W/3L,
avg −1.00R) — too small a sample to draw a real conclusion; widen the date
range or relax a filter to get a larger sample before judging the edge.

## Files

| File | What |
| --- | --- |
| `analyze_hammer_star_strategy.py` | Strategy core: load → formation-pattern flag → breakout-strength flag → select → resolve → summarize. CLI prints the funnel and per-trade table. |
| `render_hammer_star_report.py` | HTML report, styled identically to `../lxpb-v2/stop2_target2_trades_report.html` (expandable rows, H1 + 1s trio + 1min + footprint chart stack). |
| `data/1min_hammer_star_window.csv` | Auto-generated 1-minute bar cache (rebuilt on demand; safe to delete). |
| `stop3_target9_trades_report.html` | The report. |

## CLI flags

| Flag | Default | Meaning |
| --- | --- | --- |
| `--data` | `../lxpb-v2/data/24aug-CME_MINI_ES1!, 60.csv` | H1 OHLC CSV |
| `--symbol` | `ES1!` | instrument symbol |
| `--start` / `--end` | `2026-07-01` / `2026-08-31` | filters on **retest** date |
| `--stop` / `--target` | `3` / `9` | bracket size in points |
| `--include-gaps` | off | keep levels whose formation/breakout/retest only happened via a price gap |

## Gotchas worth knowing

* **Formation-pattern polarity is deliberate and non-standard for this repo** — see the table above. Don't "fix" it to match `is_spike_pp`.
* **`sys.path` ordering**: `D:\daily-analysis` contains an `lxpb` *package* that shadows `D:\lxpb\lxpb.py`; only `render_labels_report.py` (imported here) appends it, never inserts it first.
* **1-minute cache keys are index-based**, unique only within one fixed trade list — this strategy uses its own `data/1min_hammer_star_window.csv`, never `../lxpb-v2`'s cache.
* **Exit resolution is look-ahead free**: `_pin_exact_exit` is always called with `not_before=touch_time`.
