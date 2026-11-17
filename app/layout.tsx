import type { Metadata } from "next";
import "./globals.css";
import { SITE_DESCRIPTION, SITE_NAME, SITE_URL } from "./lib/site";

const title = "OmoBench · Open-weight model performance on your hardware";

export const metadata: Metadata = {
  // Relative URLs below resolve against the canonical production address,
  // whichever deployment (production or preview) serves the page.
  metadataBase: new URL(SITE_URL),
  title,
  description: SITE_DESCRIPTION,
  alternates: { canonical: "/" },
  openGraph: {
    type: "website",
    siteName: SITE_NAME,
    url: "/",
    title,
    description: SITE_DESCRIPTION,
  },
  twitter: { card: "summary", title, description: SITE_DESCRIPTION },
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
