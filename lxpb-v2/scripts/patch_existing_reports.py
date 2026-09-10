#!/usr/bin/env python3
"""One-off migration: patch already-generated static HTML reports under
public/reports/ so their review-tracking JS talks to the new /api/rows
(Postgres) backend instead of browser localStorage -- mirrors the same edits
already made to the render_*.py source templates, applied here directly to
the pre-existing HTML bytes so we don't have to re-run expensive data
pipelines just to pick up the storage-backend change.

Every literal block below was transcribed verbatim from the *original*
(pre-edit) render_stop_target_report.py / render_cluster_selection_report.py
/ render_labels_report.py JS templates -- since JS_TEMPLATE/JS content is
shared byte-for-byte across every report that uses a given family (only the
REVIEW_STORAGE_KEY / STORAGE_KEY value differs), each pattern should match
exactly once per applicable file.

Usage: python scripts/patch_existing_reports.py [--dry-run]
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "public" / "reports"

HEAD_CLOSE_OLD = "</head>"
HEAD_CLOSE_NEW = '<script src="/js/row-store.js"></script>\n</head>'

# ---------------------------------------------------------------------------
# Canonical family (render_stop_target_report.py JS, reused verbatim by
# render_ss_confl_finetune_report.py and ss_m5_confl2/render_m5_confl2_report.py)
# ---------------------------------------------------------------------------

CANON_KEY_OLD = re.compile(
    r"const REVIEW_STORAGE_KEY = '([^']*)';\n"
    r"\n"
    r"function loadReviewStore\(\) \{\n"
    r"  try \{ return JSON\.parse\(localStorage\.getItem\(REVIEW_STORAGE_KEY\) \|\| '\{\}'\); \}\n"
    r"  catch \(e\) \{ return \{\}; \}\n"
    r"\}\n"
    r"function saveReviewStore\(store\) \{ localStorage\.setItem\(REVIEW_STORAGE_KEY, JSON\.stringify\(store\)\); \}\n"
    r"\n"
    r"function reviewRowState\(tr\) \{"
)


def canon_key_new(m: re.Match) -> str:
    key = m.group(1)
    return (
        f"const REVIEW_STORAGE_KEY = '{key}';\n"
        "const reviewStore = new RowStore(REVIEW_STORAGE_KEY);\n"
        "\n"
        "function reviewRowState(tr) {"
    )


CANON_PERSIST_OLD = """function persistReviewRow(tr) {
  const store = loadReviewStore();
  store[tr.dataset.key] = reviewRowState(tr);
  saveReviewStore(store);
  tr.classList.toggle('is-reviewed', !!store[tr.dataset.key].reviewed);
  tr.classList.toggle('is-valid', !!store[tr.dataset.key].valid);
  tr.classList.toggle('is-replayed', !!store[tr.dataset.key].replayed);
  updateReviewSummary();
  applyReviewFilters();
}"""

CANON_PERSIST_NEW = """function persistReviewRow(tr, opts) {
  const state = reviewRowState(tr);
  reviewStore.set(tr.dataset.key, state, opts);
  tr.classList.toggle('is-reviewed', !!state.reviewed);
  tr.classList.toggle('is-valid', !!state.valid);
  tr.classList.toggle('is-replayed', !!state.replayed);
  updateReviewSummary();
  applyReviewFilters();
}"""

# Two historical variants exist in already-generated files: an older one
# (no null-guard on .trade-note, since every .lvl-row is guaranteed to have
# one) and a newer one (guarded). ss_m5_confl2's report was regenerated more
# recently than the stop/target and ss_confl finetune reports and already
# has the guarded form.
CANON_INIT_OLD_GUARDED = """function initReview() {
  const store = loadReviewStore();
  document.querySelectorAll('.lvl-row').forEach(tr => {
    applyReviewRowState(tr, store[tr.dataset.key]);
    tr.querySelectorAll('.reviewed-cb, .valid-cb, .replayed-cb, .trade-note').forEach(el => {
      el.addEventListener('change', () => persistReviewRow(tr));
    });
    const noteEl = tr.querySelector('.trade-note');
    if (noteEl) noteEl.addEventListener('input', () => persistReviewRow(tr));
  });
  updateReviewSummary();
}"""

CANON_INIT_OLD_DIRECT = """function initReview() {
  const store = loadReviewStore();
  document.querySelectorAll('.lvl-row').forEach(tr => {
    applyReviewRowState(tr, store[tr.dataset.key]);
    tr.querySelectorAll('.reviewed-cb, .valid-cb, .replayed-cb, .trade-note').forEach(el => {
      el.addEventListener('change', () => persistReviewRow(tr));
    });
    tr.querySelector('.trade-note').addEventListener('input', () => persistReviewRow(tr));
  });
  updateReviewSummary();
}"""

CANON_INIT_NEW = """async function initReview() {
  await reviewStore.init();
  document.querySelectorAll('.lvl-row').forEach(tr => {
    applyReviewRowState(tr, reviewStore.get(tr.dataset.key));
    tr.querySelectorAll('.reviewed-cb, .valid-cb, .replayed-cb').forEach(el => {
      el.addEventListener('change', () => persistReviewRow(tr));
    });
    const noteEl = tr.querySelector('.trade-note');
    if (noteEl) noteEl.addEventListener('input', () => persistReviewRow(tr, { debounceMs: 500 }));
  });
  updateReviewSummary();
  applyReviewFilters();
}"""

CANON_EXPORT_OLD = """function exportReviewCsv() {
  const store = loadReviewStore();
  const header = ['key', 'reviewed', 'valid', 'replayed', 'notes'];"""

CANON_EXPORT_NEW = """function exportReviewCsv() {
  const store = reviewStore.getAll();
  const header = ['key', 'reviewed', 'valid', 'replayed', 'notes'];"""

CANON_IMPORT_CLEAR_OLD = """function importReviewCsv(evt) {
  const file = evt.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const lines = reader.result.split(/\\r?\\n/).filter(l => l.length);
    const header = parseReviewCsvLine(lines[0]);
    const store = loadReviewStore();
    for (let i = 1; i < lines.length; i++) {
      const cols = parseReviewCsvLine(lines[i]);
      const rec = {};
      header.forEach((h, j) => rec[h] = cols[j]);
      if (!rec.key) continue;
      store[rec.key] = { reviewed: rec.reviewed === '1', valid: rec.valid === '1',
                         replayed: rec.replayed === '1', notes: rec.notes || '' };
    }
    saveReviewStore(store);
    document.querySelectorAll('.lvl-row').forEach(tr => applyReviewRowState(tr, store[tr.dataset.key]));
    updateReviewSummary();
    applyReviewFilters();
    evt.target.value = '';
    alert('Imported review notes from ' + file.name);
  };
  reader.readAsText(file);
}
function clearAllReview() {
  localStorage.removeItem(REVIEW_STORAGE_KEY);
  document.querySelectorAll('.lvl-row').forEach(tr => applyReviewRowState(tr, {}));
  updateReviewSummary();
  applyReviewFilters();
}"""

CANON_IMPORT_CLEAR_NEW = """function importReviewCsv(evt) {
  const file = evt.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = async () => {
    const lines = reader.result.split(/\\r?\\n/).filter(l => l.length);
    const header = parseReviewCsvLine(lines[0]);
    const entries = {};
    for (let i = 1; i < lines.length; i++) {
      const cols = parseReviewCsvLine(lines[i]);
      const rec = {};
      header.forEach((h, j) => rec[h] = cols[j]);
      if (!rec.key) continue;
      entries[rec.key] = { reviewed: rec.reviewed === '1', valid: rec.valid === '1',
                         replayed: rec.replayed === '1', notes: rec.notes || '' };
    }
    await reviewStore.bulkSet(entries);
    document.querySelectorAll('.lvl-row').forEach(tr => applyReviewRowState(tr, reviewStore.get(tr.dataset.key)));
    updateReviewSummary();
    applyReviewFilters();
    evt.target.value = '';
    alert('Imported review notes from ' + file.name);
  };
  reader.readAsText(file);
}
async function clearAllReview() {
  await reviewStore.clearAll();
  document.querySelectorAll('.lvl-row').forEach(tr => applyReviewRowState(tr, {}));
  updateReviewSummary();
  applyReviewFilters();
}"""

# Older reports predate the MAE/MFE numeric-filter (.f-num-op/.f-num-val)
# feature, so their bootstrap tail is shorter -- two variants.
CANON_BOOT_OLD_WITH_NUMFILTER = """document.querySelectorAll('.f-num-op, .f-num-val')
  .forEach(el => el.addEventListener('input', applyReviewFilters));
initReview();
applyReviewFilters();
</script>"""

CANON_BOOT_NEW_WITH_NUMFILTER = """document.querySelectorAll('.f-num-op, .f-num-val')
  .forEach(el => el.addEventListener('input', applyReviewFilters));
initReview();
</script>"""

CANON_BOOT_OLD_NO_NUMFILTER = """document.querySelectorAll('.f-review-status, .f-review-valid, .f-review-replay, .f-review-notes')
  .forEach(cb => cb.addEventListener('change', applyReviewFilters));
initReview();
applyReviewFilters();
</script>"""

CANON_BOOT_NEW_NO_NUMFILTER = """document.querySelectorAll('.f-review-status, .f-review-valid, .f-review-replay, .f-review-notes')
  .forEach(cb => cb.addEventListener('change', applyReviewFilters));
initReview();
</script>"""

# ---------------------------------------------------------------------------
# Legacy A (render_cluster_selection_report.py)
# ---------------------------------------------------------------------------

LEGACY_A_KEY_OLD = """const STORAGE_KEY = 'lxpb_cluster_selection_v1';
const rendered = {};
const FIXED_BAR_SPACING = 6;
const H1_MAX_BAR_SPACING = 22;

/* ---------------- labeling / persistence ---------------- */
function loadStore() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); }
  catch (e) { return {}; }
}
function saveStore(s) { localStorage.setItem(STORAGE_KEY, JSON.stringify(s)); }

function rowState(tr) {"""

LEGACY_A_KEY_NEW = """const STORAGE_KEY = 'lxpb_cluster_selection_v1';
// Labels persist server-side (Postgres, via /api/rows) instead of browser
// localStorage, so they sync across devices.
const clusterStore = new RowStore(STORAGE_KEY);
const rendered = {};
const FIXED_BAR_SPACING = 6;
const H1_MAX_BAR_SPACING = 22;

/* ---------------- labeling / persistence ---------------- */
function rowState(tr) {"""

LEGACY_A_PERSIST_OLD = """function persistRow(tr) {
  const store = loadStore();
  store[tr.dataset.key] = rowState(tr);
  saveStore(store);
  markFlags(tr);
  updateSummary();
}"""

LEGACY_A_PERSIST_NEW = """function persistRow(tr, opts) {
  const state = rowState(tr);
  clusterStore.set(tr.dataset.key, state, opts);
  markFlags(tr);
  updateSummary();
}"""

LEGACY_A_INIT_OLD = """function initRows() {
  const store = loadStore();
  document.querySelectorAll('.lvl-row').forEach(tr => {
    applyDefaults(tr);
    applyRowState(tr, store[tr.dataset.key]);
    tr.querySelectorAll('.reviewed-cb, .chk-cb, .misc-note').forEach(el => {
      el.addEventListener('change', () => persistRow(tr));
    });
    tr.querySelector('.misc-note').addEventListener('input', () => persistRow(tr));
  });
  updateSummary();
}"""

LEGACY_A_INIT_NEW = """async function initRows() {
  await clusterStore.init();
  document.querySelectorAll('.lvl-row').forEach(tr => {
    applyDefaults(tr);
    applyRowState(tr, clusterStore.get(tr.dataset.key));
    tr.querySelectorAll('.reviewed-cb, .chk-cb').forEach(el => {
      el.addEventListener('change', () => persistRow(tr));
    });
    tr.querySelector('.misc-note').addEventListener('input', () => persistRow(tr, { debounceMs: 500 }));
  });
  updateSummary();
  applyFilters();
}"""

LEGACY_A_EXPORT_OLD = """function exportCsv() {
  const store = loadStore();
  const chkCols = CHECKS.map(c => c.id);"""

LEGACY_A_EXPORT_NEW = """function exportCsv() {
  const store = clusterStore.getAll();
  const chkCols = CHECKS.map(c => c.id);"""

LEGACY_A_IMPORT_CLEAR_OLD = """function importCsv(evt) {
  const file = evt.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const lines = reader.result.split(/\\r?\\n/).filter(l => l.length);
    const header = parseCsvLine(lines[0]);
    const store = loadStore();
    for (let i = 1; i < lines.length; i++) {
      const cols = parseCsvLine(lines[i]);
      const rec = {};
      header.forEach((h, j) => rec[h] = cols[j]);
      if (!rec.key) continue;
      const st = { misc: rec.misc || '' };
      if (header.includes('reviewed')) st.reviewed = rec.reviewed === '1';
      CHECKS.forEach(c => { if (header.includes(c.id)) st[c.id] = rec[c.id] === '1'; });
      store[rec.key] = st;
    }
    saveStore(store);
    document.querySelectorAll('.lvl-row').forEach(tr => applyRowState(tr, store[tr.dataset.key]));
    updateSummary();
    evt.target.value = '';
    alert('Imported labels from ' + file.name);
  };
  reader.readAsText(file);
}
function clearAll() {
  localStorage.removeItem(STORAGE_KEY);
  document.querySelectorAll('.lvl-row').forEach(tr => {
    applyDefaults(tr);
    tr.querySelector('.misc-note').value = '';
  });
  updateSummary();
}"""

LEGACY_A_IMPORT_CLEAR_NEW = """function importCsv(evt) {
  const file = evt.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = async () => {
    const lines = reader.result.split(/\\r?\\n/).filter(l => l.length);
    const header = parseCsvLine(lines[0]);
    const entries = {};
    for (let i = 1; i < lines.length; i++) {
      const cols = parseCsvLine(lines[i]);
      const rec = {};
      header.forEach((h, j) => rec[h] = cols[j]);
      if (!rec.key) continue;
      const st = { misc: rec.misc || '' };
      if (header.includes('reviewed')) st.reviewed = rec.reviewed === '1';
      CHECKS.forEach(c => { if (header.includes(c.id)) st[c.id] = rec[c.id] === '1'; });
      entries[rec.key] = st;
    }
    await clusterStore.bulkSet(entries);
    document.querySelectorAll('.lvl-row').forEach(tr => applyRowState(tr, clusterStore.get(tr.dataset.key)));
    updateSummary();
    evt.target.value = '';
    alert('Imported labels from ' + file.name);
  };
  reader.readAsText(file);
}
async function clearAll() {
  await clusterStore.clearAll();
  document.querySelectorAll('.lvl-row').forEach(tr => {
    applyDefaults(tr);
    tr.querySelector('.misc-note').value = '';
  });
  updateSummary();
}"""

LEGACY_A_BOOT_OLD = """document.querySelectorAll('.f-cb').forEach(cb => cb.addEventListener('change', applyFilters));
initRows();
applyFilters();
</script>"""

LEGACY_A_BOOT_NEW = """document.querySelectorAll('.f-cb').forEach(cb => cb.addEventListener('change', applyFilters));
initRows();
</script>"""

# ---------------------------------------------------------------------------
# Legacy B (render_labels_report.py -- also lxpb_strong_breakout_report.html,
# a frozen output of an older copy of the same script)
# ---------------------------------------------------------------------------

LEGACY_B_KEY_OLD = """function loadStore() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); }
  catch (e) { return {}; }
}
function saveStore(store) { localStorage.setItem(STORAGE_KEY, JSON.stringify(store)); }

function rowState(tr) {"""

LEGACY_B_KEY_NEW = """function rowState(tr) {"""

LEGACY_B_STORAGE_DECL_OLD = re.compile(r"(const STORAGE_KEY = '[^']*';\n)(const rendered = \{\};)")


def legacy_b_storage_decl_new(m: re.Match) -> str:
    return (
        f"{m.group(1)}"
        "// Labels persist server-side (Postgres, via /api/rows) instead of browser\n"
        "// localStorage, so they sync across devices.\n"
        "const labelStore = new RowStore(STORAGE_KEY);\n"
        f"{m.group(2)}"
    )


LEGACY_B_PERSIST_OLD = """function persistRow(tr) {
  const store = loadStore();
  store[tr.dataset.key] = rowState(tr);
  saveStore(store);
  tr.classList.toggle('is-reviewed', !!store[tr.dataset.key].reviewed);
  tr.classList.toggle('is-invalid', !store[tr.dataset.key].valid);
  updateSummary();
}"""

LEGACY_B_PERSIST_NEW = """function persistRow(tr, opts) {
  const state = rowState(tr);
  labelStore.set(tr.dataset.key, state, opts);
  tr.classList.toggle('is-reviewed', !!state.reviewed);
  tr.classList.toggle('is-invalid', !state.valid);
  updateSummary();
}"""

LEGACY_B_INIT_OLD = """function initRows() {
  const store = loadStore();
  document.querySelectorAll('.lvl-row').forEach(tr => {
    applyDefaults(tr);
    applyRowState(tr, store[tr.dataset.key]);
    tr.querySelectorAll('.reviewed-cb, .valid-cb, .feat-cb, .misc-note').forEach(el => {
      el.addEventListener('change', () => persistRow(tr));
    });
    tr.querySelector('.misc-note').addEventListener('input', () => persistRow(tr));
  });
  updateSummary();
}"""

LEGACY_B_INIT_NEW = """async function initRows() {
  await labelStore.init();
  document.querySelectorAll('.lvl-row').forEach(tr => {
    applyDefaults(tr);
    applyRowState(tr, labelStore.get(tr.dataset.key));
    tr.querySelectorAll('.reviewed-cb, .valid-cb, .feat-cb').forEach(el => {
      el.addEventListener('change', () => persistRow(tr));
    });
    tr.querySelector('.misc-note').addEventListener('input', () => persistRow(tr, { debounceMs: 500 }));
  });
  updateSummary();
  applyFilters();
}"""

LEGACY_B_EXPORT_OLD = """function exportCsv() {
  const store = loadStore();
  const featCols = FEATURES.map(f => f.id);"""

LEGACY_B_EXPORT_NEW = """function exportCsv() {
  const store = labelStore.getAll();
  const featCols = FEATURES.map(f => f.id);"""

LEGACY_B_IMPORT_CLEAR_OLD = """function importCsv(evt) {
  const file = evt.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const lines = reader.result.split(/\\r?\\n/).filter(l => l.length);
    const header = parseCsvLine(lines[0]);
    const store = loadStore();
    for (let i = 1; i < lines.length; i++) {
      const cols = parseCsvLine(lines[i]);
      const rec = {};
      header.forEach((h, j) => rec[h] = cols[j]);
      const key = rec.key;
      if (!key) continue;
      const state = { misc: rec.misc || '' };
      if (header.includes('reviewed')) state.reviewed = rec.reviewed === '1';
      if (header.includes('valid')) state.valid = rec.valid === '1';
      FEATURES.forEach(f => { if (header.includes(f.id)) state[f.id] = rec[f.id] === '1'; });
      store[key] = state;
    }
    saveStore(store);
    document.querySelectorAll('.lvl-row').forEach(tr => applyRowState(tr, store[tr.dataset.key]));
    updateSummary();
    evt.target.value = '';
    alert('Imported labels from ' + file.name);
  };
  reader.readAsText(file);
}

function clearAll() {
  localStorage.removeItem(STORAGE_KEY);
  document.querySelectorAll('.lvl-row').forEach(tr => applyDefaults(tr));
  updateSummary();
}"""

LEGACY_B_IMPORT_CLEAR_NEW = """function importCsv(evt) {
  const file = evt.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = async () => {
    const lines = reader.result.split(/\\r?\\n/).filter(l => l.length);
    const header = parseCsvLine(lines[0]);
    const entries = {};
    for (let i = 1; i < lines.length; i++) {
      const cols = parseCsvLine(lines[i]);
      const rec = {};
      header.forEach((h, j) => rec[h] = cols[j]);
      const key = rec.key;
      if (!key) continue;
      const state = { misc: rec.misc || '' };
      if (header.includes('reviewed')) state.reviewed = rec.reviewed === '1';
      if (header.includes('valid')) state.valid = rec.valid === '1';
      FEATURES.forEach(f => { if (header.includes(f.id)) state[f.id] = rec[f.id] === '1'; });
      entries[key] = state;
    }
    await labelStore.bulkSet(entries);
    document.querySelectorAll('.lvl-row').forEach(tr => applyRowState(tr, labelStore.get(tr.dataset.key)));
    updateSummary();
    evt.target.value = '';
    alert('Imported labels from ' + file.name);
  };
  reader.readAsText(file);
}

async function clearAll() {
  await labelStore.clearAll();
  document.querySelectorAll('.lvl-row').forEach(tr => applyDefaults(tr));
  updateSummary();
}"""

LEGACY_B_BOOT_OLD = """initRows();
applyFilters();
</script>"""

LEGACY_B_BOOT_NEW = """initRows();
</script>"""


class PatchError(Exception):
    pass


def apply_literal(html: str, old: str, new: str, label: str, path: Path) -> str:
    count = html.count(old)
    if count != 1:
        raise PatchError(f"[{path.name}] pattern '{label}' matched {count} times (expected 1)")
    return html.replace(old, new, 1)


def apply_literal_variants(html: str, variants: list, label: str, path: Path) -> str:
    """variants: list of (old, new) pairs. Use whichever old-text matches exactly once."""
    counts = [html.count(old) for old, _new in variants]
    hits = [i for i, c in enumerate(counts) if c == 1]
    if len(hits) != 1:
        raise PatchError(f"[{path.name}] pattern '{label}' variant match counts {counts} (expected exactly one variant to match once)")
    old, new = variants[hits[0]]
    return html.replace(old, new, 1)


def apply_regex(html: str, pattern: "re.Pattern", repl, label: str, path: Path) -> str:
    matches = list(pattern.finditer(html))
    if len(matches) != 1:
        raise PatchError(f"[{path.name}] regex pattern '{label}' matched {len(matches)} times (expected 1)")
    return pattern.sub(repl, html, count=1)


def patch_head(html: str, path: Path) -> str:
    return apply_literal(html, HEAD_CLOSE_OLD, HEAD_CLOSE_NEW, "head-close script insert", path)


def patch_canonical(html: str, path: Path) -> str:
    html = patch_head(html, path)
    html = apply_regex(html, CANON_KEY_OLD, canon_key_new, "storage-key+loadStore/saveStore removal", path)
    html = apply_literal(html, CANON_PERSIST_OLD, CANON_PERSIST_NEW, "persistReviewRow", path)
    html = apply_literal_variants(
        html,
        [(CANON_INIT_OLD_GUARDED, CANON_INIT_NEW), (CANON_INIT_OLD_DIRECT, CANON_INIT_NEW)],
        "initReview",
        path,
    )
    html = apply_literal(html, CANON_EXPORT_OLD, CANON_EXPORT_NEW, "exportReviewCsv", path)
    html = apply_literal(html, CANON_IMPORT_CLEAR_OLD, CANON_IMPORT_CLEAR_NEW, "importReviewCsv+clearAllReview", path)
    html = apply_literal_variants(
        html,
        [
            (CANON_BOOT_OLD_WITH_NUMFILTER, CANON_BOOT_NEW_WITH_NUMFILTER),
            (CANON_BOOT_OLD_NO_NUMFILTER, CANON_BOOT_NEW_NO_NUMFILTER),
        ],
        "bootstrap",
        path,
    )
    return html


def patch_legacy_a(html: str, path: Path) -> str:
    html = patch_head(html, path)
    html = apply_literal(html, LEGACY_A_KEY_OLD, LEGACY_A_KEY_NEW, "storage-key+loadStore/saveStore removal", path)
    html = apply_literal(html, LEGACY_A_PERSIST_OLD, LEGACY_A_PERSIST_NEW, "persistRow", path)
    html = apply_literal(html, LEGACY_A_INIT_OLD, LEGACY_A_INIT_NEW, "initRows", path)
    html = apply_literal(html, LEGACY_A_EXPORT_OLD, LEGACY_A_EXPORT_NEW, "exportCsv", path)
    html = apply_literal(html, LEGACY_A_IMPORT_CLEAR_OLD, LEGACY_A_IMPORT_CLEAR_NEW, "importCsv+clearAll", path)
    html = apply_literal(html, LEGACY_A_BOOT_OLD, LEGACY_A_BOOT_NEW, "bootstrap", path)
    return html


def patch_legacy_b(html: str, path: Path) -> str:
    html = patch_head(html, path)
    html = apply_regex(html, LEGACY_B_STORAGE_DECL_OLD, legacy_b_storage_decl_new, "labelStore instantiation", path)
    html = apply_literal(html, LEGACY_B_KEY_OLD, LEGACY_B_KEY_NEW, "loadStore/saveStore removal", path)
    html = apply_literal(html, LEGACY_B_PERSIST_OLD, LEGACY_B_PERSIST_NEW, "persistRow", path)
    html = apply_literal(html, LEGACY_B_INIT_OLD, LEGACY_B_INIT_NEW, "initRows", path)
    html = apply_literal(html, LEGACY_B_EXPORT_OLD, LEGACY_B_EXPORT_NEW, "exportCsv", path)
    html = apply_literal(html, LEGACY_B_IMPORT_CLEAR_OLD, LEGACY_B_IMPORT_CLEAR_NEW, "importCsv+clearAll", path)
    html = apply_literal(html, LEGACY_B_BOOT_OLD, LEGACY_B_BOOT_NEW, "bootstrap", path)
    return html


CANONICAL_GLOBS = [
    "stop*_trades_report*.html",
    "ss_confl1_finetune_report*.html",
    "ss_confl2_finetune_report*.html",
    "ss_m5_confl2/ss_m5_confl2_report*.html",
]
LEGACY_A_FILES = ["lxpb_cluster_selection_report.html"]
LEGACY_B_FILES = ["lxpb_labels_report.html", "lxpb_strong_breakout_report.html"]


def main():
    dry_run = "--dry-run" in sys.argv

    canonical_files = set()
    for pattern in CANONICAL_GLOBS:
        canonical_files.update(REPORTS_DIR.glob(pattern))

    jobs = [(p, patch_canonical) for p in sorted(canonical_files)]
    jobs += [(REPORTS_DIR / f, patch_legacy_a) for f in LEGACY_A_FILES]
    jobs += [(REPORTS_DIR / f, patch_legacy_b) for f in LEGACY_B_FILES]

    errors = []
    results = {}
    for path, fn in jobs:
        if not path.exists():
            print(f"SKIP (not found): {path.relative_to(ROOT)}")
            continue
        html = path.read_text(encoding="utf-8")
        try:
            patched = fn(html, path)
        except PatchError as e:
            print("ERROR:", e)
            errors.append(str(e))
            continue
        results[path] = patched
        print(f"OK ({len(patched)} bytes): {path.relative_to(ROOT)}")

    if errors:
        print(f"\n{len(errors)} file(s) failed to match expected patterns -- nothing written. Fix patterns and retry.")
        sys.exit(1)

    if dry_run:
        print(f"\nDry run OK -- all {len(results)} files matched cleanly. Re-run without --dry-run to write.")
        return

    for path, patched in results.items():
        path.write_text(patched, encoding="utf-8")
        print(f"PATCHED: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
