"""Incremental zigzags.

BoxZigzag   the ATR zigzag behind the range boxes: a port of
            patterns_pure.find_range_box_zz._zigzag_pivots, one bar per call.
StructZigzag  m5_structure's point-threshold zigzag (3pt/3 bars, fast 5pt/2, very fast
            10pt/1) behind the target and run-in rules, one bar per call.
"""
import math


class BoxZigzag:
    """step(j, h, l, atr_j, order) -> new pivots [(kind, i, price, j)]. Bar 0 is `begin`."""

    def __init__(self, atr_mult=0.75, atr_period=21):
        self.atr_mult, self.atr_period = atr_mult, atr_period
        self.direction = 0
        self.run_hi = self.run_lo = None
        self.run_hi_i = self.run_lo_i = 0

    def begin(self, h, l):
        self.run_hi, self.run_lo = h, l
        self.run_hi_i = self.run_lo_i = 0

    def step(self, j, h, l, atr_j, order=0):
        out = []
        thr = self.atr_mult * atr_j if j >= self.atr_period else math.inf
        if self.direction == 0:
            if h > self.run_hi:
                self.run_hi, self.run_hi_i = h, j
            if l < self.run_lo:
                self.run_lo, self.run_lo_i = l, j
            if self.run_hi - l >= thr and self.run_hi_i < j:
                out.append(("crest", self.run_hi_i, self.run_hi, j))
                self.direction = -1
                self.run_lo, self.run_lo_i = l, j
            elif h - self.run_lo >= thr and self.run_lo_i < j:
                out.append(("trough", self.run_lo_i, self.run_lo, j))
                self.direction = 1
                self.run_hi, self.run_hi_i = h, j
            return out
        high_first = order > 0 or (order == 0 and self.direction == 1)
        for ev in ("h", "l") if high_first else ("l", "h"):
            if ev == "h":
                if self.direction == 1:
                    if h > self.run_hi:
                        self.run_hi, self.run_hi_i = h, j
                elif h - self.run_lo >= thr:
                    out.append(("trough", self.run_lo_i, self.run_lo, j))
                    self.direction = 1
                    self.run_hi, self.run_hi_i = h, j
            else:
                if self.direction == -1:
                    if l < self.run_lo:
                        self.run_lo, self.run_lo_i = l, j
                elif self.run_hi - l >= thr:
                    out.append(("crest", self.run_hi_i, self.run_hi, j))
                    self.direction = -1
                    self.run_lo, self.run_lo_i = l, j
        return out


class StructZigzag:
    """Confirmed pivots of m5_structure._scan_zigzag, one bar per call.
    `pivots`: [(time, price, kind, confirmed_time)], kind 'high' (crest) / 'low' (trough)."""

    def __init__(self, threshold=3.0, min_bars=3, fast=(5.0, 2), veryfast=(10.0, 1)):
        self.rules = [(threshold, min_bars), fast, veryfast]
        self.pivots = []
        self.looking_for = "high"
        self.n = 0
        self.ext_price = self.ext_idx = self.ext_time = None
        self.worst = math.inf

    def _confirms(self, reversal, since):
        return any(reversal >= th and since >= mb for th, mb in self.rules)

    def step(self, t, h, l):
        i = self.n
        self.n += 1
        if i == 0:
            self.ext_price, self.ext_idx, self.ext_time = h, 0, t
            return None
        if self.looking_for == "high":
            if h > self.ext_price:
                self.ext_price, self.ext_idx, self.ext_time, self.worst = h, i, t, math.inf
            else:
                self.worst = min(self.worst, l)
                if self._confirms(self.ext_price - self.worst, i - self.ext_idx):
                    p = (self.ext_time, self.ext_price, "high", t)
                    self.pivots.append(p)
                    self.looking_for = "low"
                    self.ext_price, self.ext_idx, self.ext_time, self.worst = l, i, t, -math.inf
                    return p
        else:
            if l < self.ext_price:
                self.ext_price, self.ext_idx, self.ext_time, self.worst = l, i, t, -math.inf
            else:
                self.worst = max(self.worst, h)
                if self._confirms(self.worst - self.ext_price, i - self.ext_idx):
                    p = (self.ext_time, self.ext_price, "low", t)
                    self.pivots.append(p)
                    self.looking_for = "high"
                    self.ext_price, self.ext_idx, self.ext_time, self.worst = h, i, t, math.inf
                    return p
        return None

    def most_recent(self, kind, cutoff, after=None):
        """(time, price) of the newest `kind` pivot formed AND confirmed strictly before
        `cutoff` (and formed after `after`), like m5_structure.most_recent_pivot."""
        best = None
        for t, price, k, ct in reversed(self.pivots):
            if k != kind or t >= cutoff or ct >= cutoff:
                continue
            if after is not None and t <= after:
                continue
            if best is None or t > best[0]:
                best = (t, price)
            # pivots are time-ordered: the first hit going backwards is the newest
            break
        return best if best else (None, None)
