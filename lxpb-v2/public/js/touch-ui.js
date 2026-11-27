// iPad / touch layer for the replay charts (user, 2026-11-25: iPad Air 11" M4, finger + Magic Trackpad). Desktop is untouched:
// everything here runs only when the page decides it is on an iPad (or another tablet whose main pointer is a finger), or when
// `?ipad=1` is in the address (`?ipad=0` turns it off; either is remembered in this browser). Off = TouchUI.on is false and the
// charts keep the old touch rules (_touchPriceAxis). On:
//   * layout (html.ipad): the page fits the screen (no sideways page scroll), the ES chart and NQ stacked full width, bigger buttons
//     that wrap instead of running off the title bar, 16 px inputs (iPad Safari zooms the page into anything smaller);
//   * finger, inside the plot: one finger drags the chart any way (sideways = time, up / down = price, the price axis leaves
//     auto-fit as the desktop's first mouse press does), with a little momentum sideways; two fingers pinch = time zoom; a tap puts
//     the crosshair (and the O H L C in the title) on that candle;
//   * finger, on the price axis strip: drag down / up = taller / shorter candles, double tap = auto-fit again;
//   * finger, on the time axis strip: drag sideways = zoom time, drag up / down = scroll the page, double tap = fit the candles;
//   * Magic Trackpad: click-drag = the desktop's mouse drag; two-finger swipe sideways = time, up / down = the page (over the price
//     axis: stretch the price); pinch = time zoom where iPadOS hands it to the page (ctrl + wheel or Safari gesture events), and
//     the - / + / fit buttons on each chart always work;
//   * the page itself never pinch-zooms over a chart, so the chart gets the gesture.
// The position tool (position-tool.js) keeps the finger whenever it is placing, drawing or dragging something.
(function () {
  const KEY = 'lxpb_ipad_mode';
  const q = new URLSearchParams(location.search).get('ipad');
  let saved = null;
  try { if (q === '1' || q === '0') localStorage.setItem(KEY, q); saved = localStorage.getItem(KEY); } catch (e) { saved = q; }
  const ua = navigator.userAgent || '', touchPts = navigator.maxTouchPoints || 0;
  const auto = (touchPts > 1 && /Macintosh|iPad/.test(ua)) || window.matchMedia('(hover: none) and (pointer: coarse)').matches;
  const on = saved === '1' ? true : saved === '0' ? false : auto;
  if (on) document.documentElement.classList.add('ipad');

  const css = document.createElement('style');
  css.textContent = `
html.ipad, html.ipad body { overflow-x: hidden; -webkit-text-size-adjust: 100%; }
html.ipad .table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
html.ipad #lvl-table { width: 100%; }
html.ipad textarea.trade-note { width: 100%; min-width: 110px; height: 2.6em; box-sizing: border-box; }
html.ipad input, html.ipad select, html.ipad textarea { font-size: 16px !important; }
html.ipad input[type=checkbox] { width: 22px; height: 22px; }
html.ipad .expand-btn { min-width: 40px; min-height: 36px; }
html.ipad .replay-bar { gap: 8px; padding: 8px 10px; font-size: 14px; }
html.ipad .replay-bar button, html.ipad .replay-bar select { min-height: 38px; padding: 0 12px; font-size: 15px; touch-action: manipulation; }
html.ipad tr.chart-row > td { padding: 4px 0 !important; }
html.ipad .chart-stack { width: var(--tu-wrap-w, 100%) !important; position: sticky !important; left: 0 !important; box-sizing: border-box; }
/* one chart = the screen's height under the replay bar (svh: the height with Safari's bars showing, so it never changes while
   the page scrolls); ES + NQ: side by side in landscape, one above the other in portrait, both on the screen at once */
html.ipad .chart-pane { height: max(300px, calc(100svh - var(--tu-top, 90px) - 8px)); }
html.ipad .es-nq-row.two { grid-template-columns: 1fr 1fr; gap: 6px; }
@media (orientation: portrait) {
  html.ipad .es-nq-row.two { grid-template-columns: 1fr; }
  html.ipad .es-nq-row.two > .chart-pane { height: max(260px, calc((100svh - var(--tu-top, 90px) - 14px) / 2)); }
}
html.ipad .replay-bar { position: sticky; top: 0; }
html.ipad .chart-title.chart-title-split { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 10px; height: auto; padding: 4px 6px; }
html.ipad .chart-title-split .ct-ohlc { flex: 1 1 auto; text-align: left; }
html.ipad .pos-bar { flex: 1 1 100%; flex-wrap: wrap; justify-content: flex-start; gap: 6px; }
html.ipad .pos-btn, html.ipad .fs-btn { min-height: 38px; min-width: 40px; padding: 0 11px; font-size: 15px; line-height: 36px; touch-action: manipulation; }
html.ipad .pos-btn.tf-btn { order: -3; min-width: 44px; padding: 0 6px; }
html.ipad .pos-btn.tu-nav { order: -2; }
html.ipad .pos-btn.tu-zoom { order: -1; }
html.ipad .pos-pop { width: 280px; font-size: 15px; gap: 8px; padding: 10px 12px; }
html.ipad .pos-pop input, html.ipad .pos-pop select { width: 130px; min-height: 34px; }
html.ipad .pos-pop button { min-height: 38px; padding: 0 14px; font-size: 15px; }
html.ipad .pos-pop .pos-sw { width: 28px; height: 28px; }
html.ipad .pos-pop .pos-fibs input { width: 22px !important; height: 22px; }
html.ipad .chart-ph { touch-action: none; -webkit-user-select: none; user-select: none; -webkit-touch-callout: none; }
@media (orientation: landscape) {
  html.ipad .es-nq-row.two .pos-btn, html.ipad .es-nq-row.two .fs-btn { min-height: 32px; min-width: 30px; padding: 0 5px; font-size: 12px; line-height: 30px; }
  html.ipad .es-nq-row.two .chart-title-split { gap: 3px 6px; }
  html.ipad .es-nq-row.two .pos-bar { gap: 4px; }
}
.tu-mode { margin-left: 6px; }
`;
  document.head.appendChild(css);
  // the chart stack is exactly as wide as the table's visible box (the table itself may scroll sideways in portrait)
  const wrapW = () => {
    const w = document.querySelector('.table-wrap');
    if (w) document.documentElement.style.setProperty('--tu-wrap-w', w.clientWidth + 'px');
    // each open row: what sits above its charts (replay bar, padding) -> the charts take the rest of the screen's height
    document.querySelectorAll('tr.chart-row:not(.hidden)').forEach(row => {
      const st = row.querySelector('.chart-stack'), enr = row.querySelector('.es-nq-row');
      if (st && enr) st.style.setProperty('--tu-top', Math.ceil(enr.getBoundingClientRect().top - row.getBoundingClientRect().top) + 'px');
    });
  };
  if (on) {
    window.addEventListener('DOMContentLoaded', wrapW);
    window.addEventListener('load', wrapW);
    window.addEventListener('resize', wrapW);
    window.addEventListener('orientationchange', () => setTimeout(wrapW, 400));
    document.addEventListener('click', (e) => {      // a row opened: its charts fill the screen from the top
      const b = e.target.closest && e.target.closest('.expand-btn');
      if (!b) return;
      setTimeout(() => {
        const row = document.getElementById('chart-row-' + b.dataset.idx);
        if (!row || row.classList.contains('hidden')) return;
        wrapW();
        window.scrollTo({ top: row.getBoundingClientRect().top + window.scrollY - 2, behavior: 'smooth' });
      }, 60);
    }, true);
  }

  // a switch in the toolbar on any device that has a touch screen (and wherever the layer is on): reloads the page in the other mode
  if (on || touchPts > 0) window.addEventListener('DOMContentLoaded', () => {
    const bar = document.querySelector('.toolbar');
    if (!bar) return;
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'tu-mode';
    b.textContent = on ? 'iPad layout: on' : 'iPad layout: off';
    b.title = 'Touch / trackpad layout for an iPad (remembered in this browser; the page reloads)';
    b.addEventListener('click', (e) => {
      e.stopPropagation();
      try { localStorage.setItem(KEY, on ? '0' : '1'); } catch (err) { /* private window */ }
      location.href = location.href.replace(/([?&])ipad=[01]&?/, '$1').replace(/[?&]$/, '');
    });
    bar.appendChild(b);
  });

  function attach(chart, el, series, otherSeries, onChange, cd) {
    const all = [series].concat(otherSeries || []);
    const ts = chart.timeScale(), ps = chart.priceScale('right');
    // the library's own finger handling is off: this layer does it (mouse / trackpad clicks keep the library's)
    chart.applyOptions({ handleScroll: { horzTouchDrag: false, vertTouchDrag: false }, handleScale: { pinch: false } });
    let manual = null;                       // {mid, half}: the price range held while moved / stretched by finger or wheel
    const provider = (orig) => (manual ? { priceRange: { minValue: manual.mid - manual.half, maxValue: manual.mid + manual.half } } : orig());
    const refresh = () => { all.forEach(s => s.applyOptions({ autoscaleInfoProvider: provider })); if (onChange) onChange(); };
    const plotH = () => Math.max(1, el.clientHeight - ts.height());
    const perPx = () => (2 * manual.half / 0.84) / plotH();        // the provider's range plus the 8 % scale margins each side
    const take = () => {                     // the range on screen becomes the held one (auto-fit off, as a desktop mouse press)
      if (manual && ps.options().autoScale) return true;
      const top = series.coordinateToPrice(0), bot = series.coordinateToPrice(plotH());
      if (top == null || bot == null) return false;
      manual = { mid: (top + bot) / 2, half: Math.abs(top - bot) * 0.84 / 2 };
      ps.applyOptions({ autoScale: true });
      refresh();
      return true;
    };
    const autoFit = () => { manual = null; ps.applyOptions({ autoScale: true }); refresh(); };
    const hold = () => { if (cd) cd.tuHeld = true; };
    const fit = () => {
      if (cd) cd.tuHeld = false;
      autoFit();
      if (cd && cd.fit) cd.fit(chart);
      else if (typeof _fitBars === 'function') _fitBars(chart, ((cd && (cd.live || cd.candles)) || []).length);
    };
    const zone = (cx, cy) => {
      const r = el.getBoundingClientRect(), x = cx - r.left, y = cy - r.top;
      return y > r.height - ts.height() ? 'time' : x > ts.width() ? 'price' : 'plot';
    };
    const localX = (cx) => cx - el.getBoundingClientRect().left;
    const zoomAt = (r0, x, k) => {           // time zoom by k (> 1 = closer) around plot x, from range r0
      if (!r0) return;
      const la = r0.from + (x / Math.max(1, ts.width())) * (r0.to - r0.from);
      const span = (r0.to - r0.from) / k;
      if (span < 8 || span > 20000) return;
      ts.setVisibleLogicalRange({ from: la - (la - r0.from) / k, to: la - (la - r0.from) / k + span });
      hold();
    };
    const panPx = (r0, dx) => {              // the chart moved dx px to the right
      if (!r0) return;
      const sh = -dx * (r0.to - r0.from) / Math.max(1, ts.width());
      ts.setVisibleLogicalRange({ from: r0.from + sh, to: r0.to + sh });
      hold();
    };
    el.addEventListener('pointerdown', (e) => { if (e.pointerType !== 'touch' && zone(e.clientX, e.clientY) !== 'price') hold(); }, true);   // a trackpad click-drag
    const busy = () => !!(window.PosTool && PosTool.tools.some(t => t.el === el && !t.dead && (t.blockMouse || t.drag || t.creating || t.arm)));

    let g = null, lastTap = { t: 0, x: 0, y: 0, z: '' }, mom = 0;
    const stopMom = () => { if (mom) cancelAnimationFrame(mom); mom = 0; };
    el.addEventListener('touchstart', (e) => {
      stopMom();
      if (busy()) { g = null; return; }       // the position tool has this finger
      e.preventDefault(); e.stopPropagation();
      const t = e.touches;
      if (t.length >= 2) {
        const a = t[0], b = t[1];
        g = { z: 'pinch', d0: Math.max(20, Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY)),
              x: localX((a.clientX + b.clientX) / 2), r0: ts.getVisibleLogicalRange() };
        return;
      }
      const p = t[0], z = zone(p.clientX, p.clientY), now = Date.now();
      const dbl = now - lastTap.t < 330 && lastTap.z === z && Math.hypot(p.clientX - lastTap.x, p.clientY - lastTap.y) < 30;
      lastTap = { t: dbl ? 0 : now, x: p.clientX, y: p.clientY, z };
      if (dbl && z === 'price') { g = null; autoFit(); return; }
      if (dbl && z === 'time') { g = null; fit(); return; }
      g = { z, x0: p.clientX, y0: p.clientY, ly: p.clientY, moved: false, axis: null, r0: ts.getVisibleLogicalRange(),
            m0: null, half0: null, vx: 0, lx: p.clientX, lt: now };
      if (z === 'price') { if (take()) g.half0 = manual.half; else g = null; }
    }, { capture: true, passive: false });
    el.addEventListener('touchmove', (e) => {
      if (!g) return;
      e.preventDefault(); e.stopPropagation();
      const t = e.touches;
      if (g.z === 'pinch') {
        if (t.length < 2) return;
        const d = Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
        zoomAt(g.r0, g.x, d / g.d0);
        return;
      }
      if (t.length >= 2) {                   // a second finger came down: a pinch from here
        const a = t[0], b = t[1];
        g = { z: 'pinch', d0: Math.max(20, Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY)),
              x: localX((a.clientX + b.clientX) / 2), r0: ts.getVisibleLogicalRange() };
        return;
      }
      const p = t[0], dx = p.clientX - g.x0, dy = p.clientY - g.y0;
      if (!g.moved && Math.hypot(dx, dy) < 6) return;
      g.moved = true;
      const now = Date.now();
      if (g.z === 'plot') {
        panPx(g.r0, dx);
        if (g.m0 == null && Math.abs(dy) > 10 && take()) { g.m0 = manual.mid; g.y0b = p.clientY; }
        if (g.m0 != null) { manual.mid = g.m0 + (p.clientY - g.y0b) * perPx(); refresh(); }
        const dt = Math.max(1, now - g.lt);
        g.vx = 0.8 * ((p.clientX - g.lx) / dt) + 0.2 * g.vx; g.lx = p.clientX; g.lt = now;
      } else if (g.z === 'price') {
        manual.half = Math.max(1e-6, g.half0 * Math.exp(-dy / (plotH() * 0.5)));
        refresh();
      } else if (g.z === 'time') {
        if (!g.axis) g.axis = Math.abs(dx) >= Math.abs(dy) ? 'x' : 'y';
        if (g.axis === 'y') { window.scrollBy(0, -(p.clientY - g.ly)); g.ly = p.clientY; return; }
        const r0 = g.r0;
        if (!r0) return;
        const span = Math.min(20000, Math.max(8, (r0.to - r0.from) * Math.exp(-dx / (ts.width() * 0.5))));
        ts.setVisibleLogicalRange({ from: r0.to - span, to: r0.to });
        hold();
      }
    }, { capture: true, passive: false });
    const end = (e) => {
      if (!g) return;
      e.stopPropagation();
      if (e.touches && e.touches.length) { g = null; return; }   // one finger of a pinch lifted: wait for a fresh touch
      const was = g; g = null;
      if (was.z === 'plot' && !was.moved && e.type === 'touchend') {   // a tap: the crosshair on that candle
        const r = el.getBoundingClientRect(), x = was.x0 - r.left, y = was.y0 - r.top;
        const time = ts.coordinateToTime(x), px = series.coordinateToPrice(y);
        if (time != null && px != null) { try { chart.setCrosshairPosition(px, time, series); } catch (err) { /* old library */ } }
      }
      if (was.z === 'plot' && was.moved && Math.abs(was.vx) > 0.25 && Date.now() - was.lt < 80) {   // momentum sideways
        let v = was.vx * 16;
        const step = () => {
          v *= 0.92;
          if (Math.abs(v) < 0.5) { mom = 0; return; }
          panPx(ts.getVisibleLogicalRange(), v);
          mom = requestAnimationFrame(step);
        };
        mom = requestAnimationFrame(step);
      }
    };
    el.addEventListener('touchend', end, true);
    el.addEventListener('touchcancel', end, true);

    // Magic Trackpad: two-finger swipes arrive as wheel events, a pinch as ctrl + wheel (or Safari gesture events, below)
    let wl = { t: 0, axis: null };
    el.addEventListener('wheel', (e) => {
      const z = zone(e.clientX, e.clientY), ax = Math.abs(e.deltaX), ay = Math.abs(e.deltaY);
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1;
      if (e.ctrlKey) { e.preventDefault(); zoomAt(ts.getVisibleLogicalRange(), localX(e.clientX), Math.exp(-e.deltaY * unit / 120)); return; }
      if (z === 'price' && ay > ax) {
        e.preventDefault();
        if (take()) { manual.half = Math.max(1e-6, manual.half * Math.exp(e.deltaY * unit / 400)); refresh(); }
        return;
      }
      const now = Date.now();
      if (now - wl.t > 160 || !wl.axis) wl.axis = ax > ay ? 'x' : 'y';
      wl.t = now;
      if (wl.axis === 'x') { e.preventDefault(); panPx(ts.getVisibleLogicalRange(), -e.deltaX * unit); }
      // up / down: the page scrolls, as on the desktop
    }, { capture: true, passive: false });
    el.addEventListener('dblclick', (e) => {        // a trackpad double-click on the price axis: auto-fit, the held range dropped too
      if (zone(e.clientX, e.clientY) === 'price') { manual = null; setTimeout(refresh, 0); }
    }, true);
    let gs = null;                                   // Safari's pinch events: never zoom the page over a chart
    el.addEventListener('gesturestart', (e) => {
      e.preventDefault();
      gs = { r0: ts.getVisibleLogicalRange(), x: e.clientX != null ? localX(e.clientX) : ts.width() / 2 };
    }, { passive: false });
    el.addEventListener('gesturechange', (e) => {
      e.preventDefault();
      if (gs && !(g && g.z === 'pinch') && e.scale > 0) zoomAt(gs.r0, gs.x, e.scale);   // a finger pinch is handled above
    }, { passive: false });
    el.addEventListener('gestureend', (e) => { e.preventDefault(); gs = null; }, { passive: false });

    // - / + / fit on the chart's title bar (after the other tools have put their buttons there)
    Promise.resolve().then(() => {
      const title = el.parentNode && el.parentNode.querySelector('.chart-title');
      const bar = title && (title.querySelector('.pos-bar') || title);
      if (!bar) return;
      const fs = bar.querySelector('.fs-btn');
      const mk = (txt, tip, fn) => {
        const b = document.createElement('button');
        b.type = 'button'; b.className = 'pos-btn tu-zoom'; b.textContent = txt; b.title = tip;
        b.addEventListener('click', (e) => { e.stopPropagation(); fn(); });
        b.addEventListener('dblclick', (e) => e.stopPropagation());
        bar.insertBefore(b, fs || null);
      };
      const w = () => ts.width() / 2;
      mk('−', 'Zoom out (time)', () => zoomAt(ts.getVisibleLogicalRange(), w(), 1 / 1.4));
      mk('+', 'Zoom in (time)', () => zoomAt(ts.getVisibleLogicalRange(), w(), 1.4));
      mk('⤢ fit', 'Fit the candles and auto-fit the price axis', fit);
    });
  }

  // the pane's size changed (Safari's bars, the keyboard, a rotation): true = keep what is on screen, false = fit the candles
  // (the first real size, or a new width while the chart was never moved by hand)
  function keep(cd, w) {
    if (!on || !cd) return false;
    const same = cd.tuW != null && Math.abs(cd.tuW - w) < 1;
    cd.tuW = w;
    return same || !!cd.tuHeld;
  }

  window.TouchUI = { on, attach, keep };
})();
