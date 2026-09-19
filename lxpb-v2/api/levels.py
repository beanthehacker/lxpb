"""
/api/levels -- the data behind the /levels page (a Vercel Python function).

    GET  /api/levels           {"meta": {...}, "state": {...} | null}
    GET  /api/levels?meta=1    {"meta": {...}}                     (cheap poll)
    POST /api/levels           refresh if the stored state is from a previous
                               hour, then return the same as GET
    POST /api/levels?force=1   refresh regardless

`meta` = {built_at, now, stale, building, error}. The page calls POST when
`stale` is true, i.e. the first time anyone opens it in a new hour; a refresh
takes a few seconds once the ledger cache exists (about a minute the first
time) and holds the request open until it is done. Requests are gated by the
app's proxy.ts like every other route, so only the allowed Google account gets
here. The heavy work happens in levels_runtime/refresh.py, run as a
subprocess, so this module itself imports nothing beyond the store.
"""
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

_RUNTIME = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "levels_runtime")
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)


def _store():
    import store
    return store.default_store()


def _meta(st):
    import store
    return store.meta(st)


def run_refresh(force):
    """Run refresh.py in a fresh interpreter; returns (ok, message)."""
    cmd = [sys.executable, os.path.join(_RUNTIME, "refresh.py")] + (["--force"] if force else [])
    # Vercel's Python runtime puts the installed dependencies (pandas, ...) on
    # THIS interpreter's sys.path, not in the environment, so a bare child
    # interpreter cannot import them: hand it the parent's whole import path.
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=290, env=env)
    except subprocess.TimeoutExpired:
        return False, "refresh timed out"
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "refresh failed").strip().splitlines()[-1:]
        msg = tail[0] if tail else "refresh failed"
        if "ModuleNotFoundError" in msg:   # say whether the function itself has the dependency
            import importlib.util
            msg += f" [function process sees pandas: {importlib.util.find_spec('pandas') is not None}]"
        return False, msg
    return True, (p.stdout.strip().splitlines() or ["done"])[-1]


class handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _payload(self, st, with_state, extra=None):
        meta = _meta(st)
        if extra:
            meta.update(extra)
        state = st.get("state") if with_state else None
        # The stored state is already JSON: splice it in rather than re-parse ~1 MB.
        body = (b'{"meta":' + json.dumps(meta).encode() + b',"state":' +
                (state if state else b"null") + b"}")
        self._send(200, body)

    def do_GET(self):
        try:
            st = _store()
            q = parse_qs(urlparse(self.path).query)
            self._payload(st, with_state="meta" not in q)
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        try:
            st = _store()
            q = parse_qs(urlparse(self.path).query)
            ok, msg = run_refresh(force="force" in q)
            self._payload(st, with_state=True, extra={"refresh": msg, "refresh_ok": ok})
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})
