import { signIn } from "@/auth";

export default function LoginPage() {
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
          Sign in with the authorized Google account to view reports.
        </p>
        <form
          action={async () => {
            "use server";
            await signIn("google", { redirectTo: "/" });
          }}
        >
          <button
            type="submit"
            style={{
              width: "100%",
              padding: "10px 16px",
              borderRadius: 8,
              border: "1px solid #2a2f37",
              background: "#1b1f26",
              color: "#e6e8eb",
              fontSize: 14,
              cursor: "pointer",
            }}
          >
            Sign in with Google
          </button>
        </form>
      </div>
    </main>
  );
}
