"""
h1_bias.py
===========
H1 DIRECTIONAL BIAS: short-lived context read off the one continuous H1
series (`render_labels_report._display_h1` -- see the "continuous contracts
only" convention in CLAUDE.md; nothing here ever resamples .scid into bars).

A bias is NOT an LXPB level and has no P0/P1/P2 of its own. It is a plain
statement about the last few H1 candles: "the higher timeframe just rejected
lower prices" (BULLISH) or "just rejected higher prices" (BEARISH). It is
deliberately short-lived -- each pattern below carries its own expiry -- and
its only consumer is a REVIEW column plus one dynamic filter in the M5-native
strategy report (`ss_m5_confl2/render_m5_confl2_report.py`). Nothing here
changes which trades that strategy takes.

FOUR PATTERNS, TWO PER SIDE
---------------------------
BULLISH
  * `hammer`  -- the H1 candle is a patterns-pure hammer that also sweeps the
                 previous candle's low (its own low is below it); a shooting
                 star likewise has to trade above the previous candle's high.
  * `sfp`     -- the H1 candle SWEEPS a swing LOW (its own low goes below it)
                 and CLOSES back above it: a swing failure at the low.
BEARISH
  * `star`    -- the H1 candle is a patterns-pure shooting star.
  * `sfp`     -- the H1 candle sweeps a swing HIGH and closes back below it.

All four are patterns-pure's own definitions -- `find_hammer`,
`find_shooting_star` and `find_sfp` -- and none of them is restated here.
`find_sfp` owns which swing an SFP is measured against: the most recent
confirmed fractal pivot that is also still UNTESTED, i.e. no bar since it
formed has reached it. A swing price already traded through once is spent, so
one swing yields at most one SFP.

WHICH CANDLE IS BULLISH, WHICH IS BEARISH -- neither repo "pairing"
------------------------------------------------------------------
CLAUDE.md's two LHPB/LLPB spike pairings (detector / rejection) both answer
"which candle counts as the spike for a LEVEL of this type". THIS module
answers no such question: it reads the candle on its own, with no level
involved, so a hammer is simply bullish and a shooting star simply bearish
(patterns-pure's own reading of the two shapes). Do not map either pairing
onto it. The candle test itself IS patterns-pure's `find_hammer` /
`find_shooting_star`, as everywhere else in the repo -- nothing here restates
the thresholds.

HOW A BIAS DIES
---------------
All four die the same three ways. Written out for a BULLISH bias (a hammer
or an sfp at a low); a bearish one is the exact mirror, hanging on its
candle's HIGH instead of its low:

  a) the very NEXT H1 candle is a BIAS-THRUST candle (see below) carrying it
     through -- the move it promised has already been made, so there is no
     unspent bias left to fade;
  b) a later H1 candle trades one tick BELOW the bias candle's own low --
     the rejection that defined it has itself been rejected, and for an sfp
     that low IS the sweep, so sweeping it back is the failure failing;
  c) it goes stale: `HAMMER_BIAS_MAX_AGE` (3) H1 candles have closed after a
     hammer/shooting star, `SFP_BIAS_MAX_AGE` (4) after an sfp, with neither
     (a) nor (b) happening.

The age is the ONLY thing that differs between the four.

Only CLOSED H1 candles are ever judged. The H1 candle the query instant sits
inside is still forming, so it is offset 0 and is never a bias, never a
thrust and never breaks a low -- exactly what a trader watching the clock
would know at that moment.

BIAS-THRUST -- patterns-pure's spike-thrust, generalised, plus TWO extra rows
----------------------------------------------------------------------------
Three things separate a bias-thrust from patterns-pure's `find_spike_thrust`
(the spike-thrust definition in CLAUDE.md, the only one allowed in the repo):

  * WHICH CANDLE IT FOLLOWS. `find_spike_thrust` only ever fires on the
    candle after a hammer or shooting star. A bias-thrust also fires on the
    candle after any other bias candle in BIAS_KINDS -- an SFP today --
    measured against THAT candle's range, because every bias here has to be
    expirable the same way. Everything else is unchanged: it must close in
    the bias's own direction (up after a bullish reference candle, down
    after a bearish one) and clear one (range, body) row.
  * ONE EXTRA FLAT ROW, `EXTRA_THRUST_TIER`, that patterns-pure does not
    have: range >= 0.60x the reference candle's range with a body >= 75% of
    the thrust's own range.
  * ONE SLIDING ROW, the only row anywhere in the repo whose body
    requirement is not a constant: from range >= 0.65x needing a 50% body,
    the body eases by half a point per point of extra range down to 42%,
    which it reaches at 0.81x and holds from there on
    (`sliding_thrust_body`). A wider candle made its move more forcefully,
    so it is allowed a slightly smaller body. It is one-way: extra body
    never buys back missing range, and under 0.65x this row does not apply
    at all.

The canonical rows are never written down here -- `_thrust_after` reads
patterns-pure's own `SPIKE_THRUST_TIERS` and adds the two local rows on top
-- and `_series_masks` ASSERTS on every load that running `_thrust_after`
with the canonical rows alone (both local rows off) and hammer/star
reference candles reproduces `find_spike_thrust`'s own output exactly. So
patterns-pure stays the authority: if its table ever changes, this module
follows it or fails loudly. None of this is a change to the repo-wide
spike-thrust -- nothing outside this file sees the generalisation or either
extra row, and `patterns_pure/` is untouched.

Rows are OR'd, so the two local rows can only ever ADD candles: the flat one
adds the 0.60x..0.75x band with a >= 75% body, and the sliding one adds
everything from 0.65x up whose body clears its easing line -- which from
0.81x on means any candle with a >= 42% body, the widest single loosening of
the three. Both drive the bias EXPIRY, not just the served tag.
"""
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
for _p in (_HERE, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import render_labels_report as R     # noqa: E402  (also puts patterns_pure/ on sys.path)
import m5_structure as MS            # noqa: E402

from find_spike_thrust import find_spike_thrust, SPIKE_THRUST_TIERS   # noqa: E402
from find_sfp import find_sfp   # noqa: E402

TICK_SIZE = R.TICK_SIZE_DEFAULT

# Bars each side of the fractal swing pivot an SFP sweeps. The same plain
# k-bars-each-side fractal the swerve rule uses on M5 (MS.SWING_K_DEFAULT),
# asserted equal below so the two never drift apart; patterns-pure's find_sfp
# owns the pivot scan itself.
SWING_K = 2
assert SWING_K == MS.SWING_K_DEFAULT, "H1 bias SFP pivot must match the repo's swing pivot"

# How many CLOSED H1 candles a bias survives after its own candle.
HAMMER_BIAS_MAX_AGE = 3     # hammer / shooting star, expiry rule (c)
SFP_BIAS_MAX_AGE = 4        # sfp at a swing low / high

# The one (min range as a multiple of the reference candle's range, min body
# as a share of the thrust's own range) row this module adds on top of
# patterns-pure's SPIKE_THRUST_TIERS -- see the module docstring. The
# canonical rows are read from that constant and never restated here.
EXTRA_THRUST_TIER = (0.60, 0.75)
BIAS_THRUST_TIERS = tuple(SPIKE_THRUST_TIERS) + (EXTRA_THRUST_TIER,)

# The SLIDING row, the second thing this module adds on top of patterns-pure
# (see the module docstring). Unlike every flat row above it, the body it
# demands SHRINKS as the thrust gets wider: SLIDING_THRUST_BODY of the
# thrust's own range at SLIDING_THRUST_FLOOR, easing by
# SLIDING_THRUST_CREDIT of a point per point of extra width, and never below
# SLIDING_THRUST_BODY_FLOOR (reached at 0.81x and held from there on). A
# wider candle made its move more forcefully, so it is allowed a slightly
# smaller body; a narrow one is not. Nothing under the floor width qualifies
# on this row at all, however big its body -- extra body never buys back
# missing width.
SLIDING_THRUST_FLOOR = 0.65
SLIDING_THRUST_BODY = 0.50
SLIDING_THRUST_BODY_FLOOR = 0.42
SLIDING_THRUST_CREDIT = 0.5


def sliding_thrust_body(width):
    """The body (as a share of the thrust candle's own range) the sliding row
    demands of a thrust this wide, `width` being its range as a multiple of
    the reference candle's. Scalar or array."""
    relief = SLIDING_THRUST_CREDIT * np.maximum(0.0, width - SLIDING_THRUST_FLOOR)
    return np.maximum(SLIDING_THRUST_BODY_FLOOR, SLIDING_THRUST_BODY - relief)


def thrust_row_cleared(width, body_share):
    """The (width multiple, body share) row a candle clears, or None --
    `width` its range as a multiple of the reference candle's, `body_share`
    its body as a share of its own range. The flat rows first, then the
    sliding one, whose row is reported at the body it actually demanded."""
    for row in BIAS_THRUST_TIERS:
        if width >= row[0] and body_share >= row[1]:
            return row
    if width >= SLIDING_THRUST_FLOOR:
        need = float(sliding_thrust_body(width))
        if body_share >= need:
            return (SLIDING_THRUST_FLOOR, need)
    return None

# THE BIAS KINDS -- one row per bias: its name, its side, the key its per-bar
# candle mask is stored under in `_series_masks`, and how many closed H1
# candles it survives. Everything else is DERIVED from this table: which
# candles the thrust expiry is measured after, which extreme a bias hangs on,
# when it goes stale, and whether an entry serves it. Adding a kind is this
# row plus its mask in `_series_masks`; nothing else needs to know about it.
BIAS_KINDS = (
    ("hammer", "bull", "hammer", HAMMER_BIAS_MAX_AGE),
    ("star", "bear", "star", HAMMER_BIAS_MAX_AGE),
    ("sfp", "bull", "sfp_low", SFP_BIAS_MAX_AGE),
    ("sfp", "bear", "sfp_high", SFP_BIAS_MAX_AGE),
)
MAX_BIAS_AGE = max(age for *_, age in BIAS_KINDS)

_BIAS_CACHE = {}


# --------------------------------------------------------------------------
# Per-series masks, computed once over the whole H1 series
# --------------------------------------------------------------------------

def _thrust_after(bars, ref_bull, ref_bear, tiers, sliding=False):
    """Boolean (up, down) masks: the candle immediately after a BULLISH
    reference candle (`ref_bull`) that closes up and clears one of `tiers`
    against that reference candle's range, and the mirror after a bearish one.

    Exactly patterns-pure's own spike-thrust shape, with the reference candle
    made a parameter (it hardcodes "a hammer or a shooting star") so an SFP
    candle can be expired the same way -- see the module docstring. `tiers` is
    read from patterns-pure's `SPIKE_THRUST_TIERS`; no row is written here.

    `sliding` adds this module's own sliding row (`sliding_thrust_body`) on
    top of `tiers`. It is OFF for the check against patterns-pure, which must
    see the canonical rows and nothing else."""
    total = (bars["high"] - bars["low"]).to_numpy(float)
    body = (bars["close"] - bars["open"]).abs().to_numpy(float)
    ref_range = np.concatenate([[np.nan], total[:-1]])
    strong = np.zeros(len(bars), dtype=bool)
    for range_mult, body_share in tiers:
        strong |= (total >= ref_range * range_mult) & (body >= total * body_share)
    if sliding:
        with np.errstate(invalid="ignore", divide="ignore"):
            width = np.where(ref_range > 0, total / ref_range, np.nan)
        strong |= ((width >= SLIDING_THRUST_FLOOR) &
                   (body >= total * sliding_thrust_body(width)))
    after_bull = np.concatenate([[False], np.asarray(ref_bull)[:-1]])
    after_bear = np.concatenate([[False], np.asarray(ref_bear)[:-1]])
    closes_up = (bars["close"] > bars["open"]).to_numpy()
    closes_down = (bars["close"] < bars["open"]).to_numpy()
    return (after_bull & strong & closes_up, after_bear & strong & closes_down)


def _assert_matches_patterns_pure(bars, is_hammer, is_star):
    """`_thrust_after` restricted to patterns-pure's own rows and its own
    hammer/shooting-star reference candles must reproduce `find_spike_thrust`
    exactly. Checked on every load so the generalisation above can never
    drift from the definition CLAUDE.md pins the repo to: if patterns-pure's
    table changes, this fails loudly instead of quietly disagreeing."""
    up, down = _thrust_after(bars, is_hammer, is_star, SPIKE_THRUST_TIERS)
    canonical = find_spike_thrust(bars)
    mine = bars.index[up | down]
    if not mine.equals(canonical.index):
        raise AssertionError(
            f"h1_bias._thrust_after disagrees with patterns-pure's "
            f"find_spike_thrust on the H1 series: {len(mine)} hits vs "
            f"{len(canonical)}, {len(mine.symmetric_difference(canonical.index))} "
            f"differing bar(s). Refresh this module against patterns_pure/.")


def _sfp_masks(bars):
    """Boolean (sfp_low, sfp_high) masks plus the swing each one swept, from
    patterns-pure's `find_sfp` -- which owns the whole definition, including
    that the swing must still be UNTESTED. Nothing about it is restated here.

    Returns (sfp_low, sfp_high, swept_by_pos): the two masks and a dict from a
    bar position to (swing bar time, swing price), for the tooltip."""
    lo, hi = find_sfp(bars, k=SWING_K)
    sfp_low = bars.index.isin(lo.index)
    sfp_high = bars.index.isin(hi.index)
    pos_by_time = {t: i for i, t in enumerate(bars.index)}
    swept = {pos_by_time[t]: (r["swing_time"], float(r["swing_price"]))
             for frame in (lo, hi) for t, r in frame.iterrows()}
    return sfp_low, sfp_high, swept


def _series_masks(bars):
    """Every per-bar flag `biases_at` needs, computed once for the whole H1
    series and memoised on its last bar + length (the series only ever grows
    or is re-anchored wholesale, both of which change one of the two)."""
    key = (bars.index[-1], len(bars))
    if key in _BIAS_CACHE:
        return _BIAS_CACHE[key]
    is_hammer = bars.index.isin(R._pp_find_hammer(bars, atr=0.0).index)
    is_star = bars.index.isin(R._pp_find_shooting_star(bars, atr=0.0).index)
    _assert_matches_patterns_pure(bars, is_hammer, is_star)
    # A bias candle must also SWEEP the candle right before it: a hammer's low
    # trades below the previous candle's low, a shooting star's high above the
    # previous candle's high. Layered on top of patterns-pure's own verdict
    # (checked raw, above); no threshold of its own is restated. The first bar
    # has no previous candle, so it never qualifies.
    low, high = bars["low"].to_numpy(float), bars["high"].to_numpy(float)
    sweeps_low = np.concatenate([[False], low[1:] < low[:-1]])
    sweeps_high = np.concatenate([[False], high[1:] > high[:-1]])
    is_hammer = is_hammer & sweeps_low
    is_star = is_star & sweeps_high
    sfp_low, sfp_high, swept = _sfp_masks(bars)
    out = {"hammer": is_hammer, "star": is_star,
           "sfp_low": sfp_low, "sfp_high": sfp_high}
    # ONE bullish reference mask covering EVERY bullish bias candle in
    # BIAS_KINDS (and one bearish), so a new kind is expired by a thrust the
    # moment its row is added. A thrust is judged against the reference
    # candle's own range and the bias's own direction, both of which are
    # identical for any two biases sitting on the same candle, so merging
    # them is not an approximation -- and each expiry check below already
    # knows which kind of bias it is asking about.
    ref = {"bull": np.zeros(len(bars), dtype=bool),
           "bear": np.zeros(len(bars), dtype=bool)}
    for _kind, side, key, _age in BIAS_KINDS:
        ref[side] = ref[side] | out[key]
    thrust_up, thrust_down = _thrust_after(
        bars, ref["bull"], ref["bear"], BIAS_THRUST_TIERS, sliding=True)
    out.update({
        "thrust_up": thrust_up, "thrust_down": thrust_down, "swept": swept,
        "low": bars["low"].to_numpy(float), "high": bars["high"].to_numpy(float),
        "open": bars["open"].to_numpy(float), "times": bars.index,
    })
    _BIAS_CACHE[key] = out
    return out


# --------------------------------------------------------------------------
# The query
# --------------------------------------------------------------------------

def _anchor(bars, ts):
    """Position of the H1 candle at OFFSET 0 for an instant `ts`: the candle
    `ts` sits inside when the series has one (still forming, so never judged),
    otherwise -- `ts` fell in a market closure or a hole, where every bar up to
    it has closed -- the position just past the last closed bar, so that the
    last closed bar is offset -1. Returns None before the series starts."""
    ts = MS._as_utc(ts)
    p = int(bars.index.searchsorted(ts, side="right")) - 1
    if p < 0:
        return None
    return p if bars.index[p] + pd.Timedelta(hours=1) > ts else p + 1


def biases_at(ts, bars=None):
    """Every H1 bias still LIVE at instant `ts`, nearest candle first.

    Each entry is a dict:
      kind      'hammer' | 'star' | 'sfp'
      side      'bull' | 'bear'
      offset    negative H1 candles from the candle `ts` sits in (-1 = the
                previous, most recently closed candle)
      time      that candle's own bar time (tz-aware UTC)
      label     '<kind>@<offset>', e.g. 'hammer@-1' -- the report's metadata
      price     the candle extreme the bias hangs on (its low for a bullish
                one, its high for a bearish one)
      max_age   how many closed candles it survives in total (see the two
                MAX_AGE constants), so `offset` == -max_age is its last candle
      swing_time / swing_price
                SFP only (None otherwise): the untested swing pivot it swept
    """
    bars = R._display_h1() if bars is None else bars
    m = _series_masks(bars)
    a = _anchor(bars, ts)
    if a is None:
        return []
    out = []
    # Every bias in BIAS_KINDS, and the only thing that differs between them:
    # which candles make one, and how long it lasts. Expiry (a) and (b) are
    # shared word for word -- a bullish bias hangs on its candle's LOW and
    # dies when a later candle trades a tick under it, a bearish one on its
    # HIGH.
    for k in range(1, MAX_BIAS_AGE + 1):
        i = a - k
        if i < 0:
            break
        # Closed candles strictly after candle i, as of `ts`: i+1 .. a-1.
        closed_after = slice(i + 1, a)
        swept = {
            "bull": bool((m["low"][closed_after] <= m["low"][i] - TICK_SIZE).any()),
            "bear": bool((m["high"][closed_after] >= m["high"][i] + TICK_SIZE).any()),
        }
        for kind, side, key, max_age in BIAS_KINDS:
            if not m[key][i] or k > max_age:
                continue
            thrust = m["thrust_up"] if side == "bull" else m["thrust_down"]
            if i + 1 < a and thrust[i + 1]:            # (a) carried through
                continue
            if swept[side]:                            # (b) its extreme gave way
                continue
            swing_time, swing_price = m["swept"].get(i, (None, None))
            out.append({"kind": kind, "side": side, "offset": -k,
                        "time": m["times"][i], "label": f"{kind}@-{k}",
                        "price": float((m["low"] if side == "bull" else m["high"])[i]),
                        "max_age": max_age,
                        # SFP only: the swing it swept, for the tooltip.
                        "swing_time": swing_time if kind == "sfp" else None,
                        "swing_price": swing_price if kind == "sfp" else None})
    out.sort(key=lambda b: (-b["offset"], b["side"], b["kind"]))
    return out


def expire_swept(biases, retest_ts, fill_time, fill_price, bars=None, m5=None):
    """`biases` (as `biases_at(retest_ts)` returned them) minus any that price
    has already broken by the time a trade FILLED. Expiry (b) in the module
    docstring only looks at closed H1 candles, so a fill that trades through
    the bias candle's own extreme inside the still-forming candle -- or in a
    later one, when the fill lands after the retest's candle -- slips past it.
    Here every M5 bar closed at the fill, from the open of the retest's
    candle on, plus the fill price itself, is checked the same way: a bullish
    bias dies once price trades a tick under its low, a bearish one a tick
    over its high. Nothing after the fill is peeked at."""
    if not biases:
        return biases
    bars = R._display_h1() if bars is None else bars
    m5 = R._display_m5() if m5 is None else m5
    retest_ts, fill_time = MS._as_utc(retest_ts), MS._as_utc(fill_time)
    a = _anchor(bars, retest_ts)
    start = retest_ts if a is None or a >= len(bars) else min(retest_ts, bars.index[a])
    seen = m5.loc[(m5.index >= start) & (m5.index + pd.Timedelta(minutes=5) <= fill_time)]
    lo = min([float(seen["low"].min())] * len(seen) + [fill_price])
    hi = max([float(seen["high"].max())] * len(seen) + [fill_price])
    return [b for b in biases
            if not ((b["side"] == "bull" and lo <= b["price"] - TICK_SIZE) or
                    (b["side"] == "bear" and hi >= b["price"] + TICK_SIZE))]


def summarize(biases):
    """(bull_labels, bear_labels) as two plain lists of '<kind>@<offset>'
    metadata strings, nearest candle first."""
    return ([b["label"] for b in biases if b["side"] == "bull"],
            [b["label"] for b in biases if b["side"] == "bear"])


def fades(biases, is_long):
    """The live biases this trade's own direction FADES: the bearish ones for
    a long, the bullish ones for a short. Non-empty means the trade is
    fading-bias."""
    against = "bear" if is_long else "bull"
    return [b for b in biases if b["side"] == against]


# BIAS SERVED -- a bias whose promised move is already being made at the very
# moment a trade enters against it. ONE test, and it is the BIAS-THRUST test
# above with nothing added: the candle right after the bias candle -- the
# candle the ENTRY ITSELF sits in -- already qualifies as that bias's thrust
# as of the entry.
#
# WHY ONLY THAT ONE CANDLE. A bias-thrust on a CLOSED candle expires the bias
# outright (rule (a) in the module docstring), so once such a candle has
# closed there is no live bias left to fade and nothing to tag. The only
# window in which a thrust can be under way and the bias still live is the
# candle still forming. So served is exactly: the bias is one candle back,
# and the candle the entry sits in is already on course to be its thrust.
#
# POINT-IN-TIME, ALWAYS. That candle has not closed, so it is measured from
# its own open, every M5 bar that had CLOSED at the entry, and the entry
# price itself standing in for the close -- nothing after the entry is read.
# That is also how the entry price enters the test: the body so far runs from
# the candle's open to the entry price, so an entry that has not travelled
# with the move cannot produce a thrust-sized body, and one taken against the
# move (below the open under a bullish bias) fails the direction test the
# same way a thrust candle closing the wrong way does.
#
# EVERY KIND IS ELIGIBLE, present and future: the test reads only the bias
# candle's range and the bias's side, which BIAS_KINDS supplies for all of
# them. It has no numbers of its own -- the rows are the bias-thrust's own,
# flat and sliding alike (`thrust_row_cleared`), exactly as the expiry's are.


def _candle_so_far(m5, t_open, fill_time, fill_price, open_price):
    """The forming H1 candle's (high, low) AS OF `fill_time`: its own open,
    every M5 bar that had CLOSED by then, and the fill price standing in for
    the close. Nothing at or after the fill's own M5 bar close is read."""
    seen = m5.loc[(m5.index >= t_open) & (m5.index + pd.Timedelta(minutes=5) <= fill_time)]
    hi = max([float(seen["high"].max())] * len(seen) + [open_price, fill_price])
    lo = min([float(seen["low"].min())] * len(seen) + [open_price, fill_price])
    return hi, lo


def served(biases, is_long, fill_time, fill_price, bars=None, m5=None):
    """The FADED biases (see `fades`) already SERVED at this entry -- see the
    comment above -- each as a copy of the bias dict plus:

      bias_range    the bias candle's own range, in points
      thrust_range  the forming candle's range so far, as a multiple of it
      thrust_body   the body so far, as a share of that range so far
      tier          the (range multiple, body share) row it cleared

    Empty means not served. Only a bias ONE candle back can be served, and
    only by the candle the entry sits in, judged on what had happened by the
    entry."""
    bars = R._display_h1() if bars is None else bars
    m5 = R._display_m5() if m5 is None else m5
    fill_time = MS._as_utc(fill_time)
    out = []
    for b in fades(biases, is_long):
        if b["offset"] != -1:      # only the candle immediately after it
            continue
        pos = bars.index.get_loc(MS._as_utc(b["time"]))
        if pos + 1 >= len(bars):
            continue
        t_open = bars.index[pos + 1]
        if not (t_open <= fill_time < t_open + pd.Timedelta(hours=1)):
            continue               # the entry is not inside that candle
        bias_range = float(bars["high"].iloc[pos]) - float(bars["low"].iloc[pos])
        if bias_range <= 0:
            continue
        open_price = float(bars["open"].iloc[pos + 1])
        hi, lo = _candle_so_far(m5, t_open, fill_time, fill_price, open_price)
        total = hi - lo
        # Signed in the bias's own direction, so a candle going the other way
        # is negative and clears no row -- the same direction test the expiry
        # makes on a closed candle's close.
        body = (fill_price - open_price) if b["side"] == "bull" else (open_price - fill_price)
        if total <= 0 or body <= 0:
            continue
        tier = thrust_row_cleared(total / bias_range, body / total)
        if tier is None:
            continue
        out.append({**b, "bias_range": bias_range,
                    "thrust_range": total / bias_range,
                    "thrust_body": body / total, "tier": tier})
    return out
