"""Render the August 2026 (Aug 1-17, all available .scid data) dashboard,
reusing render_report.render() against the full-month 1s dataset and its
12-signal trigger CSV instead of the original 08:00-09:30 session."""
import os
import render_report as R

_HERE = os.path.dirname(os.path.abspath(__file__))
CSV_1S = os.path.join(_HERE, "ES_202608_full_1s.csv")
TRIGGERS_CSV = os.path.join(_HERE, "lxpb_volume_strat_triggers_august.csv")
OUT_HTML = os.path.join(_HERE, "lxpb_volume_strat_report_august.html")
TITLE = "LXPB + Volume-Absorption Strategy — August 2026 (Aug 1-17 PT, ES)"

if __name__ == "__main__":
    R.render(csv_1s_path=CSV_1S, triggers_csv_path=TRIGGERS_CSV,
              out_html_path=OUT_HTML, title=TITLE)
