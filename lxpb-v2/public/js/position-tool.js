// Long / short position tool for the report charts (lightweight-charts 4.1.3), modelled on TradingView's.
//
// PosTool.attach(chart, series, el, titleEl, cd) puts the tool on one pane (el.id = c-<pane>-<row>). Each table row has ONE box
// (its manual trade), shared by every pane of the row named in config.shared: it is drawn on all of them and can be moved on any
// of them; panes named in config.mirror only draw it. The box is kept in time and price, not candle positions, so an H1, an M5
// and an M2 pane show it at the same moment: { side, t0, t1 (left / right edge, epoch s), entry, sl, tp, from, pane }.
//
// Click Long / Short, then a candle: the box is placed with its entry on that candle (target 2R, stop 1.5 x the average bar
// range away, 20 candles wide). Drag the target / stop / entry lines to move them, the right edge to widen it, inside the box to
// move the whole box. While dragging, prices stick to the nearest open / high / low / close of the candle under the cursor
// ("strong" magnet: always; "weak": within 12 px; hold Ctrl to drag freely); time edges stick to candles. Click a box to select
// it, Delete removes it, double-click opens its numbers. A pane drawn again on the same element (cd.tfName: another
// timeframe) gets a fresh tool, and cd.barSec gives the candle length when the candles alone cannot. Boxes are drawn by a series primitive (the library's plugin hook), so
// they pan / zoom with the chart, and are kept in this browser's localStorage.
//
// The entry is a PRICE, not a time: the trade is entered when price trades at it again from `from` on = the end of the candle
// the box sits on, or the replay time when that candle is still forming (config.cursor). How the trade went is the page's job
// (config.result(row, pane) = { state 'waiting' / 'open' / 'closed', fillT, fillPx, exitT, exitPx, lastT, lastPx }); the box
// shades the stretch from the fill to the exit (or to the last price).
//
// The box itself carries no text (user, 2026-11-03): its numbers show in one small panel beside it (right of the box, left when
// there is no room) while the box is hovered or dragged: target / stop = points and ticks, and $ at the risk $ of the settings
// (Qty = risk $ / (stop points x $50), ES: 0.25 tick, $50 a point), and the risk / reward ratio.
(function () {
  const PT_VALUE = 50, TICK = 0.25, MAGNET_PX = 12, HANDLE_PX = 6;
  const LS = 'postool_v1_';
  const lsGet = (k, d) => { try { const v = localStorage.getItem(LS + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } };
  const lsSet = (k, v) => { try { localStorage.setItem(LS + k, JSON.stringify(v)); } catch (e) { /* private window */ } };
  const settings = Object.assign({ account: 50000, riskPct: 1, magnet: 'strong' }, lsGet('settings', {}));
  const tools = [];
  const config = { shared: [], mirror: [], cursor: () => null, result: () => null, onChange: null };
  let active = null;                       // the tool last clicked: Delete / Esc go to it
  const roundTick = (p) => Math.round(p / TICK) * TICK;
  const num = (v, d) => v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  const FILE = location.pathname.split('/').pop();
  const rowKey = (i) => { const r = document.querySelector('tr.lvl-row[data-idx="' + i + '"]'); return r && r.dataset.key ? r.dataset.key : String(i); };
  const boxKey = (i) => 'box_' + FILE + '_' + rowKey(i);
  const boxes = {};
  const getBox = (i) => (i in boxes ? boxes[i] : (boxes[i] = lsGet(boxKey(i), null)));
  function setBox(i, b) {
    boxes[i] = b;
    lsSet(boxKey(i), b);
    tools.forEach(t => { if (t.row === i && t.requestUpdate) t.requestUpdate(); });
    if (config.onChange) config.onChange(i);
  }
  const lastAtOrBefore = (bars, t) => {          // index of the last bar starting at or before t (-1 if none)
    let lo = 0, hi = bars.length - 1, out = -1;
    while (lo <= hi) { const m = (lo + hi) >> 1; if (bars[m].time <= t) { out = m; lo = m + 1; } else hi = m - 1; }
    return out;
  };

  function panel(ctx, rows, x0, x1, ymid, wMax) {       // [text, colour] rows in one box beside x0..x1, centred on ymid
    ctx.font = '12px Courier New, monospace';
    const w = Math.max(...rows.map(r => ctx.measureText(r[0]).width)) + 14, lh = 15, h = rows.length * lh + 8;
    const x = x1 + 8 + w <= wMax ? x1 + 8 : x0 - 8 - w >= 0 ? x0 - 8 - w : Math.max(0, wMax - w), top = ymid - h / 2;
    ctx.fillStyle = 'rgba(19, 23, 34, 0.92)'; ctx.strokeStyle = '#363a45'; ctx.lineWidth = 1;
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(x, top, w, h, 3); else ctx.rect(x, top, w, h);
    ctx.fill(); ctx.stroke();
    ctx.textAlign = 'left'; ctx.textBaseline = 'top';
    rows.forEach(([t, c], j) => { ctx.fillStyle = c; ctx.fillText(t, x + 7, top + 5 + j * lh); });
  }

  class Tool {
    constructor(chart, series, el, titleEl, cd) {
      this.chart = chart; this.series = series; this.el = el; this.cd = cd;
      const m = /^c-(\w+)-(\d+)$/.exec(el.id || '');
      this.row = m ? +m[2] : el.id; this.pane = m ? m[1] : '';
      this.edit = config.shared.indexOf(this.pane) >= 0;
      this.show = this.edit || config.mirror.indexOf(this.pane) >= 0;
      let bs = Infinity;                                   // bar length in seconds: the pane's own, else the smallest step between candles
      for (let k = 1; k < Math.min(cd.candles.length, 40); k++) bs = Math.min(bs, cd.candles[k].time - cd.candles[k - 1].time);
      this.barSec = cd.barSec || (isFinite(bs) && bs > 0 ? bs : 60);
      // a pane drawn again (another timeframe) replaces its old tool
      tools.filter(t => t.el.id === el.id).forEach(t => { t.dead = true; tools.splice(tools.indexOf(t), 1); });
      this.sel = false; this.hover = false; this.arm = null; this.drag = null; this.requestUpdate = null;
      if (!this.show) return;
      const tool = this;
      series.attachPrimitive({
        attached(p) { tool.requestUpdate = p.requestUpdate; },
        detached() { tool.requestUpdate = null; },
        updateAllViews() {},
        paneViews() {
          return [{ zOrder: () => 'top',
                    renderer: () => ({ draw: (target) => target.useMediaCoordinateSpace(({ context }) => tool.draw(context)) }) }];
        },
        // entry / target / stop prices as tags on the right-hand price axis, like TradingView's
        priceAxisViews() {
          const p = getBox(tool.row), out = [];
          if (p) [[p.tp, '#26a69a'], [p.entry, '#787b86'], [p.sl, '#ef5350']].forEach(([v, bg]) => {
            const y = tool.y(v);
            if (y != null) out.push({ coordinate: () => y, text: () => v.toFixed(2), textColor: () => '#ffffff', backColor: () => bg,
                                      visible: () => true, tickVisible: () => true });
          });
          return out;
        },
      });
      if (this.edit) { this.buttons(titleEl); this.events(); }
      tools.push(this);
    }
    get candles() { return this.cd.live || this.cd.candles; }   // a replay swaps in the candles up to its time
    y(p) { return this.series.priceToCoordinate(p); }
    // time <-> logical position: inside a candle by the fraction of its length, past the last candle extrapolated
    logicalOf(t) {
      const c = this.candles, n = c.length, k = lastAtOrBefore(c, t);
      if (k < 0) return (t - c[0].time) / this.barSec;
      return k + (k === n - 1 ? (t - c[k].time) / this.barSec : Math.min(1, (t - c[k].time) / this.barSec));
    }
    timeOf(l) {
      const c = this.candles, n = c.length, k = Math.floor(l), f = l - k;
      if (k < 0) return c[0].time + l * this.barSec;
      if (k >= n - 1) return c[n - 1].time + (l - (n - 1)) * this.barSec;
      return c[k].time + f * Math.min(this.barSec, c[k + 1].time - c[k].time);
    }
    x(t) {                                        // the library maps whole positions only: interpolate between two
      const ts = this.chart.timeScale(), l = this.logicalOf(t), k = Math.floor(l), f = l - k;
      const a = ts.logicalToCoordinate(k);
      if (a == null || f < 1e-9) return a;
      const b = ts.logicalToCoordinate(k + 1);
      return b == null ? a : a + f * (b - a);
    }
    // the entry watch starts at the end of the candle the box's left edge sits on, or now when that candle is still forming
    fromFor(t0) {
      const cur = config.cursor(this.row);
      const k = lastAtOrBefore(this.candles, t0);
      const end = (k >= 0 ? this.candles[k].time : t0) + this.barSec;
      return cur == null ? end : Math.min(end, cur);
    }

    buttons(titleEl) {
      // the title is a 3-column grid with the full-screen button in column 3: the tool's buttons share that cell
      const fs = titleEl.querySelector('.fs-btn');
      const bar = document.createElement('span');
      bar.className = 'pos-bar';
      titleEl.insertBefore(bar, fs);
      if (fs) bar.appendChild(fs);
      const mk = (text, title, fn) => {
        const b = document.createElement('button');
        b.type = 'button'; b.className = 'pos-btn'; b.textContent = text; b.title = title;
        b.addEventListener('click', (e) => { e.stopPropagation(); fn(b); });
        b.addEventListener('dblclick', (e) => e.stopPropagation());
        bar.insertBefore(b, fs || null);
        return b;
      };
      this.bLong = mk('▲ Long', 'Long position: click, then a candle (one per row: replaces the one drawn)', () => this.armFor('long'));
      this.bShort = mk('▼ Short', 'Short position: click, then a candle (one per row: replaces the one drawn)', () => this.armFor('short'));
      this.bMag = mk('', 'Magnet: strong (always snaps to O/H/L/C) / weak (within 12 px) / off. Hold Ctrl to drag freely', () => {
        settings.magnet = { strong: 'weak', weak: 'off', off: 'strong' }[settings.magnet];
        lsSet('settings', settings);
        tools.forEach(t => t.syncButtons());
      });
      mk('⚙', 'Account size and risk', (b) => this.popover(b, false));
      this.syncButtons();
    }
    syncButtons() {
      if (!this.bMag) return;
      this.bMag.textContent = '⌖ magnet: ' + settings.magnet;
      this.bLong.classList.toggle('pos-on', this.arm === 'long');
      this.bShort.classList.toggle('pos-on', this.arm === 'short');
    }
    armFor(side) { this.arm = this.arm === side ? null : side; active = this; this.syncButtons(); }

    plotPoint(ev) {
      const r = this.el.getBoundingClientRect(), ts = this.chart.timeScale();
      const x = ev.clientX - r.left, y = ev.clientY - r.top;
      return x >= 0 && x <= ts.width() && y >= 0 && y <= r.height - ts.height() ? { x, y } : null;
    }
    idxAt(x) {
      const l = this.chart.timeScale().coordinateToLogical(x);
      return l == null ? null : Math.round(l);
    }
    // the price under the cursor, stuck to the nearest O/H/L/C of the candle under it when the magnet says so
    snap(x, y, free) {
      const raw = this.series.coordinateToPrice(y);
      if (raw == null) return null;
      const i = this.idxAt(x);
      if (free || settings.magnet === 'off' || i == null || i < 0 || i >= this.candles.length) return roundTick(raw);
      const c = this.candles[i];
      let best = null, bd = Infinity;
      [c.open, c.high, c.low, c.close].forEach(v => { const d = Math.abs(this.y(v) - y); if (d < bd) { bd = d; best = v; } });
      return settings.magnet === 'strong' || bd <= MAGNET_PX ? best : roundTick(raw);
    }

    place(pt, free) {
      const c = this.candles, n = c.length;
      const i = Math.max(0, Math.min(n - 1, this.idxAt(pt.x)));
      const entry = this.snap(pt.x, pt.y, free);
      const near = c.slice(Math.max(0, i - 20), i + 1);
      const avg = near.reduce((s, b) => s + b.high - b.low, 0) / Math.max(1, near.length);
      const d = Math.max(1, roundTick(avg * 1.5)), sign = this.arm === 'long' ? 1 : -1;
      const t0 = c[i].time;
      setBox(this.row, { side: this.arm, t0, t1: this.timeOf(i + 20), entry, sl: entry - sign * d, tp: entry + sign * 2 * d,
                         from: this.fromFor(t0), pane: this.cd.tfName || this.pane });
      this.sel = true;
      this.arm = null;
      this.syncButtons();
    }

    geom(p) {
      const x0 = this.x(p.t0), x1 = this.x(p.t1), ye = this.y(p.entry), yt = this.y(p.tp), ys = this.y(p.sl);
      return [x0, x1, ye, yt, ys].some(v => v == null) ? null : { x0, x1, ye, yt, ys };
    }
    hit(x, y) {
      const p = getBox(this.row), g = p && this.geom(p);
      if (!g || x < g.x0 - HANDLE_PX || x > g.x1 + HANDLE_PX) return null;
      const top = Math.min(g.yt, g.ys), bot = Math.max(g.yt, g.ys);
      if (Math.abs(x - g.x1) <= HANDLE_PX && y >= top && y <= bot) return 'right';
      if (Math.abs(y - g.yt) <= HANDLE_PX) return 'tp';
      if (Math.abs(y - g.ys) <= HANDLE_PX) return 'sl';
      if (Math.abs(y - g.ye) <= HANDLE_PX && x <= g.x0 + 3 * HANDLE_PX) return 'entry';
      if (y > top && y < bot) return 'body';
      return null;
    }

    draw(ctx) {
      const p = getBox(this.row), g = p && this.geom(p);
      if (!g) return;
      const riskPts = Math.abs(p.entry - p.sl), rewPts = Math.abs(p.tp - p.entry);
      const riskUsd = settings.account * settings.riskPct / 100;
      const qty = riskPts > 0 ? riskUsd / (riskPts * PT_VALUE) : 0;
      const w = g.x1 - g.x0;
      ctx.fillStyle = 'rgba(38, 166, 154, 0.22)';
      ctx.fillRect(g.x0, Math.min(g.ye, g.yt), w, Math.abs(g.yt - g.ye));
      ctx.fillStyle = 'rgba(239, 83, 80, 0.22)';
      ctx.fillRect(g.x0, Math.min(g.ye, g.ys), w, Math.abs(g.ys - g.ye));
      const r = config.result(this.row, this.pane);
      if (r && r.fillT != null) {                      // the stretch the trade has run: shaded, dashed fill -> exit / last price
        const endT = r.state === 'closed' ? r.exitT : r.lastT, endPx = r.state === 'closed' ? r.exitPx : r.lastPx;
        const xf = this.x(r.fillT), xe = this.x(endT), yf = this.y(r.fillPx), yx = this.y(endPx);
        if (![xf, xe, yf, yx].some(v => v == null)) {
          const up = (p.side === 'long' ? 1 : -1) * (endPx - r.fillPx) >= 0;
          ctx.fillStyle = up ? 'rgba(38, 166, 154, 0.30)' : 'rgba(239, 83, 80, 0.30)';
          ctx.fillRect(xf, Math.min(yf, yx), Math.max(1, xe - xf), Math.abs(yx - yf));
          ctx.strokeStyle = '#d1d4dc'; ctx.lineWidth = 1; ctx.setLineDash([4, 3]);
          ctx.beginPath(); ctx.moveTo(xf, yf); ctx.lineTo(xe, yx); ctx.stroke(); ctx.setLineDash([]);
          ctx.fillStyle = '#2962ff';
          ctx.beginPath(); ctx.arc(xf, yf, 3, 0, 2 * Math.PI); ctx.fill();
        }
      }
      ctx.strokeStyle = '#9ca3af'; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(g.x0, g.ye); ctx.lineTo(g.x1, g.ye); ctx.stroke();
      if (this.hover || this.drag) {
        const usd = (v) => '$' + num(Math.round(v), 0);
        const tgt = ['target ' + num(rewPts, 2) + ' pt  ' + num(rewPts / TICK, 0) + ' t  +' + usd(qty * rewPts * PT_VALUE), '#26a69a'];
        const stp = ['stop   ' + num(riskPts, 2) + ' pt  ' + num(riskPts / TICK, 0) + ' t  -' + usd(riskUsd), '#ef5350'];
        const rr = ['R:R    ' + num(riskPts > 0 ? rewPts / riskPts : 0, 2) + '  qty ' + num(qty, 2), '#d1d4dc'];
        panel(ctx, g.yt < g.ys ? [tgt, rr, stp] : [stp, rr, tgt], g.x0, g.x1, g.ye, this.chart.timeScale().width());
      }
      if (this.sel && this.edit) {
        [[g.x0, g.yt], [g.x0, g.ys], [g.x0, g.ye], [g.x1, g.ye]].forEach(([hx, hy]) => {
          ctx.fillStyle = '#131722'; ctx.strokeStyle = '#2962ff'; ctx.lineWidth = 2;
          ctx.beginPath(); ctx.arc(hx, hy, 5, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
        });
      }
    }

    events() {
      const el = this.el;
      const block = (e) => { e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation(); };
      el.addEventListener('pointerdown', (e) => {
        const pt = this.plotPoint(e);
        if (!pt) return;
        active = this;
        if (this.arm) { this.place(pt, e.ctrlKey); this.blockMouse = true; block(e); return; }
        const h = this.hit(pt.x, pt.y);
        if (!h) { if (this.sel) { this.sel = false; if (this.requestUpdate) this.requestUpdate(); } return; }
        this.sel = true;
        const p = getBox(this.row);
        this.drag = { part: h, p0: Object.assign({}, p), l0: this.chart.timeScale().coordinateToLogical(pt.x),
                      dy: pt.y - this.y(p.entry) };
        this.blockMouse = true;
        block(e);
        if (this.requestUpdate) this.requestUpdate();
      }, true);
      // the library pans on mousedown / touchstart: keep those from it while a box is being placed or dragged
      ['mousedown', 'touchstart'].forEach(t => el.addEventListener(t, (e) => { if (this.blockMouse) block(e); }, { capture: true, passive: false }));
      window.addEventListener('pointermove', (e) => {
        if (this.dead) return;
        if (!this.drag) {
          const pt = this.plotPoint(e);
          const h = pt && this.hit(pt.x, pt.y);
          const cur = this.arm && pt ? 'crosshair' : !h ? '' : h === 'right' ? 'ew-resize' : h === 'body' ? 'move' : 'ns-resize';
          el.querySelectorAll('canvas').forEach(c => { c.style.cursor = cur; });
          if (!!h !== this.hover) { this.hover = !!h; if (this.requestUpdate) this.requestUpdate(); }
          return;
        }
        const r = el.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
        const d = this.drag, p = Object.assign({}, getBox(this.row)), s = d.p0.side === 'long' ? 1 : -1;
        const minGap = TICK;
        if (d.part === 'right') {
          const i = this.idxAt(x);
          if (i != null) p.t1 = this.timeOf(Math.max(Math.round(this.logicalOf(p.t0)) + 1, i));
        } else if (d.part === 'body') {
          const l = this.chart.timeScale().coordinateToLogical(x);
          const di = l == null ? 0 : Math.round(l - d.l0);
          const ne = this.snap(x, y - d.dy, e.ctrlKey);
          if (ne != null) {
            const dp = ne - d.p0.entry;
            const i0 = Math.max(0, Math.min(this.candles.length - 1, Math.round(this.logicalOf(d.p0.t0)) + di));
            const t0 = this.candles[i0].time;
            Object.assign(p, { t0, t1: this.timeOf(this.logicalOf(d.p0.t1) + di), from: this.fromFor(t0), pane: this.cd.tfName || this.pane,
                               entry: d.p0.entry + dp, tp: d.p0.tp + dp, sl: d.p0.sl + dp });
          }
        } else {
          const v = this.snap(x, y, e.ctrlKey);
          if (v != null) {
            if (d.part === 'tp') p.tp = s > 0 ? Math.max(v, p.entry + minGap) : Math.min(v, p.entry - minGap);
            if (d.part === 'sl') p.sl = s > 0 ? Math.min(v, p.entry - minGap) : Math.max(v, p.entry + minGap);
            if (d.part === 'entry') {
              p.entry = s > 0 ? Math.min(Math.max(v, p.sl + minGap), p.tp - minGap) : Math.min(Math.max(v, p.tp + minGap), p.sl - minGap);
            }
          }
        }
        setBox(this.row, p);
      });
      window.addEventListener('pointerup', () => { this.drag = null; setTimeout(() => { this.blockMouse = false; }, 0); });
      el.addEventListener('dblclick', (e) => {
        const pt = this.plotPoint(e);
        if (!pt || !this.hit(pt.x, pt.y)) return;
        block(e);
        this.popover(null, true, e.clientX, e.clientY);
      }, true);
    }

    popover(btn, withBox, cx, cy) {
      document.querySelectorAll('.pos-pop').forEach(n => n.remove());
      const box = document.createElement('div');
      box.className = 'pos-pop';
      const r = btn ? btn.getBoundingClientRect() : { left: cx, bottom: cy };
      box.style.left = Math.min(r.left, window.innerWidth - 240) + 'px';
      box.style.top = (r.bottom + 4) + 'px';
      const fields = [['account', 'Account size $', settings.account], ['riskPct', 'Risk %', settings.riskPct]];
      const p = withBox ? getBox(this.row) : null;
      if (p) fields.push(['entry', 'Entry', p.entry], ['tp', 'Target', p.tp], ['sl', 'Stop', p.sl]);
      box.innerHTML = fields.map(([n, l, v]) => '<label>' + l + '<input data-f="' + n + '" type="number" step="any" value="' + v + '"></label>').join('')
        + '<div class="pos-pop-btns">' + (p ? '<button data-a="del">Delete</button>' : '') + '<button data-a="ok">Apply</button></div>';
      document.body.appendChild(box);
      const val = (n) => parseFloat(box.querySelector('[data-f="' + n + '"]').value);
      box.addEventListener('click', (e) => {
        const a = e.target.dataset && e.target.dataset.a;
        if (a === 'ok') {
          if (val('account') > 0) settings.account = val('account');
          if (val('riskPct') > 0) settings.riskPct = val('riskPct');
          lsSet('settings', settings);
          if (p) { const q = Object.assign({}, p); ['entry', 'tp', 'sl'].forEach(f => { if (!isNaN(val(f))) q[f] = val(f); }); setBox(this.row, q); }
          tools.forEach(t => { if (t.requestUpdate) t.requestUpdate(); });
          if (config.onChange) config.onChange(null);       // account / risk: every row's $ changes
          box.remove();
        } else if (a === 'del') { this.sel = false; setBox(this.row, null); box.remove(); }
      });
    }
  }

  document.addEventListener('keydown', (e) => {
    if (!active || /INPUT|TEXTAREA|SELECT/.test((e.target.tagName || ''))) return;
    if ((e.key === 'Delete' || e.key === 'Backspace') && active.sel) {
      active.sel = false; setBox(active.row, null); e.preventDefault();
    } else if (e.key === 'Escape') {
      active.arm = null; active.syncButtons();
      document.querySelectorAll('.pos-pop').forEach(n => n.remove());
    }
  });
  document.addEventListener('pointerdown', (e) => {
    if (!e.target.closest('.pos-pop') && !e.target.closest('.pos-btn')) document.querySelectorAll('.pos-pop').forEach(n => n.remove());
  }, true);

  const css = document.createElement('style');
  css.textContent = '.pos-bar{grid-column:3;justify-self:end;display:flex;gap:4px;align-items:center}'
    + '.pos-bar .fs-btn{grid-column:auto}'
    + '.pos-btn{background:#1b2330;color:#e5e7eb;border:1px solid #334155;border-radius:4px;padding:0 7px;'
    + 'font:12px/1.9 Courier New,monospace;cursor:pointer;white-space:nowrap}.pos-btn:hover{background:#273449}'
    + '.pos-btn.pos-on{background:#2962ff!important;color:#fff!important}.pos-btn:disabled{opacity:.4;cursor:default}'
    + '.pos-pop{position:fixed;z-index:10000;background:#1e222d;border:1px solid #363a45;border-radius:4px;padding:8px 10px;'
    + 'font:12px Courier New,monospace;color:#d1d4dc;display:flex;flex-direction:column;gap:5px;width:220px}'
    + '.pos-pop label{display:flex;justify-content:space-between;gap:8px;align-items:center}'
    + '.pos-pop input{width:100px;background:#131722;color:#d1d4dc;border:1px solid #363a45;padding:2px 4px}'
    + '.pos-pop-btns{display:flex;justify-content:flex-end;gap:6px;margin-top:4px}'
    + '.pos-pop button{background:#2962ff;color:#fff;border:0;border-radius:3px;padding:3px 10px;cursor:pointer}';
  document.head.appendChild(css);

  window.PosTool = {
    tools, config, settings, getBox,
    refresh: (i) => tools.filter(t => i == null || t.row === i).forEach(t => { if (t.requestUpdate) t.requestUpdate(); }),
    attach: (chart, series, el, titleEl, cd) => new Tool(chart, series, el, titleEl, cd),
  };
})();
