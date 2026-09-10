import NextAuth from "next-auth";
import Google from "next-auth/providers/google";

const ALLOWED_EMAIL = process.env.ALLOWED_EMAIL || "nisarg92modi@gmail.com";

export const { handlers, signIn, signOut, auth } = NextAuth({
  providers: [
    Google({
      clientId: process.env.GOOGLE_CLIENT_ID,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET,
    }),
  ],
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
