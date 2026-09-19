"""
bootstrap.py -- make the repo's report modules importable in the levels
runtime (a Vercel Python function, or the local dev server).

Two things differ from running a report on the dev machine:

  * The function's bundle is the Vercel Root Directory (`lxpb-v2/`), so the two
    files the report modules import from the PARENT directory -- `lxpb.py` and
    `data/build_es_h1_2026_backadjusted.py` -- are not there. `vendor/` holds
    copies (see sync_runtime.py), put first on sys.path.
  * There is no Sierra Chart tick data. The levels dashboard reads no ticks, but
    render_labels_report imports `scidReader` at module level, so a stub that
    raises if anything ever calls it stands in for it -- on every machine, so
    the dashboard is proven tick-free wherever it runs.

Call `setup()` once, before importing any report module.
"""
import os
import sys
import types

RUNTIME_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(RUNTIME_DIR)            # lxpb-v2/
VENDOR_DIR = os.path.join(RUNTIME_DIR, "vendor")
SEED_DIR = os.path.join(RUNTIME_DIR, "seed")
SEED_M5 = os.path.join(SEED_DIR, "es_m5_history.csv.gz")
_done = False


def _stub_scid_reader():
    mod = types.ModuleType("scidReader")

    def get_scid_df(*_a, **_k):
        raise RuntimeError("tick (.scid) data is not available in the levels runtime")

    mod.get_scid_df = get_scid_df
    sys.modules["scidReader"] = mod


def setup():
    global _done
    if _done:
        return
    for p in (ROOT, os.path.join(ROOT, "ss_m5_confl2"), VENDOR_DIR):
        if p in sys.path:
            sys.path.remove(p)
    # Order matters: vendor/ first so `import lxpb` never resolves to a stale
    # sibling, then the repo's own modules.
    sys.path[:0] = [VENDOR_DIR, ROOT, os.path.join(ROOT, "ss_m5_confl2")]
    _stub_scid_reader()
    _done = True
