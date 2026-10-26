import Link from "next/link";
import { SiteHeader } from "@/components/product/site-header";
import { Badge } from "@/components/ui/badge";
import { comparisonHref } from "@/app/lib/comparison";
import { demoResults, type DemoResult } from "@/app/data/demo-results";
import {
  getRegistryResult,
  type RegistryResult,
} from "@/app/data/registry-results";

const expectedArtifacts = [
  ["result.json", "The normalized, schema-validated benchmark summary."],
  ["guidellm-benchmarks.json.gz", "The compressed raw measurement output from GuideLLM."],
  ["guidellm.stdout.log", "The runner log, with local endpoint details redacted."],
  ["environment.json", "Detected operating system, accelerator, driver, CPU, and memory details."],
  ["reproduce.md", "The exact command, configuration, and notes needed to repeat the run."],
];

type RunDetailsProps = {
  params: Promise<{ runId: string }>;
};

function formatMetric(value: number, suffix = ""): string {
  return `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value)}${suffix}`;
}

function formatStartedAt(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}

function DetailShell({
  badge,
  children,
}: Readonly<{
  badge: React.ReactNode;
  children: React.ReactNode;
}>) {
  return (
    <main className="product-page min-h-screen text-slate-50">
      <div className="mx-auto max-w-6xl px-6 py-8 sm:px-10 lg:px-12">
        <SiteHeader active="results" />
        <div className="mt-8">{badge}</div>
        {children}
      </div>
    </main>
  );
}

function LocalRunDetails({ result }: Readonly<{ result: RegistryResult }>) {
  const reproducibilityFields = [
    [result.modelSource ? "Model revision (of weights source)" : "Model revision", result.modelRevision],
    ...(result.modelSource ? [["Weights source", result.modelSource]] : []),
    ["Runtime", `${result.framework} ${result.frameworkVersion}`],
    ["Runtime arguments", result.runtimeArguments.length ? result.runtimeArguments.join(" ") : "None recorded"],
    ["GuideLLM", result.runnerVersion],
    ["CPU / RAM", `${result.hardware.cpu} · ${formatMetric(result.hardware.ramGb, " GB")}`],
    ["Operating system", result.hardware.os],
    ["Driver version", result.hardware.driverVersion ?? "Not reported"],
    ["Configuration fingerprint", result.configurationFingerprint],
    ["Source commit", result.sourceCommit ?? "Not recorded"],
  ];

  return (
    <DetailShell
      badge={
        <Badge className="border-amber-400/30 bg-amber-400/10 text-amber-200" variant="outline">
          Registry evidence
        </Badge>
      }
    >
      <section className="border-b border-white/10 py-14">
        <Link className="font-mono text-xs text-amber-300 transition hover:text-amber-200 hover:underline" href="/#local-results">
          ← Back to results
        </Link>
        <p className="mt-8 font-mono text-xs uppercase tracking-[0.22em] text-amber-300">Registry run record</p>
        <h1 className="mt-3 max-w-4xl text-4xl font-semibold tracking-[-0.04em] sm:text-5xl">
          {result.model} on {result.hardware.display}
        </h1>
        <p className="mt-5 max-w-3xl text-lg leading-8 text-slate-300">
          This record is included in this website build. Inspect its measurements, declared setup and evidence metadata below. Inclusion in a local preview alone does not establish community review.
        </p>
      </section>

      <Link className="product-button mt-6" href={comparisonHref(result.id)}>Compare this setup ↗</Link>

      <section className="grid gap-4 py-10 sm:grid-cols-2 lg:grid-cols-5">
        {[
          ["Execution device", result.executionDevice.toUpperCase()],
          ["Profile", result.profile],
          ["Framework", `${result.framework} ${result.frameworkVersion}`],
          ["Quantization", result.quantization ?? "Unspecified"],
          ["Run started", formatStartedAt(result.startedAt)],
        ].map(([label, value]) => (
          <div className="rounded-xl border border-white/10 bg-white/[0.025] p-5" key={label}>
            <p className="font-mono text-xs uppercase tracking-[0.16em] text-slate-500">{label}</p>
            <p className="mt-3 text-sm font-medium text-slate-100">{value}</p>
          </div>
        ))}
      </section>

      <section className="grid gap-4 border-y border-white/10 py-10 lg:grid-cols-[0.9fr_1.1fr]">
        <div className="rounded-xl border border-amber-400/20 bg-amber-400/[0.035] p-6">
          <p className="font-mono text-xs uppercase tracking-[0.18em] text-amber-300">Performance summary</p>
          <dl className="mt-6 space-y-4">
            {[
              ["Time to first token, p50", formatMetric(result.ttftP50, " ms")],
              ["Time to first token, p95", formatMetric(result.ttftP95, " ms")],
              ["Inter-token latency, p50", formatMetric(result.itlP50, " ms")],
              ["Inter-token latency, p95", formatMetric(result.itlP95, " ms")],
              ["Output tokens per second", formatMetric(result.outputTokensPerSecond)],
              ["Total tokens per second", formatMetric(result.totalTokensPerSecond)],
              ["Requests per second", formatMetric(result.requestsPerSecond)],
              ["Error rate", formatMetric(result.errorRatePercent, "%")],
            ].map(([label, value]) => (
              <div className="flex items-end justify-between gap-4 border-b border-white/10 pb-4 last:border-0 last:pb-0" key={label}>
                <dt className="text-sm text-slate-400">{label}</dt>
                <dd className="font-mono text-lg font-medium text-slate-100">{value}</dd>
              </div>
            ))}
          </dl>
        </div>

        <div className="rounded-xl border border-white/10 bg-white/[0.025] p-6">
          <p className="font-mono text-xs uppercase tracking-[0.18em] text-slate-500">Reproducibility record</p>
          <dl className="mt-5 space-y-3 text-sm">
            {reproducibilityFields.map(([label, value]) => (
              <div className="grid gap-2 border-b border-white/10 pb-3 last:border-0 last:pb-0 sm:grid-cols-[9rem_1fr]" key={label}>
                <dt className="text-slate-400">{label}</dt>
                <dd className="break-all font-mono text-xs text-slate-200">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      <section className="py-12" id="evidence">
        <div className="flex flex-col gap-3 border-b border-white/10 pb-6 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="font-mono text-xs uppercase tracking-[0.2em] text-slate-500">Evidence bundle</p>
            <h2 className="mt-2 text-2xl font-semibold tracking-tight">Files retained with this result.</h2>
          </div>
          <Badge className="border-white/15 bg-white/5 text-slate-300" variant="outline">{result.runKind} run · {result.submitter}</Badge>
        </div>
        <div className="mt-6 grid gap-3 md:grid-cols-2">
          <article className="rounded-xl border border-white/10 bg-white/[0.025] p-5">
            <p className="font-mono text-sm text-slate-100">result.json</p>
            <p className="mt-2 text-sm leading-6 text-slate-400">Normalized summary used to render this registry record.</p>
          </article>
          {result.artifacts.map((artifact) => (
            <article className="rounded-xl border border-white/10 bg-white/[0.025] p-5" key={artifact.path}>
              <p className="font-mono text-sm text-slate-100">{artifact.path}</p>
              <p className="mt-2 text-sm leading-6 text-slate-400">{artifact.label}</p>
              <p className="mt-3 break-all font-mono text-xs text-slate-500">{artifact.sha256}</p>
            </article>
          ))}
        </div>
      </section>
    </DetailShell>
  );
}

function DemoRunDetails({ result }: Readonly<{ result: DemoResult }>) {
  return (
    <DetailShell
      badge={
        <Badge className="border-amber-300/30 bg-amber-300/10 text-amber-200" variant="outline">
          Illustrative local data
        </Badge>
      }
    >
      <section className="border-b border-white/10 py-14">
        <Link className="font-mono text-xs text-amber-300 transition hover:text-amber-200 hover:underline" href="/#compare">
          ← Back to demonstration
        </Link>
        <p className="mt-8 font-mono text-xs uppercase tracking-[0.22em] text-amber-300">Run record preview</p>
        <h1 className="mt-3 max-w-4xl text-4xl font-semibold tracking-[-0.04em] sm:text-5xl">
          {result.model} on {result.hardware}
        </h1>
        <p className="mt-5 max-w-3xl text-lg leading-8 text-slate-300">
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
            <p className="font-mono text-xs uppercase tracking-[0.16em] text-slate-500">{label}</p>
            <p className="mt-3 text-sm font-medium text-slate-100">{value}</p>
          </div>
        ))}
      </section>

      <section className="grid gap-4 border-y border-white/10 py-10 lg:grid-cols-[1fr_1fr]">
        <div className="rounded-xl border border-amber-400/20 bg-amber-400/[0.035] p-6">
          <p className="font-mono text-xs uppercase tracking-[0.18em] text-amber-300">Performance summary</p>
          <dl className="mt-6 space-y-4">
            <div className="flex items-end justify-between gap-4 border-b border-white/10 pb-4">
              <dt className="text-sm text-slate-400">Time to first token</dt>
              <dd className="font-mono text-2xl font-medium text-slate-100">{result.ttft}</dd>
            </div>
            <div className="flex items-end justify-between gap-4">
              <dt className="text-sm text-slate-400">Output tokens per second</dt>
              <dd className="font-mono text-2xl font-medium text-amber-300">{result.outputTokensPerSecond}</dd>
            </div>
          </dl>
        </div>

        <div className="rounded-xl border border-white/10 bg-white/[0.025] p-6">
          <p className="font-mono text-xs uppercase tracking-[0.18em] text-slate-500">Reproducibility record</p>
          <p className="mt-3 text-sm leading-6 text-slate-400">A genuine result replaces every pending field below with detected, versioned values.</p>
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
                <dt className="text-slate-400">{label}</dt>
                <dd className="font-mono text-xs text-slate-300">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      <section className="py-12">
        <div className="flex flex-col gap-3 border-b border-white/10 pb-6 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="font-mono text-xs uppercase tracking-[0.2em] text-slate-500">Evidence bundle</p>
            <h2 className="mt-2 text-2xl font-semibold tracking-tight">What a real submission will retain.</h2>
          </div>
          <Badge className="border-white/15 bg-white/5 text-slate-300" variant="outline">Awaiting real run</Badge>
        </div>
        <div className="mt-6 grid gap-3 md:grid-cols-2">
          {expectedArtifacts.map(([name, description]) => (
            <article className="rounded-xl border border-white/10 bg-white/[0.025] p-5" key={name}>
              <p className="font-mono text-sm text-slate-100">{name}</p>
              <p className="mt-2 text-sm leading-6 text-slate-400">{description}</p>
            </article>
          ))}
        </div>
      </section>
    </DetailShell>
  );
}

function ResultNotFound() {
  return (
    <main className="min-h-screen bg-slate-950 px-6 py-12 text-slate-50 sm:px-10">
      <div className="mx-auto max-w-3xl rounded-2xl border border-white/10 bg-white/[0.025] p-8">
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-amber-200">Result not found</p>
        <h1 className="mt-3 text-3xl font-semibold">There is no local record with this ID.</h1>
        <p className="mt-3 max-w-xl leading-7 text-slate-400">Return to the comparison page and choose an available packaged result or an illustrative row.</p>
        <Link className="mt-6 inline-flex rounded-md bg-amber-400 px-4 py-2.5 text-sm font-medium text-slate-950 transition hover:bg-amber-300" href="/">
          Back to OmoBench
        </Link>
      </div>
    </main>
  );
}

export default async function RunDetails({ params }: RunDetailsProps) {
  const { runId } = await params;
  const localResult = getRegistryResult(runId);

  if (localResult) {
    return <LocalRunDetails result={localResult} />;
  }

  const demoResult = demoResults.find((candidate) => candidate.id === runId);
  if (demoResult) {
    return <DemoRunDetails result={demoResult} />;
  }

  return <ResultNotFound />;
}
