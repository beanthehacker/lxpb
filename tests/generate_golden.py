"""
Regenerate the golden regression fixtures under tests/fixtures/.

Run this ONLY when a change to lxpb.py's detection logic is intentional
(and has been verified correct). test_lxpb.py::test_matches_golden_* will
fail against stale fixtures otherwise -- that's the point: it flags any
unintentional behavior change (a bug) introduced by future edits.

Usage:
    python tests/generate_golden.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lxpb import detect_lxpb_h1, load_ohlc_data  # noqa: E402

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

SYMBOLS = {
    "es":  "dataTest/es-h1.csv",
    "nq":  "dataTest/nq-h1.csv",
    "rty": "dataTest/rty-h1.csv",
}


def main():
    os.makedirs(FIXTURES_DIR, exist_ok=True)
    for sym, rel_path in SYMBOLS.items():
        csv_path = os.path.join(REPO_ROOT, rel_path)
        df = load_ohlc_data(csv_path)
        touch_lv0, touch_lv1, retests = detect_lxpb_h1(df)

        retests = retests.sort_values(["formation_time", "breakout_time", "type"]).reset_index(drop=True)
        touch_lv1 = touch_lv1.sort_values(["formation_time", "type"]).reset_index(drop=True)

        retests_path = os.path.join(FIXTURES_DIR, f"{sym}_h1_retests_golden.csv")
        lv1_path = os.path.join(FIXTURES_DIR, f"{sym}_h1_touch_lv1_golden.csv")
        retests.to_csv(retests_path, index=False)
        touch_lv1.to_csv(lv1_path, index=False)
        print(f"{sym}: {len(df)} bars -> {len(retests)} retests, {len(touch_lv1)} open one-touch "
              f"-> wrote {retests_path}, {lv1_path}")


if __name__ == "__main__":
    main()
