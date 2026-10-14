import { NextResponse } from "next/server";
import { auth, ALLOWED_EMAIL } from "@/auth";

export default auth((req) => {
  const isAllowed = req.auth?.user?.email?.toLowerCase() === ALLOWED_EMAIL.toLowerCase();
  if (!isAllowed) {
    const loginUrl = new URL("/login", req.nextUrl.origin);
    return NextResponse.redirect(loginUrl);
  }
});

// Gate everything (pages, the /reports/*.html static files, and the /api/rows
// endpoints) except the auth routes and the login page itself, and except the
// reports published for the public (user decision 2026-10-14): the M1 Sep fade
// report and the one script its page loads. Both are static files; the review
// state API (/api/rows) stays gated, so a public visitor can read the report but
// never read or write anyone's saved notes. To publish another report, add its
// path here (a prefix match, so ".html" also covers the raw ".html.gz").
// The matcher must be a literal (Next reads it at build time, no variables):
//   reports/ss_m1_confl/sep_month_fade.html   the report (a prefix, so the raw .html.gz too)
//   js/row-store.js                           the one script the report's page loads
export const config = {
  matcher: ["/((?!api/auth|login|_next/static|_next/image|favicon.ico|reports/ss_m1_confl/sep_month_fade.html|js/row-store.js).*)"],
};
