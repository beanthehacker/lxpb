// VOLUME PROFILE for the cross bar trade charts (SAMPLE, 2026-11-09; options and their defaults are placeholders for the user to
// pick). Volume at price comes from the .scid ticks (cross_bar/vap.py): every trade's price and size, summed per minute and tick
// for the replay window and the 6 trading days before the cross bar, per 30 minutes further back. Everything is point in time:
// a profile only ever holds minutes that had CLOSED at the row's replay time T.
//
//   Day          the current trading day's developing profile (15:00 PT the day before to T), drawn from the session's start,
//                with its VAH / VAL / POC; "+ past days" also draws every earlier day's own profile where it happened.
//   Right side   one profile on the chart's right edge (in its blank 20%):
//                  composite  every minute of the lookback up to T, rows within +-band of the last price, HVN / LVN marked
//                  last visit the previous visit of the area (same rule as the history: the stretch of H1 bars that last traded
//                             within 50 pt of the day's open before price left that band), at most its last 5 trading days
//                  last leg   the last completed zigzag leg known at T (patterns-pure find_zigzag, M5 or H1, from the data file)
//                  current leg from the last confirmed zigzag pivot to T
//   Levels       VAH / VAL / POC of the last N complete days and weeks, as lines from the period's end to the right edge;
//                solid = naked (price has not traded there since, up to T), dotted = traded since. Prices of different
//                periods within the confluence tolerance are joined into one band, labelled with how many lines it holds.
//
// Value area (CBOT / Market Profile convention): the POC is the price with the most volume (a tie goes to the one nearest the
// profile's middle); from it the value area grows by comparing the volume of the next TWO prices above with the next two below and
// adding the larger pair (both on a tie), until it holds >= the value-area percentage (70%) of the profile's volume.
(function () {
  const KEY = 'postool_v1_vp_settings';
  const DEF = { day: true, past: false, right: 'comp', look: 'visit', band: 50, legTf: 'M5', dN: 3, wN: 2, naked: false,
                tol: 1, va: 70 };
  let S = Object.assign({}, DEF);
  try { Object.assign(S, JSON.parse(localStorage.getItem(KEY) || '{}')); } catch (e) { /* private window */ }
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (e) { /* private window */ } };
  const panes = [];
  const C = {
    va: 'rgba(41,98,255,0.42)', out: 'rgba(120,123,134,0.30)', poc: 'rgba(255,152,0,0.85)',
    vaLine: '#2962ff', pocLine: '#ff9800', hvn: '#00e676', lvn: '#f23645', wPoc: '#ff5cb8', wVa: '#d1d4dc', text: '#9ca3af',
  };

  // ---- the row's buckets: coarse (30 min) then fine (1 min), one sorted list
  function decode(vp) {
    const t = [], e = [], lo = [], off = [], n = [], V = [];
    [vp.c, vp.f].forEach(p => {
      let tt = p.t0, ll = p.b, k = 0, r = p.r;
      while (k < r.length) {
        tt += r[k] * 60; ll += r[k + 1]; const m = r[k + 2];
        t.push(tt); e.push(tt + p.dur); lo.push(ll); off.push(V.length); n.push(m);
        for (let q = 0; q < m; q++) V.push(r[k + 3 + q]);
        k += 3 + m;
      }
    });
    return { t, e, lo, off, n, V, first: t.length ? t[0] : Infinity };
  }
  function buckets(i) {
    const st = _rp(i);
    if (!st.data || !st.data.vp) return null;
    if (!st.vpB) st.vpB = decode(st.data.vp);
    return st.vpB;
  }
  // volume at price of every bucket with a <= start and end <= b
  function profile(B, a, b) {
    let k = _lb(B.t, a), mn = Infinity, mx = -Infinity;
    const k0 = k;
    for (; k < B.t.length && B.t[k] < b; k++) if (B.e[k] <= b) { mn = Math.min(mn, B.lo[k]); mx = Math.max(mx, B.lo[k] + B.n[k] - 1); }
    if (!isFinite(mn)) return null;
    const v = new Float64Array(mx - mn + 1);
    let tot = 0;
    for (k = k0; k < B.t.length && B.t[k] < b; k++) {
      if (B.e[k] > b) continue;
      const o = B.off[k], d = B.lo[k] - mn;
      for (let q = 0; q < B.n[k]; q++) { v[d + q] += B.V[o + q]; tot += B.V[o + q]; }
    }
    return { lo: mn, v, tot, a, b };
  }
  function clip(P, cLo, cHi) {                       // rows cLo..cHi (ticks) only
    if (!P) return null;
    const a = Math.max(P.lo, cLo), b = Math.min(P.lo + P.v.length - 1, cHi);
    if (b < a) return null;
    const v = P.v.slice(a - P.lo, b - P.lo + 1);
    return { lo: a, v, tot: v.reduce((s, x) => s + x, 0), a: P.a, b: P.b };
  }
  function valueArea(P, pct) {
    const v = P.v, n = v.length, mid = (n - 1) / 2;
    let poc = 0;
    for (let k = 1; k < n; k++) if (v[k] > v[poc] || (v[k] === v[poc] && Math.abs(k - mid) < Math.abs(poc - mid))) poc = k;
    let lo = poc, hi = poc, acc = v[poc];
    const tgt = P.tot * pct / 100;
    while (acc < tgt && (lo > 0 || hi < n - 1)) {
      const up = (hi + 1 < n ? v[hi + 1] : 0) + (hi + 2 < n ? v[hi + 2] : 0);
      const dn = (lo - 1 >= 0 ? v[lo - 1] : 0) + (lo - 2 >= 0 ? v[lo - 2] : 0);
      const goUp = hi < n - 1 && (lo === 0 || up >= dn), goDn = lo > 0 && (hi === n - 1 || dn >= up);
      if (goUp) for (let s = 0; s < 2 && hi < n - 1; s++) acc += v[++hi];
      if (goDn) for (let s = 0; s < 2 && lo > 0; s++) acc += v[--lo];
    }
    return { poc: (P.lo + poc) * 0.25, vah: (P.lo + hi) * 0.25, val: (P.lo + lo) * 0.25 };
  }
  // high / low volume nodes: peaks and troughs of the profile smoothed over +-1 pt, at least 2 pt from a higher (lower) one
  function nodes(P) {
    const v = P.v, n = v.length, sm = new Float64Array(n), W = 4, R = 8;
    for (let k = 0; k < n; k++) { let s = 0, w = 0; for (let d = -W; d <= W; d++) { const j = k + d; if (j >= 0 && j < n) { const ww = W + 1 - Math.abs(d); s += v[j] * ww; w += ww; } } sm[k] = s / w; }
    const mx = Math.max.apply(null, sm), hv = [], lv = [];
    for (let k = 0; k < n; k++) {
      let isMax = true, isMin = true;
      for (let d = -R; d <= R; d++) { const j = k + d; if (j < 0 || j >= n || d === 0) continue; if (sm[j] > sm[k]) isMax = false; if (sm[j] < sm[k]) isMin = false; }
      if (isMax && sm[k] >= 0.3 * mx) hv.push(k);
      else if (isMin && k > R && k < n - 1 - R) lv.push(k);
    }
    const lvn = lv.filter(k => {                     // a trough between two high nodes, under half the smaller of them
      const a = hv.filter(h => h < k).pop(), b = hv.find(h => h > k);
      return a != null && b != null && sm[k] <= 0.5 * Math.min(sm[a], sm[b]);
    });
    return { hvn: hv.map(k => (P.lo + k) * 0.25), lvn: lvn.map(k => (P.lo + k) * 0.25) };
  }

  // ---- trading days (15:00 PT the day before to 14:00 PT) and weeks (Sunday 15:00 PT to Friday 14:00 PT)
  const day0 = (t) => _bucket(t, 'D1');
  const weekday = (s) => new Date((s + 9 * 3600 + _ptOff(s)) * 1000).getUTCDay();   // of the trading day the session belongs to
  const prevDay = (s) => day0(s - 3600);
  const fmtD = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Los_Angeles', month: 'numeric', day: 'numeric' });
  const dLabel = (s) => fmtD.format(new Date((s + 9 * 3600) * 1000));
  function tradingDaysBack(s, n) {                   // the session n trading days before session s
    while (n > 0) { s = prevDay(s); const w = weekday(s); if (w !== 0 && w !== 6) n--; }
    return s;
  }
  const weekStart = (s) => day0(s - ((weekday(s) + 6) % 7) * 86400 + 7200);   // Monday's session (a weekend bucket: that week's)

  // has price traded at px from a to b? M1 bars where the row has them, H1 bars before that
  function traded(i, px, a, b) {
    const D = _rp(i).data, m0 = D.m1t[0];
    if (a < m0) {
      for (let k = _lb(D.h1t, a); k < D.h1.length && D.h1[k].time < Math.min(b, m0); k++) if (D.h1[k].low <= px && px <= D.h1[k].high) return true;
    }
    for (let k = _lb(D.m1t, Math.max(a, m0)); k < D.m1.length && D.m1[k].time + 60 <= b; k++) if (D.m1[k].low <= px && px <= D.m1[k].high) return true;
    return false;
  }
  function lastPrice(i, T) {
    const D = _rp(i).data, k = _lb(D.m1t, T) - 1;
    return k >= 0 ? D.m1[k].close : null;
  }
  // the previous visit (render_cross_bar_trades.last_visit, H1 bars as they stood at T): [start, end) or null
  function visit(i, T) {
    const H = _barsAt(i, 'H1'), d0 = day0(T), k0 = _lb(H.map(c => c.time), d0);
    if (k0 >= H.length || k0 === 0) return null;
    const p = H[k0].open, inb = (c) => c.low <= p + 50 && c.high >= p - 50;
    let k = k0 - 1;
    while (k >= 0 && inb(H[k])) k--;
    while (k >= 0 && !inb(H[k])) k--;
    if (k < 0) return null;
    const kEnd = k;
    while (k >= 0 && inb(H[k])) k--;
    const start = Math.max(day0(H[k + 1].time), tradingDaysBack(day0(H[kEnd].time), 4));
    return { a: start, b: H[kEnd].time + 3600, p };
  }

  // ---- what one pane shows at T (cached; complete periods never change)
  function compute(i, tf) {
    const st = _rp(i), B = buckets(i), T = st.T;
    if (!B) return null;
    const ck = T + '|' + tf + '|' + JSON.stringify(S);
    if (st.vpC && st.vpC.k === ck) return st.vpC.v;
    const memo = st.vpMemo = st.vpMemo || {};
    const full = (a, b) => { const k = a + '-' + b; if (!(k in memo)) { const P = a >= B.first ? profile(B, a, b) : null; memo[k] = P ? { P, va: valueArea(P, S.va) } : null; } return memo[k]; };
    const out = { sessions: [], right: null, levels: [], bands: [] };
    const s0 = day0(T), sub = _LEN[tf] < 14400;
    if (S.day && sub) {
      const P = profile(B, s0, T);
      if (P) out.sessions.push({ P, va: valueArea(P, S.va), a: s0, b: s0 + 82800, live: true });
      if (S.past) for (let s = prevDay(s0), c = 0; s >= B.first && c < 40; s = prevDay(s), c++) {
        const f = full(s, s + 86400);
        if (f) out.sessions.push({ P: f.P, va: f.va, a: s, b: s + 82800 });
      }
    }
    const px = lastPrice(i, T);
    if (S.right !== 'none' && px != null) {
      let a = null, b = T, name = '';
      if (S.right === 'comp') {
        if (S.look === 'visit') { const v = visit(i, T); a = v ? v.a : null; name = 'composite since last visit'; }
        else { a = tradingDaysBack(s0, +S.look); name = 'composite ' + S.look + ' days'; }
        name += ' ±' + S.band;
      } else if (S.right === 'visit') {
        const v = visit(i, T);
        if (v) { a = v.a; b = v.b; name = 'last visit ' + dLabel(v.a) + (day0(v.b - 1) !== v.a ? '-' + dLabel(day0(v.b - 1)) : ''); }
      } else {
        const Z = ((st.data.vp.zz || {})[S.legTf.toLowerCase()] || []).filter(z => z[3] <= T);
        const len = S.legTf === 'H1' ? 3600 : 300;
        if (S.right === 'leg' && Z.length >= 2) { const p = Z[Z.length - 2], q = Z[Z.length - 1]; a = p[0]; b = q[0] + len; name = S.legTf + ' leg ' + p[1].toFixed(2) + ' → ' + q[1].toFixed(2); }
        if (S.right === 'curleg' && Z.length) { const q = Z[Z.length - 1]; a = q[0]; name = S.legTf + ' current leg from ' + q[1].toFixed(2); }
      }
      if (a != null && a >= B.first) {
        let P = profile(B, a, b);
        if (P && S.right === 'comp') P = clip(P, Math.round((px - S.band) / 0.25), Math.round((px + S.band) / 0.25));
        if (P && P.tot > 0) out.right = { P, va: valueArea(P, S.va), nodes: S.right === 'comp' ? nodes(P) : null, name };
      } else if (S.right !== 'none') out.right = { name: (name || S.right) + ': not in the data' };
    }
    const lv = [];
    for (let s = prevDay(s0), c = 0; c < S.dN && s >= B.first; s = prevDay(s)) {
      const f = full(s, s + 86400);
      if (!f) continue;
      c++;
      const lab = dLabel(s);
      [['POC', f.va.poc], ['VAH', f.va.vah], ['VAL', f.va.val]].forEach(([k, p]) => lv.push({ k: 'd' + k, p, from: s + 86400, lab: 'd' + k + ' ' + lab, w: 1 }));
    }
    for (let w0 = weekStart(s0), c = 0; c < S.wN; c++) {
      w0 = weekStart(prevDay(w0));
      if (w0 < B.first) break;
      const f = full(w0, w0 + 5 * 86400);
      if (!f) continue;
      const lab = dLabel(w0);
      [['POC', f.va.poc], ['VAH', f.va.vah], ['VAL', f.va.val]].forEach(([k, p]) => lv.push({ k: 'w' + k, p, from: w0 + 5 * 86400, lab: 'w' + k + ' ' + lab, w: 2 }));
    }
    lv.forEach(l => { l.naked = !traded(i, l.p, l.from, T); });
    out.levels = lv.filter(l => l.naked || !S.naked);
    const srt = out.levels.slice().sort((x, y) => x.p - y.p);    // confluence: lines of different periods within the tolerance
    for (let k = 0; k < srt.length;) {
      let j = k;
      while (j + 1 < srt.length && srt[j + 1].p - srt[j].p <= S.tol) j++;
      const g = srt.slice(k, j + 1), per = new Set(g.map(l => l.lab.split(' ')[1] + l.k[0]));
      if (per.size >= 2) out.bands.push({ lo: g[0].p, hi: g[g.length - 1].p, n: g.length, labs: g.map(l => l.lab) });
      k = j + 1;
    }
    st.vpC = { k: ck, v: out };
    return out;
  }

  // ---- drawing
  function draw(pv, ctx) {
    const st = _rp(pv.i);
    if (!st.data) return;
    const tf = st.tf[pv.pane], cd = pv.cd, R = compute(pv.i, tf);
    if (!R) return;
    const chart = pv.chart, series = pv.series, ts = chart.timeScale();
    const W = ts.width(), H = pv.el.clientHeight - 28;
    const bars = cd.live || cd.candles, times = bars.map(c => c.time), n = times.length, len = _LEN[tf];
    const xOf = (t) => {
      let k = _lb(times, t), lg;
      if (k < n && times[k] === t) lg = k;
      else if (k >= n) lg = n - 1 + (t - times[n - 1]) / len;
      else if (k === 0) lg = -(times[0] - t) / len;
      else lg = Math.min(k, k - 1 + (t - times[k - 1]) / len);
      return ts.logicalToCoordinate(lg);
    };
    const y = (p) => series.priceToCoordinate(p);
    const rowPx = Math.abs(y(100) - y(100.25)) || 1, bin = Math.max(1, Math.ceil(1.6 / rowPx));
    const font = '11px Courier New, monospace';
    ctx.font = font; ctx.textBaseline = 'middle';
    // one profile: bars from x0 growing by dir (+1 right, -1 left), widest bar = wMax px
    const prof = (P, va, x0, dir, wMax) => {
      const v = P.v, groups = [];
      let mx = 0;
      const g0 = Math.floor(P.lo / bin);
      for (let k = 0; k < v.length; k++) {
        const g = Math.floor((P.lo + k) / bin) - g0;
        groups[g] = (groups[g] || 0) + v[k];
      }
      groups.forEach(x => { mx = Math.max(mx, x || 0); });
      const pocG = Math.floor(va.poc / 0.25 / bin) - g0;
      groups.forEach((x, g) => {
        if (!x) return;
        const tLo = (g + g0) * bin, tHi = tLo + bin - 1, yT = y((tHi + 0.5) * 0.25), yB = y((tLo - 0.5) * 0.25);
        if (yT == null || yB == null) return;
        const w = x / mx * wMax, inVa = tHi * 0.25 >= va.val && tLo * 0.25 <= va.vah;
        ctx.fillStyle = g === pocG ? C.poc : inVa ? C.va : C.out;
        ctx.fillRect(dir > 0 ? x0 : x0 - w, Math.min(yT, yB) + 0.5, w, Math.max(1, Math.abs(yB - yT) - 1));
      });
    };
    const hline = (p, x0, x1, color, width, dash) => {
      const yy = y(p);
      if (yy == null) return;
      ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash(dash || []);
      ctx.beginPath(); ctx.moveTo(x0, Math.round(yy) + 0.5); ctx.lineTo(x1, Math.round(yy) + 0.5); ctx.stroke(); ctx.setLineDash([]);
    };
    // day profiles where they happened
    R.sessions.forEach(s => {
      const xa = xOf(s.a), xb = xOf(s.b);
      if (xa == null || xb == null || xb < 0 || xa > W) return;
      const wMax = Math.min((xb - xa) * 0.6, W * 0.3);
      if (wMax < 6) return;
      prof(s.P, s.va, xa, 1, wMax);
      const x1 = s.live ? Math.max(ts.logicalToCoordinate(n - 1) || 0, xa + wMax) : xb;   // today's lines run to the last candle
      hline(s.va.poc, xa, x1, C.pocLine, 1);
      hline(s.va.vah, xa, x1, C.vaLine, 1, [4, 3]);
      hline(s.va.val, xa, x1, C.vaLine, 1, [4, 3]);
    });
    // confluence bands, then the period levels
    const used = [];
    const label = (txt, yy, color, x) => {
      if (yy == null || used.some(u => Math.abs(u - yy) < 11)) return;
      used.push(yy);
      ctx.fillStyle = color; ctx.textAlign = 'right'; ctx.fillText(txt, x, yy - 6);
    };
    const xR = W - 2, xL = R.right && R.right.P ? xR - W * 0.17 - 4 : xR;
    R.bands.forEach(b => {
      const yT = y(b.hi + 0.25), yB = y(b.lo - 0.25);
      if (yT == null || yB == null) return;
      ctx.fillStyle = 'rgba(255,255,255,0.10)'; ctx.fillRect(0, yT, W, yB - yT);
      label('×' + b.n + ' ' + b.labs.join(' / '), yT + 2, '#ffffff', xL);
    });
    R.levels.forEach(l => {
      const xa = Math.max(0, xOf(l.from) || 0), wk = l.k[0] === 'w';
      const col = l.k.endsWith('POC') ? (wk ? C.wPoc : C.pocLine) : (wk ? C.wVa : C.vaLine);
      ctx.globalAlpha = l.naked ? 0.9 : 0.45;
      hline(l.p, xa, xR, col, l.w, l.naked ? [] : [1, 3]);
      ctx.globalAlpha = 1;
      label(l.lab + (l.naked ? '' : ' (traded)'), y(l.p), col, xL);
    });
    // the right-edge profile
    if (R.right) {
      ctx.fillStyle = C.text; ctx.textAlign = 'right';
      ctx.fillText(R.right.name, xR, 12);
      if (R.right.P) {
        const wMax = W * 0.17;
        prof(R.right.P, R.right.va, xR, -1, wMax);
        const xa = xR - wMax;
        hline(R.right.va.poc, xa, xR, C.pocLine, 1);
        hline(R.right.va.vah, xa, xR, C.vaLine, 1, [4, 3]);
        hline(R.right.va.val, xa, xR, C.vaLine, 1, [4, 3]);
        [['POC', R.right.va.poc, C.pocLine], ['VAH', R.right.va.vah, C.vaLine], ['VAL', R.right.va.val, C.vaLine]].forEach(([k, p, c]) => label(k + ' ' + p.toFixed(2), y(p) + 6, c, xR));
        if (R.right.nodes) {
          const mark = (p, c, k) => { const yy = y(p); if (yy == null) return; ctx.fillStyle = c; ctx.fillRect(xR - 8, yy - 1, 8, 2); label(k + ' ' + p.toFixed(2), yy + 6, c, xR - 10); };
          R.right.nodes.hvn.forEach(p => mark(p, C.hvn, 'HVN'));
          R.right.nodes.lvn.forEach(p => mark(p, C.lvn, 'LVN'));
        }
      }
    }
  }

  // ---- settings popover (the VP button on each chart's title bar)
  function popover(btn) {
    document.querySelectorAll('.vp-pop').forEach(p => p.remove());
    const box = document.createElement('div');
    box.className = 'vp-pop';
    const sel = (f, opts) => '<select data-f="' + f + '">' + opts.map(([v, t]) => '<option value="' + v + '"' + (String(S[f]) === String(v) ? ' selected' : '') + '>' + t + '</option>').join('') + '</select>';
    const chk = (f, t) => '<label><input type="checkbox" data-f="' + f + '"' + (S[f] ? ' checked' : '') + '> ' + t + '</label>';
    box.innerHTML = '<div class="vp-h">Volume profile (sample)</div>'
      + '<div>' + chk('day', 'Day: today\'s developing profile') + '</div>'
      + '<div>' + chk('past', '+ past days\' profiles') + '</div>'
      + '<div>Right side ' + sel('right', [['none', 'none'], ['comp', 'composite ±band'], ['visit', 'last visit'], ['leg', 'last zigzag leg'], ['curleg', 'current zigzag leg']]) + '</div>'
      + '<div>Composite lookback ' + sel('look', [['5', '5 days'], ['10', '10 days'], ['20', '20 days'], ['visit', 'since last visit']]) + ' band ±' + sel('band', [[25, 25], [50, 50], [100, 100]]) + '</div>'
      + '<div>Zigzag ' + sel('legTf', [['M5', 'M5'], ['H1', 'H1']]) + '</div>'
      + '<div>Levels: last ' + sel('dN', [[0, 0], [1, 1], [3, 3], [5, 5], [10, 10]]) + ' days, ' + sel('wN', [[0, 0], [1, 1], [2, 2], [4, 4]]) + ' weeks</div>'
      + '<div>' + chk('naked', 'naked levels only') + ' confluence ±' + sel('tol', [[0.5, '0.5'], [1, '1'], [2, '2']]) + ' pt</div>'
      + '<div>Value area ' + sel('va', [[68, '68%'], [70, '70%']]) + '</div>';
    const r = btn.getBoundingClientRect();
    document.body.appendChild(box);
    box.style.left = (Math.max(8, Math.min(r.left, window.innerWidth - box.offsetWidth - 8)) + window.scrollX) + 'px';
    box.style.top = (r.bottom + window.scrollY + 4) + 'px';
    box.addEventListener('change', (e) => {
      const f = e.target.dataset.f;
      if (!f) return;
      S[f] = e.target.type === 'checkbox' ? e.target.checked : (isNaN(+e.target.value) ? e.target.value : +e.target.value);
      save();
      panes.forEach(p => p.update && p.update());
    });
    setTimeout(() => document.addEventListener('pointerdown', function off(e) {
      if (!box.contains(e.target) && e.target !== btn) { box.remove(); document.removeEventListener('pointerdown', off, true); }
    }, true), 0);
  }
  const css = document.createElement('style');
  css.textContent = '.vp-pop{position:absolute;z-index:50;background:#131722;border:1px solid #363a45;border-radius:6px;padding:8px 10px;'
    + 'font:12px Courier New,monospace;color:#d1d4dc;display:flex;flex-direction:column;gap:6px;box-shadow:0 4px 18px rgba(0,0,0,.5)}'
    + '.vp-pop select{background:#1b2330;color:#e5e7eb;border:1px solid #334155;border-radius:3px;font:12px Courier New,monospace}'
    + '.vp-pop .vp-h{font-weight:bold;color:#fff}';
  document.head.appendChild(css);

  window.VolProfile = {
    settings: S,
    refresh: () => panes.forEach(p => p.update && p.update()),
    attach(chart, series, el, titleEl, cd) {
      const m = /^c-(a|b)-(\d+)$/.exec(el.id || '');
      if (!m) return;
      const pv = { chart, series, el, cd, pane: m[1], i: +m[2], update: null };
      for (let k = panes.length - 1; k >= 0; k--) if (panes[k].el.id === el.id) panes.splice(k, 1);
      panes.push(pv);
      series.attachPrimitive({
        attached(p) { pv.update = p.requestUpdate; },
        detached() { pv.update = null; },
        updateAllViews() {},
        paneViews() {
          return [{ zOrder: () => 'bottom',
                    renderer: () => ({ draw: (target) => target.useMediaCoordinateSpace(({ context }) => { try { draw(pv, context); } catch (e) { console.error(e); } }) }) }];
        },
      });
      const bar = titleEl.querySelector('.pos-bar') || titleEl;
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'pos-btn'; b.textContent = 'VP'; b.title = 'Volume profile settings';
      b.addEventListener('click', (e) => { e.stopPropagation(); popover(b); });
      bar.insertBefore(b, bar.querySelector('.fs-btn') || null);
    },
    _test: { valueArea, profile, compute, visit },
  };
})();
