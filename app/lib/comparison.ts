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

// The context window the server was started with, where the contributor
// declared it. "Not recorded" is its own answer: a 1.0 result never collected
// it, and a 1.1 result only has it if somebody wrote it down.
export function contextLabel(result: ResultRecord): string {
  const setting = result.serving?.settings.find((entry) => entry.name === "context_length");
  return setting ? `${setting.value} tokens` : "Not recorded";
}

export function quantizationLabel(result: ResultRecord): string {
  return result.quantization?.trim() || "Not recorded";
}

export function runtimeLabel(result: ResultRecord): string {
  const engine = result.serving?.engine;
  const application = result.serving?.application;
  const build = engine?.name
    ? `${engine.name} ${engine.version ?? ""}`.trim()
    : `${result.framework} ${result.frameworkVersion}`.trim();
  // The hosting application's release and the engine build it embeds are two
  // different versions, so they are never run together into one string.
  return application ? `${build} · hosted by ${application.name} ${application.version}` : build;
}

export function tokenizerLabel(result: ResultRecord): string {
  if (!result.tokenizer) return "Not recorded";
  const loaded = result.tokenizer.loaded === "loaded-before-run"
    ? "loaded before the run" : "not loaded by OmoBench";
  return `${result.tokenizer.source} · ${loaded}`;
}

// --- Is this a comparison worth looking at? --------------------------------
//
// "Comparable" is not a property of two runs. It is a property of two runs and
// a question. Asking which of two machines is faster requires the model and
// the workload to match and *requires* the hardware to differ; judging that
// pair incomparable because their hardware differs answers a question nobody
// asked. So the question comes in, and the answer says what it assumed.
//
// Three outcomes, and the strictest one is unchanged from what the leaderboard
// already required. Ranking means every dimension matches. Qualified means the
// runs differ only where the question meant them to, or something needed is
// simply not recorded -- look at them side by side, with the qualification
// stated. Unsuitable means they differ somewhere the question holds fixed.

export type ComparisonQuestion = "exact" | "hardware" | "runtime" | "model" | "workload";
export type Suitability = "ranking" | "qualified" | "unsuitable";

export const comparisonQuestions: Record<ComparisonQuestion, { title: string; detail: string }> = {
  exact: {
    title: "Repeat of the same setup",
    detail: "Everything matches: the same model, weights, quantization, workload, runtime, "
      + "placement and transport. Only these can be ranked against each other.",
  },
  hardware: {
    title: "Different hardware, same everything else",
    detail: "The machines are meant to differ. The model, its weights and quantization, the "
      + "workload and the tokenizer are held fixed, so what is left to explain the difference "
      + "is the hardware and how the weights sat on it.",
  },
  runtime: {
    title: "Different serving runtime",
    detail: "The engine and its build are meant to differ. The model, workload and hardware are "
      + "held fixed.",
  },
  model: {
    title: "Different model",
    detail: "The models are meant to differ. Token rates across different models do not measure "
      + "the same semantic work: a token is not a unit of meaning, and two models do not spend "
      + "them alike.",
  },
  workload: {
    title: "Different workloads on the same machine",
    detail: "The machine is held fixed and the workloads are meant to differ. This asks what one "
      + "computer does across the things it could be asked to run. It does not rank the models "
      + "against each other: a token is not a unit of meaning, two models do not spend tokens "
      + "alike, and none of these numbers says anything about the quality of an answer.",
  },
};

type Dimension = {
  id: string;
  label: string;
  // The questions that intend this dimension to differ. Empty means no
  // question does: a difference here is never the point of a comparison.
  varies: ComparisonQuestion[];
  value: (result: ResultRecord) => string | null;
  // The short statement the leaderboard has always shown when this dimension
  // stands in the way. Kept verbatim so the rule reads the same as it did.
  blocker: string;
  // Reported when it differs, but never a reason to refuse. The leaderboard
  // has always been "the same declared workload on different setups", so the
  // machine, the engine build and the device vary freely in every question;
  // saying so is useful, gating on it would not be.
  informational?: boolean;
  // Questions under which an otherwise-informational dimension is held fixed.
  // Asking what one machine does across workloads is not a question two
  // machines can answer, so the hardware stops being a free variable there.
  fixedFor?: ComparisonQuestion[];
  // A dimension neither run records is usually an agreement about silence.
  // Quantization is the exception: it changes the numbers enough that two runs
  // which both omit it cannot be ranked against each other.
  requiredOnBoth?: boolean;
};

const dimensions: Dimension[] = [
  // Fixed under every question. The workload has to be the same workload, and
  // a relay inside every request is not a property of the thing measured.
  {
    id: "profile", label: "Benchmark profile", varies: [], blocker: "Different benchmark profile",
    value: (r) => r.profile || null,
  },
  {
    id: "profileBytes", label: "Profile checksum", varies: [],
    blocker: "Different or missing profile checksum", value: (r) => r.profileSha256 || null,
  },
  {
    id: "runner", label: "Runner version", varies: [],
    blocker: "Different or missing runner version", value: (r) => r.runnerVersion || null,
  },
  {
    id: "transport", label: "Transport", varies: [], blocker: "Different transport",
    value: (r) => r.transport.kind,
  },
  {
    id: "arrangement", label: "Serving arrangement", varies: [],
    blocker: "Different serving arrangement",
    value: (r) => r.serving?.relationship ?? "same-host",
  },

  // The model and the bytes behind it. Only a question about models lets these
  // differ, and then only with the warning that token rates across models do
  // not measure the same work.
  { id: "model", label: "Model", varies: ["model", "workload"], blocker: "Different model",
    value: (r) => r.model || null },
  {
    // Judged by weightsIdentity() below, not by string equality: an
    // identity nobody established is never an agreement, and a file
    // fingerprint and a repository commit are different kinds of thing, and
    // a bare revision with no recorded kind establishes neither.
    id: "weights", label: "Weights", varies: ["model", "workload"],
    blocker: "Different weights", value: (r) => describeWeights(weightsIdentity(r)),
  },
  {
    id: "quantization", label: "Quantization", varies: ["model", "workload"],
    blocker: "Different quantization", requiredOnBoth: true,
    value: (r) => (r.quantization?.trim() ? r.quantization : null),
  },
  {
    id: "tokenizer", label: "Tokenizer", varies: ["model", "workload"],
    blocker: "Different or unrecorded tokenizer source",
    value: (r) => r.tokenizer?.source ?? null,
  },
  {
    id: "catalog", label: "Catalog status", varies: ["model", "workload"],
    blocker: "One model is proposed, not catalogued",
    // Three states, and only one of them is a pending request. An
    // uncatalogued result was accepted on its own declared eligibility; it is
    // not waiting on anybody, so it is not held out of a ranking as if it were.
    value: (r) => (r.catalogStatus === "proposed" ? "proposed, not catalogued" : "catalogued"),
  },

  // How the weights sat on the machine. Two machines or two engines place them
  // differently, and that is the point of asking about either.
  {
    id: "placement", label: "Weight placement", varies: ["hardware", "runtime", "workload"],
    blocker: "Different weight placement",
    value: (r) => (r.serving?.placement.mode === "cpu-gpu-offload" ? "split CPU/GPU" : "undivided"),
  },

  // The context window the server was started with. It changes what a request
  // costs to serve, so two runs that differ here differ in what was measured,
  // not only in how fast it went: holding the hardware or the engine as the
  // explanation would be wrong. Only a question about workloads lets it vary,
  // because a different model may need a different window. Recorded from 1.1
  // on and only when declared; two runs that both omit it agree about silence.
  {
    id: "context", label: "Context length", varies: ["workload"],
    blocker: "Different context length",
    value: (r) => r.serving?.settings.find(
      (setting) => setting.name === "context_length")?.value ?? null,
  },

  // Reported, never gated.
  {
    id: "runtime", label: "Runtime build", varies: [], informational: true, blocker: "",
    value: (r) => `${r.framework} ${r.frameworkVersion}`,
  },
  {
    id: "device", label: "Execution device", varies: [], informational: true, blocker: "",
    value: (r) => r.executionDevice,
  },
  {
    id: "hardware", label: "Serving hardware", varies: [], informational: true,
    fixedFor: ["workload"], blocker: "Different serving hardware",
    value: (r) => r.hardware.display || null,
  },
];

export type ComparisonAssessment = {
  suitability: Suitability;
  question: ComparisonQuestion;
  // The short statements that keep this pair out of a ranking.
  blockers: string[];
  // Why it is what it is, in prose, for the reader rather than the filter.
  reasons: string[];
  missing: string[];
  differences: string[];
};

// --- weights identity ---------------------------------------------------------

// A value recorded as a revision that names a place instead of bytes: a drive,
// a share, an absolute or relative path, a home directory, or any URL.
const PATH_LIKE = /^(?:[A-Za-z]:(?:[\\/]|$)|\\\\|\/\/|\/|~|\.{1,2}(?:[\\/]|$))/;
const URL_LIKE = /^[A-Za-z][A-Za-z0-9+.-]*:\/\//;
const MOVING = new Set(["main", "master", "head", "latest"]);

export type WeightsIdentity =
  | { established: true; kind: "file-fingerprint" | "repository-commit"; key: string; label: string;
      provenance: string | null }
  | { established: false; why: string };

/** What a record establishes about its weights, typed, without guessing from a string's shape. */
export function weightsIdentity(result: ResultRecord): WeightsIdentity {
  const weights = result.weights;
  if (weights?.identity === "content-hash" && weights.manifestSha256) {
    return { established: true, kind: "file-fingerprint", key: weights.manifestSha256,
      label: `file fingerprint ${weights.manifestSha256}`, provenance: weights.provenance };
  }
  if (weights?.identity === "repository-revision" && weights.repository && weights.repositoryRevision) {
    return { established: true, kind: "repository-commit", key: `${weights.repository}@${weights.repositoryRevision}`,
      label: `${weights.repository} at ${weights.repositoryRevision}`, provenance: weights.provenance };
  }
  // No typed identity. The record stays readable and its revision is shown
  // wherever the run is, but a bare model.revision never establishes which
  // bytes ran: its contract was "an immutable model revision or commit SHA"
  // and never recorded which of those a value is, nor checked it against
  // anything. Two equal strings of that kind are equal strings, not the same
  // weights, so they are never read as agreeing. Nothing about a value's
  // length or shape changes that.
  const revision = (result.modelRevision ?? "").trim();
  if (!revision) return { established: false, why: "no weights revision is recorded" };
  if (URL_LIKE.test(revision)) return { established: false, why: `the recorded revision (${revision}) is a URL, which names a place, not bytes` };
  if (PATH_LIKE.test(revision) || revision.includes("\\") || revision.includes("/")) {
    return { established: false, why: `the recorded revision (${revision}) is a local folder path, which identifies no bytes` };
  }
  if (MOVING.has(revision.toLowerCase())) return { established: false, why: `the recorded revision (${revision}) is a label that moves` };
  return { established: false,
    why: `the recorded revision (${revision}) does not say what it identifies: a repository commit, a file hash or `
      + "something else. The record was made before weights identities were typed, and nothing in it establishes which" };
}

function describeWeights(identity: WeightsIdentity): string | null {
  return identity.established ? identity.label : null;
}

const KIND_WORDS = {
  "file-fingerprint": "a fingerprint of the model files",
  "repository-commit": "a repository commit",
} as const;

function compareWeights(left: ResultRecord, right: ResultRecord):
    { same: true; note?: string } | { same: false; blocker: string; reason: string } {
  const a = weightsIdentity(left), b = weightsIdentity(right);
  if (!a.established || !b.established) {
    const parts = [!a.established ? `the reference run: ${a.why}` : null, !b.established ? `the other run: ${b.why}` : null]
      .filter(Boolean).join("; ");
    return { same: false, blocker: "Weights identity not established",
      reason: `Whether these runs used the same weights cannot be established (${parts}).` };
  }
  if (a.kind !== b.kind) {
    return { same: false, blocker: "Weights identified differently",
      reason: `One run identifies its weights by ${KIND_WORDS[a.kind]} and the other by ${KIND_WORDS[b.kind]}; `
        + "the two are not interchangeable, so they cannot be shown to be the same bytes." };
  }
  if (a.key !== b.key) {
    return { same: false, blocker: "Different weights", reason: `Different weights: ${a.label} against ${b.label}.` };
  }
  const chosen = [a, b].some(id => id.provenance === "contributor-selected");
  return { same: true, note: chosen
    ? "Weights: the same file fingerprint, at least one chosen by its contributor after the run rather than verified as the file served."
    : undefined };
}

export function assess(
  reference: ResultRecord,
  candidate: ResultRecord,
  question: ComparisonQuestion = "exact",
): ComparisonAssessment {
  const blockers: string[] = [];
  const reasons: string[] = [];
  const missing: string[] = [];
  const differences: string[] = [];
  let suitability: Suitability = "ranking";

  for (const dimension of dimensions) {
    if (dimension.id === "weights") {
      // Gaps are listed apart from differences, as for every other dimension.
      for (const [result, where] of [[reference, "the reference run"], [candidate, "the other run"]] as const) {
        const identity = weightsIdentity(result);
        if (!identity.established) missing.push(`Weights identity is not established on ${where}`);
      }
      const verdict = compareWeights(reference, candidate);
      if (verdict.same) {
        if (verdict.note) differences.push(verdict.note);
        continue;
      }
      if (dimension.varies.includes(question)) {
        differences.push(`Weights: ${verdict.reason}`);
        suitability = suitability === "unsuitable" ? "unsuitable" : "qualified";
        continue;
      }
      suitability = "unsuitable";
      blockers.push(verdict.blocker);
      reasons.push(`${verdict.reason} This comparison holds the weights fixed.`);
      continue;
    }
    const left = dimension.value(reference);
    const right = dimension.value(candidate);
    const absent = left === null || right === null;

    if (absent && left === right) {
      // Both silent. Usually an agreement rather than a difference.
      if (!dimension.requiredOnBoth) continue;
      missing.push(`${dimension.label} is not recorded on either run`);
      blockers.push(`${dimension.label} not recorded`);
      suitability = suitability === "unsuitable" ? "unsuitable" : "qualified";
      continue;
    }
    if (absent) {
      missing.push(`${dimension.label} is not recorded on ${
        left === null ? "the reference run" : "the other run"}`);
    }
    if (left === right) continue;
    // A dimension the question holds fixed gates it, however freely it varies
    // elsewhere. This is checked before the informational shortcut, which
    // would otherwise let the fixed thing differ and only mention it.
    const fixedHere = dimension.fixedFor?.includes(question) ?? false;
    if (dimension.informational && !fixedHere) {
      differences.push(`${dimension.label}: ${left ?? "not recorded"} against ${right ?? "not recorded"}`);
      continue;
    }
    if (!fixedHere && dimension.varies.includes(question)) {
      differences.push(`${dimension.label}: ${left ?? "not recorded"} against ${right ?? "not recorded"}`);
      suitability = suitability === "unsuitable" ? "unsuitable" : "qualified";
      continue;
    }
    suitability = "unsuitable";
    blockers.push(dimension.blocker);
    reasons.push(
      dimension.id === "transport"
        ? `Different transport: ${left} against ${right}. A bridged run carries that bridge's `
          + "latency inside every number it reports, and it was never subtracted, so what "
          + "separates these two is not what is being measured."
        : dimension.id === "catalog"
        ? "One of these models is proposed for the catalog rather than catalogued. Its "
          + "measurements are real; its identity is still a request, so it does not join a ranking."
        : dimension.id === "hardware"
        ? `These ran on different machines (${left ?? "not recorded"} against ${right ?? "not recorded"}). `
          + "Asking what one computer does across workloads is a question about one computer. "
          + "To compare machines, ask the hardware question instead."
        : `${dimension.label} differs (${left ?? "not recorded"} against ${right ?? "not recorded"}), `
          + "which this comparison holds fixed. Ask a different question, or choose a different run.");
  }

  // A run that cannot be shown to have been running throughout never ranks,
  // whatever the question. The registry refuses such a bundle, so one should
  // not be here at all — but a bundle can be read from a file, a fork or a
  // branch that never went through the registry, and a measurement that may
  // contain time the machine was not running is not a measurement of the
  // model under any question somebody might ask of it.
  //
  // The two states are kept apart in what they say, because only one of them
  // is a conclusion: a confirmed suspension, against two clocks that
  // disagreed while the kernel recorded nothing.
  for (const [record, side] of [[reference, "This run"], [candidate, "The other run"]] as const) {
    const state = record.execution?.interruption?.state;
    if (state !== "suspended" && state !== "clock-jump") continue;
    const seconds = Math.round(record.execution?.interruption?.observedSeconds ?? 0);
    suitability = "unsuitable";
    blockers.push(state === "suspended" ? "Suspended while measuring" : "Clocks disagreed while measuring");
    reasons.push(state === "suspended"
      ? `${side} was suspended for ${seconds} seconds while it was measuring. Nothing in a `
        + "benchmark can tell a paused machine from slow inference, so that time is inside its "
        + "latencies. It is kept for diagnosis and does not rank."
      : `${side} had its wall clock and monotonic clock disagree by ${seconds} seconds while it `
        + "was measuring, with no suspend recorded by the kernel. That is either a pause it was "
        + "never told about or an adjusted clock, and nothing establishes which, so it is not "
        + "established that the machine was running throughout. It does not rank.");
  }

  if (suitability === "ranking") {
    reasons.push("The declared workload matches on every dimension, including the exact profile "
      + "bytes and the transport. Hardware, engine build and execution device may differ: that "
      + "is what this comparison is for.");
  } else {
    reasons.unshift(comparisonQuestions[question].detail);
  }
  // Said whenever the models differ on purpose, which is both questions that
  // let them. Measuring one machine across workloads is still not a ranking
  // of the workloads, and nothing here is a statement about answer quality.
  if ((question === "model" || question === "workload")
      && differences.some((text) => text.startsWith("Model:"))) {
    reasons.push("Token rates are not comparable across models as a measure of work done: a "
      + "token is not a unit of meaning, and two models do not spend them alike.");
  }
  return { suitability, question, blockers, reasons, missing, differences };
}

export function suitabilityLabel(assessment: ComparisonAssessment): string {
  if (assessment.suitability === "ranking") return "Directly comparable";
  if (assessment.suitability === "qualified") return "Comparable, with qualifications";
  return "Not comparable for this question";
}

// Compare declared workloads, not configuration fingerprints: fingerprints
// include the hardware and runtime that people specifically want to compare.
export function incompatibilities(reference: ResultRecord, candidate: ResultRecord): string[] {
  // Strict ranking eligibility, unchanged: the same declared workload, the
  // same transport, nothing unrecorded that matters. Expressed through
  // assess() so the leaderboard, the filters and the detail pages cannot
  // drift apart.
  return assess(reference, candidate, "exact").blockers;
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

export function comparisonHref(id: string, peers: string[] = []): string {
  const query = peers.length ? `&peers=${peers.map(encodeURIComponent).join(",")}` : "";
  return `/compare?run=${encodeURIComponent(id)}${query}`;
}
