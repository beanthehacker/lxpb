"""Growing OHLC series with a Wilder ATR kept up to date bar by bar.

Times are UTC nanoseconds (int). The ATR is the same recursion as
patterns_pure.find_range._atr_series (ewm alpha=1/period, adjust=False over the true
range, first bar excluded), so ATR[k] only ever depends on bars 0..k.
"""
import bisect


class Series:
    __slots__ = ("period", "t", "o", "h", "l", "c", "atr")

    def __init__(self, period=21):
        self.period = period
        self.t, self.o, self.h, self.l, self.c, self.atr = [], [], [], [], [], []

    def __len__(self):
        return len(self.t)

    def atr_for(self, h, l):
        """ATR a next bar with this high/low would get; needs the close before it."""
        n = len(self.t)
        if n == 0:
            return float("nan")
        pc = self.c[-1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        prev = self.atr[-1]
        if n == 1:
            return tr                      # ewm adjust=False starts on the first true range
        a = 1.0 / self.period
        return prev + a * (tr - prev)

    def append(self, t, o, h, l, c):
        self.atr.append(self.atr_for(h, l))
        self.t.append(t); self.o.append(o); self.h.append(h); self.l.append(l); self.c.append(c)

    def pop(self):
        for a in (self.t, self.o, self.h, self.l, self.c, self.atr):
            a.pop()

    def left(self, t):
        return bisect.bisect_left(self.t, t)
