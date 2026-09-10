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
// endpoints) except the auth routes and the login page itself.
export const config = {
  matcher: ["/((?!api/auth|login|_next/static|_next/image|favicon.ico).*)"],
};
