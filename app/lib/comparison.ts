import type { ResultRecord } from "./result-record";

export type MetricKey = "outputTokensPerSecond" | "ttftP50" | "ttftP95" | "itlP50" | "itlP95";
export type ComparisonEntry = { key: string; origin: "registry" | "local" | "example"; result: ResultRecord };
export const metrics: Record<MetricKey, { title: string; unit: string; higher: boolean }> = {
  outputTokensPerSecond: { title: "Output throughput", unit: "tok/s", higher: true },
  ttftP50: { title: "First token, p50", unit: "ms", higher: false },
  ttftP95: { title: "First token, p95", unit: "ms", higher: false },
  itlP50: { title: "Between tokens, p50", unit: "ms", higher: false },
  itlP95: { title: "Between tokens, p95", unit: "ms", higher: false },
};

// How this result's requests reached its server, for a reader rather than a
// schema. A 1.0 result records nothing here: the registry refused bridged
// evidence outright, so it was a direct run, and this says which of the two
// established that.
export function transportLabel(result: ResultRecord): string {
  if (result.transport.kind === "windows-wsl-stdio-bridge") {
    return "Windows–WSL bridge · its latency is inside every number here";
  }
  return result.transportRecorded ? "Direct to the server" : "Direct (not separately recorded)";
}

export function servingLabel(result: ResultRecord): string {
  if (!result.serving) return "Serving environment not recorded";
  return result.serving.relationship === "windows-host-wsl-runner"
    ? "Windows host served, WSL2 runner measured"
    : "One host served and measured";
}

export function placementLabel(result: ResultRecord): string {
  const mode = result.serving?.placement.mode ?? "unknown";
  if (mode === "cpu-gpu-offload") {
    return `Split CPU/GPU — ${result.serving?.placement.detail ?? "split not recorded"}`;
  }
  if (mode === "unknown") return "Not recorded";
  return mode === "cpu-only" ? "CPU only" : "GPU only";
}

export function tokenizerLabel(result: ResultRecord): string {
  if (!result.tokenizer) return "Not recorded";
  const loaded = result.tokenizer.loaded === "loaded-before-run"
    ? "loaded before the run" : "not loaded by OmoBench";
  return `${result.tokenizer.source} · ${loaded}`;
}

// Compare declared workloads, not configuration fingerprints: fingerprints
// include the hardware and runtime that people specifically want to compare.
export function incompatibilities(reference: ResultRecord, candidate: ResultRecord): string[] {
  const differences: string[] = [];
  if (reference.model !== candidate.model) differences.push("Different model");
  if (reference.modelRevision !== candidate.modelRevision) differences.push("Different weights revision");
  if (reference.modelSource !== candidate.modelSource) differences.push("Different or missing weights source");
  if (!reference.quantization?.trim() || !candidate.quantization?.trim()) differences.push("Quantization not recorded");
  else if (reference.quantization !== candidate.quantization) differences.push("Different quantization");
  if (reference.profile !== candidate.profile) differences.push("Different benchmark profile");
  if (!reference.profileSha256 || !candidate.profileSha256 || reference.profileSha256 !== candidate.profileSha256) differences.push("Different or missing profile checksum");
  if (!reference.runnerVersion || !candidate.runnerVersion || reference.runnerVersion !== candidate.runnerVersion) differences.push("Different or missing runner version");
  // A relay sits inside every request a bridged run made, and its latency was
  // never separated out. Putting one beside a direct run in the same chart
  // would present a transport difference as a hardware difference.
  if (reference.transport.kind !== candidate.transport.kind) differences.push("Different transport");
  // Serving on one machine and measuring on another is a different
  // arrangement, not a different setting.
  if ((reference.serving?.relationship ?? "same-host") !== (candidate.serving?.relationship ?? "same-host")) {
    differences.push("Different serving arrangement");
  }
  // cpu-only and gpu-only are already separated by the execution device.
  // Splitting the weights across both is the arrangement that would otherwise
  // hide inside "GPU".
  if ((reference.serving?.placement.mode === "cpu-gpu-offload")
      !== (candidate.serving?.placement.mode === "cpu-gpu-offload")) {
    differences.push("Different weight placement");
  }
  // Token counts are this tokenizer's counts. Two results counted by
  // different tokenizers have not necessarily processed the same work.
  if ((reference.tokenizer?.source ?? null) !== (candidate.tokenizer?.source ?? null)) {
    differences.push("Different or unrecorded tokenizer source");
  }
  // A proposed model has not been reviewed. Its measurements are real; its
  // identity is a request, and it does not belong in a catalogued leaderboard.
  if (reference.catalogStatus !== candidate.catalogStatus) differences.push("One model is proposed, not catalogued");
  return differences;
}

export function metricDifference(value: number, reference: number, higher: boolean): { text: string; favorable: boolean | null } {
  if (![value, reference].every(n => Number.isFinite(n) && n >= 0)) return { text: "Unavailable", favorable: null };
  if (value === reference) return { text: "Same as reference", favorable: null };
  if (reference === 0) return { text: "Reference is zero, no percentage", favorable: null };
  const percent = (value - reference) / reference * 100;
  if (!Number.isFinite(percent)) return { text: "Difference outside display range", favorable: null };
  const magnitude = Math.abs(percent);
  const amount = magnitude < 0.1 ? "<0.1" : magnitude >= 1e6 ? formatMetric(magnitude) : new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(magnitude);
  return { text: `${amount}% ${percent > 0 ? "higher" : "lower"} than reference`, favorable: higher ? percent > 0 : percent < 0 };
}

export function formatMetric(value: number): string {
  return new Intl.NumberFormat("en-US", {
    maximumSignificantDigits: 4,
    notation: value >= 1e6 || (value > 0 && value < 0.001) ? "scientific" : "standard",
  }).format(value);
}

export function barPercent(value: number, maximum: number): number {
  if (!Number.isFinite(value) || !Number.isFinite(maximum) || value < 0 || maximum <= 0) return 0;
  return Math.min(100, value / maximum * 100);
}

export function rankRuns(entries: ComparisonEntry[], metric: MetricKey): Array<{ entry: ComparisonEntry; rank: number }> {
  const sorted = entries.filter(entry => Number.isFinite(entry.result[metric]) && entry.result[metric] >= 0)
    .slice().sort((a, b) => {
      const delta = a.result[metric] - b.result[metric];
      return (metrics[metric].higher ? -delta : delta) || a.key.localeCompare(b.key);
    });
  let rank = 0;
  return sorted.map((entry, index) => {
    if (index === 0 || entry.result[metric] !== sorted[index - 1].result[metric]) rank = index + 1;
    return { entry, rank };
  });
}

export function eligibleLeaderboard(entries: ComparisonEntry[], reference: ResultRecord, mode: "registry" | "example") {
  return entries.filter(entry => entry.origin === mode && incompatibilities(reference, entry.result).length === 0);
}

export function comparisonHref(id: string): string {
  return `/compare?run=${encodeURIComponent(id)}`;
}
