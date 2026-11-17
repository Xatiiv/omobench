import type { Metadata } from "next";

// The comparison page is a client component, so its metadata lives here.
const title = "Compare setups · OmoBench";
const description =
  "Compare published OmoBench results side by side: the same model across hardware, or different models on one setup, with what each comparison holds fixed.";

export const metadata: Metadata = {
  title,
  description,
  alternates: { canonical: "/compare" },
  openGraph: { url: "/compare", title, description },
};

export default function CompareLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return children;
}
