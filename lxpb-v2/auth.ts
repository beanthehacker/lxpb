import { createHash, timingSafeEqual } from "node:crypto";
import NextAuth, { CredentialsSignin } from "next-auth";
import Credentials from "next-auth/providers/credentials";
import Google from "next-auth/providers/google";

const ALLOWED_EMAIL = process.env.ALLOWED_EMAIL || "nisarg92modi@gmail.com";

// Email + password sign-in for the same single user as the Google sign-in
// (user request 2026-10-15). The session carries the same email, so everything
// keyed on it (the gate in proxy.ts, /api/rows and the review state behind it)
// is the one data set whichever way the user signed in. The password is NEVER
// in this public repo: it is the LOGIN_PASSWORD env var on the Vercel project.
// Unset -> password sign-in is refused, Google sign-in is unaffected.
const LOGIN_PASSWORD = process.env.LOGIN_PASSWORD || "";

function sha256(s: string): Buffer {
  return createHash("sha256").update(s).digest();
}

// Constant-time compare (hashing first makes both sides the same length).
function same(a: string, b: string): boolean {
  return timingSafeEqual(sha256(a), sha256(b));
}

export const { handlers, signIn, signOut, auth } = NextAuth({
  providers: [
    Google({
      clientId: process.env.GOOGLE_CLIENT_ID,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET,
    }),
    Credentials({
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
      },
      async authorize(creds) {
        const email = String(creds?.email ?? "").trim().toLowerCase();
        const password = String(creds?.password ?? "");
        // Evaluate both checks before deciding, so a wrong email and a wrong
        // password cost the same.
        const emailOk = same(email, ALLOWED_EMAIL.toLowerCase());
        const passOk = LOGIN_PASSWORD !== "" && same(password, LOGIN_PASSWORD);
        if (!emailOk || !passOk) throw new CredentialsSignin();
        return { id: ALLOWED_EMAIL.toLowerCase(), email: ALLOWED_EMAIL, name: "lxpb" };
      },
    }),
  ],
  session: { strategy: "jwt" },
  callbacks: {
    async signIn({ user }) {
      return user.email?.toLowerCase() === ALLOWED_EMAIL.toLowerCase();
    },
    async session({ session }) {
      return session;
    },
  },
  pages: {
    signIn: "/login",
  },
});

export { ALLOWED_EMAIL };
