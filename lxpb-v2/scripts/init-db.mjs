// One-off migration runner: creates the row_state table.
// Usage: after `vercel env pull .env.local` (or setting DATABASE_URL in your
// shell), run `npm run init-db`.
import { neon } from "@neondatabase/serverless";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));

const connectionString =
  process.env.DATABASE_URL || process.env.POSTGRES_URL || process.env.DATABASE_URL_UNPOOLED;

if (!connectionString) {
  console.error(
    "Set DATABASE_URL (or POSTGRES_URL) first -- e.g. `vercel env pull .env.local` then " +
      "`export $(grep -v '^#' .env.local | xargs)` (bash) or load it into this shell."
  );
  process.exit(1);
}

const sql = neon(connectionString);
const schema = readFileSync(path.join(here, "..", "db", "schema.sql"), "utf8");

// neon's tagged-template client doesn't take a raw multi-statement string,
// so strip comment lines, then split on the two statements in schema.sql
// and run them individually.
const statements = schema
  .split("\n")
  .filter((line) => !line.trim().startsWith("--"))
  .join("\n")
  .split(";")
  .map((s) => s.trim())
  .filter((s) => s.length);

for (const stmt of statements) {
  console.log("Running:", stmt.split("\n")[0], "...");
  await sql.query(stmt);
}

console.log("Done. row_state table is ready.");
