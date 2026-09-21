import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Verdict",
  description: "Cost-optimal LLM routing with a statistically proven quality floor",
};

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/pareto", label: "Pareto" },
  { href: "/spend", label: "Spend" },
  { href: "/compare", label: "Compare" },
  { href: "/traces", label: "Traces" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-dvh bg-neutral-50 text-neutral-900 antialiased dark:bg-neutral-950 dark:text-neutral-100">
        {/* Keyboard users land here first; without it the nav must be tabbed
            through on every page. */}
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-md focus:bg-neutral-900 focus:px-3 focus:py-2 focus:text-sm focus:text-white dark:focus:bg-neutral-100 dark:focus:text-neutral-900"
        >
          Skip to content
        </a>

        <header className="sticky top-0 z-40 border-b border-neutral-200 bg-neutral-50/90 backdrop-blur dark:border-neutral-800 dark:bg-neutral-950/90">
          <div className="mx-auto max-w-5xl px-4 py-3">
            <Link
              href="/"
              className="rounded text-sm font-semibold tracking-tight focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600"
            >
              Verdict
            </Link>
            {/* Scrolls rather than wraps at 375px, so the header stays one line. */}
            <nav aria-label="Primary" className="-mx-1 mt-2 overflow-x-auto">
              <ul className="flex min-w-max gap-1">
                {NAV.map((item) => (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      className="block rounded-md px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600 dark:text-neutral-300 dark:hover:bg-neutral-800"
                    >
                      {item.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </nav>
          </div>
        </header>

        <main id="main" className="mx-auto max-w-5xl px-4 py-6 sm:py-8">
          {children}
        </main>

        <footer className="mx-auto max-w-5xl px-4 pb-10 pt-4 text-xs text-neutral-500">
          Every figure on this site is read from a committed artifact. A number that is not in{" "}
          <code className="font-mono">artifacts/</code> does not exist.
        </footer>
      </body>
    </html>
  );
}
