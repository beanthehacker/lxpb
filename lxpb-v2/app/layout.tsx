import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "lxpb reports",
  description: "LXPB strategy report index",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ margin: 0, background: "#0b0d10", color: "#e6e8eb" }}>{children}</body>
    </html>
  );
}
