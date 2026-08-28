"use client";

import { useMemo, useState } from "react";
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
      <label className="mb-1.5 block font-mono text-[11px] uppercase tracking-[0.14em] text-zinc-500" htmlFor={id}>
        {label}
      </label>
      <Select onValueChange={onValueChange} value={value}>
        <SelectTrigger className="h-10 w-full border-white/10 bg-black/20 font-mono text-xs text-zinc-200 hover:bg-white/[0.04]" id={id}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent className="border-white/10 bg-zinc-900 text-zinc-100">
          <SelectItem className="font-mono text-xs focus:bg-emerald-400/15 focus:text-emerald-100" value="all">
            Any
          </SelectItem>
          {options.map((option) => (
            <SelectItem className="font-mono text-xs focus:bg-emerald-400/15 focus:text-emerald-100" key={option} value={option}>
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

  const filteredResults = useMemo(
    () =>
      registryResults.filter(
        (result) =>
          (model === "all" || result.model === model) &&
          (hardware === "all" || result.hardware.display === hardware) &&
          (framework === "all" || result.framework === framework) &&
          (profile === "all" || result.profile === profile),
      ),
    [framework, hardware, model, profile],
  );

  const hasResults = registryResults.length > 0;

  return (
    <section className="pb-16" id="local-results">
      <div className="rounded-2xl border border-emerald-400/25 bg-emerald-400/[0.035] p-6 sm:p-8">
        <div className="flex flex-col gap-3 border-b border-white/10 pb-6 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="font-mono text-xs uppercase tracking-[0.2em] text-emerald-300">Local result registry</p>
            <h2 className="mt-2 text-2xl font-semibold tracking-tight">Packaged evidence, ready to inspect.</h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-zinc-400">
              This table reads only normalized <span className="font-mono text-zinc-300">result.json</span> files from the local <span className="font-mono text-zinc-300">results/</span> directory. It never mixes them with demonstration data.
            </p>
          </div>
          <Badge className={hasResults ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-200" : "border-amber-300/30 bg-amber-300/10 text-amber-200"} variant="outline">
            {hasResults ? `${registryResults.length} packaged local ${registryResults.length === 1 ? "result" : "results"}` : "Awaiting a local result"}
          </Badge>
        </div>

        {!hasResults ? (
          <div className="mt-6 rounded-xl border border-dashed border-white/15 bg-black/20 p-6">
            <p className="font-medium text-zinc-100">No real benchmark result is stored locally yet.</p>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-zinc-400">
              After a future benchmark is packaged, copy its complete evidence directory to <span className="font-mono text-zinc-300">results/&lt;model-slug&gt;/&lt;date&gt;-&lt;run-id&gt;/</span>, then restart the local website. The result will appear here automatically.
            </p>
          </div>
        ) : (
          <>
            <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <DemoFilter id="registry-model-filter" label="Model" onValueChange={setModel} options={registryModelOptions} value={model} />
              <DemoFilter id="registry-hardware-filter" label="Hardware" onValueChange={setHardware} options={registryHardwareOptions} value={hardware} />
              <DemoFilter id="registry-framework-filter" label="Framework" onValueChange={setFramework} options={registryFrameworkOptions} value={framework} />
              <DemoFilter id="registry-profile-filter" label="Profile" onValueChange={setProfile} options={registryProfileOptions} value={profile} />
            </div>
            <p aria-live="polite" className="mt-4 text-sm text-zinc-400">
              Showing <span className="font-mono text-zinc-200">{filteredResults.length}</span> of {registryResults.length} packaged local results.
            </p>
            <div className="mt-4">
              <Table>
                <TableHeader>
                  <TableRow className="border-white/10 hover:bg-transparent">
                    <TableHead className="text-zinc-400">Hardware</TableHead>
                    <TableHead className="text-zinc-400">Framework</TableHead>
                    <TableHead className="text-zinc-400">Quantization</TableHead>
                    <TableHead className="text-zinc-400">TTFT p50</TableHead>
                    <TableHead className="text-zinc-400">Output tok/s</TableHead>
                    <TableHead className="text-right text-zinc-400">Record</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredResults.length === 0 ? (
                    <TableRow className="border-white/10 hover:bg-transparent">
                      <TableCell className="py-8 text-center text-sm text-zinc-400" colSpan={6}>
                        No packaged local results match this combination.
                      </TableCell>
                    </TableRow>
                  ) : (
                    filteredResults.map((result) => (
                      <TableRow className="border-white/10 hover:bg-white/[0.035]" key={result.id}>
                        <TableCell>
                          <p className="font-medium text-zinc-100">{result.hardware.display}</p>
                          <p className="mt-1 font-mono text-xs text-zinc-500">{result.model}</p>
                        </TableCell>
                        <TableCell className="font-mono text-xs text-zinc-300">{result.framework}</TableCell>
                        <TableCell className="font-mono text-xs text-zinc-300">{result.quantization ?? "Unspecified"}</TableCell>
                        <TableCell className="font-mono text-zinc-100">{formatMetric(result.ttftP50, " ms")}</TableCell>
                        <TableCell className="font-mono text-emerald-300">{formatMetric(result.outputTokensPerSecond)}</TableCell>
                        <TableCell className="text-right">
                          <a className="font-mono text-xs text-emerald-300 transition hover:text-emerald-200 hover:underline" href={`/runs/${result.id}`}>
                            View details
                          </a>
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
    <main className="min-h-screen bg-zinc-950 text-zinc-50">
      <div className="mx-auto max-w-6xl px-6 py-8 sm:px-10 lg:px-12">
        <nav className="flex items-center justify-between border-b border-white/10 pb-6">
          <a className="font-mono text-lg font-semibold tracking-tight" href="#top">
            omo<span className="text-emerald-400">bench</span>
          </a>
          <Badge className="border-emerald-400/30 bg-emerald-400/10 text-emerald-300" variant="outline">
            Public beta in build
          </Badge>
        </nav>

        <section className="grid gap-12 py-20 lg:grid-cols-[1.25fr_0.75fr] lg:items-end" id="top">
          <div>
            <p className="font-mono text-xs font-medium uppercase tracking-[0.24em] text-emerald-300">
              Open model operations
            </p>
            <h1 className="mt-5 max-w-3xl text-5xl font-semibold tracking-[-0.045em] text-balance sm:text-6xl">
              Reproducible performance data for open models.
            </h1>
            <p className="mt-6 max-w-2xl text-lg leading-8 text-zinc-300">
              OmoBench records how a specific model, runtime, configuration,
              and machine performed under a versioned workload. GitHub holds
              the evidence; this site makes it comparable.
            </p>
            <div className="mt-8 flex flex-wrap gap-3 text-sm">
              <a className="rounded-md bg-emerald-400 px-4 py-2.5 font-medium text-zinc-950 transition hover:bg-emerald-300" href="#workflow">
                See the benchmark loop
              </a>
              <a className="rounded-md border border-white/15 px-4 py-2.5 font-medium text-zinc-200 transition hover:border-white/30 hover:bg-white/5" href="#profiles">
                Inspect v1 profiles
              </a>
            </div>
          </div>
          <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-6 font-mono text-sm leading-7 text-zinc-300 shadow-2xl shadow-black/30">
            <p className="text-zinc-500"># future submission</p>
            <p className="mt-2 text-emerald-300">omobench run</p>
            <p>--endpoint http://localhost:8000/v1</p>
            <p>--model Qwen/Qwen3-4B</p>
            <p>--profile chat-small-v1</p>
            <p className="mt-5 text-zinc-500"># result becomes a GitHub pull request</p>
          </div>
        </section>

        <section className="border-y border-white/10 py-10" id="workflow">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="font-mono text-xs uppercase tracking-[0.2em] text-zinc-500">The core loop</p>
              <h2 className="mt-2 text-2xl font-semibold tracking-tight">One result, independently inspectable.</h2>
            </div>
            <p className="max-w-lg text-sm leading-6 text-zinc-400">The MVP keeps the registry small: runner, GitHub validation, comparison.</p>
          </div>
          <ol className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {[
              ["01", "Run", "Use a versioned profile against a local OpenAI-compatible endpoint."],
              ["02", "Package", "Keep the summary, raw runner output, configuration, and reproducibility notes together."],
              ["03", "Validate", "GitHub checks schema, required metadata, and accidental duplicates before merge."],
              ["04", "Compare", "Filter the published registry by model, runtime, hardware, and profile."],
            ].map(([number, title, description]) => (
              <li className="rounded-xl border border-white/10 bg-white/[0.025] p-5" key={number}>
                <p className="font-mono text-xs text-emerald-300">{number}</p>
                <h3 className="mt-5 text-base font-semibold">{title}</h3>
                <p className="mt-2 text-sm leading-6 text-zinc-400">{description}</p>
              </li>
            ))}
          </ol>
        </section>

        <section className="py-16" id="profiles">
          <div className="max-w-2xl">
            <p className="font-mono text-xs uppercase tracking-[0.2em] text-zinc-500">Benchmark contract v1</p>
            <h2 className="mt-2 text-3xl font-semibold tracking-tight">Three profiles, clearly named.</h2>
            <p className="mt-3 text-sm leading-6 text-zinc-400">Their YAML definitions are versioned alongside every submitted result. A later methodology change creates a new profile ID instead of rewriting history.</p>
          </div>
          <div className="mt-8 grid gap-4 lg:grid-cols-3">
            {profiles.map((profile) => (
              <article className="rounded-xl border border-white/10 bg-white/[0.025] p-6" key={profile.id}>
                <Badge className="border-white/15 bg-white/5 font-mono text-zinc-200" variant="outline">{profile.id}</Badge>
                <p className="mt-6 text-lg font-semibold">{profile.purpose}</p>
                <dl className="mt-5 space-y-2 text-sm text-zinc-400">
                  <div className="flex justify-between gap-4"><dt>Input</dt><dd className="font-mono text-zinc-200">{profile.input}</dd></div>
                  <div className="flex justify-between gap-4"><dt>Output</dt><dd className="font-mono text-zinc-200">{profile.output}</dd></div>
                  <div className="flex justify-between gap-4"><dt>Load</dt><dd className="font-mono text-zinc-200">{profile.load}</dd></div>
                </dl>
              </article>
            ))}
          </div>
        </section>

        <LocalRegistry />

        <section className="pb-16" id="compare">
          <div className="rounded-2xl border border-emerald-400/20 bg-emerald-400/[0.035] p-6 sm:p-8">
            <div className="flex flex-col gap-3 border-b border-white/10 pb-6 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <p className="font-mono text-xs uppercase tracking-[0.2em] text-emerald-300">Interface demonstration</p>
                <h2 className="mt-2 text-2xl font-semibold tracking-tight">Try the comparison interaction.</h2>
                <p className="mt-2 max-w-2xl text-sm leading-6 text-zinc-400">Choose filters to explore the interaction. Every row below is local demonstration data, not a benchmark claim. Packaged results appear separately above.</p>
              </div>
              <Badge className="border-amber-300/30 bg-amber-300/10 text-amber-200" variant="outline">Illustrative local data</Badge>
            </div>
            <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <DemoFilter id="model-filter" label="Model" onValueChange={setModel} options={modelOptions} value={model} />
              <DemoFilter id="hardware-filter" label="Hardware" onValueChange={setHardware} options={hardwareOptions} value={hardware} />
              <DemoFilter id="framework-filter" label="Framework" onValueChange={setFramework} options={frameworkOptions} value={framework} />
              <DemoFilter id="profile-filter" label="Profile" onValueChange={setProfile} options={profileOptions} value={profile} />
            </div>
            <p aria-live="polite" className="mt-4 text-sm text-zinc-400">
              Showing <span className="font-mono text-zinc-200">{filteredResults.length}</span> of {demoResults.length} illustrative rows. Choose <span className="font-mono text-zinc-200">Any</span> to remove a filter.
            </p>
            <div className="mt-4">
              <Table>
                <TableHeader>
                  <TableRow className="border-white/10 hover:bg-transparent">
                    <TableHead className="text-zinc-400">Hardware</TableHead>
                    <TableHead className="text-zinc-400">Framework</TableHead>
                    <TableHead className="text-zinc-400">Quantization</TableHead>
                    <TableHead className="text-zinc-400">TTFT</TableHead>
                    <TableHead className="text-zinc-400">Output tok/s</TableHead>
                    <TableHead className="text-right text-zinc-400">Record</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredResults.length === 0 ? (
                    <TableRow className="border-white/10 hover:bg-transparent">
                      <TableCell className="py-8 text-center text-sm text-zinc-400" colSpan={6}>
                        No illustrative rows match this combination yet.
                      </TableCell>
                    </TableRow>
                  ) : (
                    filteredResults.map((result) => (
                    <TableRow className="border-white/10 hover:bg-white/[0.035]" key={result.id}>
                      <TableCell>
                        <p className="font-medium text-zinc-100">{result.hardware}</p>
                        <p className="mt-1 font-mono text-xs text-zinc-500">{result.model}</p>
                      </TableCell>
                      <TableCell className="font-mono text-xs text-zinc-300">{result.framework}</TableCell>
                      <TableCell className="font-mono text-xs text-zinc-300">{result.quantization}</TableCell>
                      <TableCell className="font-mono text-zinc-100">{result.ttft}</TableCell>
                      <TableCell className="font-mono text-emerald-300">{result.outputTokensPerSecond}</TableCell>
                      <TableCell className="text-right">
                        <a className="font-mono text-xs text-emerald-300 transition hover:text-emerald-200 hover:underline" href={`/runs/${result.id}`}>
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
                <p className="font-mono text-xs uppercase tracking-[0.2em] text-zinc-500">Seed registry</p>
                <h2 className="mt-2 text-2xl font-semibold tracking-tight">Initial coverage plan</h2>
              </div>
              <Badge className="border-amber-300/30 bg-amber-300/10 text-amber-200" variant="outline">No benchmark results published yet</Badge>
            </div>
            <div className="mt-4">
              <Table>
                <TableHeader>
                  <TableRow className="border-white/10 hover:bg-transparent">
                    <TableHead className="text-zinc-400">Model</TableHead>
                    <TableHead className="text-zinc-400">Initial runtime coverage</TableHead>
                    <TableHead className="text-zinc-400">Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {seedPlan.map(([model, runtimes, status]) => (
                    <TableRow className="border-white/10 hover:bg-white/[0.035]" key={model}>
                      <TableCell className="font-medium text-zinc-100">{model}</TableCell>
                      <TableCell className="font-mono text-xs text-zinc-400">{runtimes}</TableCell>
                      <TableCell><Badge className="border-white/15 bg-white/5 text-zinc-300" variant="outline">{status}</Badge></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        </section>

        <footer className="border-t border-white/10 py-8 text-sm text-zinc-500">
          OmoBench is being built as a transparent registry for reproducible inference-performance evidence.
        </footer>
      </div>
    </main>
  );
}
