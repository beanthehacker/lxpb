#!/bin/bash
# SessionStart hook, Claude Code cloud sessions only: make lxpb runnable with nothing set up by hand
# except the Google Drive file IDs of the .scid tick files (environment variable, see below).
#
#   1. Python packages the reports and research scripts need, installed only if missing.
#   2. `scidReader` importable from any script: acheron's copy when /home/user/acheron is checked out,
#      else lxpb-v2/scid_only/scidReader.py (same frames). Done with a .pth file in site-packages,
#      because render_labels_report.py only adds the Windows path D:\acheron\AcheronUtils.
#   3. The .scid files into /home/user/scid from LXPB_SCID_DRIVE_IDS, e.g.
#        LXPB_SCID_DRIVE_IDS="EPU26=<drive file id>,EPZ26=<drive file id>"
#      set once in the cloud environment's settings (Environment variables). The repo is public, so
#      the IDs never go in it. A file already there and whole (and fetched from the same ID) is kept.
#
# Idempotent and non-interactive. Nothing here fails the session: problems are printed, and the
# printout (stdout) is what the session's Claude reads at start.
set -uo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

REPO="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
SCID_DIR="${LXPB_SCID_DIR:-/home/user/scid}"
ACHERON_READER=/home/user/acheron/AcheronUtils
say() { echo "[lxpb cloud setup] $*"; }

pip_install() {
  pip install --quiet --disable-pip-version-check --root-user-action=ignore "$@" 2>&1 \
    || pip install --quiet --disable-pip-version-check --root-user-action=ignore --break-system-packages "$@" 2>&1
}

# ---- 1. Python packages -------------------------------------------------------------------------
# pandas / numpy come with the image (pandas 3.0.5, numpy 2.5.3 are what the cloud reports were run
# and checked with); pinned to those only if an image ever lacks them.
python3 -c "import pandas, numpy" 2>/dev/null || pip_install "pandas==3.0.5" "numpy==2.5.3" >/dev/null || pip_install pandas numpy >/dev/null
missing=()
for pair in pyarrow:pyarrow tzdata:tzdata scipy:scipy sklearn:scikit-learn matplotlib:matplotlib gdown:gdown; do
  python3 -c "import ${pair%%:*}" 2>/dev/null || missing+=("${pair##*:}")
done
if [ ${#missing[@]} -gt 0 ]; then
  if pip_install "${missing[@]}" >/dev/null; then
    say "installed: ${missing[*]}"
  else
    say "WARNING: pip could not install: ${missing[*]}"
  fi
fi

# ---- 2. scidReader ------------------------------------------------------------------------------
if [ -f "$ACHERON_READER/scidReader.py" ]; then
  reader_dir="$ACHERON_READER"
else
  reader_dir="$REPO/lxpb-v2/scid_only"
fi
site_dir="$(python3 -c 'import site; print(site.getsitepackages()[0])' 2>/dev/null)"
if [ -n "$site_dir" ] && [ -d "$site_dir" ]; then
  echo "$reader_dir" > "$site_dir/lxpb_scidreader.pth"
fi
if python3 -c "import scidReader" 2>/dev/null; then
  say "scidReader: $reader_dir"
else
  say "WARNING: scidReader is not importable (tried $reader_dir)"
fi

# ---- 3. .scid tick files ------------------------------------------------------------------------
whole() {   # a .scid is a 56-byte header plus whole 40-byte records
  local f="$1" n
  [ -f "$f" ] || return 1
  n=$(stat -c %s "$f")
  [ "$n" -ge 96 ] && [ $(( (n - 56) % 40 )) -eq 0 ]
}

mkdir -p "$SCID_DIR"
ids="${LXPB_SCID_DRIVE_IDS:-}"
if [ -z "$ids" ]; then
  say "LXPB_SCID_DRIVE_IDS is not set: no .scid download. Ask the user to add it to the cloud"
  say "environment's Environment variables as EPU26=<drive file id>,EPZ26=<drive file id>."
else
  for pair in ${ids//,/ }; do
    sym="${pair%%=*}"
    id="${pair#*=}"
    if [ -z "$sym" ] || [ -z "$id" ] || [ "$sym" = "$pair" ]; then
      say "WARNING: ignoring malformed entry '$pair' in LXPB_SCID_DRIVE_IDS (want SYM=fileid)"
      continue
    fi
    f="$SCID_DIR/F.US.$sym.scid"
    if whole "$f" && [ "$(cat "$f.driveid" 2>/dev/null || echo "$id")" = "$id" ]; then
      continue
    fi
    rm -f "$f.part"
    if python3 -c "import sys, gdown; sys.exit(0 if gdown.download(id=sys.argv[1], output=sys.argv[2], quiet=True) else 1)" \
         "$id" "$f.part" >/dev/null 2>&1 && whole "$f.part"; then
      mv -f "$f.part" "$f"
      echo "$id" > "$f.driveid"
      say "downloaded F.US.$sym.scid"
    else
      rm -f "$f.part"
      say "WARNING: could not download F.US.$sym.scid (Drive id $id): check the id and that the file is shared 'anyone with the link'"
    fi
  done
fi

# What is on disk, with each file's tick span (read off its first and last records).
python3 - "$SCID_DIR" <<'PY' 2>/dev/null
import glob, os, sys
import numpy as np
SHIFT = 2209161600000000
rec = np.dtype([("t", "<u8"), ("rest", "V32")])
files = sorted(glob.glob(os.path.join(sys.argv[1], "F.US.*.scid")))
if not files:
    print(f"[lxpb cloud setup] no .scid files in {sys.argv[1]}")
for f in files:
    mm = np.memmap(f, dtype=rec, offset=56, mode="r")
    span = ""
    if len(mm):
        a, b = (np.datetime64(int(mm["t"][i]) - SHIFT, "us") for i in (0, -1))
        span = f", ticks {str(a)[:16]} -> {str(b)[:16]} UTC"
    print(f"[lxpb cloud setup] {os.path.basename(f)}: {os.path.getsize(f) / 1e6:,.0f} MB{span}")
PY
exit 0
