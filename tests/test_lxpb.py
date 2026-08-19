"""
Tests for lxpb.py — the ported/corrected H1 LXPB detector.

These validate the three behavior changes confirmed against
D:\\daily-analysis\\lxpb-h1-apr2026\\lxpb_h1_detect.py (the source of truth):

  1. Retest requires only range overlap (or a gap past the level) plus the
     4h minimum wait -- NOT a directional open/close condition.
  2. Gap bars (entirely past a level) count as breakouts/retests, not just
     bars whose range overlaps the level.
  3. Every level is classified as is_spike / is_swing at formation time.
  4. Each retest carries an fta (first target available) and stop_loss.

Plus golden-file regression tests against real recent H1 data (dataTest/)
so any future refactor that unintentionally changes output is caught.

Run: python -m pytest tests/test_lxpb.py -v
Regenerate fixtures (only after a verified intentional logic change):
    python tests/generate_golden.py
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lxpb import (  # noqa: E402
    advance_one_bar, detect_lxpb_h1, is_hammer, is_shootingstar,
    load_ohlc_data, new_state,
)

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _mk_bars(rows: list) -> pd.DataFrame:
    """Build a small OHLC DataFrame for hand-crafted fixtures.

    `rows` is a list of (ts_str, open, high, low, close).
    """
    idx = pd.DatetimeIndex([pd.Timestamp(r[0]) for r in rows])
    return pd.DataFrame(
        {
            "open":  [r[1] for r in rows],
            "high":  [r[2] for r in rows],
            "low":   [r[3] for r in rows],
            "close": [r[4] for r in rows],
        },
        index=idx,
    )


def _run(bars: pd.DataFrame) -> dict:
    state = new_state()
    for b in bars.itertuples(index=True):
        advance_one_bar(state, b)
    return state


# ─── 1) Retest is direction-agnostic ─────────────────────────────────

def test_retest_fires_without_directional_close():
    """A bar that touches a one-touch level but closes on the SAME side
    it opened (i.e. would fail the old directional retest check) still
    counts as a valid retest once MIN_HOURS has elapsed."""
    bars = _mk_bars([
        ("2026-01-01 00:00", 105, 110, 100, 108),  # forms LHPB@110
        ("2026-01-01 01:00", 109, 112, 108, 111),  # breakout (close>110)
        ("2026-01-01 05:00", 109, 111, 108, 109.5),  # touches 110 but opens
                                                       # AND closes below it
                                                       # (no directional cross)
    ])
    state = _run(bars)
    rets = [r for r in state["retests"] if r["type"] == "LHPB" and r["price"] == 110]
    assert len(rets) == 1, f"expected direction-agnostic retest to fire; got {state}"
    assert rets[0]["retest_time"] == pd.Timestamp("2026-01-01 05:00")


def test_retest_requires_min_hours_regardless_of_direction():
    """Touching too soon (<4h) still consumes the level without firing a
    retest, whether or not direction would have qualified under the old rule."""
    bars = _mk_bars([
        ("2026-01-01 00:00", 105, 110, 100, 108),
        ("2026-01-01 01:00", 109, 112, 108, 111),  # breakout
        ("2026-01-01 02:00", 111, 112, 109, 109.5),  # touch after only 1h
    ])
    state = _run(bars)
    rets = [r for r in state["retests"] if r["type"] == "LHPB" and r["price"] == 110]
    assert rets == []
    assert [lv for lv in state["touch_lv1"] if lv["price"] == 110] == []


# ─── 2) Gap handling ──────────────────────────────────────────────────

def test_gap_up_breakout():
    bars = _mk_bars([
        ("2026-01-01 00:00", 105, 110, 100, 108),  # forms LHPB@110
        ("2026-01-01 01:00", 113, 115, 112, 114),  # entirely above 110
    ])
    state = _run(bars)
    lhpb_110 = [lv for lv in state["touch_lv1"] if lv["type"] == "LHPB" and lv["price"] == 110]
    assert len(lhpb_110) == 1
    assert lhpb_110[0]["breakout_low"] == 112


def test_gap_down_breakout():
    bars = _mk_bars([
        ("2026-01-01 00:00", 105, 110, 100, 102),  # forms LLPB@100
        ("2026-01-01 01:00",  96,  98,  90,  92),  # entirely below 100
    ])
    state = _run(bars)
    llpb_100 = [lv for lv in state["touch_lv1"] if lv["type"] == "LLPB" and lv["price"] == 100]
    assert len(llpb_100) == 1
    assert llpb_100[0]["breakout_high"] == 98


def test_gap_over_retest_synthetic_entry():
    bars = _mk_bars([
        ("2026-01-01 00:00", 105, 110, 100, 108),  # LHPB@110 formed
        ("2026-01-01 01:00", 109, 112, 108, 111),  # breakout
        ("2026-01-01 02:00", 111, 113, 110.5, 112),
        ("2026-01-01 03:00", 112, 114, 111,   113),
        ("2026-01-01 04:00", 113, 115, 112,   114),
        ("2026-01-01 05:00", 108, 109, 100,   102),  # gaps clean below 110
    ])
    state = _run(bars)
    rets = [r for r in state["retests"] if r["type"] == "LHPB" and r["price"] == 110]
    assert len(rets) == 1
    r = rets[0]
    assert r["entry_price"] == 110
    assert not (r["retest_low"] <= r["entry_price"] <= r["retest_high"])


# ─── 3) Spike / swing classification ─────────────────────────────────

def test_is_hammer_and_shootingstar_single_bar():
    # Hammer: small body, long lower wick, tiny upper wick.
    assert is_hammer(100, 101, 90, 100.5) is True
    # Shooting star: small body, long upper wick, tiny lower wick.
    assert is_shootingstar(100, 111, 99.5, 100.5) is True
    # A plain, evenly-wicked bar is neither.
    assert is_hammer(100, 105, 95, 102) is False
    assert is_shootingstar(100, 105, 95, 102) is False


def test_is_swing_high_classification():
    """LHPB formed at the middle bar is a swing high when its high exceeds
    both the previous and the next bar's high."""
    bars = _mk_bars([
        ("2026-01-01 00:00", 100, 105, 98, 102),   # prev: high=105
        ("2026-01-01 01:00", 102, 112, 101, 108),  # formation bar: high=112 (swing high)
        ("2026-01-01 02:00", 108, 109, 104, 106),  # next: high=109
    ])
    state = _run(bars)
    lhpb_112 = [lv for lv in state["touch_lv0"] if lv["type"] == "LHPB" and lv["price"] == 112]
    assert len(lhpb_112) == 1
    assert lhpb_112[0]["is_swing"] is True


def test_is_swing_high_false_when_not_extreme():
    bars = _mk_bars([
        ("2026-01-01 00:00", 100, 105, 98, 102),   # prev: high=105
        ("2026-01-01 01:00", 102, 104, 101, 103),  # formation bar: high=104 < prev's 105
        ("2026-01-01 02:00", 103, 106, 100, 105),  # next: high=106 (also happens to break out 104)
    ])
    state = _run(bars)
    # The level may have already broken out onto touch_lv1 by the time the
    # run ends (bar2's close crosses 104) -- is_swing must have finalized
    # to False regardless of which bucket it ended up in.
    lhpb_104 = [lv for lv in state["touch_lv0"] + state["touch_lv1"]
                if lv["type"] == "LHPB" and lv["price"] == 104]
    assert len(lhpb_104) == 1
    assert lhpb_104[0]["is_swing"] is False


def test_is_swing_low_classification():
    bars = _mk_bars([
        ("2026-01-01 00:00", 100, 105, 98, 102),   # prev: low=98
        ("2026-01-01 01:00", 100, 101,  90, 95),   # formation bar: low=90 (swing low)
        ("2026-01-01 02:00",  95, 100,  92, 97),   # next: low=92
    ])
    state = _run(bars)
    llpb_90 = [lv for lv in state["touch_lv0"] if lv["type"] == "LLPB" and lv["price"] == 90]
    assert len(llpb_90) == 1
    assert llpb_90[0]["is_swing"] is True


def test_first_bar_levels_are_never_swing():
    """The very first bar has no look-back neighbor -- is_swing must be
    False immediately (not left pending), matching the original boundary
    behavior of is_swing_high/is_swing_low(index < lookback)."""
    bars = _mk_bars([("2026-01-01 00:00", 100, 105, 98, 102)])
    state = _run(bars)
    assert all(lv["is_swing"] is False for lv in state["touch_lv0"])


def test_is_spike_propagates_through_breakout_and_retest():
    """is_spike/is_swing set at formation must survive promotion to
    touch_lv1 and into the final retests record."""
    bars = _mk_bars([
        ("2026-01-01 00:00", 100, 105, 98, 102),
        ("2026-01-01 01:00", 100, 130, 99, 100.5),  # formation bar: big hammer-ish LLPB@99 candidate
        ("2026-01-01 02:00", 100.5, 101, 95, 96),   # next bar (for swing calc)
        ("2026-01-01 03:00",  96,  97,  90,  91),   # breakout of LLPB@99 (open>99? no -- just check propagation)
        ("2026-01-01 04:00",  91,  92,  85,  86),
        ("2026-01-01 05:00",  86,  87,  80,  81),
        ("2026-01-01 06:00",  81, 100,  79,  82),   # touches back up to 99+ after >4h -> retest
    ])
    state = _run(bars)
    # Just confirm is_spike/is_swing keys are present and boolean (not None)
    # on every touch_lv1 and retest record -- i.e. classification always
    # finalizes before a level can be acted on.
    for lv in state["touch_lv1"]:
        assert lv["is_spike"] in (True, False)
        assert lv["is_swing"] in (True, False)
    for r in state["retests"]:
        assert r["is_spike"] in (True, False)
        assert r["is_swing"] in (True, False)


# ─── 4) FTA / stop loss ───────────────────────────────────────────────

def test_fta_and_stop_loss_values():
    """FTA (LHPB) = min low of bars strictly between breakout and retest.
    Stop loss (LHPB) = low of the breakout bar."""
    bars = _mk_bars([
        ("2026-01-01 00:00", 105, 110, 100,   108),   # forms LHPB@110
        ("2026-01-01 01:00", 109, 112, 108,   111),   # breakout, breakout_low=108
        ("2026-01-01 02:00", 111, 113, 110.5, 112),   # between-bar low=110.5 (deepest, doesn't touch 110)
        ("2026-01-01 03:00", 112, 114, 111,   113),   # between-bar low=111
        ("2026-01-01 04:00", 113, 115, 112,   114),   # between-bar low=112
        ("2026-01-01 05:00", 111, 112, 109,   110.5), # exactly 4h after breakout, touches 110
    ])
    state = _run(bars)
    rets = [r for r in state["retests"] if r["type"] == "LHPB" and r["price"] == 110]
    assert len(rets) == 1
    r = rets[0]
    assert r["fta"] == 110.5
    assert r["stop_loss"] == 108


# ─── Golden-file regression (real recent H1 data) ────────────────────

@pytest.mark.parametrize("symbol", ["es", "nq", "rty"])
def test_matches_golden_retests(symbol):
    """Regression guard: detect_lxpb_h1 on the committed dataTest/ H1 CSVs
    must keep producing exactly the retests captured in tests/fixtures/.
    Any diff here means detection behavior changed -- verify intentionally
    before regenerating via tests/generate_golden.py."""
    df = load_ohlc_data(os.path.join(REPO_ROOT, "dataTest", f"{symbol}-h1.csv"))
    _, touch_lv1, retests = detect_lxpb_h1(df)

    retests = retests.sort_values(["formation_time", "breakout_time", "type"]).reset_index(drop=True)
    touch_lv1 = touch_lv1.sort_values(["formation_time", "type"]).reset_index(drop=True)

    golden_retests = pd.read_csv(
        os.path.join(FIXTURES_DIR, f"{symbol}_h1_retests_golden.csv"),
        parse_dates=["formation_time", "breakout_time", "retest_time"],
    )
    golden_lv1 = pd.read_csv(
        os.path.join(FIXTURES_DIR, f"{symbol}_h1_touch_lv1_golden.csv"),
        parse_dates=["formation_time", "breakout_time"],
    )

    pd.testing.assert_frame_equal(retests, golden_retests, check_dtype=False, rtol=1e-9, atol=1e-9)
    pd.testing.assert_frame_equal(touch_lv1, golden_lv1, check_dtype=False, rtol=1e-9, atol=1e-9)
