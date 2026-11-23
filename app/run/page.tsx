import type { Metadata } from "next";
import Link from "next/link";
import { SiteHeader } from "@/components/product/site-header";
import { USER_GUIDE_URL, userGuideSection } from "../lib/site";
import { windowsCompanionGuideUrl, windowsRelease, windowsReleasesUrl } from "../lib/windows-release";

export const metadata: Metadata = {
  title: "Run a benchmark · OmoBench",
  description: "Install OmoBench, connect the model server you already run, and measure how fast your model runs on your hardware.",
  alternates: { canonical: "/run" },
  openGraph: { url: "/run", title: "Run a benchmark · OmoBench" },
};

const steps: Array<[string, string, string]> = [
  ["01", "Connect your model server", "Start LM Studio or another OpenAI-compatible server and load a model. OmoBench lists what it serves and finds the matching tokenizer."],
  ["02", "Confirm the serving hardware", "Read the CPU, memory and graphics cards, and say where the model runs: GPU, CPU, or split between them."],
  ["03", "Run the standard test", "A fixed chat workload, the same one everybody runs, so results compare. Watch throughput and latency as it happens."],
  ["04", "Review, keep or publish", "Results stay on your computer. Publishing is optional: you review every file before a pull request is opened."],
];

export default function RunPage() {
  return <main className="product-page min-h-screen px-6 pb-16 text-slate-50 sm:px-10">
    <div className="mx-auto max-w-6xl pt-6">
      <SiteHeader active="run" />
      <section className="run-hero">
        <p className="eyebrow"><span className="status-dot" /> YOUR HARDWARE. YOUR RESULT.</p>
        <h1>Find out what<br />your setup can do.</h1>
        <p>Connect the model server you already run, confirm your hardware and run a fixed workload. OmoBench runs on your computer and keeps the evidence with you.</p>
      </section>
      <div className="launch-grid">
        <section className="launch-card launch-primary">
          <p className="eyebrow">01 / ALREADY INSTALLED</p>
          <h2>Open OmoBench.</h2>
          <p>Start your model server and load a model first. The panel opens at <strong>Connect your model server</strong>.</p>
          <a className="product-button" href="omobench://open">Open OmoBench <span aria-hidden="true">↗</span></a>
          <p className="launch-note">Opening the panel does not download a model or start a test. You choose when to begin.</p>
          <details>
            <summary>Nothing opened?</summary>
            <p>Your browser may ask permission to open OmoBench. If nothing happens, open <strong>OmoBench</strong> from the Start menu and select <strong>Open panel</strong>. The companion window shows whether the panel started; this website cannot detect it.</p>
            <p>If OmoBench is not installed yet, use the installer on the right.</p>
          </details>
        </section>
        <section className="launch-card">
          <p className="eyebrow">02 / FIRST TIME HERE</p>
          <h2>Install on Windows.</h2>
          <p>The installer sets up the companion and a private benchmark environment in WSL2. No source checkout, Git or Node.js needed.</p>
          {windowsRelease ? <>
            <a className="product-button secondary-button" href={windowsRelease.downloadUrl}>Download for Windows <span aria-hidden="true">↓</span></a>
            <p className="launch-note">Version {windowsRelease.version} · Windows 10 (2004+) / Windows 11 · per-user installation</p>
            <details><summary>Publisher and download verification</summary>
              <p>{windowsRelease.signed ? "This build is code-signed." : "This build is unsigned. Windows will warn about an unknown publisher."}</p>
              <p>In PowerShell, run <code>Get-FileHash .\OmoBenchSetup.exe -Algorithm SHA256</code> and compare the result with:</p>
              <p className="break-all font-mono">{windowsRelease.sha256}</p>
              <p>Source revision: <code>{windowsRelease.revision.slice(0,12)}</code>. <a className="text-brand-300 underline" href={windowsRelease.releaseUrl}>Release notes</a></p>
            </details>
          </> : <>
            <span className="unavailable-download">Public download coming soon</span>
            <p className="launch-note">The installer is built and tested, but no public release is available yet. <a className="text-brand-300 underline" href={windowsCompanionGuideUrl}>Use the installation guide</a> or check <a className="text-brand-300 underline" href={windowsReleasesUrl}>releases</a>.</p>
          </>}
          <details><summary>What does my machine need?</summary>
            <p>64-bit Windows 10 (version 2004 or later) or Windows 11, with WSL2. Installing WSL needs an administrator command and a restart. A missing Python 3 or its venv package inside Linux needs one manual Linux installation step. The installer checks all of these and tells you what to do.</p>
            <p>You also need a model server, such as LM Studio, with a model you have downloaded. OmoBench does not download models; it only fetches the small tokenizer files it needs, when you ask it to.</p>
          </details>
        </section>
      </div>
      <section className="py-12">
        <p className="eyebrow">FROM CONNECTION TO RESULT</p>
        <div className="mt-5 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
          {steps.map(([n, title, body]) => <div className="rounded-xl border border-white/10 p-5" key={n}><span className="font-mono text-xs text-brand-300">{n}</span><h3 className="mt-3 font-semibold">{title}</h3><p className="mt-2 text-sm leading-6 text-slate-400">{body}</p></div>)}
        </div>
        <p className="mt-6 text-sm leading-6 text-slate-300">New to this? The <a className="text-brand-300 underline" href={USER_GUIDE_URL}>user guide</a> walks through every step with LM Studio, including the tokenizer, hardware, reading results and publishing. OmoBench measures speed and reliability, not the quality of a model&apos;s answers.</p>
      </section>
      <details className="rounded-xl border border-white/10 bg-white/[.025] p-6">
        <summary className="cursor-pointer font-medium">Linux, macOS, or WSL2 from source</summary>
        <div className="mt-5 space-y-5 text-sm leading-7 text-slate-300">
          <p>In an OmoBench checkout, with a Python 3.10+ environment activated (3.12 is tested), install the panel and the pinned benchmark runner, then start the panel:</p>
          <pre className="overflow-auto rounded-lg bg-black/20 p-4 text-brand-200"><code>{"python -m pip install -e './cli[ui,runner]'\nomobench ui"}</code></pre>
          <p>Keep the terminal open and use the full link it prints, including its <code>#token=</code> part. The panel opens at <strong>Connect your model server</strong>; enter your server&apos;s address, such as <code>http://127.0.0.1:1234</code> for LM Studio, and press <strong>Connect</strong>.</p>
          <p>CPU, GPU and split CPU/GPU runs can all be packaged and submitted. Nothing is uploaded until you review the files and create a pull request, and a result appears here only after review, merge and a site build.</p>
          <a className="inline-block text-brand-300 underline" href={userGuideSection("linux-macos-or-windows-without-the-installer")}>Read the setup instructions ↗</a>
        </div>
      </details>
      <footer className="mt-10 flex flex-wrap justify-between gap-4 border-t border-white/10 pt-6 text-xs leading-6 text-slate-400"><span>Runs locally. Shares only after your review.</span><span className="flex gap-4"><a href={USER_GUIDE_URL} className="text-brand-300">User guide ↗</a><Link href="/" className="text-brand-300">Back to the registry ↗</Link></span></footer>
    </div>
  </main>;
}
