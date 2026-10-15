import { AuthError } from "next-auth";
import { redirect } from "next/navigation";
import { signIn } from "@/auth";

const field = {
  width: "100%",
  boxSizing: "border-box" as const,
  padding: "10px 12px",
  borderRadius: 8,
  border: "1px solid #2a2f37",
  background: "#0b0d10",
  color: "#e6e8eb",
  fontSize: 16, // 16px keeps iOS Safari from zooming on focus
  marginBottom: 10,
};

const button = {
  width: "100%",
  padding: "10px 16px",
  borderRadius: 8,
  border: "1px solid #2a2f37",
  background: "#1b1f26",
  color: "#e6e8eb",
  fontSize: 14,
  cursor: "pointer",
};

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;
  return (
    <main
      style={{
        minHeight: "100dvh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#0b0d10",
        color: "#e6e8eb",
        fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif",
        padding: 16,
      }}
    >
      <div
        style={{
          border: "1px solid #23262b",
          borderRadius: 12,
          padding: "40px 36px",
          background: "#12151a",
          textAlign: "center",
          maxWidth: 360,
          width: "100%",
        }}
      >
        <h1 style={{ fontSize: 20, margin: "0 0 8px" }}>lxpb reports</h1>
        <p style={{ color: "#9aa1ab", fontSize: 14, margin: "0 0 24px" }}>
          Sign in with the authorized account to view reports.
        </p>
        <form
          action={async (formData: FormData) => {
            "use server";
            try {
              await signIn("credentials", {
                email: formData.get("email"),
                password: formData.get("password"),
                redirectTo: "/",
              });
            } catch (e) {
              // A successful sign-in redirects by throwing; only a failed one
              // is an AuthError.
              if (e instanceof AuthError) redirect("/login?error=1");
              throw e;
            }
          }}
        >
          <input
            name="email"
            type="email"
            placeholder="Email"
            autoComplete="username"
            required
            style={field}
          />
          <input
            name="password"
            type="password"
            placeholder="Password"
            autoComplete="current-password"
            required
            style={field}
          />
          {error ? (
            <p style={{ color: "#f0716b", fontSize: 13, margin: "0 0 10px" }}>
              Wrong email or password.
            </p>
          ) : null}
          <button type="submit" style={button}>
            Sign in
          </button>
        </form>
        <p style={{ color: "#6b7280", fontSize: 12, margin: "16px 0" }}>or</p>
        <form
          action={async () => {
            "use server";
            await signIn("google", { redirectTo: "/" });
          }}
        >
          <button type="submit" style={button}>
            Sign in with Google
          </button>
        </form>
      </div>
    </main>
  );
}
