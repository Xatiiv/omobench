import type { ResultRecord } from "@/app/lib/result-record";

// Hardware-first filtering of published results: "how does this model run on
// different machines?" and "how do different models run on this machine?".
//
// Every fact here is read from what the result records, and "not recorded" is
// its own answer. A result that does not say how much memory its card has does
// not match a 16 GB filter, and is not counted as having none.

export type ExecutionMode = "cpu-only" | "gpu-only" | "cpu-gpu-offload" | "unknown";

export const EXECUTION_MODE_LABEL: Record<ExecutionMode, string> = {
  "cpu-only": "CPU only",
  "gpu-only": "GPU only",
  "cpu-gpu-offload": "Split CPU/GPU",
  unknown: "Not recorded",
};

export const NOT_RECORDED = "Not recorded";

export type InferenceGpu = { count: number; memoryGb: number | null; model: string };

export type HardwareFacets = {
  cpu: string | null;
  cpuCores: number | null;
  cpuThreads: number | null;
  executionMode: ExecutionMode;
  // Devices that performed inference. Installed but unused cards are not here.
  inferenceGpus: InferenceGpu[];
  inferenceGpuCount: number;
  // Installed memory when recorded, otherwise what the operating system
  // reported. Both are the serving machine's.
  ramGb: number | null;
  ramIsInstalled: boolean;
  runtime: string;
  quantization: string;
};

export function hardwareFacets(result: ResultRecord): HardwareFacets {
  const serving = result.serving;
  let inferenceGpus: InferenceGpu[];
  if (serving) {
    inferenceGpus = serving.accelerators
      .filter(device => device.role === "inference")
      .map(device => ({ count: device.count, memoryGb: device.memoryGb, model: device.model }));
  } else {
    // 1.0: hardware.gpus lists the devices the run used, with declared memory.
    inferenceGpus = result.hardware.gpus.map(gpu => ({ count: gpu.count, memoryGb: gpu.memoryGb, model: gpu.model }));
  }
  let executionMode: ExecutionMode;
  if (serving && serving.placement.mode !== "unknown") executionMode = serving.placement.mode;
  else if (result.executionDevice === "cpu") executionMode = "cpu-only";
  // A GPU run that never said whether some layers stayed on the CPU.
  else executionMode = "unknown";
  const installed = serving?.host.ramInstalledGb?.value ?? null;
  const reported = serving ? serving.host.ramGb.value : result.hardware.ramGb;
  const application = serving?.application;
  return {
    cpu: serving ? serving.host.cpu.value : result.hardware.cpu,
    cpuCores: serving?.host.cpuCores?.value ?? null,
    cpuThreads: serving?.host.cpuThreads?.value ?? null,
    executionMode,
    inferenceGpus,
    inferenceGpuCount: inferenceGpus.reduce((total, gpu) => total + gpu.count, 0),
    ramGb: installed ?? reported ?? null,
    ramIsInstalled: installed !== null,
    runtime: application ? application.name : result.framework,
    quantization: result.quantization ?? NOT_RECORDED,
  };
}

// Capacity ranges, in GB. Upper bounds are inclusive, so a 16 GB card and a
// 32 GB machine fall in the range a person expects. Windows often reports a
// little less than the label (15.92 GB for a 16 GB card): that stays in range.
export type CapacityRange = { id: string; label: string; min: number; max: number };

export const RAM_RANGES: CapacityRange[] = [
  { id: "ram-16", label: "Up to 16 GB", min: 0, max: 16 },
  { id: "ram-32", label: "More than 16 GB, up to 32 GB", min: 16, max: 32 },
  { id: "ram-64", label: "More than 32 GB, up to 64 GB", min: 32, max: 64 },
  { id: "ram-128", label: "More than 64 GB, up to 128 GB", min: 64, max: 128 },
  { id: "ram-more", label: "More than 128 GB", min: 128, max: Number.POSITIVE_INFINITY },
];

export const VRAM_RANGES: CapacityRange[] = [
  { id: "vram-8", label: "Up to 8 GB per GPU", min: 0, max: 8 },
  { id: "vram-12", label: "More than 8 GB, up to 12 GB per GPU", min: 8, max: 12 },
  { id: "vram-16", label: "More than 12 GB, up to 16 GB per GPU", min: 12, max: 16 },
  { id: "vram-24", label: "More than 16 GB, up to 24 GB per GPU", min: 16, max: 24 },
  { id: "vram-48", label: "More than 24 GB, up to 48 GB per GPU", min: 24, max: 48 },
  { id: "vram-more", label: "More than 48 GB per GPU", min: 48, max: Number.POSITIVE_INFINITY },
];

export function inRange(value: number | null, range: CapacityRange): boolean {
  return value !== null && value > range.min && value <= range.max;
}

// Per GPU: every inference device must be in range. Two 8 GB cards are two
// 8 GB cards, not one 16 GB card, so they never match a 16 GB-per-GPU filter.
export function vramMatches(facets: HardwareFacets, range: CapacityRange): boolean {
  return facets.inferenceGpus.length > 0 && facets.inferenceGpus.every(gpu => inRange(gpu.memoryGb, range));
}

export type HardwareFilters = {
  cpu: string;
  gpu: string;
  ram: string;
  vram: string;
  mode: string;
  model: string;
  quantization: string;
  runtime: string;
  profile: string;
};

export const ANY = "all";
export const NO_FILTERS: HardwareFilters = {
  cpu: ANY, gpu: ANY, ram: ANY, vram: ANY, mode: ANY, model: ANY, quantization: ANY, runtime: ANY, profile: ANY,
};

const NO_GPU = "No inference GPU";

export function matchesFilters(result: ResultRecord, filters: HardwareFilters): boolean {
  const facets = hardwareFacets(result);
  const ram = RAM_RANGES.find(range => range.id === filters.ram);
  const vram = VRAM_RANGES.find(range => range.id === filters.vram);
  return (filters.cpu === ANY || (facets.cpu ?? NOT_RECORDED) === filters.cpu)
    && (filters.gpu === ANY || (filters.gpu === NO_GPU
      ? facets.inferenceGpus.length === 0
      : facets.inferenceGpus.some(gpu => gpu.model === filters.gpu)))
    && (filters.ram === ANY || (filters.ram === NOT_RECORDED ? facets.ramGb === null : !!ram && inRange(facets.ramGb, ram)))
    && (filters.vram === ANY || (filters.vram === NOT_RECORDED
      ? facets.inferenceGpus.some(gpu => gpu.memoryGb === null)
      : !!vram && vramMatches(facets, vram)))
    && (filters.mode === ANY || facets.executionMode === filters.mode)
    && (filters.model === ANY || result.model === filters.model)
    && (filters.quantization === ANY || facets.quantization === filters.quantization)
    && (filters.runtime === ANY || facets.runtime === filters.runtime)
    && (filters.profile === ANY || result.profile === filters.profile);
}

export function filterResults<T extends ResultRecord>(results: T[], filters: HardwareFilters): T[] {
  return results.filter(result => matchesFilters(result, filters));
}

export type FilterOption = { value: string; label: string };

const sortedUnique = (values: string[]) => Array.from(new Set(values)).sort((a, b) => a.localeCompare(b));

// Choices come from the data: a range nobody's result falls in is not offered.
export function filterOptions(results: ResultRecord[]): Record<keyof HardwareFilters, FilterOption[]> {
  const facets = results.map(hardwareFacets);
  const plain = (values: string[]) => sortedUnique(values).map(value => ({ value, label: value }));
  const gpuModels = sortedUnique(facets.flatMap(f => f.inferenceGpus.map(gpu => gpu.model)));
  const ranges = (all: CapacityRange[], present: (range: CapacityRange) => boolean, missing: boolean) => [
    ...all.filter(present).map(range => ({ value: range.id, label: range.label })),
    ...(missing ? [{ value: NOT_RECORDED, label: NOT_RECORDED }] : []),
  ];
  const modes = (Object.keys(EXECUTION_MODE_LABEL) as ExecutionMode[])
    .filter(mode => facets.some(f => f.executionMode === mode));
  return {
    cpu: plain(facets.map(f => f.cpu ?? NOT_RECORDED)),
    gpu: [
      ...gpuModels.map(value => ({ value, label: value })),
      ...(facets.some(f => f.inferenceGpus.length === 0) ? [{ value: NO_GPU, label: NO_GPU }] : []),
    ],
    ram: ranges(RAM_RANGES, range => facets.some(f => inRange(f.ramGb, range)), facets.some(f => f.ramGb === null)),
    vram: ranges(VRAM_RANGES, range => facets.some(f => vramMatches(f, range)),
      facets.some(f => f.inferenceGpus.some(gpu => gpu.memoryGb === null))),
    mode: modes.map(mode => ({ value: mode, label: EXECUTION_MODE_LABEL[mode] })),
    model: plain(results.map(r => r.model)),
    quantization: plain(facets.map(f => f.quantization)),
    runtime: plain(facets.map(f => f.runtime)),
    profile: plain(results.map(r => r.profile)),
  };
}

export function formatGb(value: number | null): string {
  if (value === null) return NOT_RECORDED;
  return `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value)} GB`;
}

export function gpuSummary(facets: HardwareFacets): string {
  if (facets.inferenceGpus.length === 0) return "None";
  return facets.inferenceGpus.map(gpu => `${gpu.count > 1 ? `${gpu.count} × ` : ""}${gpu.model}`).join(" + ");
}

export function vramSummary(facets: HardwareFacets): string {
  if (facets.inferenceGpus.length === 0) return "—";
  return facets.inferenceGpus
    .map(gpu => `${gpu.count > 1 ? `${gpu.count} × ` : ""}${gpu.memoryGb === null ? NOT_RECORDED : formatGb(gpu.memoryGb)}`)
    .join(" + ") + (facets.inferenceGpus.every(gpu => gpu.memoryGb === null) ? "" : " each");
}

// What a set of runs does not share, named so a reader is not left to assume
// that rows side by side measured the same thing.
export function differingSettings(results: ResultRecord[]): string[] {
  if (results.length < 2) return [];
  const differs = (read: (result: ResultRecord) => string) => new Set(results.map(read)).size > 1;
  const context = (r: ResultRecord) =>
    r.serving?.settings.find(setting => setting.name === "context_length")?.value ?? NOT_RECORDED;
  const checks: Array<[string, (r: ResultRecord) => string]> = [
    ["model", r => r.model],
    ["profile (workload)", r => `${r.profile}|${r.profileSha256}`],
    ["quantization", r => r.quantization ?? NOT_RECORDED],
    ["runtime", r => `${hardwareFacets(r).runtime} ${r.frameworkVersion}`],
    ["execution mode", r => hardwareFacets(r).executionMode],
    ["context length", context],
    ["transport", r => r.transport.kind],
    ["tokenizer", r => r.tokenizer?.source ?? NOT_RECORDED],
  ];
  return checks.filter(([, read]) => differs(read)).map(([label]) => label);
}

// "LM Studio 0.4.28 (build 2)", not "LM Studio LM Studio 0.4.28 (build 2)":
// some recorded versions already carry the application's name.
export function runtimeWithVersion(result: ResultRecord): string {
  const name = hardwareFacets(result).runtime;
  const version = result.frameworkVersion.trim();
  return version.toLowerCase().startsWith(name.toLowerCase()) ? version : `${name} ${version}`.trim();
}
