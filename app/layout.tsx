import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "OmoBench — Open model performance registry",
  description:
    "A GitHub-backed registry of reproducible self-hosted language-model performance benchmarks.",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
