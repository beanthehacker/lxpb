// Shared per-report row-state store, backed by /api/rows (Postgres) instead
// of localStorage, so Reviewed/Valid/Replayed/Notes-style state syncs across
// devices. Loaded via <script src="/js/row-store.js"> before each report's
// inline script.
//
// Usage:
//   const store = new RowStore(REPORT_KEY);
//   await store.init();            // hydrates store.cache from the server
//   store.get(rowKey);             // -> fields object (sync, from cache)
//   store.set(rowKey, fields);     // persists immediately
//   store.set(rowKey, fields, { debounceMs: 500 }); // coalesces rapid calls (e.g. typing)
//   await store.clearAll();
//   await store.bulkSet({ rowKey: fields, ... });    // CSV import
(function (global) {
  function RowStore(reportKey) {
    this.reportKey = reportKey;
    this.cache = {};
    this._timers = {};
  }

  RowStore.prototype.init = async function () {
    try {
      const res = await fetch("/api/rows?report=" + encodeURIComponent(this.reportKey));
      this.cache = res.ok ? await res.json() : {};
    } catch (e) {
      // Opened as a local file, or the API is unreachable -- degrade to an
      // empty, in-memory-only store rather than breaking row init entirely.
      this.cache = {};
    }
    return this.cache;
  };

  RowStore.prototype.getAll = function () {
    return this.cache;
  };

  RowStore.prototype.get = function (key) {
    return this.cache[key] || {};
  };

  RowStore.prototype.set = function (key, fields, opts) {
    opts = opts || {};
    this.cache[key] = fields;
    const reportKey = this.reportKey;
    const send = function () {
      fetch("/api/rows", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ report: reportKey, key: key, fields: fields }),
      }).catch(function () {
        /* best-effort; row stays correct in local cache/UI, will resync on next init() */
      });
    };
    if (!opts.debounceMs) {
      send();
      return;
    }
    const timerKey = key;
    if (this._timers[timerKey]) clearTimeout(this._timers[timerKey]);
    this._timers[timerKey] = setTimeout(send, opts.debounceMs);
  };

  RowStore.prototype.clearAll = async function () {
    this.cache = {};
    await fetch("/api/rows?report=" + encodeURIComponent(this.reportKey), { method: "DELETE" });
  };

  RowStore.prototype.bulkSet = async function (entries) {
    Object.assign(this.cache, entries);
    await fetch("/api/rows/bulk", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ report: this.reportKey, entries: entries }),
    });
  };

  global.RowStore = RowStore;
})(window);
