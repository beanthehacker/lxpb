"""Retro-fit the excursion-percentile tables onto already-rendered
stop/target trade reports.

render_stop_target_report now emits an "Excursion percentiles" block above the
trades table, but the existing reports each cost ~20 minutes of real tick
resolution to produce. The percentiles are a pure function of two columns that
are already IN the rendered HTML ("MAE (win)" and "MFE (loss)"), so they can be
recomputed by parsing the report rather than by re-resolving 500 trades.

Both the markup and the CSS come from render_stop_target_report itself
(excursion_percentile_html / EXCURSION_CSS), so a patched report is identical
to a freshly rendered one. Re-running is a no-op on an already-patched file.

    python add_excursion_percentiles.py                 # every report here
    python add_excursion_percentiles.py FILE [FILE ...]
"""
import os
import re
import sys

import render_stop_target_report as S

_HERE = os.path.dirname(os.path.abspath(__file__))

# Column positions are read from each report's own <thead> rather than
# hardcoded: older reports predate the "Exit px" column, so fixed indices
# would read MAE/MFE off by one and silently summarise the wrong numbers.
COL_HEADERS = {"outcome": "Outcome", "mae": "MAE (win)", "mfe": "MFE (loss)"}

# Anchors chosen to be the exact seams a fresh render produces: the CSS goes
# straight after the last rule preceding EXCURSION_CSS, and the table straight
# after the summary <div> that the header template closes before it. The
# "Clear all" button is what makes the second anchor unique -- the bare
# "</div>\n</div>\n" it ends with also closes the filter panel further down.
CSS_ANCHOR = "                       font-size:0.9em; padding:3px 5px; }\n"
SUMMARY_END = "Clear all</button>\n  </div>\n</div>\n"


def _cells(row_html):
    return [re.sub(r"<[^>]+>", "", c).strip()
            for c in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)]


def _columns(html):
    """Map of role -> <td> index, located by header text."""
    head = re.search(r"<thead>(.*?)</thead>", html, re.S)
    if not head:
        return None
    ths = [re.sub(r"<[^>]+>", "", x).strip()
           for x in re.findall(r"<th[^>]*>(.*?)</th>", head.group(1), re.S)]
    try:
        return {role: ths.index(text) for role, text in COL_HEADERS.items()}
    except ValueError:
        return None


def _num(cell):
    return None if cell in ("-", "") else float(cell)


def patch(path):
    with open(path, encoding="utf-8") as f:
        html = f.read()
    name = os.path.basename(path)

    if 'class="pctile-wrap"' in html:
        print(f"  {name}: already has the table, skipped")
        return False

    m = re.search(r"<h1>.*?Stop ([0-9.]+) / Target ([0-9.]+)", html, re.S)
    if not m:
        print(f"  {name}: no 'Stop X / Target Y' heading, skipped")
        return False
    stop = float(m.group(1))

    col = _columns(html)
    if col is None:
        print(f"  {name}: no Outcome/MAE/MFE columns in the header, skipped")
        return False

    buckets = {("stop", "mfe"): [], ("target", "mae"): [],
               ("candle", "mfe"): [], ("candle", "mae"): []}
    label = {"WIN": "target", "LOSS": "stop", "CANDLE": "candle"}
    for row in re.findall(r'<tr class="lvl-row.*?</tr>', html, re.S):
        c = _cells(row)
        outcome = label.get(c[col["outcome"]])
        if outcome is None:
            continue
        for role in ("mae", "mfe"):
            key = (outcome, role)
            if key in buckets:
                v = _num(c[col[role]])
                if v is not None:
                    buckets[key].append(v)

    block = S.excursion_percentile_html([
        ("MFE &mdash; losing trades", "ran this far in favour before hitting stop",
         buckets[("stop", "mfe")]),
        ("MAE &mdash; winning trades", "heat taken before reaching target",
         buckets[("target", "mae")]),
        ("MFE &mdash; candle exits", "ran this far in favour before the candle rule fired",
         buckets[("candle", "mfe")]),
        ("MAE &mdash; candle exits", "heat taken before the candle rule fired",
         buckets[("candle", "mae")]),
    ], stop)
    if not block:
        print(f"  {name}: no MAE/MFE values to summarise, skipped")
        return False

    for anchor, insert in ((CSS_ANCHOR, S.EXCURSION_CSS + "\n"), (SUMMARY_END, block)):
        if html.count(anchor) != 1:
            raise SystemExit(
                f"{name}: expected exactly one insertion point, found "
                f"{html.count(anchor)} for {anchor!r} -- the report template "
                f"changed, so this backfill would put the block in the wrong place")
        html = html.replace(anchor, anchor + insert)

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    counts = ", ".join(f"{k[0]}/{k[1].upper()}={len(v)}"
                       for k, v in buckets.items() if v)
    print(f"  {name}: patched (stop={S._fmt_pts(stop)}; {counts})")
    return True


if __name__ == "__main__":
    paths = sys.argv[1:] or sorted(
        os.path.join(_HERE, f) for f in os.listdir(_HERE)
        if re.fullmatch(r"stop.*_trades_report.*\.html", f))
    print(f"{len(paths)} report(s):")
    print(f"\npatched {sum(patch(p) for p in paths)} of {len(paths)}")
