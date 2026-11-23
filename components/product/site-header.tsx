import Link from "next/link";

export function Brand() {
  return <span className="product-brand" role="img" aria-label="OmoBench"><svg viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="25" className="brand-o" /><g transform="rotate(-18 32 32)"><path d="M21 25h22" className="brand-o" /><path d="M21 39h22" className="brand-mo" /></g></svg><span aria-hidden="true"><span className="brand-o">O</span><span className="brand-mo">MO</span><span className="brand-bench">Bench</span></span></span>;
}

export function SiteHeader({ active }: { active?: "run" | "results" | "compare" }) {
  return <header className="site-header">
    <Link href="/" className="brand-link" aria-label="OmoBench home"><Brand /></Link>
    <nav aria-label="Main navigation">
      <Link href="/#local-results" aria-current={active === "results" ? "page" : undefined}>Results</Link>
      <Link href="/compare" aria-current={active === "compare" ? "page" : undefined}>Compare</Link>
      <Link href="/#profiles" className="nav-profiles">Profiles</Link>
      <Link href="/run" className="nav-run" aria-current={active === "run" ? "page" : undefined}>Run benchmark <span aria-hidden="true">↗</span></Link>
    </nav>
  </header>;
}
