import Link from "next/link";

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
        <ol className="mt-10 space-y-8 text-sm leading-7 text-zinc-300">
          <li><h2 className="text-lg font-medium text-zinc-50">1. Prepare your local environment</h2>
            <p>Open a terminal in your OmoBench checkout and activate its Python environment.
              Execution currently requires Linux, macOS, or WSL2. Install the UI and pinned runner:</p>
            <pre className="mt-3 overflow-auto rounded-lg border border-white/10 bg-white/5 p-4 text-emerald-200"><code>{"python -m pip install -e './cli[ui,runner]'"}</code></pre>
          </li>
          <li><h2 className="text-lg font-medium text-zinc-50">2. Open the control panel</h2>
            <pre className="mt-3 overflow-auto rounded-lg border border-white/10 bg-white/5 p-4 text-emerald-200"><code>omobench ui</code></pre>
            <p className="mt-3">Keep the terminal open. If your browser does not open automatically, use the full connection link printed in the terminal.
              It opens a <strong>guided benchmark by default</strong>: downloading, verifying, running, and helping
              submit one reviewed small CPU model — nothing to type but a submitter identity when you share a result.</p>
          </li>
          <li><h2 className="text-lg font-medium text-zinc-50">3. Or connect a server you already run</h2>
            <p>Already have a model loaded in llama.cpp, LM Studio, vLLM, or SGLang? Open <strong>/advanced</strong> in
              the same panel instead. Use a localhost OpenAI-compatible streaming endpoint and configure CPU or GPU
              execution there; the advanced panel tests the connection, runs the benchmark, and shows measurements
              and live logs.</p>
          </li>
          <li><h2 className="text-lg font-medium text-zinc-50">4. Review the evidence</h2>
            <p>CPU and GPU measurements can both be viewed, packaged, and submitted. For a
              confirmed run on either device, enter the model and runtime details, validate the
              bundle, and review the files. You can download the bundle or create a GitHub pull
              request using your local GitHub CLI login and repository write access.
              CI and maintainer review still decide what enters the public registry.</p>
          </li>
        </ol>
        <div className="mt-10 rounded-lg border border-emerald-400/20 bg-emerald-400/5 p-5 text-sm leading-7 text-zinc-300">
          This hosted page provides the launch instructions. The control panel runs on localhost;
          the hosted website cannot start programs on your PC. Node.js is not needed for the local panel.
        </div>
        <a className="mt-8 inline-block text-sm text-emerald-300 hover:underline" href="https://github.com/Xatiiv/omobench/blob/main/docs/browser-control.md">Read the full setup and demo guide ↗</a>
      </div>
    </main>
  );
}
