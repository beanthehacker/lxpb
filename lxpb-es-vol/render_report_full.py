"""Render the FULL-.scid-history (2026-05-28 to 2026-08-17) dashboard,
reusing render_report.render() against the full-range 1s dataset and its
73-signal trigger CSV (superset of the August-only 12-signal dashboard)."""
import os
import render_report as R

_HERE = os.path.dirname(os.path.abspath(__file__))
CSV_1S = os.path.join(_HERE, "ES_full_1s.csv")
TRIGGERS_CSV = os.path.join(_HERE, "lxpb_volume_strat_triggers_full.csv")
OUT_HTML = os.path.join(_HERE, "lxpb_volume_strat_report_full.html")
TITLE = "LXPB + Volume-Absorption Strategy — Full .scid history (2026-05-28 to 2026-08-17, ES)"

if __name__ == "__main__":
    R.render(csv_1s_path=CSV_1S, triggers_csv_path=TRIGGERS_CSV,
              out_html_path=OUT_HTML, title=TITLE)
