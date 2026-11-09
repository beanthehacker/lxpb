// Long / short position tool for the report charts (lightweight-charts 4.1.3), modelled on TradingView's.
//
// PosTool.attach(chart, series, el, titleEl, cd) puts the tool on one pane (el.id = c-<pane>-<row>). Each table row has ONE box
// (its manual trade), shared by every pane of the row named in config.shared: it is drawn on all of them and can be moved on any
// of them; panes named in config.mirror only draw it. The box is kept in time and price, not candle positions, so an H1, an M5
// and an M2 pane show it at the same moment: { side, t0, t1 (left / right edge, epoch s), entry, sl, tp, from, pane }.
//
// Click Long / Short, then a candle: the box is placed with its entry on that candle (target 2R, stop 1.5 x the average bar
// range away, 20 candles wide). Drag the target / stop / entry lines to move them, the start point (left end of the entry line)
// to move the start in time and price, the left / right edge to move that edge in time, inside the box to move the whole box. While dragging, prices stick to the nearest open / high / low / close of the candle under the cursor
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
//
// DRAWINGS (user, 2026-11-08), like TradingView's: a horizontal line (Alt+H or "─ Line", then click a point: a line across the
// whole chart), a horizontal ray (Alt+J or "→ Ray", then click a point: a line from that point to the right; both with their
// price tagged on the price axis) and a rectangle (Alt+Shift+R or "▭ Rect", then click two corners,
// or press, drag and release). Any number per row, shared by the row's panes like the box, kept in time and price, prices stuck to
// O/H/L/C by the magnet (Ctrl: free), times to candles. Hover shows a drawing's points; click selects it (a rectangle's prices
// and times then show on the axes); drag a point to move it (rectangle: 4 corners, 4 edge midpoints), the line / inside to move
// the whole drawing; Delete removes, Esc cancels, double-click opens its colours (7 swatches, user 2026-11-09: blue, orange, red,
// bright green, dark green, white, pink; no colour picker), width, style and prices. The last style used becomes the default for
// the next one.
(function () {
  const PT_VALUE = 50, TICK = 0.25, MAGNET_PX = 12, HANDLE_PX = 6;
  const LS = 'postool_v1_';
  const lsGet = (k, d) => { try { const v = localStorage.getItem(LS + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } };
  const lsSet = (k, v) => { try { localStorage.setItem(LS + k, JSON.stringify(v)); } catch (e) { /* private window */ } };
  const settings = Object.assign({ account: 50000, riskPct: 1, magnet: 'strong',
                                   rayStyle: { color: '#2962ff', width: 1, dash: 'solid' },
                                   hlineStyle: { color: '#2962ff', width: 1, dash: 'solid' },
                                   rectStyle: { color: '#9c27b0', fill: '#9c27b0', alpha: 0.2, width: 1, dash: 'solid' } },
                                 lsGet('settings', {}));
  const DASH = { solid: [], dashed: [6, 4], dotted: [1.5, 2.5] };
  const PALETTE = [['blue', '#2962ff'], ['orange', '#ff9800'], ['red', '#f23645'], ['bright green', '#00e676'],
                   ['dark green', '#1b8a3c'], ['white', '#ffffff'], ['pink', '#ff5cb8']];
  const rgba = (hex, a) => { const n = parseInt(hex.slice(1), 16); return 'rgba(' + (n >> 16 & 255) + ',' + (n >> 8 & 255) + ',' + (n & 255) + ',' + a + ')'; };
  const tools = [];
  const config = { shared: [], mirror: [], cursor: () => null, result: () => null, onChange: null };
  let active = null;                       // the tool last clicked: Delete / Esc go to it
  let under = null;                        // the tool under the mouse: drawing shortcuts (Alt+H / J / Shift+R) go to it
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
  // the row's drawings (lines, rays, rectangles): [{ id, type 'hline' / 'ray', t, p, color, width, dash }
  //                                          | { id, type 'rect', t0, p0, t1, p1, color, fill, alpha, width, dash }]
  const drawKey = (i) => 'draw_' + FILE + '_' + rowKey(i);
  const draws = {};
  const getDraws = (i) => (i in draws ? draws[i] : (draws[i] = lsGet(drawKey(i), [])));
  function setDraws(i, list) {
    draws[i] = list;
    lsSet(drawKey(i), list);
    tools.forEach(t => { if (t.row === i && t.requestUpdate) t.requestUpdate(); });
  }
  const putDraw = (i, d) => setDraws(i, getDraws(i).map(o => (o.id === d.id ? d : o)));
  const dropDraw = (i, id) => setDraws(i, getDraws(i).filter(o => o.id !== id));
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
      this.selD = null; this.hoverD = null; this.creating = null;   // selected / hovered drawing id, the rectangle being drawn
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
          const tag = (v, bg) => {
            const y = tool.y(v);
            if (y != null) out.push({ coordinate: () => y, text: () => v.toFixed(2), textColor: () => '#ffffff', backColor: () => bg,
                                      visible: () => true, tickVisible: () => true });
          };
          if (p) [[p.tp, '#26a69a'], [p.entry, '#787b86'], [p.sl, '#ef5350']].forEach(([v, bg]) => tag(v, bg));
          getDraws(tool.row).forEach(d => {          // a ray's price always; a rectangle's two prices while it is selected / hovered
            if (d.type !== 'rect') tag(d.p, d.color);
            else if (tool.focus(d)) { tag(d.p0, d.color); tag(d.p1, d.color); }
          });
          return out;
        },
        timeAxisViews() {                            // the selected / hovered drawing's times on the time axis
          const d = tool.focused(), out = [];
          if (!d) return out;
          const f = tool.chart.options().localization.timeFormatter;
          (d.type === 'rect' ? [d.t0, d.t1] : d.type === 'ray' ? [d.t] : []).forEach(t => {
            const x = tool.x(t);
            if (x != null) out.push({ coordinate: () => x, text: () => (f ? f(t) : String(t)), textColor: () => '#ffffff',
                                      backColor: () => d.color, visible: () => true, tickVisible: () => true });
          });
          return out;
        },
        // a selected rectangle's price and time span shaded on the axes, as TradingView does
        priceAxisPaneViews() { return tool.axisBand('price'); },
        timeAxisPaneViews() { return tool.axisBand('time'); },
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
      this.bHline = mk('─ Line', 'Horizontal line (Alt+H): click a point', () => this.armFor('hline'));
      this.bRay = mk('→ Ray', 'Horizontal ray (Alt+J): click a point', () => this.armFor('ray'));
      this.bRect = mk('▭ Rect', 'Rectangle (Alt+Shift+R): click two corners, or press, drag and release', () => this.armFor('rect'));
      mk('⚙', 'Account size and risk', (b) => this.popover(b, false));
      this.syncButtons();
    }
    syncButtons() {
      if (!this.bMag) return;
      this.bMag.textContent = '⌖ magnet: ' + settings.magnet;
      this.bLong.classList.toggle('pos-on', this.arm === 'long');
      this.bShort.classList.toggle('pos-on', this.arm === 'short');
      this.bHline.classList.toggle('pos-on', this.arm === 'hline');
      this.bRay.classList.toggle('pos-on', this.arm === 'ray');
      this.bRect.classList.toggle('pos-on', this.arm === 'rect');
    }
    armFor(side) { this.cancelCreate(); this.arm = this.arm === side ? null : side; active = this; this.syncButtons(); }
    cancelCreate() { if (this.creating) { dropDraw(this.row, this.creating.id); this.creating = null; this.selD = null; } }

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
      if (Math.hypot(x - g.x0, y - g.ye) <= 2 * HANDLE_PX) return 'start';   // the start point: its time and the entry price
      if (Math.abs(x - g.x1) <= HANDLE_PX && y >= top && y <= bot) return 'right';
      if (Math.abs(x - g.x0) <= HANDLE_PX && y >= top && y <= bot) return 'left';
      if (Math.abs(y - g.yt) <= HANDLE_PX) return 'tp';
      if (Math.abs(y - g.ys) <= HANDLE_PX) return 'sl';
      if (Math.abs(y - g.ye) <= HANDLE_PX) return 'entry';
      if (y > top && y < bot) return 'body';
      return null;
    }

    // ---- drawings
    tAt(x) {                                      // the start of the candle under x (past the last candle: extrapolated)
      const l = this.chart.timeScale().coordinateToLogical(x);
      return l == null ? null : this.timeOf(Math.round(l));
    }
    focus(d) { return this.selD === d.id || this.hoverD === d.id; }
    focused() {
      const id = this.selD || this.hoverD;
      return id ? getDraws(this.row).find(d => d.id === id) || null : null;
    }
    dGeom(d) {
      if (d.type !== 'rect') { const x = this.x(d.t), y = this.y(d.p); return x == null || y == null ? null : { x, y }; }
      const x0 = this.x(d.t0), x1 = this.x(d.t1), y0 = this.y(d.p0), y1 = this.y(d.p1);
      return [x0, x1, y0, y1].some(v => v == null) ? null : { x0, x1, y0, y1 };
    }
    dHandles(d, g) {                              // [part, x, y]: c<t><p> corner, et<k> time edge, ep<k> price edge
      if (d.type !== 'rect') return [['a', g.x, g.y]];
      const mx = (g.x0 + g.x1) / 2, my = (g.y0 + g.y1) / 2;
      return [['c00', g.x0, g.y0], ['c01', g.x0, g.y1], ['c10', g.x1, g.y0], ['c11', g.x1, g.y1],
              ['et0', g.x0, my], ['et1', g.x1, my], ['ep0', mx, g.y0], ['ep1', mx, g.y1]];
    }
    onLine(d, g, x, y) {                          // on a line, a ray or a rectangle's border
      if (d.type === 'hline') return Math.abs(y - g.y) <= HANDLE_PX;
      if (d.type === 'ray') return x >= g.x - HANDLE_PX && Math.abs(y - g.y) <= HANDLE_PX;
      const l = Math.min(g.x0, g.x1), r = Math.max(g.x0, g.x1), t = Math.min(g.y0, g.y1), b = Math.max(g.y0, g.y1);
      const inX = x >= l - HANDLE_PX && x <= r + HANDLE_PX, inY = y >= t - HANDLE_PX && y <= b + HANDLE_PX;
      return (inY && (Math.abs(x - l) <= HANDLE_PX || Math.abs(x - r) <= HANDLE_PX))
          || (inX && (Math.abs(y - t) <= HANDLE_PX || Math.abs(y - b) <= HANDLE_PX));
    }
    // what is under the cursor: the selected drawing's points, then drawings' points and lines / borders (newest first), then
    // the box, then the inside of rectangles
    hitAll(x, y) {
      const ds = getDraws(this.row);
      const sd = this.selD && ds.find(d => d.id === this.selD), sg = sd && this.dGeom(sd);
      if (sg) for (const [part, hx, hy] of this.dHandles(sd, sg)) if (Math.hypot(x - hx, y - hy) <= HANDLE_PX + 2) return { d: sd, part };
      for (let k = ds.length - 1; k >= 0; k--) {
        const d = ds[k], g = this.dGeom(d);
        if (!g) continue;
        if (d.id !== this.selD) for (const [part, hx, hy] of this.dHandles(d, g)) if (Math.hypot(x - hx, y - hy) <= HANDLE_PX + 2) return { d, part };
        if (this.onLine(d, g, x, y)) return { d, part: 'move' };
      }
      const b = this.hit(x, y);
      if (b) return { box: b };
      for (let k = ds.length - 1; k >= 0; k--) {
        const d = ds[k], g = d.type === 'rect' && this.dGeom(d);
        if (g && x > Math.min(g.x0, g.x1) && x < Math.max(g.x0, g.x1) && y > Math.min(g.y0, g.y1) && y < Math.max(g.y0, g.y1)) return { d, part: 'move' };
      }
      return null;
    }
    handleCursor(h) {
      if (!h) return '';
      if (h.box) return h.box === 'right' || h.box === 'left' ? 'ew-resize' : h.box === 'body' || h.box === 'start' ? 'move' : 'ns-resize';
      const q = h.part;
      if (q === 'move' || q === 'a') return 'pointer';
      if (q[1] === 't') return 'ew-resize';
      if (q[1] === 'p') return 'ns-resize';
      const g = this.dGeom(h.d), xr = (q[1] === '1') === (g.x1 > g.x0), yb = (q[2] === '1') === (g.y1 > g.y0);
      return xr === yb ? 'nwse-resize' : 'nesw-resize';
    }
    newDraw(type, q) {
      const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
      const d = type !== 'rect' ? Object.assign({ id, type, t: q.t, p: q.p }, settings[type + 'Style'])
                               : Object.assign({ id, type, t0: q.t, p0: q.p, t1: q.t, p1: q.p }, settings.rectStyle);
      setDraws(this.row, getDraws(this.row).concat([d]));
      this.selD = id; this.sel = false;
      return d;
    }
    axisBand(which) {
      const d = this.focused();
      if (!d || d.type !== 'rect' || this.selD !== d.id) return [];
      const tool = this;
      return [{ zOrder: () => 'bottom', renderer: () => ({ draw: (target) => target.useMediaCoordinateSpace(({ context, mediaSize }) => {
        const g = tool.dGeom(d);
        if (!g) return;
        context.fillStyle = rgba(d.color, 0.25);
        if (which === 'price') context.fillRect(0, Math.min(g.y0, g.y1), mediaSize.width, Math.abs(g.y1 - g.y0));
        else context.fillRect(Math.min(g.x0, g.x1), 0, Math.abs(g.x1 - g.x0), mediaSize.height);
      }) }) }];
    }
    drawDraws(ctx) {
      const W = this.chart.timeScale().width();
      const crisp = (v, w) => (w % 2 ? Math.round(v) + 0.5 : Math.round(v));
      getDraws(this.row).forEach(d => {
        const g = this.dGeom(d);
        if (!g) return;
        ctx.lineWidth = d.width || 1; ctx.strokeStyle = d.color; ctx.setLineDash(DASH[d.dash] || []);
        if (d.type !== 'rect') {
          const y = crisp(g.y, ctx.lineWidth);
          ctx.beginPath(); ctx.moveTo(d.type === 'hline' ? 0 : g.x, y); ctx.lineTo(Math.max(W, g.x), y); ctx.stroke();
        } else {
          const l = Math.min(g.x0, g.x1), t = Math.min(g.y0, g.y1), w = Math.abs(g.x1 - g.x0), h = Math.abs(g.y1 - g.y0);
          ctx.fillStyle = rgba(d.fill, d.alpha); ctx.fillRect(l, t, w, h);
          ctx.strokeRect(crisp(l, ctx.lineWidth), crisp(t, ctx.lineWidth), Math.round(w), Math.round(h));
        }
        ctx.setLineDash([]);
        if (this.edit && this.focus(d)) this.dHandles(d, g).forEach(([, hx, hy]) => {
          ctx.fillStyle = '#131722'; ctx.strokeStyle = '#2962ff'; ctx.lineWidth = 2;
          ctx.beginPath(); ctx.arc(hx, hy, 5, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
        });
      });
    }

    draw(ctx) {
      this.drawDraws(ctx);
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
        if (this.creating) {                       // a rectangle's second corner
          const c = this.creating, d = getDraws(this.row).find(o => o.id === c.id);
          this.creating = null;
          if (d && d.t0 === d.t1 && d.p0 === d.p1) { dropDraw(this.row, c.id); this.selD = null; }
          this.blockMouse = true; block(e); return;
        }
        if (this.arm === 'hline' || this.arm === 'ray' || this.arm === 'rect') {
          const q = { t: this.tAt(pt.x), p: this.snap(pt.x, pt.y, e.ctrlKey) };
          if (q.t != null && q.p != null) {
            const d = this.newDraw(this.arm, q);
            if (this.arm === 'rect') this.creating = { id: d.id, x: pt.x, y: pt.y, moved: false };
          }
          this.arm = null; this.syncButtons();
          this.blockMouse = true; block(e); return;
        }
        if (this.arm) { this.place(pt, e.ctrlKey); this.blockMouse = true; block(e); return; }
        const hh = this.hitAll(pt.x, pt.y);
        if (!hh) {
          if (this.sel || this.selD) { this.sel = false; this.selD = null; if (this.requestUpdate) this.requestUpdate(); }
          return;
        }
        if (hh.d) {                                // a drawing: select it, drag a point or the whole of it
          const d0 = Object.assign({}, hh.d);
          this.selD = d0.id; this.sel = false;
          this.drag = { draw: d0.id, part: hh.part, d0, l0: this.chart.timeScale().coordinateToLogical(pt.x),
                        dy: pt.y - this.y(d0.type === 'rect' ? d0.p0 : d0.p) };
          this.blockMouse = true; block(e);
          if (this.requestUpdate) this.requestUpdate();
          return;
        }
        const h = hh.box;
        this.sel = true; this.selD = null;
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
        const r = el.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
        if (this.creating) {                       // the rectangle being drawn follows the cursor
          const c = this.creating, d = getDraws(this.row).find(o => o.id === c.id);
          if (!d) { this.creating = null; return; }
          const t = this.tAt(x), p = this.snap(x, y, e.ctrlKey);
          if (Math.hypot(x - c.x, y - c.y) > 4) c.moved = true;
          if (t != null && p != null) putDraw(this.row, Object.assign({}, d, { t1: t, p1: p }));
          el.querySelectorAll('canvas').forEach(cv => { cv.style.cursor = 'crosshair'; });
          return;
        }
        if (!this.drag) {
          const pt = this.plotPoint(e);
          if (pt) under = this; else if (under === this) under = null;
          const h = pt && this.hitAll(pt.x, pt.y);
          const cur = this.arm && pt ? 'crosshair' : this.handleCursor(h);
          el.querySelectorAll('canvas').forEach(c => { c.style.cursor = cur; });
          const hb = !!(h && h.box), hd = h && h.d ? h.d.id : null;
          if (hb !== this.hover || hd !== this.hoverD) { this.hover = hb; this.hoverD = hd; if (this.requestUpdate) this.requestUpdate(); }
          return;
        }
        if (this.drag.draw) { this.dragDraw(x, y, e.ctrlKey); return; }
        const d = this.drag, p = Object.assign({}, getBox(this.row)), s = d.p0.side === 'long' ? 1 : -1;
        const minGap = TICK;
        if (d.part === 'right') {
          const i = this.idxAt(x);
          if (i != null) p.t1 = this.timeOf(Math.max(Math.round(this.logicalOf(p.t0)) + 1, i));
        } else if (d.part === 'left' || d.part === 'start') {   // move the start in time (and, from the start point, the entry price)
          const i = this.idxAt(x);
          if (i != null) {
            const k = Math.max(0, Math.min(this.candles.length - 1, i, Math.round(this.logicalOf(p.t1)) - 1));
            p.t0 = this.candles[k].time; p.from = this.fromFor(p.t0); p.pane = this.cd.tfName || this.pane;
          }
          const v = d.part === 'start' ? this.snap(x, y, e.ctrlKey) : null;
          if (v != null) p.entry = s > 0 ? Math.min(Math.max(v, p.sl + minGap), p.tp - minGap) : Math.min(Math.max(v, p.tp + minGap), p.sl - minGap);
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
      window.addEventListener('pointerup', () => {
        if (this.creating && this.creating.moved) this.creating = null;   // press, drag, release: done
        this.drag = null; setTimeout(() => { this.blockMouse = false; }, 0);
      });
      el.addEventListener('dblclick', (e) => {
        const pt = this.plotPoint(e), h = pt && this.hitAll(pt.x, pt.y);
        if (!h) return;
        block(e);
        if (h.d) this.drawPopover(h.d.id, e.clientX, e.clientY);
        else this.popover(null, true, e.clientX, e.clientY);
      }, true);
    }
    dragDraw(x, y, free) {
      const d = this.drag, cur = getDraws(this.row).find(o => o.id === d.draw);
      if (!cur) return;
      const q = Object.assign({}, cur), d0 = d.d0;
      if (d.part === 'move') {                    // the whole drawing: whole candles in time, its anchor price by the magnet
        const l = this.chart.timeScale().coordinateToLogical(x), di = l == null ? 0 : Math.round(l - d.l0);
        const sh = (t) => this.timeOf(this.logicalOf(t) + di);
        const ne = this.snap(x, y - d.dy, free);
        if (ne == null) return;
        if (q.type !== 'rect') Object.assign(q, { t: sh(d0.t), p: ne });
        else Object.assign(q, { t0: sh(d0.t0), t1: sh(d0.t1), p0: ne, p1: d0.p1 + ne - d0.p0 });
      } else {
        const t = this.tAt(x), p = this.snap(x, y, free), k = d.part;
        if (k === 'a') { if (t != null) q.t = t; if (p != null) q.p = p; }
        else if (k[0] === 'c') { if (t != null) q['t' + k[1]] = t; if (p != null) q['p' + k[2]] = p; }
        else if (k[1] === 't') { if (t != null) q['t' + k[2]] = t; }
        else if (p != null) q['p' + k[2]] = p;
      }
      putDraw(this.row, q);
    }
    drawPopover(id, cx, cy) {                     // a drawing's colours, width, style and prices
      document.querySelectorAll('.pos-pop').forEach(n => n.remove());
      const d = getDraws(this.row).find(o => o.id === id);
      if (!d) return;
      const box = document.createElement('div');
      box.className = 'pos-pop';
      box.style.left = Math.min(cx, window.innerWidth - 240) + 'px';
      box.style.top = Math.min(cy + 4, window.innerHeight - 260) + 'px';
      const row = (l, inner) => '<label>' + l + inner + '</label>';
      const sel = (f, opts, v) => '<select data-f="' + f + '">' + opts.map(o => '<option' + (String(o) === String(v) ? ' selected' : '') + '>' + o + '</option>').join('') + '</select>';
      const numIn = (f, v) => '<input data-f="' + f + '" type="number" step="0.25" value="' + v + '">';
      const sws = (f, v) => '<span class="pos-sws" data-f="' + f + '" data-v="' + v + '">' + PALETTE.map(([n, c]) =>
        '<button type="button" class="pos-sw' + (c === v ? ' on' : '') + '" data-c="' + c + '" title="' + n + '" style="background:' + c + '"></button>').join('') + '</span>';
      let h = row(d.type !== 'rect' ? 'Line' : 'Border', sws('color', d.color));
      if (d.type === 'rect') h += row('Fill', sws('fill', d.fill))
                                + row('Opacity', '<input data-f="alpha" type="range" min="0" max="1" step="0.05" value="' + d.alpha + '">');
      h += row('Width', sel('width', [1, 2, 3, 4], d.width)) + row('Style', sel('dash', ['solid', 'dashed', 'dotted'], d.dash));
      h += d.type !== 'rect' ? row('Price', numIn('p', d.p)) : row('Price 1', numIn('p0', d.p0)) + row('Price 2', numIn('p1', d.p1));
      box.innerHTML = h + '<div class="pos-pop-btns"><button data-a="del">Delete</button><button data-a="ok">Apply</button></div>';
      document.body.appendChild(box);
      const val = (f) => { const n = box.querySelector('[data-f="' + f + '"]'); return n.classList.contains('pos-sws') ? n.dataset.v : n.value; };
      box.addEventListener('click', (e) => {
        const a = e.target.dataset && e.target.dataset.a, sw = e.target.dataset && e.target.dataset.c;
        if (sw) {                                   // a colour swatch: it becomes its row's colour
          const grp = e.target.parentNode;
          grp.dataset.v = sw;
          grp.querySelectorAll('.pos-sw').forEach(b => b.classList.toggle('on', b === e.target));
        } else if (a === 'ok') {
          const q = Object.assign({}, d, { color: val('color'), width: +val('width'), dash: val('dash') });
          if (d.type === 'rect') Object.assign(q, { fill: val('fill'), alpha: +val('alpha') });
          (d.type !== 'rect' ? ['p'] : ['p0', 'p1']).forEach(f => { const v = parseFloat(val(f)); if (!isNaN(v)) q[f] = v; });
          const st = d.type + 'Style';                                // the next one is drawn in this style
          settings[st] = d.type !== 'rect' ? { color: q.color, width: q.width, dash: q.dash }
                                          : { color: q.color, fill: q.fill, alpha: q.alpha, width: q.width, dash: q.dash };
          lsSet('settings', settings);
          putDraw(this.row, q);
          box.remove();
        } else if (a === 'del') { this.selD = null; dropDraw(this.row, id); box.remove(); }
      });
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
    if (/INPUT|TEXTAREA|SELECT/.test((e.target.tagName || ''))) return;
    const to = under && !under.dead ? under : active;
    if (to && e.altKey && !e.ctrlKey && to.bRay && (e.code === 'KeyH' || e.code === 'KeyJ' || (e.shiftKey && e.code === 'KeyR'))) {
      to.armFor({ KeyH: 'hline', KeyJ: 'ray', KeyR: 'rect' }[e.code]); e.preventDefault(); return;
    }
    if (!active) return;
    if ((e.key === 'Delete' || e.key === 'Backspace') && active.selD) {
      dropDraw(active.row, active.selD); active.selD = null; active.hoverD = null; e.preventDefault();
    } else if ((e.key === 'Delete' || e.key === 'Backspace') && active.sel) {
      active.sel = false; setBox(active.row, null); e.preventDefault();
    } else if (e.key === 'Escape') {
      active.cancelCreate();
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
    + '.pos-pop input,.pos-pop select{width:100px;background:#131722;color:#d1d4dc;border:1px solid #363a45;padding:2px 4px;box-sizing:border-box}'
    + '.pos-pop input[type=range]{padding:0}.pos-sws{display:flex;gap:3px}'
    + '.pos-pop .pos-sw{width:15px;height:15px;padding:0;border:1px solid #363a45;border-radius:3px;cursor:pointer}'
    + '.pos-pop .pos-sw.on{outline:2px solid #d1d4dc;outline-offset:1px}'
    + '.pos-pop-btns{display:flex;justify-content:flex-end;gap:6px;margin-top:4px}'
    + '.pos-pop button{background:#2962ff;color:#fff;border:0;border-radius:3px;padding:3px 10px;cursor:pointer}';
  document.head.appendChild(css);

  window.PosTool = {
    tools, config, settings, getBox, getDraws,
    refresh: (i) => tools.filter(t => i == null || t.row === i).forEach(t => { if (t.requestUpdate) t.requestUpdate(); }),
    attach: (chart, series, el, titleEl, cd) => new Tool(chart, series, el, titleEl, cd),
  };
})();
