import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Verdict",
  description: "Cost-optimal LLM routing with a statistically proven quality floor",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      {/* Recruiters open portfolio links on phones. Mobile-first is not optional. */}
      <body className="min-h-dvh bg-neutral-950 text-neutral-100 antialiased">{children}</body>
    </html>
  );
}
