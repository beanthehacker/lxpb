import { NextRequest, NextResponse } from "next/server";
import { auth, ALLOWED_EMAIL } from "@/auth";
import { sql } from "@/lib/db";

async function requireSession() {
  const session = await auth();
  if (session?.user?.email?.toLowerCase() !== ALLOWED_EMAIL.toLowerCase()) {
    return null;
  }
  return session;
}

// POST /api/rows/bulk  { report, entries: { [row_key]: fields } } -> upsert many (CSV import)
export async function POST(req: NextRequest) {
  if (!(await requireSession())) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

  const body = await req.json().catch(() => null);
  const report = body?.report;
  const entries = body?.entries;
  if (!report || typeof entries !== "object" || entries === null) {
    return NextResponse.json({ error: "missing report/entries" }, { status: 400 });
  }

  const keys = Object.keys(entries);
  if (keys.length === 0) return NextResponse.json({ ok: true, count: 0 });
  if (keys.length > 20000) {
    return NextResponse.json({ error: "too many rows in one bulk import" }, { status: 400 });
  }

  // Batch in chunks to keep each statement reasonably sized.
  const CHUNK = 500;
  let count = 0;
  for (let i = 0; i < keys.length; i += CHUNK) {
    const chunkKeys = keys.slice(i, i + CHUNK);
    const rows = chunkKeys.map((k) => ({ row_key: k, fields: entries[k] }));
    await sql`
      insert into row_state (report_key, row_key, fields, updated_at)
      select ${report}, x.row_key, x.fields, now()
      from jsonb_to_recordset(${JSON.stringify(rows)}::jsonb) as x(row_key text, fields jsonb)
      on conflict (report_key, row_key)
      do update set fields = excluded.fields, updated_at = now()
    `;
    count += chunkKeys.length;
  }

  return NextResponse.json({ ok: true, count });
}
