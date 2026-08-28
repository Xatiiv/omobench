import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { demoResults } from "@/app/data/demo-results";

const expectedArtifacts = [
  ["result.json", "The normalized, schema-validated benchmark summary."],
  ["guidellm-benchmarks.json.gz", "The compressed raw measurement output from GuideLLM."],
  ["stdout.txt", "The runner log, with local endpoint details redacted."],
  ["environment.json", "Detected operating system, accelerator, driver, CPU, and memory details."],
  ["reproduce.md", "The exact command, configuration, and notes needed to repeat the run."],
];

type RunDetailsProps = {
  params: Promise<{ runId: string }>;
};

export default async function RunDetails({ params }: RunDetailsProps) {
  const { runId } = await params;
  const result = demoResults.find((candidate) => candidate.id === runId);

  if (!result) {
    return (
      <main className="min-h-screen bg-zinc-950 px-6 py-12 text-zinc-50 sm:px-10">
        <div className="mx-auto max-w-3xl rounded-2xl border border-white/10 bg-white/[0.025] p-8">
          <p className="font-mono text-xs uppercase tracking-[0.2em] text-amber-200">Local demo</p>
          <h1 className="mt-3 text-3xl font-semibold">Result not found</h1>
          <p className="mt-3 max-w-xl leading-7 text-zinc-400">This local example does not exist. Return to the comparison preview and choose a result there.</p>
          <Link className="mt-6 inline-flex rounded-md bg-emerald-400 px-4 py-2.5 text-sm font-medium text-zinc-950 transition hover:bg-emerald-300" href="/#compare">
            Back to comparison
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-zinc-950 text-zinc-50">
      <div className="mx-auto max-w-6xl px-6 py-8 sm:px-10 lg:px-12">
        <nav className="flex items-center justify-between border-b border-white/10 pb-6">
          <Link className="font-mono text-lg font-semibold tracking-tight" href="/">
            omo<span className="text-emerald-400">bench</span>
          </Link>
          <Badge className="border-amber-300/30 bg-amber-300/10 text-amber-200" variant="outline">
            Illustrative local data
          </Badge>
        </nav>

        <section className="border-b border-white/10 py-14">
          <Link className="font-mono text-xs text-emerald-300 transition hover:text-emerald-200 hover:underline" href="/#compare">
            ← Back to comparison
          </Link>
          <p className="mt-8 font-mono text-xs uppercase tracking-[0.22em] text-emerald-300">Run record preview</p>
          <h1 className="mt-3 max-w-4xl text-4xl font-semibold tracking-[-0.04em] sm:text-5xl">
            {result.model} on {result.hardware}
          </h1>
          <p className="mt-5 max-w-3xl text-lg leading-8 text-zinc-300">
            This is the shape of an OmoBench result page. Its metrics are demonstration values only; the reproducibility fields and raw evidence intentionally remain unfilled until a real run is executed.
          </p>
        </section>

        <section className="grid gap-4 py-10 sm:grid-cols-2 lg:grid-cols-4">
          {[
            ["Profile", result.profile],
            ["Framework", result.framework],
            ["Quantization", result.quantization],
            ["Hardware", result.hardware],
          ].map(([label, value]) => (
            <div className="rounded-xl border border-white/10 bg-white/[0.025] p-5" key={label}>
              <p className="font-mono text-xs uppercase tracking-[0.16em] text-zinc-500">{label}</p>
              <p className="mt-3 text-sm font-medium text-zinc-100">{value}</p>
            </div>
          ))}
        </section>

        <section className="grid gap-4 border-y border-white/10 py-10 lg:grid-cols-[1fr_1fr]">
          <div className="rounded-xl border border-emerald-400/20 bg-emerald-400/[0.035] p-6">
            <p className="font-mono text-xs uppercase tracking-[0.18em] text-emerald-300">Performance summary</p>
            <dl className="mt-6 space-y-4">
              <div className="flex items-end justify-between gap-4 border-b border-white/10 pb-4">
                <dt className="text-sm text-zinc-400">Time to first token</dt>
                <dd className="font-mono text-2xl font-medium text-zinc-100">{result.ttft}</dd>
              </div>
              <div className="flex items-end justify-between gap-4">
                <dt className="text-sm text-zinc-400">Output tokens per second</dt>
                <dd className="font-mono text-2xl font-medium text-emerald-300">{result.outputTokensPerSecond}</dd>
              </div>
            </dl>
          </div>

          <div className="rounded-xl border border-white/10 bg-white/[0.025] p-6">
            <p className="font-mono text-xs uppercase tracking-[0.18em] text-zinc-500">Reproducibility record</p>
            <p className="mt-3 text-sm leading-6 text-zinc-400">A genuine result replaces every pending field below with detected, versioned values.</p>
            <dl className="mt-6 space-y-3 text-sm">
              {[
                ["Model revision", "Pending a real run"],
                ["Runtime version", "Pending a real run"],
                ["Runtime arguments", "Pending a real run"],
                ["GPU driver and memory", "Pending a real run"],
                ["GuideLLM version", "Pending a real run"],
                ["Configuration fingerprint", "Pending a real run"],
              ].map(([label, value]) => (
                <div className="flex items-center justify-between gap-6 border-b border-white/10 pb-3 last:border-0 last:pb-0" key={label}>
                  <dt className="text-zinc-400">{label}</dt>
                  <dd className="font-mono text-xs text-zinc-300">{value}</dd>
                </div>
              ))}
            </dl>
          </div>
        </section>

        <section className="py-12">
          <div className="flex flex-col gap-3 border-b border-white/10 pb-6 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="font-mono text-xs uppercase tracking-[0.2em] text-zinc-500">Evidence bundle</p>
              <h2 className="mt-2 text-2xl font-semibold tracking-tight">What a real submission will retain.</h2>
            </div>
            <Badge className="border-white/15 bg-white/5 text-zinc-300" variant="outline">Awaiting real run</Badge>
          </div>
          <div className="mt-6 grid gap-3 md:grid-cols-2">
            {expectedArtifacts.map(([name, description]) => (
              <article className="rounded-xl border border-white/10 bg-white/[0.025] p-5" key={name}>
                <p className="font-mono text-sm text-zinc-100">{name}</p>
                <p className="mt-2 text-sm leading-6 text-zinc-400">{description}</p>
              </article>
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}
