// Some report HTML files are too large to commit raw (GitHub rejects any
// blob over 100MB), so they're committed gzip-compressed instead
// (public/reports/**/*.html.gz) and inflated back to the plain .html both
// build paths actually serve: imported by build-index.mjs (the static
// Vercel deploy) and run standalone here before next dev/next build (the
// alternate Next.js app path). The inflated .html is never committed -- see
// .gitignore.
import { readdirSync, statSync, readFileSync, writeFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import path from "node:path";
import { fileURLToPath } from "node:url";

function walk(dir) {
  if (!statSync(dir, { throwIfNoEntry: false })) return [];
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(full));
    else if (entry.isFile() && entry.name.endsWith(".html.gz")) out.push(full);
  }
  return out;
}

export function inflateGzReports(reportsDir) {
  for (const gzPath of walk(reportsDir)) {
    const htmlPath = gzPath.slice(0, -".gz".length);
    const gzStat = statSync(gzPath);
    const htmlStat = statSync(htmlPath, { throwIfNoEntry: false });
    if (htmlStat && htmlStat.mtimeMs >= gzStat.mtimeMs) continue; // already up to date
    writeFileSync(htmlPath, gunzipSync(readFileSync(gzPath)));
    console.log(`Inflated ${path.relative(reportsDir, gzPath)} -> ${path.basename(htmlPath)}`);
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const here = path.dirname(fileURLToPath(import.meta.url));
  inflateGzReports(path.join(here, "..", "public", "reports"));
}
