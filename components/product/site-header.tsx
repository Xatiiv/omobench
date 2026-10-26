import Link from "next/link";

export function Brand() {
  return <span className="product-brand"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M25 9a12 12 0 1 0 2 11"/><path d="m7 18 5-5 5 6 8-12"/></svg><span>omo<span>bench</span></span></span>;
}

export function SiteHeader({ active }: { active?: "run" | "results" }) {
  return <header className="site-header">
    <Link href="/" className="brand-link" aria-label="OmoBench home"><Brand /></Link>
    <nav aria-label="Main navigation">
      <Link href="/#local-results" aria-current={active === "results" ? "page" : undefined}>Results</Link>
      <Link href="/#profiles">Profiles</Link>
      <Link href="/run" className="nav-run" aria-current={active === "run" ? "page" : undefined}>Run benchmark <span aria-hidden="true">↗</span></Link>
    </nav>
  </header>;
}
