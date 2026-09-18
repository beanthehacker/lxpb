"""
serve.py -- local server for the upcoming-M5-levels dashboard.

    python serve.py [--port 8765]        then open http://127.0.0.1:8765

Serves the page and the last built state, and re-runs build.py at the top of
every hour (a few seconds past it, so the hour's last M5/H1 bars have closed
and TradingView has them). Refreshing happens HERE rather than in the browser
because TradingView's data socket only accepts a tradingview.com Origin, which
a page cannot send; the page just polls /api/status and reloads its data when
a new build lands. A failed build keeps the previous state on screen, shows
the error, and retries every couple of minutes.

    GET  /               the page
    GET  /api/state      state.json from the last good build
    GET  /api/status     {built_at, building, error, next_run}
    POST /api/refresh    start a build now (ignored if one is running)
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(_HERE, "data", "state.json")
LOG_PATH = os.path.join(_HERE, "data", "build.log")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/static/lightweight-charts.js": (os.path.join("static", "lightweight-charts.js"),
                                            "application/javascript")}
HOUR_LAG_S = 45          # run this long after the top of the hour
RETRY_S = 120
MAX_RETRIES = 5

_lock = threading.Lock()
_status = {"building": False, "error": None, "next_run": None}
_wake = threading.Event()


def _built_at():
    try:
        return int(os.path.getmtime(STATE_PATH))
    except OSError:
        return None


def _next_hour_run(now=None):
    now = time.time() if now is None else now
    return (int(now // 3600) + 1) * 3600 + HOUR_LAG_S


def _run_build():
    """One build subprocess. Returns True on success. Never raises."""
    with _lock:
        if _status["building"]:
            return False
        _status["building"] = True
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "w") as log:
            rc = subprocess.run([sys.executable, os.path.join(_HERE, "build.py")],
                                stdout=log, stderr=subprocess.STDOUT).returncode
        if rc != 0:
            with open(LOG_PATH) as f:
                tail = f.read().strip().splitlines()[-1:] or ["build failed"]
            raise RuntimeError(f"build exited {rc}: {tail[0]}")
        _status["error"] = None
        return True
    except Exception as e:                       # keep serving the old state
        _status["error"] = str(e)
        return False
    finally:
        _status["building"] = False


def _scheduler():
    built, now = _built_at(), time.time()
    this_run = _next_hour_run(now) - 3600
    last_due = this_run if now >= this_run else this_run - 3600   # latest hourly run time already passed
    ok = _run_build() if built is None or built < last_due else True
    retries = 0
    while True:
        if ok:
            retries, due = 0, _next_hour_run()
        else:
            retries += 1
            due = time.time() + RETRY_S if retries <= MAX_RETRIES else _next_hour_run()
            if retries > MAX_RETRIES:
                retries = 0
        _status["next_run"] = int(due)
        _wake.clear()
        while time.time() < due and not _wake.is_set():
            _wake.wait(timeout=min(30, max(0.0, due - time.time())))
        ok = _run_build()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype, cache="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in STATIC:
            name, ctype = STATIC[path]
            with open(os.path.join(_HERE, name), "rb") as f:
                self._send(200, f.read(), ctype,
                           cache="max-age=86400" if path.startswith("/static") else "no-store")
        elif path == "/api/state":
            try:
                with open(STATE_PATH, "rb") as f:
                    self._send(200, f.read(), "application/json")
            except OSError:
                self._json({"error": "no build yet"}, 503)
        elif path == "/api/status":
            self._json({"built_at": _built_at(), **_status, "now": int(time.time())})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path.split("?")[0] == "/api/refresh":
            if not _status["building"]:
                _wake.set()
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, *args):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    threading.Thread(target=_scheduler, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"upcoming M5 levels dashboard: http://127.0.0.1:{args.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
