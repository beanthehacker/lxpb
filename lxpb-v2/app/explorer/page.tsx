import fs from "node:fs";
import path from "node:path";
import type { Metadata } from "next";
import Explorer, { type DatasetRef } from "./Explorer";

export const metadata: Metadata = {
  title: "Trade explorer",
  description: "Build filter combinations over the M5 Confluence v2 trades",
};

// Every ss_m5_confl2 report that has a facts file next to it (written by
// ss_m5_confl2/trade_facts.py). Newest-first by name: 2026, 2025, then ranges.
const DIR = path.join(process.cwd(), "public", "reports", "ss_m5_confl2");
const LABELS: Record<string, string> = { "2025": "2025", "2026": "2026", "jul-aug": "Jul–Aug 2026" };

function datasets(): DatasetRef[] {
  if (!fs.existsSync(DIR)) return [];
  return fs
    .readdirSync(DIR)
    .filter((f) => f.endsWith(".trades.json.gz"))
    .map((f) => {
      const id = f.replace(/\.trades\.json\.gz$/, "");
      return { id, label: LABELS[id] || id, url: `/reports/ss_m5_confl2/${f}`, report: `/reports/ss_m5_confl2/${id}.html` };
    })
    .sort((a, b) => (/^\d{4}$/.test(b.id) ? 1 : 0) - (/^\d{4}$/.test(a.id) ? 1 : 0) || b.id.localeCompare(a.id));
}

export default function ExplorerPage() {
  return <Explorer datasets={datasets()} />;
}
