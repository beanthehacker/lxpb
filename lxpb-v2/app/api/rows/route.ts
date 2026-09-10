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

// GET /api/rows?report=<key> -> { [row_key]: fields }
export async function GET(req: NextRequest) {
  if (!(await requireSession())) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

  const report = req.nextUrl.searchParams.get("report");
  if (!report) return NextResponse.json({ error: "missing report" }, { status: 400 });

  const rows = await sql`select row_key, fields from row_state where report_key = ${report}`;
  const out: Record<string, unknown> = {};
  for (const r of rows as unknown as { row_key: string; fields: unknown }[]) {
    out[r.row_key] = r.fields;
  }
  return NextResponse.json(out);
}

// POST /api/rows  { report, key, fields } -> upsert one row
export async function POST(req: NextRequest) {
  if (!(await requireSession())) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

  const body = await req.json().catch(() => null);
  const report = body?.report;
  const key = body?.key;
  const fields = body?.fields;
  if (!report || !key || typeof fields !== "object" || fields === null) {
    return NextResponse.json({ error: "missing report/key/fields" }, { status: 400 });
  }

  await sql`
    insert into row_state (report_key, row_key, fields, updated_at)
    values (${report}, ${key}, ${JSON.stringify(fields)}::jsonb, now())
    on conflict (report_key, row_key)
    do update set fields = excluded.fields, updated_at = now()
  `;
  return NextResponse.json({ ok: true });
}

// DELETE /api/rows?report=<key> -> clear all rows for a report
export async function DELETE(req: NextRequest) {
  if (!(await requireSession())) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

  const report = req.nextUrl.searchParams.get("report");
  if (!report) return NextResponse.json({ error: "missing report" }, { status: 400 });

  await sql`delete from row_state where report_key = ${report}`;
  return NextResponse.json({ ok: true });
}
