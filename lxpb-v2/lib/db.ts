import { neon } from "@neondatabase/serverless";

const connectionString =
  process.env.DATABASE_URL || process.env.POSTGRES_URL || process.env.DATABASE_URL_UNPOOLED;

// `null` (rather than throwing here) when unset, so pages/routes that don't
// touch the DB still build and serve fine before Postgres is configured.
// Callers must check for null and respond accordingly (see app/api/rows*).
export const sql = connectionString ? neon(connectionString) : null;
