import { neon } from "@neondatabase/serverless";

// Vercel's Neon storage integration prefixes injected vars with the
// resource name ("NEON_DB").
const connectionString = process.env.NEON_DB_DATABASE_URL;

// `null` (rather than throwing here) when unset, so pages/routes that don't
// touch the DB still build and serve fine before Postgres is configured.
// Callers must check for null and respond accordingly (see app/api/rows*).
export const sql = connectionString ? neon(connectionString) : null;
