"use client";

import { useMemo, useState, useSyncExternalStore } from "react";
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
import { registryResults } from "@/app/data/registry-results";
import {
  ANY,
  EXECUTION_MODE_LABEL,
  NO_FILTERS,
  NOT_RECORDED,
  differingSettings,
  filterOptions,
  filterResults,
  formatGb,
  gpuSummary,
  hardwareFacets,
  runtimeWithVersion,
  vramSummary,
  type ExecutionMode,
  type FilterOption,
  type HardwareFilters,
} from "@/app/lib/hardware-filters";

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

// Hardware first: the filters a person reaches for when asking "how does this
// model run on other machines?" or "what runs well on mine?". Choices come
// from the published results, and "Not recorded" is offered as its own answer.
const registryOptions = filterOptions(registryResults);
const FILTER_KEYS = Object.keys(NO_FILTERS) as Array<keyof HardwareFilters>;

function deviceBadgeClassName(mode: ExecutionMode): string {
  return mode === "cpu-only"
    ? "border-sky-300/30 bg-sky-300/10 text-sky-200"
    : mode === "cpu-gpu-offload"
      ? "border-violet-300/30 bg-violet-300/10 text-violet-200"
      : "border-amber-400/30 bg-amber-400/10 text-amber-200";
}

type RegistryFilterProps = {
  id: string;
  label: string;
  onValueChange: (value: string) => void;
  options: FilterOption[];
  value: string;
};

function RegistryFilter({ id, label, onValueChange, options, value }: RegistryFilterProps) {
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
            <SelectItem className="font-mono text-xs focus:bg-amber-400/15 focus:text-amber-100" key={option.value} value={option.value}>
              {option.label}
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

function filtersFromSearch(search: string): HardwareFilters {
  const params = new URLSearchParams(search);
  const filters = { ...NO_FILTERS };
  for (const key of FILTER_KEYS) {
    const value = params.get(key);
    if (value && registryOptions[key].some(option => option.value === value)) filters[key] = value;
  }
  return filters;
}

function writeFiltersToAddress(filters: HardwareFilters) {
  const params = new URLSearchParams();
  for (const name of FILTER_KEYS) if (filters[name] !== ANY) params.set(name, filters[name]);
  const query = params.toString();
  window.history.replaceState(null, "", `${window.location.pathname}${query ? `?${query}` : ""}#local-results`);
}

function subscribeToLocation(onChange: () => void) {
  window.addEventListener("popstate", onChange);
  return () => window.removeEventListener("popstate", onChange);
}

function LocalRegistry() {
  // Filters live in the address, so a filtered view can be shared or reopened.
  // What the address says is the starting point; a choice made here wins.
  const search = useSyncExternalStore(subscribeToLocation, () => window.location.search, () => "");
  const [chosen, setChosen] = useState<HardwareFilters | null>(null);
  const filters = chosen ?? filtersFromSearch(search);
  const update = (next: HardwareFilters) => { setChosen(next); writeFiltersToAddress(next); };
  const setFilter = (key: keyof HardwareFilters) => (value: string) => update({ ...filters, [key]: value });

  const filteredResults = useMemo(() => filterResults(registryResults, filters), [filters]);
  const differing = differingSettings(filteredResults);
  const sameWorkload = filters.model !== ANY && filters.profile !== ANY && filteredResults.length > 1;
  const active = FILTER_KEYS.some(key => filters[key] !== ANY);

  const hasResults = registryResults.length > 0;

  return (
    <section className="pb-16" id="local-results">
      <div className="rounded-2xl border border-amber-400/25 bg-amber-400/[0.035] p-6 sm:p-8">
        <div className="flex flex-col gap-3 border-b border-white/10 pb-6 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="font-mono text-xs uppercase tracking-[0.2em] text-amber-300">Benchmark registry</p>
            <h2 className="mt-2 text-2xl font-semibold tracking-tight">Performance with the evidence to back it up.</h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-400">
              Start from the hardware: choose a processor, graphics card or memory size to see how models run on it, or choose a model to see it across machines. Open a result to inspect its configuration, measurements and source artifacts.
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
            <p className="mt-5 font-mono text-[11px] uppercase tracking-[0.18em] text-slate-400">Serving hardware</p>
            <div aria-label="Serving hardware filters" className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-5" role="group">
              <RegistryFilter id="registry-cpu-filter" label="CPU" onValueChange={setFilter("cpu")} options={registryOptions.cpu} value={filters.cpu} />
              <RegistryFilter id="registry-gpu-filter" label="Inference GPU" onValueChange={setFilter("gpu")} options={registryOptions.gpu} value={filters.gpu} />
              <RegistryFilter id="registry-vram-filter" label="VRAM per inference GPU" onValueChange={setFilter("vram")} options={registryOptions.vram} value={filters.vram} />
              <RegistryFilter id="registry-ram-filter" label="System RAM" onValueChange={setFilter("ram")} options={registryOptions.ram} value={filters.ram} />
              <RegistryFilter id="registry-mode-filter" label="Execution" onValueChange={setFilter("mode")} options={registryOptions.mode} value={filters.mode} />
            </div>
            <p className="mt-4 font-mono text-[11px] uppercase tracking-[0.18em] text-slate-400">Model and workload</p>
            <div aria-label="Model and workload filters" className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-4" role="group">
              <RegistryFilter id="registry-model-filter" label="Model" onValueChange={setFilter("model")} options={registryOptions.model} value={filters.model} />
              <RegistryFilter id="registry-quantization-filter" label="Quantization" onValueChange={setFilter("quantization")} options={registryOptions.quantization} value={filters.quantization} />
              <RegistryFilter id="registry-runtime-filter" label="Runtime" onValueChange={setFilter("runtime")} options={registryOptions.runtime} value={filters.runtime} />
              <RegistryFilter id="registry-profile-filter" label="Profile" onValueChange={setFilter("profile")} options={registryOptions.profile} value={filters.profile} />
            </div>
            <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-slate-400">
              <p aria-live="polite">
                Showing <span className="font-mono text-slate-200">{filteredResults.length}</span> of {registryResults.length} registry results.
              </p>
              {active && <button className="font-mono text-xs text-amber-300 hover:underline" onClick={() => update(NO_FILTERS)} type="button">Clear filters</button>}
              {sameWorkload && <Link className="font-mono text-xs text-sky-300 hover:underline" href={comparisonHref(filteredResults[0].id, filteredResults.slice(1, 4).map(result => result.id))}>Compare these runs side by side ↗</Link>}
            </div>
            {differing.length > 0 && (
              <p className="mt-2 text-xs leading-5 text-amber-200/80" role="note">
                These runs differ in {differing.join(", ")}. Rows side by side are not the same measurement unless model, profile and settings match; the comparison page says exactly what differs.
              </p>
            )}
            <div className="mt-4">
              <Table>
                <TableHeader>
                  <TableRow className="border-white/10 hover:bg-transparent">
                    <TableHead className="text-slate-400">CPU</TableHead>
                    <TableHead className="text-slate-400">Inference GPU</TableHead>
                    <TableHead className="text-slate-400">RAM</TableHead>
                    <TableHead className="text-slate-400">VRAM</TableHead>
                    <TableHead className="text-slate-400">Model</TableHead>
                    <TableHead className="text-slate-400">Quantization</TableHead>
                    <TableHead className="text-slate-400">Runtime</TableHead>
                    <TableHead className="text-slate-400">Profile</TableHead>
                    <TableHead className="text-slate-400">Output tok/s</TableHead>
                    <TableHead className="text-slate-400">TTFT p50</TableHead>
                    <TableHead className="text-right text-slate-400">Record</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredResults.length === 0 ? (
                    <TableRow className="border-white/10 hover:bg-transparent">
                      <TableCell className="py-8 text-center text-sm text-slate-400" colSpan={11}>
                        No registry results match this combination.
                      </TableCell>
                    </TableRow>
                  ) : (
                    filteredResults.map((result) => {
                      const facets = hardwareFacets(result);
                      return (
                        <TableRow className="border-white/10 hover:bg-white/[0.035]" key={result.id}>
                          <TableCell>
                            <p className="font-medium text-slate-100">{facets.cpu ?? NOT_RECORDED}</p>
                            <p className="mt-1 font-mono text-[11px] text-slate-500">
                              {facets.cpuCores !== null ? `${facets.cpuCores} cores` : "Cores not recorded"}
                              {facets.cpuThreads !== null ? ` · ${facets.cpuThreads} threads` : ""}
                            </p>
                          </TableCell>
                          <TableCell>
                            <p className="text-slate-100">{gpuSummary(facets)}</p>
                            <Badge className={`mt-1 ${deviceBadgeClassName(facets.executionMode)}`} variant="outline">
                              {EXECUTION_MODE_LABEL[facets.executionMode]}
                            </Badge>
                          </TableCell>
                          <TableCell className="font-mono text-xs text-slate-300">
                            {formatGb(facets.ramGb)}
                            {facets.ramGb !== null && <span className="block text-[11px] text-slate-500">{facets.ramIsInstalled ? "installed" : "usable, as reported"}</span>}
                          </TableCell>
                          <TableCell className="font-mono text-xs text-slate-300">{vramSummary(facets)}</TableCell>
                          <TableCell className="font-mono text-xs text-slate-200">{result.model}</TableCell>
                          <TableCell className="font-mono text-xs text-slate-300">{facets.quantization}</TableCell>
                          <TableCell className="font-mono text-xs text-slate-300">{runtimeWithVersion(result)}</TableCell>
                          <TableCell className="font-mono text-xs text-slate-300">{result.profile}</TableCell>
                          <TableCell className="font-mono text-amber-300">{formatMetric(result.outputTokensPerSecond)}</TableCell>
                          <TableCell className="font-mono text-slate-100">{formatMetric(result.ttftP50, " ms")}</TableCell>
                          <TableCell className="text-right">
                            <a className="font-mono text-xs text-amber-300 transition hover:text-amber-200 hover:underline" href={`/runs/${result.id}`}>
                              View details
                            </a>
                            <Link className="mt-2 block font-mono text-xs text-sky-300 hover:underline" href={comparisonHref(result.id)}>Compare ↗</Link>
                          </TableCell>
                        </TableRow>
                      );
                    })
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

        <footer className="border-t border-white/10 py-8 text-sm text-slate-500">
          OmoBench is being built as a transparent registry for reproducible inference-performance evidence.
        </footer>
      </div>
    </main>
  );
}
