"""Per-trade facts file for the /explorer page, read out of a finished report.

The ss_m5_confl2 report already ships every reading its live filters use as
data-* attributes on each trade row (tags, gaps, the per-n / per-k / per-window
arrays, every target rule's outcome, the quick-exit path). This pulls exactly
those values back out -- no strategy logic is restated here -- and writes them
as one compact JSON document, so the explorer filters the same numbers the
report's own panel does without loading the 300 MB of embedded charts.

Output: <report stem>.trades.json.gz next to the report, e.g.
public/reports/ss_m5_confl2/2025.html(.gz) -> 2025.trades.json.gz

Usage:
    python ss_m5_confl2/trade_facts.py public/reports/ss_m5_confl2/2025.html.gz [...]
"""
import gzip
import html
import json
import os
import re
import sys

SCHEMA_VERSION = 1

_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
_ROW_RE = re.compile(r'<tr class="lvl-row([^"]*)"(.*?)>(.*?)</tr>', re.S)
_TD_RE = re.compile(r'<td([^>]*)>(.*?)</td>', re.S)
_TAG_RE = re.compile(r'<[^>]+>')
_NUM_RE = re.compile(r'-?\d+(?:\.\d+)?')


def _read(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return f.read()


def _attrs(s):
    return {k: html.unescape(v) for k, v in _ATTR_RE.findall(s)}


def _text(cell_html):
    return html.unescape(_TAG_RE.sub(" ", cell_html)).split()


def _num(v):
    """'' / missing -> None, else float (ints stay ints)."""
    if v is None or v == "":
        return None
    f = float(v)
    return int(f) if f.is_integer() and "." not in v else f


def _round(x, nd=6):
    if isinstance(x, float):
        return round(x, nd)
    if isinstance(x, list):
        return [_round(v, nd) for v in x]
    if isinstance(x, dict):
        return {k: _round(v, nd) for k, v in x.items()}
    return x


def _json_attr(a, name):
    v = a.get(name)
    return json.loads(v) if v else None


def _cells(body):
    """{css class of the cell (first class) or 'td<n>': (attrs, inner html)}."""
    out = {}
    for n, (attr_s, inner) in enumerate(_TD_RE.findall(body)):
        a = _attrs(attr_s)
        classes = [c for c in a.get("class", "").split() if c not in ("left", "time-stacked")]
        key = next((c for c in classes if c.endswith("-cell")), None) or (
            classes[0] if classes else f"td{n}")
        out[key] = (a, inner)
    return out


def _pt_time(cell):
    """'2026-07-01<br><span>03:40:00 PT</span>' -> '2026-07-01 03:40:00'."""
    words = _text(cell[1]) if cell else []
    return " ".join(w for w in words if w != "PT") if len(words) >= 2 else None


def _first_num(cell):
    if not cell:
        return None
    m = _NUM_RE.search(" ".join(_text(cell[1])))
    return float(m.group(0)) if m else None


def _mode(p):
    """One target rule's payload, keeping only the numbers (the report's own
    HTML snippets for each cell are left behind)."""
    def f(k):
        return _num(p.get(k))
    return {
        "dist": p.get("dist"),
        "target": _first_num((None, p.get("tgt", ""))),
        "rr": f("rrVal"),
        "r": f("r"),
        "pnl": f("pnlPts"),
        "outcome": p.get("outcome") or None,
        "label": " ".join(_text(p.get("outcomeLabel", ""))) or None,
        "mgmtR": f("mgmtR"),
        "mgmtPnl": f("mgmtPnl"),
        "mgmtOutcome": p.get("mgmtOutcome") or None,
        "mgmtFired": p.get("mgmtFired") == "1",
        "tags": (p.get("tags") or "").split(),
        "exitSec": f("exitSec"),
        "mgmtExitSec": f("mgmtExitSec"),
        "mae": _num(p["mae"]) if p.get("mae") not in (None, "-") else None,
        "mfe": _num(p["mfe"]) if p.get("mfe") not in (None, "-") else None,
    }


def _trade(row_cls, attr_s, body):
    a = _attrs(attr_s)
    c = _cells(body)
    filled = "unfilled-row" not in row_cls
    modes = {m: _mode(p) for m, p in (_json_attr(a, "data-modes") or {}).items()}
    bias_words = _text(c["bias-cell"][1]) if "bias-cell" in c else []
    members = (" ".join(_text(c["merged-h1-levels"][1])) if "merged-h1-levels" in c else "")
    return {
        "idx": int(a["data-idx"]),
        "i": int(_first_num(c.get("td0")) or 0),
        "key": a.get("data-key"),
        "type": " ".join(_text(c["type-cell"][1])) if "type-cell" in c else None,
        "filled": filled,
        "reason": None if filled else " ".join(_text(c["outcome-cell"][1])),
        "retest": _pt_time(c.get("retest-cell")),
        "entryTime": _pt_time(c.get("entry-touch-cell")),
        "entry": _first_num(c.get("entry-cell")),
        "stop": _first_num(c.get("stop-cell")),
        "risk": _num(a.get("data-risk")),
        "comm": _num(a.get("data-comm")),
        "contracts": _num(a.get("data-contracts")),
        "members": len([m for m in members.split(",") if m.strip()]),
        "h1confl": _first_num(c.get("h1-confl-cell")),
        "bias": " ".join(bias_words) if bias_words and bias_words != ["-"] else None,
        "biasTitle": c["bias-cell"][0].get("title") or None if "bias-cell" in c else None,
        "tags": (a.get("data-base-tags") or "").split(),
        "daygap": _num(a.get("data-daygap")),
        "h1gap": _num(a.get("data-h1gap")),
        "mingap": _num(a.get("data-mingap")),
        "vspikeoffs": _num(a.get("data-vspikeoffs")),
        "erByK": _json_attr(a, "data-er-by-k"),
        "p1RatioByWindow": _json_attr(a, "data-p1-ratio-by-window"),
        "boxByN": _json_attr(a, "data-box-by-n"),
        "apprByK": _json_attr(a, "data-appr-by-k"),
        "fresh": _json_attr(a, "data-fresh"),
        "sfp": _json_attr(a, "data-sfp"),
        "qx": _json_attr(a, "data-qx"),
        "modes": modes,
    }


def _panel_defaults(page):
    """The report's own filter-panel starting values, so the explorer's
    'report defaults' preset starts where the report does."""
    p0 = page.find('<div class="filter-panel">')
    panel = page[p0:page.find('<table id="lvl-table"', p0)] if p0 >= 0 else ""
    d = {"num": {}, "inputs": {}, "selects": {}, "checked": {}}
    for m in re.finditer(r'<select class="f-num-op" data-target="(\w+)">(.*?)</select>', panel, re.S):
        sel = re.search(r'<option value="(\w+)" selected', m.group(2))
        d["num"].setdefault(m.group(1), {})["op"] = sel.group(1) if sel else "any"
    for m in re.finditer(r'<input type="number" class="f-num-val" data-target="(\w+)" value="([^"]*)"', panel):
        d["num"].setdefault(m.group(1), {})["value"] = _num(m.group(2))
    for m in re.finditer(r'<input type="number"[^>]*id="([\w-]+)"[^>]*value="([^"]*)"', panel):
        d["inputs"][m.group(1)] = _num(m.group(2))
    for m in re.finditer(r'<select[^>]*id="([\w-]+)"[^>]*>(.*?)</select>', panel, re.S):
        opts = re.findall(r'<option value="([^"]*)"', m.group(2))
        sel = re.search(r'<option value="([^"]*)" selected', m.group(2))
        d["selects"][m.group(1)] = {"options": opts, "value": sel.group(1) if sel else (opts[0] if opts else None)}
    for m in re.finditer(r'<input type="checkbox" class="([^"]*)"([^>]*)>', panel):
        a = _attrs(m.group(2))
        key = a.get("data-tag") or a.get("data-mode") or a.get("value") or a.get("id")
        d["checked"].setdefault(m.group(1).split()[-1], {})[key] = bool(
            re.search(r"\schecked\b", m.group(2)))
    return d


def extract(path):
    page = _read(path)
    t0 = page.find('<table id="lvl-table"')
    t1 = page.find("</table>", t0)
    if t0 < 0:
        raise ValueError(f"{path}: no trade table")
    trades = [_trade(cls, attrs, body) for cls, attrs, body in _ROW_RE.findall(page[t0:t1])]
    key = re.search(r"const REVIEW_STORAGE_KEY = '([^']*)'", page)
    title = re.search(r"<title>(.*?)</title>", page, re.S)
    rng = re.search(r"in\s+\[(\d{4}-\d{2}-\d{2}),\s*(\d{4}-\d{2}-\d{2})\]", page)
    return {
        "schema": SCHEMA_VERSION,
        "source": os.path.basename(path).replace(".gz", ""),
        "title": html.unescape(title.group(1)).strip() if title else None,
        "start": rng.group(1) if rng else None,
        "end": rng.group(2) if rng else None,
        "reviewKey": key.group(1) if key else None,
        "defaults": _panel_defaults(page),
        "trades": _round(trades),
    }


def facts_path(report_path):
    """public/.../2025.html(.gz) -> public/.../2025.trades.json.gz"""
    stem = re.sub(r"\.html(\.gz)?$", "", report_path)
    return stem + ".trades.json.gz"


def write(report_path, out_path=None):
    facts = extract(report_path)
    out_path = out_path or facts_path(report_path)
    body = json.dumps(facts, separators=(",", ":")).encode("utf-8")
    tmp = out_path + ".part"
    # mtime=0: identical facts give byte-identical files, so an unchanged
    # report never shows up as a changed .gz in git.
    with open(tmp, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as f:
        f.write(body)
    os.replace(tmp, out_path)
    n = len(facts["trades"])
    nf = sum(t["filled"] for t in facts["trades"])
    print(f"{report_path}: {n} rows ({nf} filled) -> {out_path} "
          f"({len(body) / 1e6:.1f} MB raw, {os.path.getsize(out_path) / 1e6:.1f} MB gz)")
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for p in sys.argv[1:]:
        write(p)
