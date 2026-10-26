"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { SiteHeader } from "@/components/product/site-header";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { comparisonHref } from "@/app/lib/comparison";
import { demoResults, type DemoResult } from "@/app/data/demo-results";
import { registryResults, type RegistryResult } from "@/app/data/registry-results";

const profiles = [
  {
    id: "chat-small-v1",
    input: "1,024 tokens",
    output: "256 tokens",
    load: "1 stream",
    purpose: "Interactive assistant latency",
  },
  {
    id: "chat-small-extended-v1",
    input: "1,024 tokens",
    output: "256 tokens",
    load: "1 stream",
    purpose: "More time for slower hardware",
  },
  {
    id: "chat-long-v1",
    input: "8,192 tokens",
    output: "512 tokens",
    load: "1 stream",
    purpose: "Long-context assistant latency",
  },
  {
    id: "throughput-v1",
    input: "1,024 tokens",
    output: "256 tokens",
    load: "16 streams",
    purpose: "Sustained serving throughput",
  },
];

const seedPlan = [
  ["Qwen3-4B", "vLLM · SGLang · llama.cpp", "Planned"],
  ["Llama 3.1 8B Instruct", "vLLM · SGLang · llama.cpp", "Planned"],
  ["DeepSeek-R1-Distill-Qwen-14B", "vLLM · SGLang", "Planned"],
  ["Mistral Small 3.1 24B", "vLLM · SGLang", "Planned"],
  ["Qwen3-32B", "vLLM · SGLang", "Planned"],
];

const getOptions = (key: keyof DemoResult) =>
  Array.from(new Set(demoResults.map((result) => result[key]))).sort();

const modelOptions = getOptions("model");
const hardwareOptions = getOptions("hardware");
const frameworkOptions = getOptions("framework");
const profileOptions = getOptions("profile");

const getRegistryOptions = (select: (result: RegistryResult) => string) =>
  Array.from(new Set(registryResults.map(select))).sort();

const registryModelOptions = getRegistryOptions((result) => result.model);
const registryHardwareOptions = getRegistryOptions((result) => result.hardware.display);
const registryFrameworkOptions = getRegistryOptions((result) => result.framework);
const registryProfileOptions = getRegistryOptions((result) => result.profile);
const registryExecutionDeviceOptions = getRegistryOptions((result) => result.executionDevice.toUpperCase());

function deviceBadgeClassName(executionDevice: RegistryResult["executionDevice"]): string {
  return executionDevice === "cpu"
    ? "border-sky-300/30 bg-sky-300/10 text-sky-200"
    : "border-amber-400/30 bg-amber-400/10 text-amber-200";
}

type DemoFilterProps = {
  id: string;
  label: string;
  onValueChange: (value: string) => void;
  options: string[];
  value: string;
};

function DemoFilter({ id, label, onValueChange, options, value }: DemoFilterProps) {
  return (
    <div>
      <label className="mb-1.5 block font-mono text-[11px] uppercase tracking-[0.14em] text-slate-500" htmlFor={id}>
        {label}
      </label>
      <Select onValueChange={onValueChange} value={value}>
        <SelectTrigger className="h-10 w-full border-white/10 bg-black/20 font-mono text-xs text-slate-200 hover:bg-white/[0.04]" id={id}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent className="border-white/10 bg-slate-900 text-slate-100">
          <SelectItem className="font-mono text-xs focus:bg-amber-400/15 focus:text-amber-100" value="all">
            Any
          </SelectItem>
          {options.map((option) => (
            <SelectItem className="font-mono text-xs focus:bg-amber-400/15 focus:text-amber-100" key={option} value={option}>
              {option}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function formatMetric(value: number, suffix = ""): string {
  return `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(value)}${suffix}`;
}

function LocalRegistry() {
  const [model, setModel] = useState("all");
  const [hardware, setHardware] = useState("all");
  const [framework, setFramework] = useState("all");
  const [profile, setProfile] = useState("all");
  const [device, setDevice] = useState("all");

  const filteredResults = useMemo(
    () =>
      registryResults.filter(
        (result) =>
          (model === "all" || result.model === model) &&
          (hardware === "all" || result.hardware.display === hardware) &&
          (framework === "all" || result.framework === framework) &&
          (profile === "all" || result.profile === profile) &&
          (device === "all" || result.executionDevice.toUpperCase() === device),
      ),
    [device, framework, hardware, model, profile],
  );

  const hasResults = registryResults.length > 0;

  return (
    <section className="pb-16" id="local-results">
      <div className="rounded-2xl border border-amber-400/25 bg-amber-400/[0.035] p-6 sm:p-8">
        <div className="flex flex-col gap-3 border-b border-white/10 pb-6 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="font-mono text-xs uppercase tracking-[0.2em] text-amber-300">Benchmark registry</p>
            <h2 className="mt-2 text-2xl font-semibold tracking-tight">Performance with the evidence to back it up.</h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-400">
              Explore submitted runs by model, hardware and workload. Open a result to inspect its configuration, measurements and source artifacts.
            </p>
          </div>
          <Badge className={hasResults ? "border-amber-400/30 bg-amber-400/10 text-amber-200" : "border-amber-300/30 bg-amber-300/10 text-amber-200"} variant="outline">
            {hasResults ? `${registryResults.length} registry ${registryResults.length === 1 ? "result" : "results"}` : "Awaiting a submitted result"}
          </Badge>
        </div>

        {!hasResults ? (
          <div className="mt-6 rounded-xl border border-dashed border-white/15 bg-black/20 p-6">
            <p className="font-medium text-slate-100">Be the first to put your setup on the board.</p>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-400">
              Run a benchmark, review the evidence and submit it for validation. Accepted results appear here after review and a website build.
            </p>
            <Link className="mt-4 inline-block text-sm text-amber-300 hover:underline" href="/compare">Open the visual comparison workspace ↗</Link>
          </div>
        ) : (
          <>
            <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              <DemoFilter id="registry-model-filter" label="Model" onValueChange={setModel} options={registryModelOptions} value={model} />
              <DemoFilter id="registry-device-filter" label="Execution device" onValueChange={setDevice} options={registryExecutionDeviceOptions} value={device} />
              <DemoFilter id="registry-hardware-filter" label="Hardware" onValueChange={setHardware} options={registryHardwareOptions} value={hardware} />
              <DemoFilter id="registry-framework-filter" label="Framework" onValueChange={setFramework} options={registryFrameworkOptions} value={framework} />
              <DemoFilter id="registry-profile-filter" label="Profile" onValueChange={setProfile} options={registryProfileOptions} value={profile} />
            </div>
            <p aria-live="polite" className="mt-4 text-sm text-slate-400">
              Showing <span className="font-mono text-slate-200">{filteredResults.length}</span> of {registryResults.length} registry results.
            </p>
            <div className="mt-4">
              <Table>
                <TableHeader>
                  <TableRow className="border-white/10 hover:bg-transparent">
                    <TableHead className="text-slate-400">Device</TableHead>
                    <TableHead className="text-slate-400">Hardware</TableHead>
                    <TableHead className="text-slate-400">Framework</TableHead>
                    <TableHead className="text-slate-400">Quantization</TableHead>
                    <TableHead className="text-slate-400">TTFT p50</TableHead>
                    <TableHead className="text-slate-400">Output tok/s</TableHead>
                    <TableHead className="text-right text-slate-400">Record</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredResults.length === 0 ? (
                    <TableRow className="border-white/10 hover:bg-transparent">
                      <TableCell className="py-8 text-center text-sm text-slate-400" colSpan={7}>
                        No registry results match this combination.
                      </TableCell>
                    </TableRow>
                  ) : (
                    filteredResults.map((result) => (
                      <TableRow className="border-white/10 hover:bg-white/[0.035]" key={result.id}>
                        <TableCell>
                          <Badge className={deviceBadgeClassName(result.executionDevice)} variant="outline">
                            {result.executionDevice.toUpperCase()}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <p className="font-medium text-slate-100">{result.hardware.display}</p>
                          <p className="mt-1 font-mono text-xs text-slate-500">{result.model}</p>
                        </TableCell>
                        <TableCell className="font-mono text-xs text-slate-300">{result.framework}</TableCell>
                        <TableCell className="font-mono text-xs text-slate-300">{result.quantization ?? "Unspecified"}</TableCell>
                        <TableCell className="font-mono text-slate-100">{formatMetric(result.ttftP50, " ms")}</TableCell>
                        <TableCell className="font-mono text-amber-300">{formatMetric(result.outputTokensPerSecond)}</TableCell>
                        <TableCell className="text-right">
                          <a className="font-mono text-xs text-amber-300 transition hover:text-amber-200 hover:underline" href={`/runs/${result.id}`}>
                            View details
                          </a>
                          <Link className="mt-2 block font-mono text-xs text-sky-300 hover:underline" href={comparisonHref(result.id)}>Compare ↗</Link>
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </div>
          </>
        )}
      </div>
    </section>
  );
}

export default function Home() {
  const [model, setModel] = useState("all");
  const [hardware, setHardware] = useState("all");
  const [framework, setFramework] = useState("all");
  const [profile, setProfile] = useState("all");

  const filteredResults = useMemo(
    () =>
      demoResults.filter(
        (result) =>
          (model === "all" || result.model === model) &&
          (hardware === "all" || result.hardware === hardware) &&
          (framework === "all" || result.framework === framework) &&
          (profile === "all" || result.profile === profile),
      ),
    [framework, hardware, model, profile],
  );

  return (
    <main className="product-page min-h-screen text-slate-50">
      <div className="mx-auto max-w-7xl px-6 py-6 sm:px-10 lg:px-12">
        <SiteHeader />

        <section className="product-hero" id="top">
          <div className="hero-copy">
            <p className="eyebrow"><span className="status-dot" /> OPEN MODEL BENCHMARKING</p>
            <h1>Put your AI<br /><span>to the test.</span></h1>
            <p className="hero-description">Measure inference speed, response time and reliability on your own hardware. Every result starts with a repeatable workload and ends with evidence you can inspect.</p>
            <div className="hero-actions">
              <Link className="product-button" href="/run">Run a benchmark <span aria-hidden="true">↗</span></Link>
              <Link className="product-button secondary-button" href="/compare">Compare setups <span aria-hidden="true">↗</span></Link>
            </div>
            <div className="hero-capabilities"><span>CPU + GPU</span><span>Local execution</span><span>Open evidence</span></div>
          </div>
          <div className="setup-visual" aria-label="A benchmark combines your model, runtime, hardware and a fixed profile">
            <div className="visual-topline"><span>YOUR BENCHMARK</span><span className="visual-tag">LOCAL FIRST</span></div>
            <div className="setup-orbit" aria-hidden="true">
              <div className="orbit-ring ring-one" /><div className="orbit-ring ring-two" />
              <div className="orbit-core"><svg viewBox="0 0 60 60"><path d="M15 20h30v28H15zM20 20V12h20v8M9 26h6m-6 10h6m30-10h6m-6 10h6M24 48v6m12-6v6"/><path d="m21 36 6-8 6 10 7-9"/></svg></div>
              <span className="orbit-label label-model">01 / MODEL</span><span className="orbit-label label-runtime">02 / RUNTIME</span><span className="orbit-label label-hardware">03 / HARDWARE</span>
            </div>
            <div className="visual-bottom"><div><span className="eyebrow">ONE FIXED WORKLOAD</span><strong>Your setup, measured.</strong></div><span className="visual-arrow" aria-hidden="true">↗</span></div>
            <div className="visual-legend"><span><i /> Throughput</span><span><i /> Latency</span><span><i /> Reliability</span></div>
          </div>
        </section>

        <section className="border-y border-white/10 py-10" id="workflow">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="font-mono text-xs uppercase tracking-[0.2em] text-slate-500">The core loop</p>
              <h2 className="mt-2 text-2xl font-semibold tracking-tight">One result, independently inspectable.</h2>
            </div>
            <p className="max-w-lg text-sm leading-6 text-slate-400">A clear path from your own machine to a result others can reproduce.</p>
          </div>
          <ol className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {[
              ["01", "Run", "Use a versioned profile against a local OpenAI-compatible endpoint."],
              ["02", "Package", "Keep the summary, raw runner output, configuration, and reproducibility notes together."],
              ["03", "Validate", "GitHub checks schema, required metadata, and accidental duplicates before merge."],
              ["04", "Compare", "Filter the published registry by model, runtime, hardware, and profile."],
            ].map(([number, title, description]) => (
              <li className="rounded-xl border border-white/10 bg-white/[0.025] p-5" key={number}>
                <p className="font-mono text-xs text-amber-300">{number}</p>
                <h3 className="mt-5 text-base font-semibold">{title}</h3>
                <p className="mt-2 text-sm leading-6 text-slate-400">{description}</p>
              </li>
            ))}
          </ol>
        </section>

        <section className="py-16" id="profiles">
          <div className="max-w-2xl">
            <p className="font-mono text-xs uppercase tracking-[0.2em] text-slate-500">Benchmark contract v1</p>
            <h2 className="mt-2 text-3xl font-semibold tracking-tight">Choose the workload that matters.</h2>
            <p className="mt-3 text-sm leading-6 text-slate-400">Their YAML definitions are versioned alongside every submitted result. A later methodology change creates a new profile ID instead of rewriting history.</p>
          </div>
          <div className="mt-8 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {profiles.map((profile) => (
              <article className="rounded-xl border border-white/10 bg-white/[0.025] p-6" key={profile.id}>
                <Badge className="border-white/15 bg-white/5 font-mono text-slate-200" variant="outline">{profile.id}</Badge>
                <p className="mt-6 text-lg font-semibold">{profile.purpose}</p>
                <dl className="mt-5 space-y-2 text-sm text-slate-400">
                  <div className="flex justify-between gap-4"><dt>Input</dt><dd className="font-mono text-slate-200">{profile.input}</dd></div>
                  <div className="flex justify-between gap-4"><dt>Output</dt><dd className="font-mono text-slate-200">{profile.output}</dd></div>
                  <div className="flex justify-between gap-4"><dt>Load</dt><dd className="font-mono text-slate-200">{profile.load}</dd></div>
                </dl>
              </article>
            ))}
          </div>
        </section>

        <LocalRegistry />

        <section className="pb-16" id="compare">
          <div className="rounded-2xl border border-amber-400/20 bg-amber-400/[0.035] p-6 sm:p-8">
            <div className="flex flex-col gap-3 border-b border-white/10 pb-6 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <p className="font-mono text-xs uppercase tracking-[0.2em] text-amber-300">Interface demonstration</p>
                <h2 className="mt-2 text-2xl font-semibold tracking-tight">Try the comparison interaction.</h2>
                <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-400">Choose filters to explore the interaction. Every row below is local demonstration data, not a benchmark claim. Packaged results appear separately above.</p>
              </div>
              <Badge className="border-amber-300/30 bg-amber-300/10 text-amber-200" variant="outline">Illustrative local data</Badge>
            </div>
            <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <DemoFilter id="model-filter" label="Model" onValueChange={setModel} options={modelOptions} value={model} />
              <DemoFilter id="hardware-filter" label="Hardware" onValueChange={setHardware} options={hardwareOptions} value={hardware} />
              <DemoFilter id="framework-filter" label="Framework" onValueChange={setFramework} options={frameworkOptions} value={framework} />
              <DemoFilter id="profile-filter" label="Profile" onValueChange={setProfile} options={profileOptions} value={profile} />
            </div>
            <p aria-live="polite" className="mt-4 text-sm text-slate-400">
              Showing <span className="font-mono text-slate-200">{filteredResults.length}</span> of {demoResults.length} illustrative rows. Choose <span className="font-mono text-slate-200">Any</span> to remove a filter.
            </p>
            <div className="mt-4">
              <Table>
                <TableHeader>
                  <TableRow className="border-white/10 hover:bg-transparent">
                    <TableHead className="text-slate-400">Hardware</TableHead>
                    <TableHead className="text-slate-400">Framework</TableHead>
                    <TableHead className="text-slate-400">Quantization</TableHead>
                    <TableHead className="text-slate-400">TTFT</TableHead>
                    <TableHead className="text-slate-400">Output tok/s</TableHead>
                    <TableHead className="text-right text-slate-400">Record</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredResults.length === 0 ? (
                    <TableRow className="border-white/10 hover:bg-transparent">
                      <TableCell className="py-8 text-center text-sm text-slate-400" colSpan={6}>
                        No illustrative rows match this combination yet.
                      </TableCell>
                    </TableRow>
                  ) : (
                    filteredResults.map((result) => (
                    <TableRow className="border-white/10 hover:bg-white/[0.035]" key={result.id}>
                      <TableCell>
                        <p className="font-medium text-slate-100">{result.hardware}</p>
                        <p className="mt-1 font-mono text-xs text-slate-500">{result.model}</p>
                      </TableCell>
                      <TableCell className="font-mono text-xs text-slate-300">{result.framework}</TableCell>
                      <TableCell className="font-mono text-xs text-slate-300">{result.quantization}</TableCell>
                      <TableCell className="font-mono text-slate-100">{result.ttft}</TableCell>
                      <TableCell className="font-mono text-amber-300">{result.outputTokensPerSecond}</TableCell>
                      <TableCell className="text-right">
                        <a className="font-mono text-xs text-amber-300 transition hover:text-amber-200 hover:underline" href={`/runs/${result.id}`}>
                          View details
                        </a>
                      </TableCell>
                    </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </div>
          </div>
        </section>

        <section className="pb-16">
          <div className="rounded-2xl border border-white/10 bg-white/[0.025] p-6 sm:p-8">
            <div className="flex flex-col gap-3 border-b border-white/10 pb-6 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <p className="font-mono text-xs uppercase tracking-[0.2em] text-slate-500">Seed registry</p>
                <h2 className="mt-2 text-2xl font-semibold tracking-tight">Initial coverage plan</h2>
              </div>
              <Badge className="border-amber-300/30 bg-amber-300/10 text-amber-200" variant="outline">No benchmark results published yet</Badge>
            </div>
            <div className="mt-4">
              <Table>
                <TableHeader>
                  <TableRow className="border-white/10 hover:bg-transparent">
                    <TableHead className="text-slate-400">Model</TableHead>
                    <TableHead className="text-slate-400">Initial runtime coverage</TableHead>
                    <TableHead className="text-slate-400">Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {seedPlan.map(([model, runtimes, status]) => (
                    <TableRow className="border-white/10 hover:bg-white/[0.035]" key={model}>
                      <TableCell className="font-medium text-slate-100">{model}</TableCell>
                      <TableCell className="font-mono text-xs text-slate-400">{runtimes}</TableCell>
                      <TableCell><Badge className="border-white/15 bg-white/5 text-slate-300" variant="outline">{status}</Badge></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        </section>

        <footer className="border-t border-white/10 py-8 text-sm text-slate-500">
          OmoBench is being built as a transparent registry for reproducible inference-performance evidence.
        </footer>
      </div>
    </main>
  );
}
