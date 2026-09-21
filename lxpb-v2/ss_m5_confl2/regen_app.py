"""Click-to-run regen for the ss_m5_confl2 reports (Tk window, no extra installs).

Runs render_m5_confl2_report.py once per selected report in a fresh process and
streams its output. Nothing about the strategy is restated here; this only
supplies --start/--end/--output/--workers/--max-rows.

Launch: double-click regen_ss_m5_confl2.bat (repo root), or
        pythonw ss_m5_confl2/regen_app.py
"""
import gzip
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
RENDER = os.path.join(HERE, "render_m5_confl2_report.py")
OUT_DIR = os.path.join(REPO, "public", "reports", "ss_m5_confl2")

# (label, output file, extra args, also write .html.gz)
REPORTS = [
    ("Jul-Aug 2026 (default report)", "jul-aug.html", [], False),
    ("Full 2025", "2025.html",
     ["--start", "2025-01-01", "--end", "2025-12-31"], True),
    ("Full 2026", "2026.html",
     ["--start", "2026-01-01", "--end", "2026-12-31"], True),
]


def python_exe():
    """Console python next to this interpreter (pythonw has no stdout)."""
    exe = sys.executable
    if os.path.basename(exe).lower() == "pythonw.exe":
        cand = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(cand):
            return cand
    return exe


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ss_m5_confl2 regen")
        self.geometry("980x640")
        self.q = queue.Queue()
        self.proc = None
        self.stop_flag = False

        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        self.sel = []
        for label, *_ in REPORTS:
            v = tk.BooleanVar(value=True)
            ttk.Checkbutton(top, text=label, variable=v).pack(side="left", padx=6)
            self.sel.append(v)

        opts = ttk.Frame(self, padding=(8, 0))
        opts.pack(fill="x")
        self.smoke = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            opts, variable=self.smoke,
            text="Smoke test first (--max-rows 20, written to a temp file, not the reports)",
        ).pack(side="left")
        ttk.Label(opts, text="   Workers:").pack(side="left")
        self.workers = tk.IntVar(value=1)
        ttk.Spinbox(opts, from_=1, to=8, width=3, textvariable=self.workers).pack(side="left")

        btns = ttk.Frame(self, padding=8)
        btns.pack(fill="x")
        self.run_btn = ttk.Button(btns, text="Run", command=self.start)
        self.run_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.push_btn = ttk.Button(btns, text="Commit && Push", command=self.commit_push)
        self.push_btn.pack(side="left", padx=(24, 0))
        self.status = ttk.Label(btns, text="Idle")
        self.status.pack(side="left", padx=12)

        self.log = tk.Text(self, wrap="none", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.after(100, self.pump)

    # -- ui plumbing -------------------------------------------------------
    def pump(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.log.insert("end", val)
                    self.log.see("end")
                elif kind == "status":
                    self.status.config(text=val)
                elif kind == "push_on":
                    self.push_btn.config(state="normal")
                elif kind == "done":
                    self.run_btn.config(state="normal")
                    self.push_btn.config(state="normal")
                    self.stop_btn.config(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self.pump)

    def start(self):
        jobs = [r for r, v in zip(REPORTS, self.sel) if v.get()]
        if not jobs:
            return
        self.stop_flag = False
        self.log.delete("1.0", "end")
        self.run_btn.config(state="disabled")
        self.push_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        threading.Thread(target=self.work, args=(jobs, self.smoke.get(), self.workers.get()),
                         daemon=True).start()

    def stop(self):
        self.stop_flag = True
        if self.proc and self.proc.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                           capture_output=True)

    # -- git ---------------------------------------------------------------
    def commit_push(self):
        if self.proc and self.proc.poll() is None:
            return
        self.run_btn.config(state="disabled")
        self.push_btn.config(state="disabled")
        threading.Thread(target=self.git_work, daemon=True).start()

    def git(self, *args):
        self.q.put(("log", f"\n$ git {' '.join(args)}\n"))
        p = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.q.put(("log", (p.stdout or "") + (p.stderr or "")))
        return p

    def git_work(self):
        try:
            self.q.put(("status", "Committing..."))
            rel = os.path.relpath(OUT_DIR, REPO)
            if self.git("add", "-A", "--", rel).returncode != 0:
                self.q.put(("status", "git add failed"))
                return
            if self.git("diff", "--cached", "--quiet", "--", rel).returncode == 0:
                self.q.put(("log", "\nNothing to commit in the report folder.\n"))
                self.q.put(("status", "Nothing to commit"))
                return
            msg = ("Regen ss_m5_confl2 reports\n\n"
                   "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>")
            if self.git("commit", "-m", msg, "--", rel).returncode != 0:
                self.q.put(("status", "Commit failed"))
                return
            self.q.put(("status", "Pushing..."))
            if self.git("push").returncode != 0:
                self.q.put(("status", "Push failed (committed locally)"))
                return
            self.q.put(("status", "Committed & pushed"))
        finally:
            self.q.put(("done", None))
            self.q.put(("push_on", None))

    # -- worker ------------------------------------------------------------
    def run_render(self, extra, output, max_rows, workers):
        cmd = [python_exe(), "-u", RENDER, *extra, "--output", output,
               "--workers", str(workers)]
        if max_rows:
            cmd += ["--max-rows", str(max_rows)]
        self.q.put(("log", f"\n$ {' '.join(cmd)}\n"))
        self.proc = subprocess.Popen(
            cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for line in self.proc.stdout:
            self.q.put(("log", line))
        return self.proc.wait()

    def work(self, jobs, smoke, workers):
        try:
            tmp_dir = os.path.join(HERE, "data", "_regen_smoke")
            if smoke:
                os.makedirs(tmp_dir, exist_ok=True)
                for label, name, extra, _ in jobs:
                    self.q.put(("status", f"Smoke test: {label}"))
                    rc = self.run_render(extra, os.path.join(tmp_dir, name), 20, 1)
                    if rc != 0 or self.stop_flag:
                        self.q.put(("log", f"\nSMOKE TEST FAILED/STOPPED ({label}) -- nothing written.\n"))
                        self.q.put(("status", "Failed" if not self.stop_flag else "Stopped"))
                        return
                shutil.rmtree(tmp_dir, ignore_errors=True)
            for label, name, extra, do_gz in jobs:
                self.q.put(("status", f"Regenerating: {label}"))
                final = os.path.join(OUT_DIR, name)
                part = final + ".new"
                rc = self.run_render(extra, part, None, workers)
                if rc != 0 or self.stop_flag or not os.path.exists(part):
                    if os.path.exists(part):
                        os.remove(part)
                    self.q.put(("log", f"\n{label}: FAILED/STOPPED -- previous report left untouched.\n"))
                    self.q.put(("status", "Failed" if not self.stop_flag else "Stopped"))
                    return
                os.replace(part, final)
                if do_gz:
                    with open(final, "rb") as f_in, gzip.open(final + ".gz", "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                self.q.put(("log", f"\n{label}: done -> {final}\n"))
            self.q.put(("status", "All done"))
        finally:
            self.q.put(("done", None))


if __name__ == "__main__":
    App().mainloop()
