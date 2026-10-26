import Link from "next/link";
import { SiteHeader } from "@/components/product/site-header";
import { windowsCompanionGuideUrl, windowsRelease, windowsReleasesUrl } from "../lib/windows-release";

export default function RunPage() {
  return <main className="product-page min-h-screen px-6 pb-16 text-slate-50 sm:px-10">
    <div className="mx-auto max-w-6xl pt-6">
      <SiteHeader active="run" />
      <section className="run-hero">
        <p className="eyebrow"><span className="status-dot" /> YOUR HARDWARE. YOUR RESULT.</p>
        <h1>Find out what<br />your setup can do.</h1>
        <p>Choose a test, watch performance as it happens, and explore the result. OmoBench runs locally on your machine and keeps the evidence with you.</p>
      </section>
      <div className="launch-grid">
        <section className="launch-card launch-primary">
          <p className="eyebrow">01 / READY TO RUN</p>
          <h2>Open your benchmark dashboard.</h2>
          <p>Already have the Windows companion? Open the guided panel to choose a model, prepare your setup and start a benchmark.</p>
          <a className="product-button" href="omobench://open">Open OmoBench <span aria-hidden="true">↗</span></a>
          <p className="launch-note">Opening the panel does not download a model or start a test. You choose when to begin.</p>
          <details>
            <summary>Nothing opened?</summary>
            <p>Check your taskbar for the OmoBench companion. Your browser may ask permission to open it. If it is not installed or registered, follow the setup guide first.</p>
            <p>You can also open the companion from the Start menu and select <strong>Open panel</strong>. The companion shows whether startup succeeded, this website cannot detect it.</p>
          </details>
        </section>
        <section className="launch-card">
          <p className="eyebrow">02 / FIRST TIME HERE</p>
          <h2>Get set up on Windows.</h2>
          <p>The installer finds an existing OmoBench environment or prepares one for you. No repository to clone and no interpreter paths to find.</p>
          {windowsRelease ? <>
            <a className="product-button secondary-button" href={windowsRelease.downloadUrl}>Download for Windows <span aria-hidden="true">↓</span></a>
            <p className="launch-note">Version {windowsRelease.version} · Windows 10 (2004+) / Windows 11 · per-user installation</p>
            <details><summary>Publisher and download verification</summary>
              <p>{windowsRelease.signed ? "This build is code-signed." : "This build is unsigned. Windows will warn about an unknown publisher."}</p>
              <p>In PowerShell, run <code>Get-FileHash .\OmoBenchSetup.exe -Algorithm SHA256</code> and compare the result with:</p>
              <p className="break-all font-mono">{windowsRelease.sha256}</p>
              <p>Source revision: <code>{windowsRelease.revision.slice(0,12)}</code>. <a className="text-amber-300 underline" href={windowsRelease.releaseUrl}>Release notes</a></p>
            </details>
          </> : <>
            <span className="unavailable-download">Public download coming soon</span>
            <p className="launch-note">The installer is built and tested, but no public release is available yet. <a className="text-amber-300 underline" href={windowsCompanionGuideUrl}>Use the installation guide</a> or check <a className="text-amber-300 underline" href={windowsReleasesUrl}>releases</a>.</p>
          </>}
          <details><summary>What does my machine need?</summary>
            <p>Windows 10 (version 2004 or later) or Windows 11, with WSL2. Installing WSL may require an administrator command and a restart. Missing Python 3 or its venv package requires a manual Linux installation step.</p>
            <p>The installer checks these prerequisites and explains what to do. Model and runtime downloads happen later, after you choose a test in the panel.</p>
          </details>
        </section>
      </div>
      <section className="py-12">
        <p className="eyebrow">FROM SETUP TO INSIGHT</p>
        <div className="mt-5 grid gap-5 sm:grid-cols-3">
          {[["01", "Choose your test", "A fixed profile makes the workload clear and the result repeatable."], ["02", "Watch it run", "Follow throughput, first-token latency, request progress and the live log."], ["03", "Inspect your result", "Review the measurements and evidence before sharing anything."]].map(([n,title,body]) => <div className="rounded-xl border border-white/10 p-5" key={n}><span className="font-mono text-xs text-amber-300">{n}</span><h3 className="mt-3 font-semibold">{title}</h3><p className="mt-2 text-sm leading-6 text-slate-400">{body}</p></div>)}
        </div>
      </section>
      <details className="rounded-xl border border-white/10 bg-white/[.025] p-6">
        <summary className="cursor-pointer font-medium">Manual setup, Linux, macOS or WSL2</summary>
        <div className="mt-5 space-y-5 text-sm leading-7 text-slate-300">
          <p>In your OmoBench checkout, activate your Python environment and install the UI and pinned runner:</p>
          <pre className="overflow-auto rounded-lg bg-black/20 p-4 text-amber-200"><code>{"python -m pip install -e './cli[ui,runner]'\nomobench ui"}</code></pre>
          <p>Keep the terminal open and use the browser link it prints. The guided panel supports the reviewed CPU recipes. To connect an existing llama.cpp, LM Studio, vLLM or SGLang server, choose <strong>Advanced panel</strong>. All CLI commands remain available.</p>
          <p>CPU and GPU measurements can be packaged and submitted. Review the evidence, validate the bundle and download it, or create a pull request using your local GitHub CLI authentication. Registry inclusion follows validation and maintainer review.</p>
          <a className="inline-block text-amber-300 underline" href="https://github.com/Xatiiv/omobench/blob/main/docs/browser-control.md">Read the full setup guide ↗</a>
        </div>
      </details>
      <footer className="mt-10 flex flex-wrap justify-between gap-4 border-t border-white/10 pt-6 text-xs leading-6 text-slate-400"><span>Runs locally. Shares only after your review.</span><Link href="/" className="text-amber-300">Back to the registry ↗</Link></footer>
    </div>
  </main>;
}
