import { neon } from "@neondatabase/serverless";

const connectionString =
  process.env.DATABASE_URL ||
  process.env.POSTGRES_URL ||
  process.env.DATABASE_URL_UNPOOLED ||
  // Vercel's Neon integration prefixes injected vars with the resource name
  // ("NEON_DB") instead of the plain DATABASE_URL/POSTGRES_URL Vercel's own
  // native Postgres integration used to set.
  process.env.NEON_DB_DATABASE_URL;

// `null` (rather than throwing here) when unset, so pages/routes that don't
// touch the DB still build and serve fine before Postgres is configured.
// Callers must check for null and respond accordingly (see app/api/rows*).
export const sql = connectionString ? neon(connectionString) : null;
