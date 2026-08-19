import os
import time
import combine_and_scan as S

_HERE = os.path.dirname(os.path.abspath(__file__))
t0 = time.time()
out = S.scan(
    # Ported from daily-analysis (was: r"D:\daily-analysis\lxpb-volume-strat\...").
    # ES_202608_full_1s.csv is a large (~88MB) exported dataset not copied into
    # this repo; place it alongside this script (or re-export via
    # export_es_1s_range.py) before running.
    csv_1s_path=os.path.join(_HERE, "ES_202608_full_1s.csv"),
    out_csv_path=os.path.join(_HERE, "lxpb_volume_strat_triggers_august.csv"),
)
print(f"\nTotal elapsed: {time.time()-t0:.1f}s")
