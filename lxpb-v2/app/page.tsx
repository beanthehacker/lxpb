import fs from "node:fs";
import path from "node:path";

type ReportFile = {
  href: string;
  name: string;
  sizeBytes: number;
  mtime: number;
};

type Group = {
  key: string;
  title: string;
  description: string;
  files: ReportFile[];
};

const REPORTS_DIR = path.join(process.cwd(), "public", "reports");

const GROUP_ORDER: { key: string; title: string; description: string; test: (rel: string) => boolean }[] = [
  {
    key: "m5_confl2",
    title: "M5 Confluence v2",
    description: "M5-native LXPB confluence strategy reports, with live in-browser dynamic-filter toggles.",
    test: (rel) => rel.startsWith("ss_m5_confl2/"),
  },
  {
    key: "ss_confl1",
    title: "SS1 Confluence Fine-Tune",
    description: "Same-side H1-confluence fine-tuning for the SS1 strategy.",
    test: (rel) => rel.startsWith("ss_confl1_finetune_report"),
  },
  {
    key: "ss_confl2",
    title: "SS2 Confluence Fine-Tune",
    description: "Same-side H1-confluence fine-tuning for the SS2 strategy.",
    test: (rel) => rel.startsWith("ss_confl2_finetune_report"),
  },
  {
    key: "stop_target",
    title: "Stop / Target Sweeps",
    description: "MAE/MFE excursion-aware stop/target trade sweep reports, across stop-target combos and exit models.",
    test: (rel) => /^stop[\d.]+_target[\d.]+/.test(rel),
  },
  {
    key: "cluster_selection",
    title: "Cluster Selection",
    description: "Which level to take when a retest sweeps several at once -- scored + hand-checked.",
    test: (rel) => rel.startsWith("lxpb_cluster_selection_report"),
  },
  {
    key: "labels",
    title: "Hand Labels",
    description: "Hand-labeling tool for completed LXPB retests.",
    test: (rel) => rel.startsWith("lxpb_labels_report") || rel.startsWith("lxpb_strong_breakout_report"),
  },
  {
    key: "exit_analysis",
    title: "Exit Analysis",
    description: "Exit-model analysis reports.",
    test: (rel) => rel.startsWith("exit_analysis_report"),
  },
  {
    key: "chart",
    title: "Charts",
    description: "Full-year price/level charts.",
    test: (rel) => rel.startsWith("lxpb_2026_full_year_chart"),
  },
];

function walk(dir: string, base = ""): ReportFile[] {
  if (!fs.existsSync(dir)) return [];
  const out: ReportFile[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const rel = base ? `${base}/${entry.name}` : entry.name;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...walk(full, rel));
    } else if (entry.isFile() && entry.name.toLowerCase().endsWith(".html")) {
      const stat = fs.statSync(full);
      out.push({ href: `/reports/${rel}`, name: rel, sizeBytes: stat.size, mtime: stat.mtimeMs });
    }
  }
  return out;
}

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function buildGroups(): Group[] {
  const files = walk(REPORTS_DIR).sort((a, b) => a.name.localeCompare(b.name));
  const groups: Group[] = GROUP_ORDER.map((g) => ({ key: g.key, title: g.title, description: g.description, files: [] }));
  const other: Group = { key: "other", title: "Other", description: "", files: [] };

  for (const f of files) {
    const rule = GROUP_ORDER.find((g) => g.test(f.name));
    if (rule) {
      groups.find((g) => g.key === rule.key)!.files.push(f);
    } else {
      other.files.push(f);
    }
  }
  if (other.files.length) groups.push(other);
  return groups.filter((g) => g.files.length > 0);
}

export default function HomePage() {
  const groups = buildGroups();
  const totalFiles = groups.reduce((n, g) => n + g.files.length, 0);

  return (
    <main
      style={{
        fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif",
        maxWidth: 980,
        margin: "0 auto",
        padding: "32px 20px 64px",
      }}
    >
      <header style={{ marginBottom: 32 }}>
        <h1 style={{ fontSize: 22, margin: "0 0 4px" }}>lxpb reports</h1>
        <p style={{ color: "#9aa1ab", fontSize: 13, margin: 0 }}>
          {totalFiles} report{totalFiles === 1 ? "" : "s"} -- click to open in a new tab.
        </p>
      </header>

      {groups.map((g) => (
        <section key={g.key} style={{ marginBottom: 28 }}>
          <h2 style={{ fontSize: 15, margin: "0 0 2px", color: "#e6e8eb" }}>{g.title}</h2>
          {g.description && (
            <p style={{ color: "#6f7680", fontSize: 12, margin: "0 0 10px" }}>{g.description}</p>
          )}
          <div style={{ display: "grid", gap: 8 }}>
            {g.files.map((f) => (
              <a
                key={f.href}
                href={f.href}
                target="_blank"
                rel="noreferrer"
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "baseline",
                  gap: 12,
                  padding: "10px 14px",
                  border: "1px solid #23262b",
                  borderRadius: 8,
                  background: "#12151a",
                  color: "#e6e8eb",
                  textDecoration: "none",
                  fontSize: 13,
                }}
              >
                <span style={{ fontFamily: "ui-monospace, monospace", wordBreak: "break-all" }}>{f.name}</span>
                <span style={{ color: "#6f7680", fontSize: 11, whiteSpace: "nowrap" }}>{formatSize(f.sizeBytes)}</span>
              </a>
            ))}
          </div>
        </section>
      ))}
    </main>
  );
}
