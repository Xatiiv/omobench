import Link from "next/link";
import {
  windowsCompanionGuideUrl,
  windowsRelease,
  windowsReleasesUrl,
} from "../lib/windows-release";

export default function RunPage() {
  return (
    <main className="min-h-screen bg-zinc-950 px-6 py-12 text-zinc-50">
      <div className="mx-auto max-w-3xl">
        <Link className="text-sm text-emerald-300 hover:underline" href="/">← OmoBench registry</Link>
        <p className="mt-14 font-mono text-xs uppercase tracking-[0.2em] text-emerald-300">Local benchmark control</p>
        <h1 className="mt-4 text-4xl font-semibold tracking-tight">Run a benchmark from your browser.</h1>
        <p className="mt-6 text-lg leading-8 text-zinc-300">
          Launch the local OmoBench control panel on the machine running your model.
          It uses the existing CLI to execute fixed profiles, stream logs, and save evidence.
        </p>

        <section className="mt-10 rounded-xl border border-sky-400/30 bg-sky-400/5 p-6">
          <h2 className="text-lg font-medium text-zinc-50">New to OmoBench on Windows?</h2>
          <p className="mt-3 text-sm leading-7 text-zinc-300">
            The Windows installer installs the OmoBench companion, checks what your machine already
            has, and prepares a benchmark environment if you do not have one — no repository to clone
            and no Python paths to find.
          </p>
          <p className="mt-3 text-sm leading-7 text-zinc-300">
            It cannot do quite everything for you. If Windows Subsystem for Linux is not installed
            yet, that step needs an administrator command and a restart, and if your Linux
            distribution is missing Python&nbsp;3 or its <code className="text-sky-200">venv</code>{" "}
            package, installing those needs your Linux password. The installer detects both cases,
            tells you the exact command to run, and continues once you have.
          </p>

          {windowsRelease ? (
            <>
              <a
                className="mt-5 inline-block rounded-lg bg-sky-400 px-5 py-3 text-sm font-semibold text-sky-950 hover:bg-sky-300"
                href={windowsRelease.downloadUrl}
              >
                Download OmoBench for Windows
              </a>
              <p className="mt-4 text-xs leading-6 text-zinc-400">
                Version {windowsRelease.version} · 64-bit Windows 10 (version 2004 or later) or
                Windows 11 · installs for your user account only, no administrator rights needed.
              </p>
              <p className="mt-3 text-xs leading-6 text-zinc-400">
                {windowsRelease.signed ? (
                  <>This build is code-signed. </>
                ) : (
                  <>
                    This build is <strong>not code-signed</strong>, so Windows will warn about an
                    unknown publisher.{" "}
                  </>
                )}
                Verify the download before running it — in PowerShell,{" "}
                <code className="text-sky-200">Get-FileHash .\OmoBenchSetup.exe -Algorithm SHA256</code>{" "}
                should print:
              </p>
              <p className="mt-2 break-all font-mono text-[11px] leading-5 text-zinc-400">
                {windowsRelease.sha256}
              </p>
              <p className="mt-3 text-xs leading-6 text-zinc-400">
                Built from revision{" "}
                <code className="text-sky-200">{windowsRelease.revision.slice(0, 12)}</code>. See the{" "}
                <a className="text-sky-300 hover:underline" href={windowsRelease.releaseUrl}>
                  release notes
                </a>{" "}
                for checksums and changes.
              </p>
            </>
          ) : (
            <>
              <p className="mt-5 inline-block cursor-not-allowed rounded-lg border border-zinc-600 bg-zinc-800/60 px-5 py-3 text-sm font-semibold text-zinc-400">
                Download not published yet
              </p>
              <p className="mt-4 text-xs leading-6 text-zinc-400">
                There is no published installer to download yet, so this page does not offer one — a
                link that led nowhere would just waste your time. The installer is built and tested
                on every change; you can build it yourself today, or watch the{" "}
                <a className="text-sky-300 hover:underline" href={windowsReleasesUrl}>
                  releases page
                </a>{" "}
                for the first published build.
              </p>
              <p className="mt-3 text-xs leading-6 text-zinc-400">
                Building it takes one command on Windows and needs no developer tools installed —
                see{" "}
                <a className="text-sky-300 hover:underline" href={windowsCompanionGuideUrl}>
                  the Windows installation guide
                </a>
                .
              </p>
            </>
          )}
        </section>

        <section className="mt-10 rounded-xl border border-emerald-400/30 bg-emerald-400/5 p-6">
          <h2 className="text-lg font-medium text-zinc-50">Already set up the Windows companion?</h2>
          <p className="mt-3 text-sm leading-7 text-zinc-300">
            This button asks Windows to open the OmoBench companion installed on your own PC. The companion
            starts the local panel in your WSL environment and opens it in your browser — no terminal, no
            token to copy. This page never talks to the panel itself.
          </p>
          <a
            className="mt-5 inline-block rounded-lg bg-emerald-400 px-5 py-3 text-sm font-semibold text-emerald-950 hover:bg-emerald-300"
            href="omobench://open"
          >
            Open OmoBench
          </a>
          <p className="mt-4 text-xs leading-6 text-zinc-400">
            Nothing happened? The companion is not installed or registered on this PC yet, you are on a
            different machine from the one running your model, or your browser blocked the handler. Windows
            may first ask which app to use — choose <strong>OmoBench companion</strong>. Nothing on this page
            can tell whether the panel actually opened, so it will not claim that it did: check your taskbar
            for the companion window, which shows starting, ready, or failed, and has its own diagnostic log.
          </p>
          <p className="mt-3 text-xs leading-6 text-zinc-400">
            You do not need this page to use the companion: its own window has an <strong>Open panel</strong>{" "}
            button that does exactly the same thing, and any <code className="text-emerald-200">omobench://open</code>{" "}
            link works once the companion is registered.
          </p>
          <p className="mt-3 text-xs leading-6 text-zinc-400">
            Not set up yet? The companion is a small Windows program that you run once to choose your WSL
            distribution, your OmoBench checkout, and the Python environment that already has OmoBench
            installed. It is built from this repository — see{" "}
            <a
              className="text-emerald-300 hover:underline"
              href="https://github.com/Xatiiv/omobench/blob/main/docs/windows-companion.md"
            >
              the companion guide
            </a>
            {" "}for how to build and set it up. If you used the installer above, this is already done —
            it finds an existing installation, or prepares one, and registers the link for you.
          </p>
        </section>

        <h2 className="mt-12 text-lg font-medium text-zinc-50">What the companion does for you</h2>
        <ol className="mt-4 space-y-3 text-sm leading-7 text-zinc-300">
          <li>1. Starts your configured WSL distribution and the existing panel, using the Python interpreter you chose.</li>
          <li>2. Opens the authenticated <code className="text-emerald-200">127.0.0.1</code> panel address in your default browser.</li>
          <li>3. Keeps running in the background with <strong>Open panel</strong> and <strong>Stop</strong> controls. Clicking the button again reuses the same panel instead of starting a second one.</li>
        </ol>
        <p className="mt-4 text-sm leading-7 text-zinc-400">
          Launching only opens the panel. Downloading a model and starting a benchmark stay explicit
          actions inside the guided panel.
        </p>

        <details className="mt-10 rounded-lg border border-white/10 bg-white/5 p-5">
          <summary className="cursor-pointer text-sm font-medium text-zinc-100">
            Manual setup — any OS, no companion needed
          </summary>
          <ol className="mt-6 space-y-8 text-sm leading-7 text-zinc-300">
            <li><h3 className="text-base font-medium text-zinc-50">1. Prepare your local environment</h3>
              <p>Open a terminal in your OmoBench checkout and activate its Python environment.
                Execution currently requires Linux, macOS, or WSL2. Install the UI and pinned runner:</p>
              <pre className="mt-3 overflow-auto rounded-lg border border-white/10 bg-white/5 p-4 text-emerald-200"><code>{"python -m pip install -e './cli[ui,runner]'"}</code></pre>
            </li>
            <li><h3 className="text-base font-medium text-zinc-50">2. Open the control panel</h3>
              <pre className="mt-3 overflow-auto rounded-lg border border-white/10 bg-white/5 p-4 text-emerald-200"><code>omobench ui</code></pre>
              <p className="mt-3">Keep the terminal open. If your browser does not open automatically, use the full connection link printed in the terminal.
                It opens a <strong>guided benchmark by default</strong>: downloading, verifying, running, and helping
                submit one reviewed small CPU model — nothing to type but a submitter identity when you share a result.</p>
            </li>
            <li><h3 className="text-base font-medium text-zinc-50">3. Or connect a server you already run</h3>
              <p>Already have a model loaded in llama.cpp, LM Studio, vLLM, or SGLang? Open <strong>/advanced</strong> in
                the same panel instead. Use a localhost OpenAI-compatible streaming endpoint and configure CPU or GPU
                execution there; the advanced panel tests the connection, runs the benchmark, and shows measurements
                and live logs.</p>
            </li>
            <li><h3 className="text-base font-medium text-zinc-50">4. Review the evidence</h3>
              <p>CPU and GPU measurements can both be viewed, packaged, and submitted. For a
                confirmed run on either device, enter the model and runtime details, validate the
                bundle, and review the files. You can download the bundle or create a GitHub pull
                request using your local GitHub CLI login and repository write access.
                CI and maintainer review still decide what enters the public registry.</p>
            </li>
          </ol>
        </details>

        <div className="mt-10 rounded-lg border border-emerald-400/20 bg-emerald-400/5 p-5 text-sm leading-7 text-zinc-300">
          The control panel runs on localhost and is protected by a connection token. This hosted page cannot
          read it, cannot reach the panel&apos;s APIs, and cannot start programs by itself — the
          <code className="mx-1 text-emerald-200">omobench://</code> link only asks Windows to open software you
          installed and registered yourself. Node.js is not needed for the local panel.
        </div>
        <a className="mt-8 inline-block text-sm text-emerald-300 hover:underline" href="https://github.com/Xatiiv/omobench/blob/main/docs/browser-control.md">Read the full setup and demo guide ↗</a>
      </div>
    </main>
  );
}
