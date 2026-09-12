"""Retro-fit the tabbed layout + trade stat strip onto already-rendered reports.

The three renderers now (a) put the strategy write-up and the excursion
percentile table in their own "Excursion percentiles" tab, and (b) print the
numbers that describe the TRADES -- count, win rate, wins, losses, total R,
total PnL, max MAE (win), max MFE (loss) -- directly above the trades table
instead of mixed into the header summary, which is about the scan and the
review workflow.

Re-rendering a report costs ~20 minutes of real tick resolution, and none of
this needs the ticks: every number is either already in the header summary or
recoverable from the trades table's own columns. So the existing HTML is
rearranged in place, using the same CSS/markup/JS the renderers emit
(SR.TABS_CSS / SR.TABS_JS / SR.tab_bar_html / SR.trade_stats_bar_html), which
keeps a patched report identical to a freshly rendered one. Re-running is a
no-op on an already-patched file.

    python move_excursion_to_tab.py                 # every report under public/reports
    python move_excursion_to_tab.py FILE [FILE ...]
"""
import os
import re
import sys

import render_stop_target_report as SR

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPORTS = os.path.join(_HERE, "public", "reports")

# The seams a fresh render produces. CSS_ANCHOR is EXCURSION_CSS's own last
# rule (TABS_CSS is appended straight after it in SR.CSS); JS_ANCHOR is the
# start of SR.JS, which every report emits after its trades table.
CSS_ANCHOR = SR.EXCURSION_CSS
JS_ANCHOR = '\n<script src="https://unpkg.com/lightweight-charts'
TABS = [("trades", "Trades"), ("pctile", "Excursion percentiles")]

# The M5 report's recomputeDynStats, before and after the stat strip existed:
# it is the only report whose numbers are recomputed live from the rows that
# the dynamic filters leave showing. The other families' numbers are static.
DYN_JS_PATCHES = [
    ("""  let n = 0, wins = 0, sumR = 0, sumPnl = 0;""",
     """  let n = 0, wins = 0, sumR = 0, sumPnl = 0, maxWinMae = 0, maxLossMfe = 0;
  // MAE/MFE live only in their cells, and applyTargetModes (called above) has
  // already rewritten those for whichever target rule is ticked, so reading
  // the cell is reading the excursion of the bracket actually in force.
  const cellNum = (tr, sel) => {
    const c = tr.querySelector(sel);
    return c ? parseFloat(c.textContent) : NaN;
  };"""),
    ("""    if (!isNaN(rVal)) {
      n += 1;
      sumR += rVal;
      if (outcome === 'target') wins += 1;
    }""",
     """    if (!isNaN(rVal)) {
      n += 1;
      sumR += rVal;
      if (outcome === 'target') {
        wins += 1;
        const mae = cellNum(tr, '.mae-cell');
        if (!isNaN(mae) && mae > maxWinMae) maxWinMae = mae;
      } else if (outcome === 'stop') {
        const mfe = cellNum(tr, '.mfe-cell');
        if (!isNaN(mfe) && mfe > maxLossMfe) maxLossMfe = mfe;
      }
    }"""),
    ("""  setText('sum-win-rate', winRate.toFixed(1) + '%');
  setText('sum-win-rate-n', '(' + n + ')');
  setText('sum-avg-r', avgR.toFixed(2));
  setText('sum-total-r', sumR.toFixed(1));
  setText('sum-total-pnl', (sumPnl >= 0 ? '+' : '') + sumPnl.toFixed(1));""",
     """  setText('sum-trades', String(n));
  setText('sum-win-rate', winRate.toFixed(1) + '%');
  setText('sum-wins', String(wins));
  setText('sum-losses', String(n - wins));
  setText('sum-avg-r', avgR.toFixed(2));
  setText('sum-total-r', sumR.toFixed(1));
  setText('sum-total-pnl', (sumPnl >= 0 ? '+' : '') + sumPnl.toFixed(1));
  setText('sum-max-win-mae', maxWinMae.toFixed(2));
  setText('sum-max-loss-mfe', maxLossMfe.toFixed(2));"""),
]


def _strip(fragment):
    return re.sub(r"<[^>]+>", "", fragment).strip()


def _trade_rows(html):
    """(column index by header text, [row cells]) for the trades table.

    Located by id rather than by position: the excursion table now precedes it
    and has a <thead> of its own, so the file's first <thead> is no longer the
    trades table's."""
    start = html.find('id="lvl-table"')
    head = re.search(r"<thead>(.*?)</thead>", html[start:], re.S)
    ths = [_strip(x) for x in re.findall(r"<th[^>]*>(.*?)</th>", head.group(1), re.S)]
    rows = [[_strip(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", m.group(1), re.S)]
            for m in re.finditer(r'<tr class="lvl-row[^"]*"[^>]*>(.*?)</tr>',
                                 html[start:], re.S)]
    return {name: i for i, name in enumerate(ths)}, rows


def _col_values(cols, rows, name):
    """Numeric values of one column, skipping blanks and unfilled rows."""
    i = cols.get(name)
    out = []
    for cells in rows if i is not None else []:
        if i >= len(cells):
            continue
        try:
            out.append(float(cells[i].replace("+", "")))
        except ValueError:
            continue
    return out


def _boxes(summary):
    """[(whole box html, value, label)] for one <div class="summary">."""
    return [(m.group(0), _strip(m.group(1)), _strip(m.group(2)))
            for m in re.finditer(
                r'<div class="box[^"]*">\s*<strong[^>]*>(.*?)</strong>(.*?)</div>',
                summary, re.S)]


def _take(summary, label):
    """Pull the box with exactly this label out of `summary`."""
    for whole, value, lbl in _boxes(summary):
        if lbl == label:
            return value, summary.replace(whole, "", 1)
    raise ValueError(f"summary has no {label!r} box")


def _stat_boxes(html, summary, title):
    """(stat-strip boxes, summary with those boxes removed).

    Values are moved verbatim wherever the report already computed them; only
    what no report family printed -- total PnL, and wins/losses where there was
    no such box -- is recovered from the trades table's own columns."""
    cols, rows = _trade_rows(html)
    # startswith, not ==: the outcome cell carries the row's badges (R < MIN,
    # SWERVE BLOCKED, MGMT ...R) in the same <td> as its WIN/LOSS label.
    outcomes = [c[cols["Outcome"]] for c in rows if cols["Outcome"] < len(c)]
    wins_n = sum(1 for o in outcomes if o.startswith("WIN"))
    losses_n = sum(1 for o in outcomes if o.startswith("LOSS"))
    ids = {}

    if 'id="sum-win-rate"' in summary:          # M5 report: live, JS-driven numbers
        m = re.search(r'<div class="box true">\s*<strong id="sum-win-rate">(.*?)</strong>'
                      r'\s*win rate\s*<span id="sum-win-rate-n">\((\d+)\)</span>\s*</div>',
                      summary, re.S)
        win_rate, n = m.group(1), m.group(2)
        summary = summary.replace(m.group(0), "", 1)
        total_r, summary = _take(summary, "total R")
        total_pnl, summary = _take(summary, "total PnL (pts)")
        max_mae, summary = _take(summary, "max MAE (win)")
        max_mfe, summary = _take(summary, "max MFE (loss)")
        rate_label = "win rate"
        ids = {"trades taken": "sum-trades", "win rate": "sum-win-rate", "wins": "sum-wins",
               "losses": "sum-losses", "total R": "sum-total-r",
               "total PnL (pts)": "sum-total-pnl", "max MAE (win)": "sum-max-win-mae",
               "max MFE (loss)": "sum-max-loss-mfe"}
    elif "fine-tuned win rate" in summary:      # fine-tuned entry/exit report
        win_rate, rate_box = next((v, l) for _, v, l in _boxes(summary)
                                  if l.startswith("fine-tuned win rate"))
        n = re.search(r"\((\d+)\)", rate_box).group(1)
        _, summary = _take(summary, rate_box)
        total_r, summary = _take(summary, "fine-tuned total R")
        max_mae, summary = _take(summary, "max MAE (win)")
        max_mfe, summary = _take(summary, "max MFE (loss)")
        total_pnl = "%+.1f" % sum(_col_values(cols, rows, "PnL"))
        rate_label = "fine-tuned win rate"
    else:                                       # fixed stop/target report
        n, summary = _take(summary, "trades")
        wins, summary = _take(summary, "wins")
        losses, summary = _take(summary, "losses")
        wins_n, losses_n = int(wins), int(losses)
        win_rate, summary = _take(summary, "win rate")
        total_r, summary = _take(summary, "total R")
        max_mae, summary = _take(summary, "max MAE (win)")
        max_mfe, summary = _take(summary, "max MFE (loss)")
        # Fixed bracket: a trade's points are its R times the stop distance.
        stop = float(re.search(r"Stop ([0-9.]+)", title).group(1))
        total_pnl = "%+.1f" % (float(total_r) * stop)
        rate_label = "win rate"

    boxes = [(n, "trades taken", False), (win_rate, rate_label, True),
             (str(wins_n), "wins", False), (str(losses_n), "losses", False),
             (total_r, "total R", False), (total_pnl, "total PnL (pts)", False),
             (max_mae, "max MAE (win)", False), (max_mfe, "max MFE (loss)", False)]
    return [(v, l, ids.get(l), hi) for v, l, hi in boxes], summary


def patch(path):
    with open(path, encoding="utf-8") as f:
        html = f.read()
    if 'class="tab-bar"' in html:
        return "already patched"
    if "pctile-wrap" not in html:
        return "no excursion table -- run add_excursion_percentiles.py first"
    if CSS_ANCHOR not in html:
        return "excursion CSS not recognised"

    html = html.replace(CSS_ANCHOR, CSS_ANCHOR + "\n" + SR.TABS_CSS, 1)

    # The two blocks that move: the strategy write-up and the percentile table.
    lead = re.search(r'<p class="lead">.*?</p>\n', html, re.S)
    pctile = re.search(r'<div class="pctile-wrap">.*?\n</div>\n', html, re.S)
    if not (lead and pctile):
        return "no lead paragraph / percentile table found"
    panel = lead.group(0) + pctile.group(0)
    html = html.replace(pctile.group(0), "", 1).replace(lead.group(0), "", 1)

    summary = re.search(r'<div class="summary">.*?\n</div>\n', html, re.S).group(0)
    title = re.search(r"<h1>(.*?)</h1>", html, re.S).group(1)
    boxes, trimmed = _stat_boxes(html, summary, title)
    html = html.replace(summary, trimmed, 1)

    # Stat strip immediately above the table it describes.
    table = '<div class="table-wrap">'
    html = html.replace(table, SR.trade_stats_bar_html(boxes) + "\n" + table, 1)

    # Tab bar + the two panels. The trades panel opens after the <h1> and
    # closes at the seam where the report's own JS begins.
    h1_end = html.index("</h1>") + len("</h1>")
    html = (html[:h1_end] + "\n" + SR.tab_bar_html(TABS)
            + '\n<div class="tab-panel" id="tab-trades">' + html[h1_end:])
    html = html.replace(JS_ANCHOR,
                        '\n</div>\n<div class="tab-panel tab-hidden" id="tab-pctile">\n'
                        + panel + "</div>\n" + JS_ANCHOR, 1)
    html = html.replace("\n</body></html>", "\n" + SR.TABS_JS + "\n</body></html>", 1)

    for old, new in DYN_JS_PATCHES:
        if old in html:
            html = html.replace(old, new, 1)

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return "patched"


def main(argv):
    targets = argv[1:] or [os.path.join(root, name)
                           for root, _, names in os.walk(_REPORTS)
                           for name in sorted(names) if name.endswith(".html")]
    for path in targets:
        with open(path, encoding="utf-8") as f:
            head = f.read(3_000_000)
        if "pctile-wrap" not in head and 'class="tab-bar"' not in head:
            continue
        print(f"{os.path.basename(path)}: {patch(path)}")


if __name__ == "__main__":
    main(sys.argv)
