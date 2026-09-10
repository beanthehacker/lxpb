import { neon } from "@neondatabase/serverless";

const connectionString =
  process.env.DATABASE_URL || process.env.POSTGRES_URL || process.env.DATABASE_URL_UNPOOLED;

if (!connectionString) {
  throw new Error(
    "No Postgres connection string found. Set DATABASE_URL (Neon/Vercel Postgres integration env var)."
  );
}

export const sql = neon(connectionString);
