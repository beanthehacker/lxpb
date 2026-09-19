"""
sync_runtime.py -- refresh the committed inputs of the levels runtime from the
rest of the repo. Run on the dev machine (needs the full repo and the exports),
then commit the results:

    python levels_runtime/sync_runtime.py

  1. vendor/lxpb.py, vendor/build_es_h1_2026_backadjusted.py
     Copies of the two parent-directory files the report modules import, so the
     Vercel bundle (Root Directory = lxpb-v2) is self-contained. lxpb.py's one
     path to patterns_pure is repointed at the sibling lxpb-v2/patterns_pure.
  2. seed/es_m5_history.csv.gz
     The deep M5 history, merged and validated by the repo's own loaders
     (render_labels_report._display_m5 over DISPLAY_M5_PATHS). It only seeds an
     empty store: from then on the runtime keeps its own history current
     (pipeline.py), so this needs re-running only if the exports change.
  3. public/levels/assets.css, assets.js
     The ss_m5_confl2 report's own stylesheet and chart renderer, so /levels
     looks and behaves like the report.
"""
import gzip
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PARENT = os.path.dirname(ROOT)
sys.path.insert(0, HERE)
import bootstrap  # noqa: E402

PATTERNS_OLD = ('PATTERNS_PURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),\n'
                '                                 "lxpb-v2", "patterns_pure")')
PATTERNS_NEW = ('PATTERNS_PURE_DIR = os.path.join(os.path.dirname(os.path.dirname(\n'
                '    os.path.dirname(os.path.abspath(__file__)))), "patterns_pure")   '
                '# vendored copy: lxpb-v2/levels_runtime/vendor/ -> lxpb-v2/patterns_pure')


def sync_vendor():
    os.makedirs(bootstrap.VENDOR_DIR, exist_ok=True)
    src = open(os.path.join(PARENT, "lxpb.py"), encoding="utf-8", newline="").read()
    src = src.replace("\r\n", "\n")
    if PATTERNS_OLD not in src:
        raise SystemExit("lxpb.py's PATTERNS_PURE_DIR definition changed -- update PATTERNS_OLD here")
    with open(os.path.join(bootstrap.VENDOR_DIR, "lxpb.py"), "w", encoding="utf-8", newline="\n") as f:
        f.write(src.replace(PATTERNS_OLD, PATTERNS_NEW))
    shutil.copyfile(os.path.join(PARENT, "data", "build_es_h1_2026_backadjusted.py"),
                    os.path.join(bootstrap.VENDOR_DIR, "build_es_h1_2026_backadjusted.py"))
    print("vendor/: lxpb.py (patterns path repointed), build_es_h1_2026_backadjusted.py")


def sync_seed():
    import render_labels_report as R
    m5 = R._display_m5()
    os.makedirs(bootstrap.SEED_DIR, exist_ok=True)
    out = m5.reset_index()
    import pandas as pd
    out["time"] = (out["time"] - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)
    csv = out[["time", "open", "high", "low", "close"]].to_csv(index=False).encode()
    with gzip.open(bootstrap.SEED_M5, "wb", compresslevel=9) as f:
        f.write(csv)
    print(f"seed/: {len(m5):,} M5 bars {m5.index[0]} -> {m5.index[-1]} "
          f"({os.path.getsize(bootstrap.SEED_M5) / 1e6:.1f} MB)")


def sync_assets():
    import render_m5_confl2_report as RM
    import render_stop_target_report as SR
    dest = os.path.join(ROOT, "public", "levels")
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, "assets.css"), "w", encoding="utf-8", newline="\n") as f:
        f.write(re.sub(r"</?style>", "", RM.CSS))
    js = SR.JS
    a, b = js.index("const rendered = {};"), js.index("function _renderTrio")
    with open(os.path.join(dest, "assets.js"), "w", encoding="utf-8", newline="\n") as f:
        f.write(js[a:b])
    print("public/levels/: assets.css, assets.js")


if __name__ == "__main__":
    sync_vendor()
    bootstrap.setup()
    sync_seed()
    sync_assets()
