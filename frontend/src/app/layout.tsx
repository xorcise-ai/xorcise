import "./globals.css";
import type { Metadata } from "next";
import { JetBrains_Mono } from "next/font/google";
import { Providers } from "./providers";
import { AppShell } from "@/components/layout/app-shell";

const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains",
  display: "swap",
});

// Every route exports its own `title` (WCAG 2.4.2) and the template appends the
// product name, so a tab reads "Run history · XORCISE" and survives truncation
// with the page-specific word first. `default` covers routes without metadata
// (not-found).
export const metadata: Metadata = {
  title: { default: "XORCISE", template: "%s · XORCISE" },
  description: "XORCISE operator surface",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={mono.variable}>
      <body>
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
