"""
dev_server.py -- run the /levels stack locally without Vercel or a login.

    python levels_runtime/dev_server.py [--port 8770]     ->  http://127.0.0.1:8770/levels

Serves api/levels.py's handler (the real one) over a file-backed store in
levels_runtime/.dev_store, plus public/levels/* for the page. To run it under
`next dev` instead, set LEVELS_PY_DEV=http://127.0.0.1:8770 for Next so its
rewrite forwards /api/levels here (the app's Google login still applies there).
"""
import argparse
import mimetypes
import os
import sys
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "api"))
import levels as api  # noqa: E402

PUBLIC = os.path.join(ROOT, "public")


class Dev(api.handler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/levels":
            return super().do_GET()
        rel = "levels/index.html" if path.rstrip("/") == "/levels" else path.lstrip("/")
        full = os.path.normpath(os.path.join(PUBLIC, rel))
        if not full.startswith(os.path.join(PUBLIC, "levels")) or not os.path.isfile(full):
            self.send_error(404)
            return
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(full)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path.split("?")[0] == "/api/levels":
            return super().do_POST()
        self.send_error(404)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8770)
    args = ap.parse_args()
    print(f"levels dev server: http://127.0.0.1:{args.port}/levels", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Dev).serve_forever()
