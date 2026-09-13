// Generates lxpb-v2/public/index.html -- the landing page linking to every
// report. Vercel serves lxpb-v2/public as the site root (see vercel.json), so
// the reports keep their existing /reports/*.html URLs and nothing is copied.
//
// Run manually with `node scripts/build-index.mjs` to preview locally.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const PUBLIC_DIR = path.join(REPO_ROOT, "lxpb-v2", "public");
const REPORTS_DIR = path.join(PUBLIC_DIR, "reports");

const GROUPS = [
  {
    title: "M5 Confluence v2",
    description:
      "M5-native LXPB confluence strategy reports, with live in-browser dynamic-filter toggles.",
    test: (rel) => rel.startsWith("ss_m5_confl2/"),
  },
  {
    title: "SS1 Confluence Fine-Tune",
    description: "Same-side H1-confluence fine-tuning for the SS1 strategy.",
    test: (rel) => rel.startsWith("ss_confl1_finetune_report"),
  },
  {
    title: "SS2 Confluence Fine-Tune",
    description: "Same-side H1-confluence fine-tuning for the SS2 strategy.",
    test: (rel) => rel.startsWith("ss_confl2_finetune_report"),
  },
  {
    title: "Stop / Target Sweeps",
    description:
      "MAE/MFE excursion-aware stop/target trade sweep reports, across stop-target combos and exit models.",
    test: (rel) => /^stop[\d.]+_target[\d.]+/.test(rel),
  },
  {
    title: "Cluster Selection",
    description: "Which level to take when a retest sweeps several at once -- scored + hand-checked.",
    test: (rel) => rel.startsWith("lxpb_cluster_selection_report"),
  },
  {
    title: "Hand Labels",
    description: "Hand-labeling tool for completed LXPB retests.",
    test: (rel) =>
      rel.startsWith("lxpb_labels_report") || rel.startsWith("lxpb_strong_breakout_report"),
  },
  {
    title: "Exit Analysis",
    description: "Exit-model analysis reports.",
    test: (rel) => rel.startsWith("exit_analysis_report"),
  },
  {
    title: "Charts",
    description: "Full-year price/level charts.",
    test: (rel) => rel.startsWith("lxpb_2026_full_year_chart"),
  },
];

function walk(dir, base = "") {
  if (!fs.existsSync(dir)) return [];
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const rel = base ? `${base}/${entry.name}` : entry.name;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...walk(full, rel));
    } else if (entry.isFile() && entry.name.toLowerCase().endsWith(".html")) {
      out.push({ name: rel, sizeBytes: fs.statSync(full).size });
    }
  }
  return out;
}

function formatSize(bytes) {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const escapeHtml = (s) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

const files = walk(REPORTS_DIR).sort((a, b) => a.name.localeCompare(b.name));
const grouped = GROUPS.map((g) => ({ ...g, files: [] }));
const other = { title: "Other", description: "", files: [] };

for (const f of files) {
  const group = grouped.find((g) => g.test(f.name));
  (group ? group.files : other.files).push(f);
}

const sections = [...grouped, other].filter((g) => g.files.length > 0);

const body = sections
  .map(
    (g) => `    <section>
      <h2>${escapeHtml(g.title)}</h2>
      ${g.description ? `<p class="desc">${escapeHtml(g.description)}</p>` : ""}
      <div class="list">
${g.files
  .map(
    (f) =>
      `        <a href="/reports/${f.name
        .split("/")
        .map(encodeURIComponent)
        .join("/")}" target="_blank" rel="noreferrer"><span class="name">${escapeHtml(
        f.name
      )}</span><span class="size">${formatSize(f.sizeBytes)}</span></a>`
  )
  .join("\n")}
      </div>
    </section>`
  )
  .join("\n");

const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>lxpb reports</title>
<style>
  body { margin: 0; background: #0b0d10; color: #e6e8eb;
         font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
  main { max-width: 980px; margin: 0 auto; padding: 32px 20px 64px; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  header p { color: #9aa1ab; font-size: 13px; margin: 0; }
  header { margin-bottom: 32px; }
  section { margin-bottom: 28px; }
  h2 { font-size: 15px; margin: 0 0 2px; color: #e6e8eb; }
  .desc { color: #6f7680; font-size: 12px; margin: 0 0 10px; }
  .list { display: grid; gap: 8px; }
  a { display: flex; justify-content: space-between; align-items: baseline; gap: 12px;
      padding: 10px 14px; border: 1px solid #23262b; border-radius: 8px;
      background: #12151a; color: #e6e8eb; text-decoration: none; font-size: 13px; }
  a:hover { border-color: #3a4049; background: #171b21; }
  .name { font-family: ui-monospace, monospace; word-break: break-all; }
  .size { color: #6f7680; font-size: 11px; white-space: nowrap; }
</style>
</head>
<body>
  <main>
    <header>
      <h1>lxpb reports</h1>
      <p>${files.length} report${files.length === 1 ? "" : "s"} -- click to open in a new tab.</p>
    </header>
${body}
  </main>
</body>
</html>
`;

fs.mkdirSync(PUBLIC_DIR, { recursive: true });
fs.writeFileSync(path.join(PUBLIC_DIR, "index.html"), html);
console.log(`Wrote lxpb-v2/public/index.html -- ${files.length} reports in ${sections.length} groups.`);
